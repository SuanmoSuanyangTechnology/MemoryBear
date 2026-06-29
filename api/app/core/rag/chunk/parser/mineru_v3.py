import logging
import re

from app.core.rag.chunk.context import ParseResult
from app.core.rag.chunk.parser.base import DocumentParser
from app.core.rag.chunk.parser.mineru_v3_client import MinerUV3Client
from app.core.rag.chunk.parser.structured_markdown import StructMarkdownParser


LOGGER = logging.getLogger(__name__)
IMAGE_PATTERN = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")


def strip_markdown_images(markdown: str) -> tuple[str, int]:
    cleaned_lines: list[str] = []
    image_count = 0
    in_code_block = False

    for line in markdown.splitlines():
        if line.strip().startswith("```"):
            in_code_block = not in_code_block
            cleaned_lines.append(line)
            continue

        if in_code_block:
            cleaned_lines.append(line)
            continue

        def replace_image(match):
            nonlocal image_count
            image_count += 1
            return match.group(1)

        cleaned_lines.append(IMAGE_PATTERN.sub(replace_image, line))

    return "\n".join(cleaned_lines), image_count


class MinerUV3Parser(DocumentParser):
    def __init__(self, client: MinerUV3Client | None = None):
        self.client = client or MinerUV3Client()

    def parse(self, ctx) -> ParseResult:
        binary = ctx.binary
        if binary is None:
            with open(ctx.filename, "rb") as file:
                binary = file.read()

        markdown = self.client.parse_to_markdown(
            file_name=ctx.filename,
            binary=binary,
            start_page_id=ctx.from_page,
            end_page_id=ctx.to_page,
            callback=ctx.callback,
        )
        markdown, image_count = strip_markdown_images(markdown)
        LOGGER.info("[MinerUV3] markdown images stripped: count=%s", image_count)
        if ctx.callback:
            ctx.callback(0.75, "Markdown images stripped.")
        blocks = StructMarkdownParser().parse_text(markdown)
        return ParseResult(blocks=blocks, merge_strategy="blocks")
