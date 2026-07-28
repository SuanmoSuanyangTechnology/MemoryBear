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
STRUCTURAL_LINE_PATTERN = re.compile(
    r"^\s{0,3}(?:#{1,6}\s+|[-+*]\s+|\d+[.)）、|｜]\s*|(?:-{3,}|\*{3,}|_{3,})\s*$)"
)

LIST_BOUNDARY = r"[、.．)）：:|｜]|\s+"
CIRCLED_MARKERS = (
    "①②③④⑤⑥⑦⑧⑨⑩"
    "⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳"
    "㉑㉒㉓㉔㉕㉖㉗㉘㉙㉚"
    "㉛㉜㉝㉞㉟㊱㊲㊳㊴㊵"
    "㊶㊷㊸㊹㊺㊻㊼㊽㊾㊿"
    "❶❷❸❹❺❻❼❽❾❿"
)
CHINESE_NUMERAL_MARKERS = "零〇一二三四五六七八九十百千万壹贰貳貮叁參肆伍陆陸柒捌玖拾"

UNORDERED_LIST_PATTERN = re.compile(r"^\s{0,3}([-+*])\s+(.+\S)\s*$")
ORDERED_LIST_PATTERN = re.compile(r"^\s{0,3}(\d+)([.)）、|｜])\s*(.+\S)\s*$")
KEYCAP_LIST_PATTERN = re.compile(r"^\s{0,3}([0-9]\ufe0f?\u20e3)\s*(.+\S)\s*$")
CIRCLED_LIST_PATTERN = re.compile(rf"^\s{{0,3}}([{CIRCLED_MARKERS}])\s*(.+\S)\s*$")
CHINESE_LIST_PATTERN = re.compile(rf"^\s{{0,3}}([{CHINESE_NUMERAL_MARKERS}]+)({LIST_BOUNDARY})\s*(.+\S)\s*$")
ALPHA_LIST_PATTERN = re.compile(r"^\s{0,3}([A-Za-z])([.)）、|｜])\s*(.+\S)\s*$")
QA_LIST_PATTERN = re.compile(r"^\s{0,3}(问|答|拓展|补充)([?？:：|｜]|\s+)\s*(.+\S)\s*$")
ORDINAL_LIST_PATTERN = re.compile(
    rf"^\s{{0,3}}(首次|其次|再次|最后|第[{CHINESE_NUMERAL_MARKERS}0-9]+)({LIST_BOUNDARY})\s*(.+\S)\s*$"
)
STEP_LIST_PATTERN = re.compile(
    rf"^\s{{0,3}}(.{{0,12}}?第[{CHINESE_NUMERAL_MARKERS}0-9]+步)({LIST_BOUNDARY})\s*(.+\S)\s*$"
)


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
        line_infos: list[MarkdownLineInfo] = []
        in_code = False
        for line_number, raw_line in enumerate(text.split("\n"), start=1):
            stripped = raw_line.strip()
            is_code_fence = stripped.startswith("```")

            if in_code:
                line_infos.append(
                    MarkdownLineInfo(
                        raw=raw_line,
                        text=raw_line,
                        line_number=line_number,
                        in_code=True,
                        is_code_fence=is_code_fence,
                    )
                )
                if is_code_fence:
                    in_code = False
                continue

            line = raw_line
            if normalize_escaped_structure:
                line = self._normalize_escaped_structural_line(line)
            line = EMPTY_HTML_ANCHOR_PATTERN.sub("", line)

            block_hint = None
            metadata: dict[str, Any] = {}
            if not is_code_fence:
                heading_match = ATX_HEADING_PATTERN.match(line)
                if heading_match:
                    block_hint = "heading"
                    metadata = {
                        "heading_level": len(heading_match.group(1)),
                        "heading_title": _strip_wrapping_emphasis(heading_match.group(2)),
                    }
                else:
                    list_metadata = self._list_metadata(line)
                    if list_metadata:
                        block_hint = "list"
                        metadata = list_metadata

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

    def _list_metadata(self, line: str) -> dict[str, Any] | None:
        patterns = (
            ("unordered", UNORDERED_LIST_PATTERN),
            ("ordered", ORDERED_LIST_PATTERN),
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
        return None


def _indent_level(line: str) -> int:
    leading = len(line) - len(line.lstrip(" "))
    return leading // 2


def _strip_wrapping_emphasis(text: str) -> str:
    value = text.strip()
    for marker in ("**", "__"):
        if value.startswith(marker) and value.endswith(marker) and len(value) > len(marker) * 2:
            return value[len(marker):-len(marker)].strip()
    return value
