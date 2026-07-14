from app.core.rag.common.token_utils import encoder, num_tokens_from_string


DEFAULT_TEXT_SEPARATORS = ["\n\n", "\n", "。", "；", " ", ""]


class TextMerger:
    def __init__(self, separators: list[str] | None = None):
        self.separators = separators or DEFAULT_TEXT_SEPARATORS

    def merge(self, value: str | list, chunk_num: int, delimiter: str | None = None) -> list[str]:
        limit = max(int(chunk_num), 1)
        separators = self._build_separators(delimiter)
        chunks: list[str] = []
        for text in self._extract_strings(value):
            chunks.extend(self._split_recursive(text, limit, separators, 0))
        return chunks

    def _build_separators(self, delimiter: str | None) -> list[str]:
        if delimiter is None:
            return list(self.separators)
        return [delimiter] + [separator for separator in self.separators if separator != delimiter]

    def _extract_strings(self, value: str | list) -> list[str]:
        if isinstance(value, str):
            return [value]
        if isinstance(value, list):
            return [item for item in value if isinstance(item, str)]
        return []

    def _split_recursive(self, text: str, limit: int, separators: list[str], separator_index: int) -> list[str]:
        if not text or not text.strip():
            return []

        separator = separators[separator_index] if separator_index < len(separators) else ""
        if self._within_limit(text, limit) and (separator_index > 0 or not separator or separator not in text):
            return [text]

        if separator == "":
            return self._hard_split(text, limit)

        parts = self._split_with_separator(text, separator)
        if len(parts) <= 1:
            return self._split_recursive(text, limit, separators, separator_index + 1)

        result: list[str] = []
        for part in parts:
            if not part or not part.strip():
                continue
            if self._within_limit(part, limit):
                result.append(part)
            else:
                result.extend(self._split_recursive(part, limit, separators, separator_index + 1))
        return result

    def _split_with_separator(self, text: str, separator: str) -> list[str]:
        raw_parts = text.split(separator)
        if separator in {"。", "；"}:
            return [
                part + (separator if index < len(raw_parts) - 1 else "")
                for index, part in enumerate(raw_parts)
                if part
            ]
        return [part for part in raw_parts if part]

    def _hard_split(self, text: str, limit: int) -> list[str]:
        tokens = encoder.encode(text)
        token_bytes = [encoder.decode_single_token_bytes(token) for token in tokens]
        chunks: list[str] = []
        start = 0

        def decode_range(begin: int, end: int) -> str:
            return b"".join(token_bytes[begin:end]).decode("utf-8")

        while start < len(tokens):
            end = min(start + limit, len(tokens))

            while end > start:
                try:
                    chunk = decode_range(start, end)
                except UnicodeDecodeError:
                    end -= 1
                    continue
                chunks.append(chunk)
                start = end
                break
            else:
                end = min(start + limit + 1, len(tokens))
                while True:
                    try:
                        chunk = decode_range(start, end)
                    except UnicodeDecodeError:
                        if end >= len(tokens):
                            raise
                        end += 1
                        continue
                    chunks.append(chunk)
                    start = end
                    break

        return chunks

    @staticmethod
    def _within_limit(text: str, limit: int) -> bool:
        return num_tokens_from_string(text) <= limit
