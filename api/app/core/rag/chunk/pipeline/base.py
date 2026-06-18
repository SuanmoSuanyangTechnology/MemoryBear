import logging
from abc import ABC, abstractmethod
from timeit import default_timer as timer

from app.core.rag.chunk.context import ChunkContext, ChunkOutputMode, MergeResult, ParseResult


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
                from app.core.rag.chunk.parent_child import build_atomic_parent_child_chunks

                return build_atomic_parent_child_chunks(finalized)
            return finalized

        start = timer()
        merge_result = self.merge(ctx, parse_result)
        if ctx.kwargs.get("section_only", False):
            return self.finalize_result(merge_result.chunks, embed_res, parse_result.url_res or [])

        url_res = parse_result.url_res or []
        url_res.extend(self.hyperlink_preprocessor.collect_url_chunks(ctx, parse_result.urls or set(), self.run_child))
        main_result = self.postprocessor.process(ctx, parse_result, merge_result)
        logging.info("naive_merge({}): {}".format(ctx.filename, timer() - start))
        if isinstance(main_result, tuple):
            from app.core.rag.chunk.parent_child import append_external_parent_child_chunks

            child_res, parent_res, parent_id_map = main_result
            return append_external_parent_child_chunks(child_res, parent_res, parent_id_map, embed_res + url_res)

        finalized = self.finalize_result(main_result, embed_res, url_res)
        return finalized

    @abstractmethod
    def parse(self, ctx: ChunkContext) -> ParseResult:
        pass

    def merge(self, ctx: ChunkContext, parse_result: ParseResult) -> MergeResult:
        from ..merger.naive import DocxMerger, ImageMerger, NaiveMerger

        if parse_result.merge_strategy == "docx":
            return DocxMerger().merge(ctx, parse_result)

        section_images = parse_result.section_images
        if section_images and all(image is None for image in section_images):
            section_images = None

        if parse_result.merge_strategy == "with_images" and section_images:
            parse_result.section_images = section_images
            return ImageMerger().merge(ctx, parse_result)

        return NaiveMerger().merge(ctx, parse_result)

    def run_child(
        self,
        filename,
        binary=None,
        ctx: ChunkContext | None = None,
        is_root=False,
        vision_model=None,
        **kwargs,
    ) -> list:
        from app.core.rag.chunk import chunk_pipeline

        child_kwargs = self.child_kwargs(ctx) if ctx else {}
        child_kwargs.update(kwargs)
        child_kwargs["is_root"] = is_root
        return chunk_pipeline(
            filename,
            binary=binary,
            lang=ctx.lang if ctx else "Chinese",
            callback=ctx.callback if ctx else None,
            vision_model=vision_model if vision_model is not None else (ctx.vision_model if ctx else None),
            **child_kwargs,
        )

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
