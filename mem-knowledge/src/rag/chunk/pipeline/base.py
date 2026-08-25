import logging
from abc import ABC, abstractmethod
from copy import deepcopy
from timeit import default_timer as timer

from ..context import (
    ChunkContext,
    ChunkOutputMode,
    ImageVisionScope,
    LogicalChunk,
    LogicalChunkType,
    MergeResult,
    ParseResult,
    is_direct_image_vision_enabled,
    is_embedded_image_vision_enabled,
)

LOGGER = logging.getLogger(__name__)


def _append_external_parent_child_chunks(
    child_res: list[dict],
    parent_res: list[dict],
    parent_id_map: dict[int, int],
    external_chunks: list[dict],
) -> tuple[list[dict], list[dict], dict[int, int]]:
    for external_chunk in external_chunks:
        child_index = len(child_res)
        parent_index = len(parent_res)
        content = external_chunk.get("content_with_weight", "")
        parent_res.append(
            {
                "content_with_weight": content,
                "image": external_chunk.get("image"),
                "metadata": deepcopy(external_chunk.get("metadata", {})),
            }
        )
        child_res.append(external_chunk)
        parent_id_map[child_index] = parent_index
    return child_res, parent_res, parent_id_map


def _build_atomic_parent_child_chunks(
    chunks: list[dict],
) -> tuple[list[dict], list[dict], dict[int, int]]:
    parent_res: list[dict] = []
    parent_id_map: dict[int, int] = {}
    for index, chunk in enumerate(chunks):
        parent_res.append(
            {
                "content_with_weight": chunk.get("content_with_weight", ""),
                "image": chunk.get("image"),
                "metadata": deepcopy(chunk.get("metadata", {})),
            }
        )
        parent_id_map[index] = index
    return chunks, parent_res, parent_id_map


class ChunkPipeline(ABC):
    def __init__(self):
        from ..postprocessor import ChunkPostProcessor
        from ..preprocessor import EmbedPreprocessor, HyperlinkPreprocessor

        self.embed_preprocessor = EmbedPreprocessor()
        self.hyperlink_preprocessor = HyperlinkPreprocessor()
        self.postprocessor = ChunkPostProcessor()

    def run(self, ctx: ChunkContext) -> list | tuple[list[dict], list[dict], dict[int, int]]:
        embed_res = self.embed_preprocessor.collect(ctx, self.run_child)
        parse_result = self.parse(ctx)

        if parse_result.direct_result is not None:
            finalized = self.finalize_result(
                parse_result.direct_result,
                embed_res,
                parse_result.url_res or [],
                parse_result.append_embed,
            )
            if ctx.chunk_output_mode is ChunkOutputMode.PARENT_CHILD:
                return _build_atomic_parent_child_chunks(finalized)
            return finalized

        start = timer()
        merge_result = self.merge(ctx, parse_result)
        self.enhance_merged_images(ctx, parse_result, merge_result)
        self.validate_direct_image_result(parse_result, merge_result)
        if ctx.kwargs.get("section_only", False):
            return self.finalize_result(merge_result.chunks, embed_res, parse_result.url_res or [])

        url_res = parse_result.url_res or []
        url_res.extend(
            self.hyperlink_preprocessor.collect_url_chunks(
                ctx, parse_result.urls or set(), self.run_child
            )
        )
        main_result = self.postprocessor.process(ctx, parse_result, merge_result)
        LOGGER.info("naive_merge(%s): %s", ctx.filename, timer() - start)
        if isinstance(main_result, tuple):
            child_res, parent_res, parent_id_map = main_result
            return _append_external_parent_child_chunks(
                child_res, parent_res, parent_id_map, embed_res + url_res
            )

        finalized = self.finalize_result(main_result, embed_res, url_res)
        return finalized

    @abstractmethod
    def parse(self, ctx: ChunkContext) -> ParseResult:
        pass

    def merge(self, ctx: ChunkContext, parse_result: ParseResult) -> MergeResult:
        from ..merger.block import BlockMerger
        from ..merger.naive import DocxMerger, ImageMerger, NaiveMerger

        if parse_result.merge_strategy == "blocks" and parse_result.blocks:
            return BlockMerger().merge(ctx, parse_result)

        if parse_result.merge_strategy == "docx":
            return DocxMerger().merge(ctx, parse_result)

        section_images = parse_result.section_images
        if section_images and all(image is None for image in section_images):
            section_images = None

        if parse_result.merge_strategy == "with_images" and section_images:
            parse_result.section_images = section_images
            return ImageMerger().merge(ctx, parse_result)

        return NaiveMerger().merge(ctx, parse_result)

    def enhance_merged_images(
        self,
        ctx: ChunkContext,
        parse_result: ParseResult,
        merge_result: MergeResult,
    ) -> None:
        try:
            from ..parser.image_vision import enhance_complete_image_chunks_with_vision
        except ModuleNotFoundError as exc:
            if exc.name == "src.rag.chunk.parser.image_vision":
                return
            raise

        enabled_scopes = set()
        if is_embedded_image_vision_enabled(ctx.parser_config):
            enabled_scopes.add(ImageVisionScope.EMBEDDED)
        if is_direct_image_vision_enabled(ctx.parser_config):
            enabled_scopes.add(ImageVisionScope.DIRECT)

        enhance_complete_image_chunks_with_vision(
            self._vision_candidate_chunks(ctx, merge_result),
            vision_model=ctx.vision_model,
            enabled_scopes=enabled_scopes,
            callback=ctx.callback,
            lang=ctx.lang,
        )

    def validate_direct_image_result(
        self,
        parse_result: ParseResult,
        merge_result: MergeResult,
    ) -> None:
        mode = parse_result.direct_image_vision_mode
        if mode not in {1, 2}:
            return

        has_direct_vision_text = any(
            chunk.type is LogicalChunkType.IMAGE
            and chunk.image_tag_complete
            and chunk.image_vision_scope is ImageVisionScope.DIRECT
            and bool(str(chunk.metadata.get("vision_text") or "").strip())
            for chunk in self._all_vision_result_chunks(merge_result)
        )
        if mode == 1 and not parse_result.direct_image_has_ocr_text and not has_direct_vision_text:
            raise ValueError("Image mixed mode produced neither OCR text nor visual description.")
        if mode == 2 and not has_direct_vision_text:
            raise ValueError("Image pure vision mode produced no visual description.")

    def _vision_candidate_chunks(
        self,
        ctx: ChunkContext,
        merge_result: MergeResult,
    ) -> list[LogicalChunk]:
        if ctx.chunk_output_mode is ChunkOutputMode.PARENT_CHILD:
            return [
                child
                for group in merge_result.parent_child_groups or []
                for child in group.children
            ]
        return list(merge_result.logical_chunks or [])

    def _all_vision_result_chunks(self, merge_result: MergeResult) -> list[LogicalChunk]:
        if merge_result.parent_child_groups is not None:
            return [child for group in merge_result.parent_child_groups for child in group.children]
        return list(merge_result.logical_chunks or [])

    def run_child(
        self,
        filename,
        binary=None,
        ctx: ChunkContext | None = None,
        is_root=False,
        vision_model=None,
        **kwargs,
    ) -> list:
        from ..context import build_chunk_context
        from ..router import FileTypeRouter

        child_kwargs = self.child_kwargs(ctx) if ctx else {}
        child_kwargs.update(kwargs)
        child_kwargs["is_root"] = is_root
        child_context = build_chunk_context(
            filename=filename,
            binary=binary,
            lang=ctx.lang if ctx else "Chinese",
            callback=ctx.callback if ctx else None,
            vision_model=(
                vision_model if vision_model is not None else (ctx.vision_model if ctx else None)
            ),
            **child_kwargs,
        )
        return FileTypeRouter().route(filename).run(child_context)

    def child_kwargs(self, ctx: ChunkContext | None) -> dict:
        if not ctx:
            return {}
        child_kwargs = dict(ctx.kwargs)
        child_kwargs.pop("is_root", None)
        child_kwargs["chunk_output_mode"] = ChunkOutputMode.NORMAL
        return child_kwargs

    def finalize_result(self, main_result, embed_res, url_res, append_embed=True) -> list:
        result = main_result or []
        if append_embed and embed_res:
            result.extend(embed_res)
        if url_res:
            result.extend(url_res)
        return result
