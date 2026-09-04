"""识别并安全公开常见的多模态模型输入异常。"""
from __future__ import annotations

import re
import uuid
from typing import Any

from app.core.error_codes import BizCode


_INPUT_LIMIT_PATTERNS = (
    re.compile(r"\btoo\s+many\s+(?:input\s+)?images?\b", re.IGNORECASE),
    re.compile(
        r"\bimages?\b.{0,100}\b(?:maximum\s+allowed|max(?:imum)?\s+(?:count|number)|limit)\b",
        re.IGNORECASE | re.DOTALL,
    ),
    re.compile(r"(?:图片|图像).{0,30}(?:数量|个数).{0,30}(?:超过|超出|上限|限制)"),
)

_IMAGE_DOWNLOAD_RATE_LIMIT_PATTERNS = (
    re.compile(
        r"\b(?:error|failed)\s+while\s+downloading\b.{0,500}\bstatus\s+code:\s*429\b",
        re.IGNORECASE | re.DOTALL,
    ),
)

_IMAGE_DIMENSION_TOO_LARGE_PATTERNS = (
    re.compile(
        r"\bimage\s+(?:length\s+and\s+width|width\s+and\s+height|dimensions?)\b.{0,200}\b(?:maximum|max(?:imum)?|upper\s+limit|at\s+most|no\s+larger\s+than|(?:must|should)\s+be\s+(?:less|smaller)\s+than|(?:cannot|must\s+not|should\s+not)\s+(?:be\s+)?(?:larger|greater)\s+than|(?:cannot|must\s+not|should\s+not)\s+exceed|exceed(?:s|ed|ing)?)\b",
        re.IGNORECASE | re.DOTALL,
    ),
    re.compile(r"(?:图片|图像).{0,30}(?:尺寸|宽度|高度).{0,50}(?:超过|超出|最大|上限|不得大于|不能大于|不大于)"),
)

_IMAGE_DIMENSION_TOO_SMALL_PATTERNS = (
    re.compile(
        r"\bimage\s+(?:length\s+and\s+width|width\s+and\s+height|dimensions?)\b.{0,200}\b(?:minimum|at\s+least|(?:must|should)\s+be\s+(?:larger|greater)\s+than|(?:cannot|must\s+not|should\s+not)\s+(?:be\s+)?(?:less|smaller)\s+than)\b",
        re.IGNORECASE | re.DOTALL,
    ),
    re.compile(r"(?:图片|图像).{0,30}(?:尺寸|宽度|高度).{0,50}(?:最小|至少|不得小于|不能小于|不小于)"),
)

_IMAGE_DIMENSION_LIMIT_PATTERNS = (
    re.compile(
        r"\bimage\s+(?:length\s+and\s+width|width\s+and\s+height|dimensions?)\b.{0,200}\b(?:do\s+not\s+meet|restrictions?|must\s+be|larger\s+than|minimum)\b",
        re.IGNORECASE | re.DOTALL,
    ),
    re.compile(r"(?:图片|图像).{0,30}(?:尺寸|宽度|高度).{0,50}(?:不符合|限制|最小|至少|大于|最大|上限|超过|超出)"),
)

_DOWNLOAD_FAILED_PATTERNS = (
    re.compile(r"\bfailed\s+to\s+download\s+multimodal\s+content\b", re.IGNORECASE),
    re.compile(
        r"\bfailed\s+to\s+(?:download|fetch|load|retrieve).{0,80}\b(?:multimodal|image|picture|media)\b",
        re.IGNORECASE | re.DOTALL,
    ),
    re.compile(
        r"\b(?:multimodal|image|picture|media)\b.{0,80}\b(?:download|fetch|load|retrieve)\s+(?:failed|failure|error)\b",
        re.IGNORECASE | re.DOTALL,
    ),
    re.compile(r"(?:图片|图像|多媒体|多模态).{0,30}(?:下载失败|无法下载|无法访问|读取失败|无法读取)"),
)


def _append_text(value: Any, texts: list[str], depth: int = 0) -> None:
    if depth > 4 or len(texts) >= 60:
        return
    if isinstance(value, dict):
        for item in value.values():
            _append_text(item, texts, depth + 1)
    elif isinstance(value, (list, tuple, set)):
        for item in value:
            _append_text(item, texts, depth + 1)
    elif value is not None:
        texts.append(str(value)[:4000])


def _extract_text(error: BaseException) -> str:
    texts: list[str] = []
    pending = [error]
    seen: set[int] = set()
    while pending and len(seen) < 8:
        current = pending.pop(0)
        if id(current) in seen:
            continue
        seen.add(id(current))
        texts.append(str(current)[:12000])
        for attr_name in ("message", "code", "type", "body"):
            _append_text(getattr(current, attr_name, None), texts)
        for linked in (getattr(current, "cause", None), current.__cause__, current.__context__):
            if isinstance(linked, BaseException) and id(linked) not in seen:
                pending.append(linked)
    return " ".join(texts)


def classify_multimodal_exception(
    error: BaseException,
    *,
    debug_id: str | None = None,
) -> dict[str, Any] | None:
    """命中目标错误时返回安全对象；其他异常返回 ``None`` 并保持原处理。"""
    text = _extract_text(error)
    resolved_debug_id = debug_id or f"err_{uuid.uuid4().hex[:12]}"

    if any(pattern.search(text) for pattern in _IMAGE_DOWNLOAD_RATE_LIMIT_PATTERNS):
        kind = "multimodal_image_download_rate_limited"
        message = "模型下载图片过于频繁，已被限流，请稍后重试；请检查上传文件是否包含过多图片，必要时可精简或拆分文件后再试。"
        retryable = True
    elif any(pattern.search(text) for pattern in _INPUT_LIMIT_PATTERNS):
        kind = "multimodal_input_limit"
        message = "当前上传文件中的图片数量超过模型单次处理上限，请减少图片数量、精简文档内容或拆分文档后重试。"
        retryable = False
    elif any(pattern.search(text) for pattern in _IMAGE_DIMENSION_TOO_LARGE_PATTERNS):
        kind = "multimodal_image_dimension_limit"
        message = "上传文件中包含尺寸过大的图片，模型对图片宽度或高度设有最大限制。请缩小或替换此类图片后重试。"
        retryable = False
    elif any(pattern.search(text) for pattern in _IMAGE_DIMENSION_TOO_SMALL_PATTERNS):
        kind = "multimodal_image_dimension_limit"
        message = "上传文件中包含尺寸过小的图片，模型要求图片宽度和高度均大于 10 像素。请删除或替换此类图片后重试。"
        retryable = False
    elif any(pattern.search(text) for pattern in _IMAGE_DIMENSION_LIMIT_PATTERNS):
        kind = "multimodal_image_dimension_limit"
        message = "上传文件中包含不符合模型要求的图片尺寸，请按模型的尺寸限制调整或替换此类图片后重试。"
        retryable = False
    elif any(pattern.search(text) for pattern in _DOWNLOAD_FAILED_PATTERNS):
        kind = "multimodal_download_failed"
        message = "部分多媒体文件无法读取，可能是图片数量过多或其他问题，请检查文件格式、大小和访问地址后重试。"
        retryable = True
    else:
        return None

    return {
        "kind": kind,
        "code": int(BizCode.INVALID_PARAMETER),
        "message": message,
        "retryable": retryable,
        "debug_id": resolved_debug_id,
    }
