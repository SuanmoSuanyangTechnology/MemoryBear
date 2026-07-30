from dataclasses import dataclass

from app.core.rag.common.token_utils import encoder, num_tokens_from_string


DEFAULT_TEXT_SEPARATORS = ["\n\n", "\n", "。", "；", " ", ""]


@dataclass(frozen=True)
class _SplitUnit:
    text: str
    prefix: str = ""


class TextMerger:
    def __init__(self, separators: list[str] | None = None):
        self.separators = separators or DEFAULT_TEXT_SEPARATORS

    def merge(
        self,
        value: str | list,
        chunk_num: int,
        delimiter: str | None = None,
        overlap: int | None = 0,
    ) -> list[str]:
        limit = max(int(chunk_num), 1)
        overlap_tokens = self._normalize_overlap(overlap, limit)
        chunks: list[str] = []
        for text in self._extract_strings(value):
            for segment in self._split_by_custom_delimiter(text, delimiter):
                if not segment or not segment.strip():
                    continue
                if self._within_limit(segment, limit):
                    chunks.append(segment)
                    continue
                split_units = self._split_recursive(segment, limit, 0)
                chunks.extend(self._merge_split_units(split_units, limit, overlap_tokens))
        return chunks

    def _extract_strings(self, value: str | list) -> list[str]:
        if isinstance(value, str):
            return [value]
        if isinstance(value, list):
            return [item for item in value if isinstance(item, str)]
        return []

    def _split_by_custom_delimiter(self, text: str, delimiter: str | None) -> list[str]:
        if not delimiter:
            return [text]
        raw_parts = text.split(delimiter)
        if delimiter in {"。", "；"}:
            return [
                part + (delimiter if index < len(raw_parts) - 1 else "")
                for index, part in enumerate(raw_parts)
                if part
            ]
        return [part for part in raw_parts if part]

    def _split_recursive(self, text: str, limit: int, separator_index: int, prefix: str = "") -> list[_SplitUnit]:
        if not text or not text.strip():
            return []
        if self._within_limit(text, limit):
            return [_SplitUnit(text=text, prefix=prefix)]

        separator = self.separators[separator_index] if separator_index < len(self.separators) else ""
        if separator == "":
            return [
                _SplitUnit(text=chunk, prefix=prefix if index == 0 else "")
                for index, chunk in enumerate(self._hard_split(text, limit))
            ]

        parts = self._split_with_separator(text, separator)
        if len(parts) <= 1:
            return self._split_recursive(text, limit, separator_index + 1, prefix)

        result: list[_SplitUnit] = []
        for index, part in enumerate(parts):
            if not part or not part.text.strip():
                continue
            unit_prefix = prefix if index == 0 else part.prefix
            result.extend(self._split_recursive(part.text, limit, separator_index + 1, unit_prefix))
        return result

    def _split_with_separator(
        self,
        text: str,
        separator: str,
    ) -> list[_SplitUnit]:
        raw_parts = text.split(separator)
        return [
            _SplitUnit(text=part, prefix="" if index == 0 else separator)
            for index, part in enumerate(raw_parts)
            if part
        ]

    def _merge_split_units(self, split_units: list[_SplitUnit], limit: int, overlap: int) -> list[str]:
        docs: list[str] = []
        current_doc: list[_SplitUnit] = []
        total = 0

        for split_unit in split_units:
            if current_doc and not self._within_limit(self._join_units([*current_doc, split_unit]), limit):
                docs.append(self._join_units(current_doc))

                while current_doc and (
                    total > overlap
                    or not self._within_limit(self._join_units([*current_doc, split_unit]), limit)
                ):
                    total -= num_tokens_from_string(current_doc[0].text)
                    current_doc = current_doc[1:]

            current_doc.append(split_unit)
            total += num_tokens_from_string(split_unit.text)

        if current_doc:
            docs.append(self._join_units(current_doc))
        return docs

    @staticmethod
    def _join_units(split_units: list[_SplitUnit]) -> str:
        if not split_units:
            return ""
        return "".join(
            split_unit.text if index == 0 else f"{split_unit.prefix}{split_unit.text}"
            for index, split_unit in enumerate(split_units)
        )

    @staticmethod
    def _normalize_overlap(overlap: int | None, limit: int) -> int:
        try:
            value = int(overlap or 0)
        except (TypeError, ValueError):
            value = 0
        if value <= 0:
            return 0
        return min(value, max(limit - 1, 0))

    def _hard_split(self, text: str, limit: int) -> list[str]:
        tokens = encoder.encode(text)
        chunks: list[str] = []
        start = 0

        def find_decodable_boundary(begin: int, end: int, step: int) -> tuple[int, str] | None:
            while begin < end <= len(tokens):
                try:
                    return end, encoder.decode(tokens[begin:end], errors="strict")
                except UnicodeDecodeError:
                    end += step
            return None

        while start < len(tokens):
            end = min(start + limit, len(tokens))
            boundary = find_decodable_boundary(start, end, -1)
            if boundary is None:
                boundary = find_decodable_boundary(start, min(start + limit + 1, len(tokens)), 1)
            if boundary is None:
                raise RuntimeError(f"Unable to find a valid UTF-8 boundary from token index {start}.")

            end, chunk = boundary
            chunks.append(chunk)
            start = end

        return chunks

    @staticmethod
    def _within_limit(text: str, limit: int) -> bool:
        return num_tokens_from_string(text) <= limit
