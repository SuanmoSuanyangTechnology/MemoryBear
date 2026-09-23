"""文本净化工具。

PostgreSQL 的 VARCHAR/TEXT/JSON 列不允许包含 NUL 字符（``U+0000``，
字节 ``0x00``），即使它是合法的 UTF-8 编码，写入时也会被 asyncpg 拒绝：

    CharacterNotInRepertoireError: invalid byte sequence for encoding "UTF8": 0x00

NUL 通常来自节点把二进制内容（ZIP/PDF 等）当作文本解码——例如
``bytes.decode("latin-1")`` 的逐字节映射、``decode("utf-8", "ignore")``
后残留的控制字符等。在工作流输出进入数据库/前端之前统一净化。
"""

from __future__ import annotations

from typing import Any

# PostgreSQL 文本类型唯一明确拒绝的字符是 U+0000，仅清洗它，
# 避免误伤包含其他控制字符（如制表符、颜色控制码）的合法内容。
_NUL_CHAR = "\x00"


def sanitize_text(value: str) -> str:
    """移除字符串中的 NUL 字符；不含 NUL 时原样返回（零拷贝）。"""
    return value.replace(_NUL_CHAR, "") if _NUL_CHAR in value else value


def sanitize_value(value: Any) -> Any:
    """递归净化 JSON 兼容结构中的字符串。

    整个结构不含任何 NUL 时原样返回（常见情况下零拷贝，避免对大型事件
    做无谓的深拷贝）；仅当检测到 NUL 时才重建受影响的 dict/list 容器。
    非字符串对象（数字、布尔、None、UUID、pydantic 模型等）原样返回。
    """
    cleaned, changed = _sanitize(value)
    return cleaned


def _sanitize(value: Any) -> tuple[Any, bool]:
    if isinstance(value, str):
        if _NUL_CHAR in value:
            return value.replace(_NUL_CHAR, ""), True
        return value, False
    if isinstance(value, dict):
        changed = False
        new_dict: dict | None = None
        for k, v in value.items():
            cleaned_v, item_changed = _sanitize(v)
            if item_changed:
                if new_dict is None:
                    new_dict = dict(value)
                new_dict[k] = cleaned_v
                changed = True
        return (new_dict if changed else value), changed
    if isinstance(value, list):
        changed = False
        new_list: list | None = None
        for i, v in enumerate(value):
            cleaned_v, item_changed = _sanitize(v)
            if item_changed:
                if new_list is None:
                    new_list = list(value)
                new_list[i] = cleaned_v
                changed = True
        return (new_list if changed else value), changed
    return value, False
