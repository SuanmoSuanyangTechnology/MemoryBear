from __future__ import annotations

import copy
import re
from typing import Any

from PIL import Image

from .token_utils import num_tokens_from_string
from .tokenizer import get_tokenizer

ALL_CODECS = (
    "utf-8",
    "gb2312",
    "gbk",
    "utf_16",
    "ascii",
    "big5",
    "big5hkscs",
    "cp037",
    "cp273",
    "cp424",
    "cp437",
    "cp500",
    "cp720",
    "cp737",
    "cp775",
    "cp850",
    "cp852",
    "cp855",
    "cp856",
    "cp857",
    "cp858",
    "cp860",
    "cp861",
    "cp862",
    "cp863",
    "cp864",
    "cp865",
    "cp866",
    "cp869",
    "cp874",
    "cp875",
    "cp932",
    "cp949",
    "cp950",
    "cp1006",
    "cp1026",
    "cp1125",
    "cp1140",
    "cp1250",
    "cp1251",
    "cp1252",
    "cp1253",
    "cp1254",
    "cp1255",
    "cp1256",
    "cp1257",
    "cp1258",
    "euc_jp",
    "euc_jis_2004",
    "euc_jisx0213",
    "euc_kr",
    "gb18030",
    "hz",
    "iso2022_jp",
    "iso2022_jp_1",
    "iso2022_jp_2",
    "iso2022_jp_2004",
    "iso2022_jp_3",
    "iso2022_jp_ext",
    "iso2022_kr",
    "latin_1",
    "iso8859_2",
    "iso8859_3",
    "iso8859_4",
    "iso8859_5",
    "iso8859_6",
    "iso8859_7",
    "iso8859_8",
    "iso8859_9",
    "iso8859_10",
    "iso8859_11",
    "iso8859_13",
    "iso8859_14",
    "iso8859_15",
    "iso8859_16",
    "johab",
    "koi8_r",
    "koi8_t",
    "koi8_u",
    "kz1048",
    "mac_cyrillic",
    "mac_greek",
    "mac_iceland",
    "mac_latin2",
    "mac_roman",
    "mac_turkish",
    "ptcp154",
    "shift_jis",
    "shift_jis_2004",
    "shift_jisx0213",
    "utf_32",
    "utf_32_be",
    "utf_32_le",
    "utf_16_be",
    "utf_16_le",
    "utf_7",
    "windows-1250",
    "windows-1251",
    "windows-1252",
    "windows-1253",
    "windows-1254",
    "windows-1255",
    "windows-1256",
    "windows-1257",
    "windows-1258",
    "latin-2",
)


def find_codec(blob: bytes) -> str:
    try:
        import chardet

        detected = chardet.detect(blob[:1024])
        if detected.get("confidence", 0) > 0.5 and detected.get("encoding") != "ascii":
            return str(detected["encoding"])
        if detected.get("confidence", 0) > 0.5:
            return "utf-8"
    except ImportError:
        pass

    for codec in ALL_CODECS:
        try:
            blob[:1024].decode(codec)
            return codec
        except Exception:
            pass
        try:
            blob.decode(codec)
            return codec
        except Exception:
            pass
    return "utf-8"


def tokenize(document: dict[str, Any], text: str, is_english: bool) -> None:
    del is_english
    document["content_with_weight"] = text
    normalized = re.sub(
        r"</?(table|td|caption|tr|th)( [^<>]{0,12})?>",
        " ",
        text,
    )
    tokenizer = get_tokenizer()
    document["content_ltks"] = tokenizer.tokenize(normalized)
    document["content_sm_ltks"] = tokenizer.fine_grained_tokenize(document["content_ltks"])


def add_positions(document: dict[str, Any], positions: list | None) -> None:
    if not positions:
        return
    page_numbers = []
    serialized_positions = []
    tops = []
    for page_number, left, right, top, bottom in positions:
        page_numbers.append(int(page_number + 1))
        tops.append(int(top))
        serialized_positions.append(
            (
                int(page_number + 1),
                int(left),
                int(right),
                int(top),
                int(bottom),
            )
        )
    document["page_num_int"] = page_numbers
    document["position_int"] = serialized_positions
    document["top_int"] = tops


def tokenize_chunks(
    chunks: list[str],
    document: dict[str, Any],
    is_english: bool,
    pdf_parser: Any = None,
) -> list[dict[str, Any]]:
    result = []
    for index, chunk in enumerate(chunks):
        if not chunk.strip():
            continue
        item = copy.deepcopy(document)
        if pdf_parser:
            try:
                item["image"], positions = pdf_parser.crop(chunk, need_position=True)
                add_positions(item, positions)
                chunk = pdf_parser.remove_tag(chunk)
            except NotImplementedError:
                pass
        else:
            add_positions(item, [[index] * 5])
        tokenize(item, chunk, is_english)
        result.append(item)
    return result


def get_delimiters(delimiters: str) -> str:
    values = []
    start = 0
    for match in re.finditer(r"`([^`]+)`", delimiters, re.I):
        left, right = match.span()
        values.append(match.group(1))
        values.extend(list(delimiters[start:left]))
        start = right
    if start < len(delimiters):
        values.extend(list(delimiters[start:]))
    values.sort(key=lambda value: -len(value))
    return "|".join(re.escape(value) for value in values if value)


def _remove_position_tags(value: str) -> str:
    return re.sub(r"@@[\t0-9.-]+?##", "", value)


def naive_merge(
    sections: str | list,
    chunk_token_num: int = 128,
    delimiter: str = "\n。；！？",
    overlapped_percent: int = 0,
) -> list[str]:
    if not sections:
        return []
    if isinstance(sections, str):
        sections = [sections]
    if isinstance(sections[0], str):
        sections = [(section, "") for section in sections]
    chunks = [""]
    token_counts = [0]

    def add_chunk(text: str, position: str) -> None:
        token_count = num_tokens_from_string(text)
        position = position or ""
        if token_count < 8:
            position = ""
        if chunks[-1] == "" or token_counts[-1] > (
            chunk_token_num * (100 - overlapped_percent) / 100.0
        ):
            if chunks:
                overlap_source = _remove_position_tags(chunks[-1])
                overlap_start = int(len(overlap_source) * (100 - overlapped_percent) / 100.0)
                text = overlap_source[overlap_start:] + text
            if text.find(position) < 0:
                text += position
            chunks.append(text)
            token_counts.append(token_count)
        else:
            if chunks[-1].find(position) < 0:
                text += position
            chunks[-1] += text
            token_counts[-1] += token_count

    delimiters = get_delimiters(delimiter)
    for section, position in sections:
        if num_tokens_from_string(section) < chunk_token_num:
            add_chunk(f"\n{section}", position)
            continue
        for subsection in re.split(f"({delimiters})", section, flags=re.DOTALL):
            if re.match(f"^{delimiters}$", subsection):
                continue
            add_chunk(f"\n{subsection}", position)
    return chunks


def concat_img(first: Any, second: Any):
    if first and not second:
        return first
    if not first and second:
        return second
    if not first and not second:
        return None
    if first is second:
        return first
    if isinstance(first, Image.Image) and isinstance(second, Image.Image):
        if first.tobytes() == second.tobytes():
            return first
    width = max(first.size[0], second.size[0])
    image = Image.new("RGB", (width, first.size[1] + second.size[1]))
    image.paste(first, (0, 0))
    image.paste(second, (0, first.size[1]))
    return image


def naive_merge_with_images(
    texts: list,
    images: list,
    chunk_token_num: int = 128,
    delimiter: str = "\n。；！？",
    overlapped_percent: int = 0,
) -> tuple[list[str], list]:
    if not texts or len(texts) != len(images):
        return [], []
    chunks = [""]
    result_images = [None]
    token_counts = [0]

    def add_chunk(text: str, image: Any, position: str = "") -> None:
        token_count = num_tokens_from_string(text)
        position = position or ""
        if token_count < 8:
            position = ""
        if chunks[-1] == "" or token_counts[-1] > (
            chunk_token_num * (100 - overlapped_percent) / 100.0
        ):
            if chunks:
                overlap_source = _remove_position_tags(chunks[-1])
                overlap_start = int(len(overlap_source) * (100 - overlapped_percent) / 100.0)
                text = overlap_source[overlap_start:] + text
            if text.find(position) < 0:
                text += position
            chunks.append(text)
            result_images.append(image)
            token_counts.append(token_count)
        else:
            if chunks[-1].find(position) < 0:
                text += position
            chunks[-1] += text
            result_images[-1] = (
                image if result_images[-1] is None else concat_img(result_images[-1], image)
            )
            token_counts[-1] += token_count

    delimiters = get_delimiters(delimiter)
    for text, image in zip(texts, images, strict=False):
        text_value = text[0] if isinstance(text, tuple) else text
        position = text[1] if isinstance(text, tuple) and len(text) > 1 else ""
        for subsection in re.split(f"({delimiters})", text_value):
            if re.match(f"^{delimiters}$", subsection):
                continue
            add_chunk(f"\n{subsection}", image, position)
    return chunks, result_images


def naive_merge_docx(
    sections: list,
    chunk_token_num: int = 128,
    delimiter: str = "\n。；！？",
) -> tuple[list[str], list]:
    if not sections:
        return [], []
    chunks = [""]
    images = [None]
    token_counts = [0]

    def add_chunk(text: str, image: Any, position: str = "") -> None:
        token_count = num_tokens_from_string(text)
        if token_count < 8:
            position = ""
        if chunks[-1] == "" or token_counts[-1] > chunk_token_num:
            if text.find(position) < 0:
                text += position
            chunks.append(text)
            images.append(image)
            token_counts.append(token_count)
        else:
            if chunks[-1].find(position) < 0:
                text += position
            chunks[-1] += text
            images[-1] = concat_img(images[-1], image)
            token_counts[-1] += token_count

    delimiters = get_delimiters(delimiter)
    pending = ""
    last_image = None
    for section, image in sections:
        last_image = image
        if not image:
            pending += f"{section}\n"
            continue
        for subsection in re.split(f"({delimiters})", pending + section):
            if re.match(f"^{delimiters}$", subsection):
                continue
            add_chunk(f"\n{subsection}", image)
        pending = ""
    if pending:
        for subsection in re.split(f"({delimiters})", pending):
            if re.match(f"^{delimiters}$", subsection):
                continue
            add_chunk(f"\n{subsection}", last_image)
    return chunks, images


__all__ = [
    "add_positions",
    "concat_img",
    "find_codec",
    "get_delimiters",
    "naive_merge",
    "naive_merge_docx",
    "naive_merge_with_images",
    "tokenize",
    "tokenize_chunks",
]
