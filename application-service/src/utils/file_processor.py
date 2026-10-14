"""File metadata helpers for resolved workflow variable defaults.

Frozen synchronous subset of the host ``core/workflow/utils/file_processor``:
the dict shapes, the 10s HTTP timeout, the HEAD -> GET fallback, and the
URL-parse fallback on request failure must stay byte-compatible with the host
so resolved defaults look identical on both write paths.
"""

from __future__ import annotations

import mimetypes
import os
from typing import Any
from urllib.parse import unquote, urlparse


def build_file_object_dict_from_url(
        url: str,
        file_type: str,
        origin_file_type: str,
) -> dict[str, Any]:
    """Build a FileObject dict for a remote_url file using only URL parsing (no HTTP request).
    Used as fallback when HTTP request fails.
    """
    name = unquote(os.path.basename(urlparse(url).path)) or None
    _, ext = os.path.splitext(name or "")
    extension = ext.lstrip(".").lower() if ext else None
    guessed_mime = mimetypes.guess_type(url)[0]
    return {
        "type": file_type,
        "url": url,
        "transfer_method": "remote_url",
        "origin_file_type": origin_file_type,
        "file_id": None,
        "name": name,
        "size": None,
        "extension": extension,
        "mime_type": guessed_mime or origin_file_type,
        "is_file": True,
    }


def fetch_remote_file_meta(
        url: str,
        file_type: str,
        origin_file_type: str,
) -> dict[str, Any]:
    """Fetch remote file metadata via HEAD (fallback GET) and build a FileObject dict.
    Falls back to URL-only parsing if the HTTP request fails.
    """
    import httpx

    name = extension = None
    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.head(url, follow_redirects=True)
            if resp.status_code != 200:
                resp = client.get(url, follow_redirects=True)

        cl = resp.headers.get("Content-Length")
        size = int(cl) if cl else None

        ct = resp.headers.get("Content-Type", "").split(";")[0].strip()
        mime_type = ct or origin_file_type

        cd = resp.headers.get("Content-Disposition", "")
        if "filename=" in cd:
            name = cd.split("filename=")[-1].strip('"').strip("'")
        if not name:
            name = unquote(os.path.basename(urlparse(url).path)) or None

        if name:
            _, ext = os.path.splitext(name)
            extension = ext.lstrip(".").lower() if ext else None
        if not extension and mime_type:
            ext = mimetypes.guess_extension(mime_type)
            extension = ext.lstrip(".").lower() if ext else None
    except Exception:
        return build_file_object_dict_from_url(url, file_type, origin_file_type)

    return build_file_object_dict_from_meta(
        file_type=file_type,
        transfer_method="remote_url",
        origin_file_type=origin_file_type,
        file_id=None,
        url=url,
        file_name=name,
        file_size=size,
        file_ext=extension,
        content_type=mime_type,
    )


def build_file_object_dict_from_meta(
        file_type: str,
        transfer_method: str,
        origin_file_type: str,
        file_id: str,
        url: str,
        file_name: str | None,
        file_size: int | None,
        file_ext: str | None,
        content_type: str | None,
) -> dict[str, Any]:
    """Build a FileObject dict from already-fetched FileMetadata fields."""
    ext = (file_ext or "").lstrip(".")
    return {
        "type": file_type,
        "url": url,
        "transfer_method": transfer_method,
        "origin_file_type": content_type or origin_file_type,
        "file_id": file_id,
        "name": file_name,
        "size": file_size,
        "extension": ext.lower() if ext else None,
        "mime_type": content_type,
        "is_file": True,
    }


__all__ = [
    "build_file_object_dict_from_meta",
    "build_file_object_dict_from_url",
    "fetch_remote_file_meta",
]
