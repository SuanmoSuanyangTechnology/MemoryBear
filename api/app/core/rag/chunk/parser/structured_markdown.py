import logging
import re
from io import BytesIO
from pathlib import Path

from bs4 import BeautifulSoup
from markdown import markdown
from PIL import Image

from app.core.rag.chunk.context import ParsedBlock, ParsedBlockType
from app.core.rag.chunk.parser.base import DocumentParser
from app.core.rag.nlp import find_codec


IMAGE_PATTERN = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")
HEADING_PATTERN = re.compile(r"^(#{1,6})\s+.*$")
LIST_PATTERN = re.compile(r"^\s*(?:[-*+]\s+|\d+\.\s+).*$")


class StructMarkdownParser(DocumentParser):
    def parse(self, ctx):
        text = self._read_text(ctx.filename, ctx.binary)
        return self.parse_text(text)

    def parse_text(self, text: str) -> list[ParsedBlock]:
        self._seq = 0
        self.blocks: list[ParsedBlock] = []
        self.lines = text.split("\n")

        index = 0
        while index < len(self.lines):
            line = self.lines[index]
            stripped = line.strip()
            if not stripped:
                index += 1
                continue

            if self._is_heading(line):
                index = self._append_heading(index)
                continue
            if stripped.startswith("```"):
                index = self._append_code(index)
                continue
            if self._is_html_table_start(line):
                index = self._append_html_table(index)
                continue
            if self._is_markdown_table_start(index):
                index = self._append_markdown_table(index)
                continue
            if IMAGE_PATTERN.search(line):
                index = self._append_image_line(index)
                continue
            if LIST_PATTERN.match(line):
                index = self._append_list(index)
                continue
            if stripped.startswith(">"):
                index = self._append_blockquote(index)
                continue

            index = self._append_text(index)

        return self.blocks

    def md_to_html(self, text: str):
        if not text:
            return []
        return BeautifulSoup(markdown(text), "html.parser")

    def get_hyperlink_urls(self, soup):
        if soup:
            return {a.get("href") for a in soup.find_all("a") if a.get("href")}
        return set()

    def load_image(self, src: str):
        import requests

        if not src:
            return None
        try:
            if src.startswith(("http://", "https://")):
                response = requests.get(src, stream=True, timeout=30)
                content_type = response.headers.get("Content-Type", "")
                if response.status_code == 200 and content_type.startswith("image/"):
                    return Image.open(BytesIO(response.content)).convert("RGB")
                return None

            local_path = Path(src)
            if not local_path.exists():
                logging.warning(f"Local image file not found: {src}")
                return None
            return Image.open(local_path).convert("RGB")
        except Exception as exc:
            logging.error(f"Failed to download/open image from {src}: {exc}")
            return None

    def _read_text(self, filename: str, binary: bytes | None) -> str:
        if binary:
            encoding = find_codec(binary)
            return binary.decode(encoding, errors="ignore")
        with open(filename, "r") as file:
            return file.read()

    def _append_block(
        self,
        block_type: ParsedBlockType,
        content,
        raw: str = "",
        start_line: int | None = None,
        end_line: int | None = None,
        metadata: dict | None = None,
        image=None,
    ) -> None:
        if isinstance(content, str) and not content.strip() and block_type is not ParsedBlockType.IMAGE:
            return
        self.blocks.append(
            ParsedBlock(
                type=block_type,
                content=content,
                raw=raw or (content if isinstance(content, str) else ""),
                seq=self._seq,
                start_line=start_line,
                end_line=end_line,
                image=image,
                metadata=metadata or {},
            )
        )
        self._seq += 1

    def _is_heading(self, line: str) -> bool:
        return bool(HEADING_PATTERN.match(line))

    def _append_heading(self, index: int) -> int:
        line = self.lines[index]
        match = HEADING_PATTERN.match(line)
        level = len(match.group(1)) if match else 1
        self._append_block(
            ParsedBlockType.HEADING,
            line,
            start_line=index + 1,
            end_line=index + 1,
            metadata={"level": level},
        )
        return index + 1

    def _append_code(self, index: int) -> int:
        start = index
        first_line = self.lines[index].strip()
        language = first_line[3:].strip()
        index += 1
        while index < len(self.lines):
            if self.lines[index].strip().startswith("```"):
                index += 1
                break
            index += 1
        content = "\n".join(self.lines[start:index])
        self._append_block(
            ParsedBlockType.CODE,
            content,
            start_line=start + 1,
            end_line=index,
            metadata={"language": language},
        )
        return index

    def _is_html_table_start(self, line: str) -> bool:
        return bool(re.search(r"<table\b", line, re.IGNORECASE))

    def _append_html_table(self, index: int) -> int:
        start = index
        while index < len(self.lines):
            if re.search(r"</table>", self.lines[index], re.IGNORECASE):
                index += 1
                break
            index += 1
        raw = "\n".join(self.lines[start:index])
        self._append_block(
            ParsedBlockType.TABLE,
            self._normalize_html_table(raw),
            raw=raw,
            start_line=start + 1,
            end_line=index,
            metadata={"table_format": "html"},
        )
        return index

    def _is_markdown_table_start(self, index: int) -> bool:
        if index + 1 >= len(self.lines):
            return False
        return "|" in self.lines[index] and self._is_table_separator(self.lines[index + 1])

    def _append_markdown_table(self, index: int) -> int:
        start = index
        index += 2
        while index < len(self.lines):
            line = self.lines[index]
            if not line.strip() or "|" not in line:
                break
            index += 1
        raw = "\n".join(self.lines[start:index])
        self._append_block(
            ParsedBlockType.TABLE,
            markdown(raw, extensions=["markdown.extensions.tables"]),
            raw=raw,
            start_line=start + 1,
            end_line=index,
            metadata={"table_format": "markdown"},
        )
        return index

    def _append_image_line(self, index: int) -> int:
        line = self.lines[index]
        cursor = 0
        for match in IMAGE_PATTERN.finditer(line):
            before = line[cursor:match.start()]
            if before.strip():
                self._append_block(
                    ParsedBlockType.TEXT,
                    before,
                    start_line=index + 1,
                    end_line=index + 1,
                )
            alt, src = match.group(1), match.group(2)
            self._append_block(
                ParsedBlockType.IMAGE,
                match.group(0),
                raw=match.group(0),
                start_line=index + 1,
                end_line=index + 1,
                metadata={"alt": alt, "src": src},
            )
            cursor = match.end()
        after = line[cursor:]
        if after.strip():
            self._append_block(
                ParsedBlockType.TEXT,
                after,
                start_line=index + 1,
                end_line=index + 1,
            )
        return index + 1

    def _append_list(self, index: int) -> int:
        start = index
        index += 1
        while index < len(self.lines):
            line = self.lines[index]
            if LIST_PATTERN.match(line) or line.startswith((" ", "\t")) or not line.strip():
                index += 1
                continue
            break
        content = "\n".join(self.lines[start:index])
        self._append_block(
            ParsedBlockType.LIST,
            content,
            start_line=start + 1,
            end_line=index,
        )
        return index

    def _append_blockquote(self, index: int) -> int:
        start = index
        index += 1
        while index < len(self.lines):
            line = self.lines[index]
            if line.strip().startswith(">") or not line.strip():
                index += 1
                continue
            break
        content = "\n".join(self.lines[start:index])
        self._append_block(
            ParsedBlockType.BLOCKQUOTE,
            content,
            start_line=start + 1,
            end_line=index,
        )
        return index

    def _append_text(self, index: int) -> int:
        start = index
        index += 1
        while index < len(self.lines):
            line = self.lines[index]
            stripped = line.strip()
            if not stripped:
                if index + 1 < len(self.lines) and self._is_text_continuation(index + 1):
                    index += 1
                    continue
                break
            if self._is_block_start(index) or IMAGE_PATTERN.search(line):
                break
            index += 1
        content = "\n".join(self.lines[start:index])
        self._append_block(
            ParsedBlockType.TEXT,
            content,
            start_line=start + 1,
            end_line=index,
        )
        return index

    def _is_text_continuation(self, index: int) -> bool:
        return (
            index < len(self.lines)
            and self.lines[index].strip()
            and not self._is_block_start(index)
            and not IMAGE_PATTERN.search(self.lines[index])
        )

    def _is_block_start(self, index: int) -> bool:
        line = self.lines[index]
        stripped = line.strip()
        return (
            self._is_heading(line)
            or stripped.startswith("```")
            or self._is_html_table_start(line)
            or self._is_markdown_table_start(index)
            or LIST_PATTERN.match(line)
            or stripped.startswith(">")
        )

    def _is_table_separator(self, line: str) -> bool:
        if "|" not in line:
            return False
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) < 2:
            return False
        return all(re.match(r"^:?-{3,}:?$", cell.replace(" ", "")) for cell in cells if cell)

    def _normalize_html_table(self, raw: str) -> str:
        tags = ["table", "td", "tr", "th", "tbody", "thead", "div"]
        pattern = re.compile(rf"<(?:{'|'.join(tags)})[^>]*>", re.IGNORECASE)

        def replace_tag(match):
            tag_name = re.match(r"<(\w+)", match.group()).group(1)
            return f"<{tag_name}>"

        return re.sub(pattern, replace_tag, raw)
