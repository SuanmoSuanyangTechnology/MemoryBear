import logging
import re
from io import BytesIO
from pathlib import Path

from bs4 import BeautifulSoup
from markdown import markdown
from PIL import Image

from ..context import ParsedBlock, ParsedBlockType
from ..tokenization import find_codec
from .base import DocumentParser
from .markdown_preprocessor import (
    QA_QUESTION_MARKERS,
    MarkdownLineInfo,
    MarkdownPreprocessor,
)

IMAGE_PATTERN = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")
HEADING_PATTERN = re.compile(r"^\s{0,3}(#{1,6})\s+(.+?)\s*$")


class StructMarkdownParser(DocumentParser):
    def parse(self, ctx):
        text = self._read_text(ctx.filename, ctx.binary)
        return self.parse_text(text)

    def parse_text(
        self, text: str, *, normalize_escaped_structure: bool = False
    ) -> list[ParsedBlock]:
        self._seq = 0
        self.blocks: list[ParsedBlock] = []
        self._heading_stack: dict[int, str] = {}
        self._compact_block_spacing = normalize_escaped_structure
        preprocess_result = MarkdownPreprocessor().preprocess(
            text,
            normalize_escaped_structure=normalize_escaped_structure,
        )
        self.lines = preprocess_result.lines
        self.line_infos = preprocess_result.line_infos

        index = 0
        while index < len(self.lines):
            line = self.lines[index]
            stripped = line.strip()
            if not stripped:
                index += 1
                continue

            if self._is_heading(index):
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
            if self._is_list_line(index):
                if self._is_qa_list_line(index):
                    index = self._append_qa_list(index)
                else:
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
        with open(filename) as file:
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
        if (
            isinstance(content, str)
            and not content.strip()
            and block_type is not ParsedBlockType.IMAGE
        ):
            return
        block_metadata = dict(metadata or {})
        block_metadata.setdefault("heading_path", self._current_heading_path())
        self.blocks.append(
            ParsedBlock(
                type=block_type,
                content=content,
                raw=raw or (content if isinstance(content, str) else ""),
                seq=self._seq,
                start_line=start_line,
                end_line=end_line,
                image=image,
                metadata=block_metadata,
            )
        )
        self._seq += 1

    def _line_info(self, index: int) -> MarkdownLineInfo:
        return self.line_infos[index]

    def _is_heading(self, index: int) -> bool:
        return self._line_info(index).block_hint == "heading"

    def _is_list_line(self, index: int) -> bool:
        return self._line_info(index).block_hint == "list"

    def _is_qa_list_line(self, index: int) -> bool:
        return (
            self._is_list_line(index)
            and self._line_info(index).metadata.get("list_marker_kind") == "qa"
        )

    def _is_qa_question_line(self, index: int) -> bool:
        return self._is_qa_list_line(index) and self._qa_marker(index) in QA_QUESTION_MARKERS

    def _qa_marker(self, index: int) -> str:
        return str(self._line_info(index).metadata.get("list_marker") or "")

    def _next_nonblank_index(self, index: int) -> int | None:
        while index < len(self.lines):
            if self.lines[index].strip():
                return index
            index += 1
        return None

    def _is_list_body_line(self, index: int) -> bool:
        return self._line_info(index).block_hint in {"list", "list_continuation"}

    def _current_heading_path(self) -> list[str]:
        return [self._heading_stack[level] for level in sorted(self._heading_stack)]

    def _append_heading(self, index: int) -> int:
        line = self.lines[index]
        line_info = self._line_info(index)
        match = HEADING_PATTERN.match(line)
        level = int(
            line_info.metadata.get("heading_level") or (len(match.group(1)) if match else 1)
        )
        title = str(
            line_info.metadata.get("heading_title")
            or (match.group(2).strip() if match else line.strip())
        )
        self._heading_stack = {
            existing_level: existing_title
            for existing_level, existing_title in self._heading_stack.items()
            if existing_level < level
        }
        self._heading_stack[level] = title
        self._append_block(
            ParsedBlockType.HEADING,
            line,
            start_line=index + 1,
            end_line=index + 1,
            metadata={
                "level": level,
                "heading_level": level,
                "heading_title": title,
                "heading_raw": line,
                "heading_path": self._current_heading_path(),
            },
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
            before = line[cursor : match.start()]
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
            if self._is_list_body_line(index) or line.startswith((" ", "\t")) or not line.strip():
                index += 1
                continue
            break
        content = self._block_content(start, index, compact_blank_lines=self._compact_block_spacing)
        self._append_block(
            ParsedBlockType.LIST,
            content,
            start_line=start + 1,
            end_line=index,
            metadata=self._list_metadata(start, index),
        )
        return index

    def _append_qa_list(self, index: int) -> int:
        start = index
        index += 1
        while index < len(self.lines):
            line = self.lines[index]
            if not line.strip():
                next_index = self._next_nonblank_index(index + 1)
                if next_index is not None and self._is_qa_question_line(next_index):
                    break
                index += 1
                continue

            if self._is_qa_list_line(index):
                if self._is_qa_question_line(index):
                    break
                index += 1
                continue

            if self._is_list_body_line(index) or line.startswith((" ", "\t")):
                index += 1
                continue

            break

        content = self._block_content(start, index, compact_blank_lines=self._compact_block_spacing)
        self._append_block(
            ParsedBlockType.LIST,
            content,
            start_line=start + 1,
            end_line=index,
            metadata=self._list_metadata(start, index),
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
        content = self._block_content(start, index)
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
            self._is_heading(index)
            or stripped.startswith("```")
            or self._is_html_table_start(line)
            or self._is_markdown_table_start(index)
            or self._is_list_line(index)
            or stripped.startswith(">")
        )

    def _is_table_separator(self, line: str) -> bool:
        if "|" not in line:
            return False
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) < 2:
            return False
        return all(re.match(r"^:?-{3,}:?$", cell.replace(" ", "")) for cell in cells if cell)

    def _block_content(self, start: int, end: int, *, compact_blank_lines: bool = False) -> str:
        lines = self.lines[start:end]
        if compact_blank_lines:
            lines = [line for line in lines if line.strip()]
        return "\n".join(lines).rstrip()

    def _normalize_html_table(self, raw: str) -> str:
        tags = ["table", "td", "tr", "th", "tbody", "thead", "div"]
        pattern = re.compile(rf"<(?:{'|'.join(tags)})[^>]*>", re.IGNORECASE)

        def replace_tag(match):
            tag_name = re.match(r"<(\w+)", match.group()).group(1)
            return f"<{tag_name}>"

        return re.sub(pattern, replace_tag, raw)

    def _list_metadata(self, start: int, end: int) -> dict:
        markers = []
        marker_kinds = []
        contains_qa_marker = False
        for index in range(start, end):
            line_info = self._line_info(index)
            if line_info.block_hint != "list":
                continue
            marker = line_info.metadata.get("list_marker")
            marker_kind = line_info.metadata.get("list_marker_kind")
            if marker:
                markers.append(marker)
            if marker_kind:
                marker_kinds.append(marker_kind)
            contains_qa_marker = contains_qa_marker or bool(
                line_info.metadata.get("contains_qa_marker")
            )

        return {
            "list_item_count": len(markers),
            "list_markers": markers,
            "list_marker_kinds": _unique_preserving_order(marker_kinds),
            "contains_qa_marker": contains_qa_marker,
        }


def _unique_preserving_order(values: list[str]) -> list[str]:
    result = []
    for value in values:
        if value not in result:
            result.append(value)
    return result
