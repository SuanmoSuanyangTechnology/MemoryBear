"""Safe request-local image validation and preparation."""

from __future__ import annotations

import base64
import binascii
import io
import warnings
from dataclasses import dataclass, field

from PIL import Image, UnidentifiedImageError

from ..errors import KnowledgeError

MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_BASE64_CHARACTERS = ((MAX_IMAGE_BYTES + 2) // 3) * 4
_MEDIA_TYPE_TO_FORMAT = {
    "image/jpeg": "JPEG",
    "image/png": "PNG",
    "image/webp": "WEBP",
    "image/bmp": "BMP",
}


@dataclass(frozen=True)
class ValidatedImageData:
    media_type: str
    data_uri: str = field(repr=False)
    decoded_bytes: int
    width: int
    height: int


def _validation_error(message: str) -> KnowledgeError:
    return KnowledgeError.from_code("KB_VALIDATION_ERROR", message)


def _input_limit_error(message: str) -> KnowledgeError:
    return KnowledgeError.from_code("KB_MULTIMODAL_INPUT_LIMIT", message)


def validate_image_data_uri(content: str) -> ValidatedImageData:
    header, separator, payload = content.partition(",")
    if not separator or not header.startswith("data:") or not header.endswith(";base64"):
        raise _validation_error("Image query must be a Base64 data URI")
    media_type = header[5:-7]
    expected_format = _MEDIA_TYPE_TO_FORMAT.get(media_type)
    if expected_format is None:
        raise _validation_error("Image query media type is not supported")
    if not payload:
        raise _validation_error("Image query content must not be empty")
    if len(payload) > MAX_BASE64_CHARACTERS:
        raise _input_limit_error("Image query exceeds the 10 MiB limit")
    try:
        binary = base64.b64decode(payload, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise _validation_error("Image query Base64 content is invalid") from exc
    if not binary:
        raise _validation_error("Image query content must not be empty")
    if len(binary) > MAX_IMAGE_BYTES:
        raise _input_limit_error("Image query exceeds the 10 MiB limit")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(binary)) as image:
                width, height = image.size
                actual_format = image.format
                image.verify()
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise _input_limit_error("Image query dimensions exceed the safe limit") from exc
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError) as exc:
        raise _validation_error("Image query content is not a valid image") from exc
    if width < 1 or height < 1:
        raise _validation_error("Image query dimensions must be positive")
    if actual_format != expected_format:
        raise _validation_error("Image query media type does not match its content")
    return ValidatedImageData(
        media_type=media_type,
        data_uri=content,
        decoded_bytes=len(binary),
        width=width,
        height=height,
    )


__all__ = [
    "MAX_IMAGE_BYTES",
    "ValidatedImageData",
    "validate_image_data_uri",
]
