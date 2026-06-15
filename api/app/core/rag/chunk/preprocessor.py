import logging

from app.core.rag.utils.file_utils import extract_embed_file, extract_html

from .context import ChunkContext


class EmbedPreprocessor:
    def collect(self, ctx: ChunkContext, run_child) -> list:
        embed_res = []
        if not ctx.is_root:
            return embed_res

        if ctx.binary is not None:
            embeds = extract_embed_file(ctx.binary)
        else:
            raise Exception("Embedding extraction from file path is not supported.")

        for embed_filename, embed_bytes in embeds:
            try:
                sub_res = run_child(
                    embed_filename,
                    binary=embed_bytes,
                    ctx=ctx,
                    is_root=False,
                ) or []
                embed_res.extend(sub_res)
            except Exception as exc:
                if ctx.callback:
                    ctx.callback(0.05, f"Failed to chunk embed {embed_filename}: {exc}")
                continue

        return embed_res


class HyperlinkPreprocessor:
    def collect_url_chunks(self, ctx: ChunkContext, urls, run_child) -> list:
        url_res = []
        if not urls or not ctx.parser_config.get("analyze_hyperlink", False) or not ctx.is_root:
            return url_res

        for index, url in enumerate(urls):
            html_bytes, _ = extract_html(url)
            if not html_bytes:
                continue
            try:
                sub_url_res = run_child(url, binary=html_bytes, ctx=ctx, is_root=False)
            except Exception as exc:
                logging.info(f"Failed to chunk url in registered file type {url}: {exc}")
                sub_url_res = run_child(
                    f"{index}.html",
                    binary=html_bytes,
                    ctx=ctx,
                    is_root=False,
                    vision_model=ctx.vision_model,
                )
            url_res.extend(sub_url_res)
        return url_res
