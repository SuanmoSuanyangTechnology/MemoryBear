import re
from dataclasses import dataclass, field
from typing import Any

EMPTY_HTML_ANCHOR_PATTERN = re.compile(
    r"<a\b(?=[^>]*(?:\bid\s*=\s*['\"][^'\"]+['\"]|\bname\s*=\s*['\"][^'\"]+['\"]))(?![^>]*\bhref\s*=)[^>]*>\s*</a>",
    re.IGNORECASE,
)

ATX_HEADING_PATTERN = re.compile(r"^\s{0,3}(#{1,6})\s+(.+?)\s*$")
ESCAPED_HEADING_PATTERN = re.compile(r"^(\s*)\\(#{1,6})(\s+)")
ESCAPED_UNORDERED_LIST_PATTERN = re.compile(r"^(\s*)\\([-+*])(\s+)")
ESCAPED_THEMATIC_BREAK_PATTERN = re.compile(r"^(\s*)\\((?:-{3,}|\*{3,}|_{3,}))(\s*)$")
ESCAPED_EMPHASIS_PATTERN = re.compile(r"\\([*_])")
ESCAPED_CODE_FENCE_PATTERN = re.compile(
    r"^(?P<indent>\s*)(?P<fence>(?:\\`){3})(?!\\`)(?P<info>[^\r\n]*)$"
)
STRUCTURAL_LINE_PATTERN = re.compile(
    r"^\s{0,3}(?:#{1,6}\s+|[-+*]\s+|\d+[.)）、|｜]\s*|(?:-{3,}|\*{3,}|_{3,})\s*$)"
)

LIST_BOUNDARY = r"[、.．)）：:|｜]|\s+"
CIRCLED_MARKERS = (
    "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳㉑㉒㉓㉔㉕㉖㉗㉘㉙㉚㉛㉜㉝㉞㉟㊱㊲㊳㊴㊵㊶㊷㊸㊹㊺㊻㊼㊽㊾㊿❶❷❸❹❺❻❼❽❾❿"
)
CHINESE_NUMERAL_MARKERS = "零〇一二三四五六七八九十百千万壹贰貳貮叁參肆伍陆陸柒捌玖拾"

UNORDERED_LIST_PATTERN = re.compile(r"^\s{0,3}([-+*])\s+(.+\S)\s*$")
ORDERED_LIST_PATTERN = re.compile(r"^\s{0,3}(\d+)(?:[)）、|｜]|[.．](?!\d))\s*(.+\S)\s*$")
EMPTY_ORDERED_LIST_PATTERN = re.compile(r"^\s{0,3}(\d{1,3})([.)）、|｜])\s*$")
PAREN_ORDERED_LIST_PATTERN = re.compile(
    rf"^\s{{0,3}}([（(]\s*(?:\d+|[{CHINESE_NUMERAL_MARKERS}]+)\s*[）)])\s*(.+\S)\s*$"
)
KEYCAP_LIST_PATTERN = re.compile(r"^\s{0,3}([0-9]\ufe0f?\u20e3)\s*(.+\S)\s*$")
CIRCLED_LIST_PATTERN = re.compile(rf"^\s{{0,3}}([{CIRCLED_MARKERS}])\s*(.+\S)\s*$")
CHINESE_LIST_PATTERN = re.compile(
    rf"^\s{{0,3}}([{CHINESE_NUMERAL_MARKERS}]+)({LIST_BOUNDARY})\s*(.+\S)\s*$"
)
ALPHA_LIST_PATTERN = re.compile(r"^\s{0,3}([A-Za-z])([.)）|｜]|、(?!\s*[A-Za-z]))\s*(.+\S)\s*$")
QA_QUESTION_MARKERS = {"问", "问题", "提问", "追问"}
QA_LIST_PATTERN = re.compile(
    r"^\s{0,3}(问题|答案|提问|回答|答复|回复|追问|问|答|拓展|补充)([?？:：|｜]|\s+)\s*(\S(?:.*\S)?)\s*$"
)
DEFINITION_LIST_PATTERN = re.compile(
    r"^\s{0,3}(?!问|答|拓展|补充)([^:：\n]{1,32})([：:])\s*(\S(?:.*\S)?)\s*$"
)
ORDINAL_LIST_PATTERN = re.compile(
    rf"^\s{{0,3}}(首次|其次|再次|最后|第[{CHINESE_NUMERAL_MARKERS}0-9]+)({LIST_BOUNDARY})\s*(.+\S)\s*$"
)
STEP_LIST_PATTERN = re.compile(
    rf"^\s{{0,3}}(.{{0,12}}?第[{CHINESE_NUMERAL_MARKERS}0-9]+步)({LIST_BOUNDARY})\s*(.+\S)\s*$"
)
PREFIXED_LIST_PATTERN = re.compile(
    rf"^\s{{0,3}}(?P<prefix>[^:：|｜\n]{{1,24}}[：:|｜])\s*"
    rf"(?P<marker>"
    rf"\d+(?:[)）、|｜]|[.．](?!\d))"
    rf"|[（(]\s*(?:\d+|[{CHINESE_NUMERAL_MARKERS}]+)\s*[）)]"
    rf"|[0-9]\ufe0f?\u20e3"
    rf"|[{CIRCLED_MARKERS}]"
    rf"|[{CHINESE_NUMERAL_MARKERS}]+(?:{LIST_BOUNDARY})"
    rf"|[A-Za-z](?:[.)）|｜]|、(?!\s*[A-Za-z]))"
    rf"|(?:首次|其次|再次|最后|第[{CHINESE_NUMERAL_MARKERS}0-9]+)(?:{LIST_BOUNDARY})"
    rf"|.{{0,12}}?第[{CHINESE_NUMERAL_MARKERS}0-9]+步(?:{LIST_BOUNDARY})"
    rf")\s*(?P<body>.*\S)?\s*$"
)
LIST_CONTINUATION_PATTERN = re.compile(r"^\s{0,3}.{1,24}(?:简介|介绍|说明|定义)[：:].+\S\s*$")
QA_CONTINUATION_AFTER_BLANK_PATTERN = re.compile(
    r"^(?:"
    r"(?=.{12,})(?=.*[。！？；，,.;:：]).+"
    r"|(?:当|若|如果|因此|所以|同时|另外|此外|其中|相关|产品|试验|测试|验证|优先|严酷|注意|GB/T|IEC).+"
    r")"
)
MAX_QA_CONTINUATION_PARAGRAPHS = 3
IMAGE_LINE_PATTERN = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")
HTML_TABLE_LINE_PATTERN = re.compile(r"^\s*<table\b", re.IGNORECASE)
MARKDOWN_TABLE_LINE_PATTERN = re.compile(r"^\s*\|.+\|\s*$")


@dataclass
class MarkdownLineInfo:
    raw: str
    text: str
    line_number: int
    in_code: bool = False
    is_code_fence: bool = False
    block_hint: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class MarkdownPreprocessResult:
    lines: list[str]
    line_infos: list[MarkdownLineInfo]


class MarkdownPreprocessor:
    def preprocess(
        self,
        text: str,
        *,
        normalize_escaped_structure: bool = False,
    ) -> MarkdownPreprocessResult:
        raw_lines = text.split("\n")
        escaped_code_fence_indexes = (
            self._paired_escaped_code_fence_indexes(raw_lines)
            if normalize_escaped_structure
            else set()
        )
        line_infos: list[MarkdownLineInfo] = []
        in_code = False
        in_list_context = False
        in_qa_list_context = False
        qa_blank_lines = 0
        qa_continuation_paragraphs = 0
        for line_index, raw_line in enumerate(raw_lines):
            line_number = line_index + 1
            line = raw_line
            if line_index in escaped_code_fence_indexes:
                line = self._normalize_escaped_code_fence(line)
            stripped = line.strip()
            is_code_fence = stripped.startswith("```")

            if in_code:
                line_infos.append(
                    MarkdownLineInfo(
                        raw=raw_line,
                        text=line,
                        line_number=line_number,
                        in_code=True,
                        is_code_fence=is_code_fence,
                    )
                )
                if is_code_fence:
                    in_code = False
                continue

            if normalize_escaped_structure and not is_code_fence:
                line = self._normalize_escaped_structural_line(line)
                line = EMPTY_HTML_ANCHOR_PATTERN.sub("", line)
            stripped = line.strip()

            block_hint = None
            metadata: dict[str, Any] = {}
            if not is_code_fence:
                after_qa_blank = False
                if not stripped:
                    if in_qa_list_context:
                        qa_blank_lines += 1
                else:
                    after_qa_blank = qa_blank_lines > 0
                    qa_blank_lines = 0
                heading_match = ATX_HEADING_PATTERN.match(line)
                if heading_match:
                    block_hint = "heading"
                    metadata = {
                        "heading_level": len(heading_match.group(1)),
                        "heading_title": _strip_wrapping_emphasis(heading_match.group(2)),
                    }
                    in_list_context = False
                    in_qa_list_context = False
                    qa_continuation_paragraphs = 0
                else:
                    list_metadata = self._list_metadata(line, enhanced=normalize_escaped_structure)
                    if list_metadata:
                        block_hint = "list"
                        metadata = list_metadata
                        in_list_context = True
                        in_qa_list_context = in_qa_list_context or bool(
                            list_metadata.get("contains_qa_marker")
                        )
                        if list_metadata.get("contains_qa_marker"):
                            qa_continuation_paragraphs = 0
                    elif (
                        normalize_escaped_structure
                        and in_list_context
                        and self._is_list_continuation(
                            line,
                            in_qa_context=in_qa_list_context,
                            after_blank=in_qa_list_context and after_qa_blank,
                            qa_continuation_paragraphs=qa_continuation_paragraphs,
                        )
                    ):
                        block_hint = "list_continuation"
                        metadata = {
                            "list_continuation": True,
                            "qa_continuation": in_qa_list_context,
                            "list_level": _indent_level(line),
                        }
                        if in_qa_list_context and after_qa_blank:
                            qa_continuation_paragraphs += 1
                    elif stripped and not IMAGE_LINE_PATTERN.search(line):
                        in_list_context = False
                        in_qa_list_context = False
                        qa_continuation_paragraphs = 0
                    elif IMAGE_LINE_PATTERN.search(line):
                        in_list_context = False
                        in_qa_list_context = False
                        qa_continuation_paragraphs = 0

            line_infos.append(
                MarkdownLineInfo(
                    raw=raw_line,
                    text=line,
                    line_number=line_number,
                    is_code_fence=is_code_fence,
                    block_hint=block_hint,
                    metadata=metadata,
                )
            )
            if is_code_fence:
                in_code = True
                in_list_context = False
                in_qa_list_context = False
                qa_blank_lines = 0
                qa_continuation_paragraphs = 0

        return MarkdownPreprocessResult(
            lines=[line_info.text for line_info in line_infos],
            line_infos=line_infos,
        )

    def _normalize_escaped_structural_line(self, line: str) -> str:
        normalized, heading_count = ESCAPED_HEADING_PATTERN.subn(r"\1\2\3", line, count=1)
        normalized, list_count = ESCAPED_UNORDERED_LIST_PATTERN.subn(r"\1\2\3", normalized, count=1)
        normalized, rule_count = ESCAPED_THEMATIC_BREAK_PATTERN.subn(r"\1\2\3", normalized, count=1)
        if heading_count or list_count or rule_count or STRUCTURAL_LINE_PATTERN.match(normalized):
            normalized = ESCAPED_EMPHASIS_PATTERN.sub(r"\1", normalized)
        return normalized

    @staticmethod
    def _normalize_escaped_code_fence(line: str) -> str:
        match = ESCAPED_CODE_FENCE_PATTERN.match(line)
        if match is None:
            return line
        fence = match.group("fence").replace("\\`", "`")
        return f"{match.group('indent')}{fence}{match.group('info')}"

    @staticmethod
    def _paired_escaped_code_fence_indexes(lines: list[str]) -> set[int]:
        paired_indexes: set[int] = set()
        opening_index: int | None = None
        in_literal_code = False

        for index, line in enumerate(lines):
            if opening_index is not None:
                match = ESCAPED_CODE_FENCE_PATTERN.match(line)
                if match is not None and not match.group("info").strip():
                    paired_indexes.update((opening_index, index))
                    opening_index = None
                continue

            if line.strip().startswith("```"):
                in_literal_code = not in_literal_code
                continue
            if in_literal_code:
                continue
            if ESCAPED_CODE_FENCE_PATTERN.match(line) is not None:
                opening_index = index

        return paired_indexes

    def _list_metadata(self, line: str, *, enhanced: bool = False) -> dict[str, Any] | None:
        patterns = (
            ("unordered", UNORDERED_LIST_PATTERN),
            ("ordered", ORDERED_LIST_PATTERN),
        )
        if enhanced:
            patterns = (
                *patterns,
                ("ordered", EMPTY_ORDERED_LIST_PATTERN),
                ("ordered_parenthesis", PAREN_ORDERED_LIST_PATTERN),
                ("keycap", KEYCAP_LIST_PATTERN),
                ("circled", CIRCLED_LIST_PATTERN),
                ("step", STEP_LIST_PATTERN),
                ("ordinal", ORDINAL_LIST_PATTERN),
                ("chinese", CHINESE_LIST_PATTERN),
                ("alpha", ALPHA_LIST_PATTERN),
                ("qa", QA_LIST_PATTERN),
            )
        for marker_kind, pattern in patterns:
            match = pattern.match(line)
            if not match:
                continue
            marker = match.group(1)
            return {
                "list_marker": marker,
                "list_marker_kind": marker_kind,
                "list_level": _indent_level(line),
                "contains_qa_marker": marker_kind == "qa",
            }
        if not enhanced:
            return None
        prefixed_match = PREFIXED_LIST_PATTERN.match(line)
        if prefixed_match:
            return {
                "list_marker": prefixed_match.group("marker").strip(),
                "list_marker_kind": "prefixed",
                "list_prefix": prefixed_match.group("prefix").strip(),
                "list_level": _indent_level(line),
                "contains_qa_marker": False,
            }
        definition_match = DEFINITION_LIST_PATTERN.match(line)
        if definition_match:
            return {
                "list_marker": definition_match.group(1).strip(),
                "list_marker_kind": "definition",
                "list_level": _indent_level(line),
                "contains_qa_marker": False,
            }
        return None

    def _is_list_continuation(
        self,
        line: str,
        *,
        in_qa_context: bool = False,
        after_blank: bool = False,
        qa_continuation_paragraphs: int = 0,
    ) -> bool:
        stripped = line.strip()
        if not stripped:
            return False
        if (
            stripped.startswith(("```", ">"))
            or IMAGE_LINE_PATTERN.search(line)
            or ATX_HEADING_PATTERN.match(line)
            or HTML_TABLE_LINE_PATTERN.match(line)
            or MARKDOWN_TABLE_LINE_PATTERN.match(line)
        ):
            return False
        if in_qa_context:
            if after_blank:
                return qa_continuation_paragraphs < MAX_QA_CONTINUATION_PARAGRAPHS and bool(
                    QA_CONTINUATION_AFTER_BLANK_PATTERN.match(stripped)
                )
            return True
        return bool(LIST_CONTINUATION_PATTERN.match(line))


def _indent_level(line: str) -> int:
    leading = len(line) - len(line.lstrip(" "))
    return leading // 2


def _strip_wrapping_emphasis(text: str) -> str:
    value = text.strip()
    for marker in ("**", "__"):
        if value.startswith(marker) and value.endswith(marker) and len(value) > len(marker) * 2:
            return value[len(marker) : -len(marker)].strip()
    return value
