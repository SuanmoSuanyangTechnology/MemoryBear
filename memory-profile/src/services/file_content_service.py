from __future__ import annotations

import uuid
from typing import Any
from urllib.parse import urlparse

from src.bootstrap import get_settings

_PERMANENT_PATH_MARKER = "/storage/permanent/"


def extract_permanent_file_id(url: Any, *, trust_any_host: bool = False) -> str | None:
    """识别本服务自铸的永久下载 URL，并取出其中的文件 ID。

    trust_any_host=False（URL 来自请求，不可信）：只认当前 FILE_LOCAL_SERVER_URL
    前缀，避免任意公网 URL 被误判成本服务自有文件而触发服务端回拉（SSRF）。
    trust_any_host=True（URL 来自已持久化的元数据 / 变量池，可信）：额外接受路径
    形态，这样域名或端口变更后，旧数据里的永久 URL 仍能还原成文件 ID 自行取字节。
    """
    if not isinstance(url, str):
        return None
    normalized_url = url.split("?", 1)[0]
    configured_prefix = (
        f"{get_settings().file_local_server_url.rstrip('/')}{_PERMANENT_PATH_MARKER}"
    )
    if normalized_url.startswith(configured_prefix):
        candidate = normalized_url[len(configured_prefix) :]
    elif trust_any_host:
        path = urlparse(normalized_url).path
        marker_index = path.rfind(_PERMANENT_PATH_MARKER)
        if marker_index < 0:
            return None
        candidate = path[marker_index + len(_PERMANENT_PATH_MARKER) :]
    else:
        return None

    candidate = candidate.strip("/")
    try:
        return str(uuid.UUID(candidate))
    except (TypeError, ValueError):
        return None
