# -*- coding: UTF-8 -*-
import mimetypes
import os
import uuid
from typing import Any
from urllib.parse import urlparse, unquote

TRANSFORM_FILE_TYPE = {
    'text/plain': 'document/text',
    'text/markdown': 'document/markdown',
    'text/x-markdown': 'document/x-markdown',

    'application/pdf': 'document/pdf',

    'application/msword': 'document/doc',
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document': 'document/docx',

    'application/vnd.ms-powerpoint': 'document/ppt',
    'application/vnd.openxmlformats-officedocument.presentationml.presentation': 'document/pptx',
}
ALLOWED_FILE_TYPES = [
    'text/plain',
    'text/markdown',
    'text/x-markdown',
    'application/pdf',
    'application/msword',
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    'application/vnd.ms-powerpoint',
    'application/vnd.openxmlformats-officedocument.presentationml.presentation',
    'image/jpg',
    'image/jpeg',
    'image/png',
    'image/gif',
    'image/bmp',
    'image/webp',
    'image/svg+xml',
    'video/mp4',
    'video/quicktime',
    'video/x-msvideo',
    'video/x-matroska',
    'video/webm',
    'video/x-flv',
    'video/x-ms-wmv',
    'audio/mpeg',
    'audio/wav',
    'audio/ogg',
    'audio/aac',
    'audio/flac',
    'audio/mp4',
    'audio/x-ms-wma',
    'audio/x-m4a',
]


def mime_to_file_type(mime_type):
    if mime_type not in ALLOWED_FILE_TYPES:
        return None
    return TRANSFORM_FILE_TYPE.get(mime_type, mime_type)


def build_file_object_dict_from_url(url: str, file_type: str, origin_file_type: str) -> dict[str, Any]:
    """Build a FileObject dict for a remote_url file using only URL parsing (no HTTP request).
    Used as fallback when HTTP request fails.
    """
    raw_path = url.split("?")[0]
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


async def fetch_remote_file_meta(
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
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.head(url, follow_redirects=True)
            if resp.status_code != 200:
                resp = await client.get(url, follow_redirects=True)

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


def resolve_local_file_object_dict(
        db,
        upload_file_id: str | uuid.UUID,
        file_type: str,
        origin_file_type: str,
) -> dict[str, Any] | None:
    """Query FileMetadata and build a FileObject dict for a local_file.
    Returns None if the file is not found or not completed.
    """
    from app.models.file_metadata_model import FileMetadata
    from app.core.config import settings

    try:
        fid = uuid.UUID(str(upload_file_id))
    except ValueError:
        return None

    meta = db.query(FileMetadata).filter(
        FileMetadata.id == fid,
        FileMetadata.status == "completed"
    ).first()
    if not meta:
        return None

    url = f"{settings.FILE_LOCAL_SERVER_URL}/storage/permanent/{fid}"
    return build_file_object_dict_from_meta(
        file_type=file_type,
        transfer_method="local_file",
        origin_file_type=origin_file_type,
        file_id=str(fid),
        url=url,
        file_name=meta.file_name,
        file_size=meta.file_size,
        file_ext=meta.file_ext,
        content_type=meta.content_type,
    )
