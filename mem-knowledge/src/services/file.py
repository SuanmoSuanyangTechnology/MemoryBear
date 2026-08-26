"""Knowledge file behavior copied from the legacy service and controller."""

from __future__ import annotations

import logging
import struct
import uuid
import zlib
from collections.abc import AsyncIterator
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..api.dependencies import Principal
from ..api.schemas.document import Document as DocumentSchema
from ..api.schemas.document import DocumentCreate
from ..api.schemas.file import File as FileSchema
from ..api.schemas.file import FileCreate, FileUpdate
from ..errors import KnowledgeError
from ..models.owned import (
    FILE_ROLE_DERIVED_IMAGE,
    FILE_ROLE_SOURCE,
    Document,
    File,
    Knowledge,
)
from ..rag.knowledge_graph import GraphPipelineConfigError
from ..rag.parser_config import normalize_document_parser_config
from ..repositories import document as document_repository
from ..repositories import file as file_repository
from . import knowledge as knowledge_service
from .knowledge_file_storage import KnowledgeFileStorage, generate_kb_file_key
from .qa_export import cleanup_export_file, iter_export_file

logger = logging.getLogger(__name__)
ZIP_STREAM_CHUNK_SIZE = 1024 * 1024


@dataclass(frozen=True)
class QAExportSpec:
    kb_id: uuid.UUID
    document_id: uuid.UUID
    file_ext: str | None
    file_name: str


@dataclass(frozen=True)
class QAExportFile:
    path: str
    filename: str
    media_type: str


@dataclass(frozen=True)
class UploadPlan:
    file_id: uuid.UUID
    file_key: str
    kb_id: uuid.UUID
    parent_id: uuid.UUID
    created_by: uuid.UUID
    file_name: str
    file_ext: str
    file_size: int
    parser_id: str
    parser_config: dict[str, Any]


@dataclass(frozen=True)
class UploadOutcome:
    file_id: uuid.UUID
    document_id: uuid.UUID
    document_data: dict[str, Any]


@dataclass(frozen=True)
class StoredFileSnapshot:
    file_id: uuid.UUID
    kb_id: uuid.UUID
    file_key: str
    file_name: str
    file_ext: str


@dataclass(frozen=True)
class FileDeletionPlan:
    file_ids: tuple[uuid.UUID, ...]
    storage_keys: tuple[str, ...]
    derived_file_ids: tuple[uuid.UUID, ...]
    derived_storage_keys: tuple[str, ...]


def _not_found(message: str = "File resource not found") -> KnowledgeError:
    return KnowledgeError.from_code("KB_RESOURCE_NOT_FOUND", message)


def file_to_data(file: File) -> dict[str, Any]:
    return FileSchema.model_validate(file).model_dump(mode="json")


def document_to_data(document: Document) -> dict[str, Any]:
    return DocumentSchema.model_validate(document).model_dump(mode="json")


async def get_file(
    db: AsyncSession,
    file_id: uuid.UUID,
    principal: Principal,
    kb_id: uuid.UUID | None = None,
) -> File | None:
    return await file_repository.get_file_by_id_in_workspace_async(
        db,
        file_id,
        principal.workspace_id,
        kb_id,
    )


async def require_parent_folder(
    db: AsyncSession,
    kb_id: uuid.UUID,
    parent_id: uuid.UUID | None,
    principal: Principal,
) -> None:
    if parent_id is None or parent_id == kb_id:
        return
    parent = await get_file(db, parent_id, principal, kb_id)
    if parent is None or parent.file_ext != "folder":
        raise _not_found("Parent folder does not exist")


async def list_files(
    db: AsyncSession,
    kb_id: uuid.UUID,
    parent_id: uuid.UUID,
    principal: Principal,
    *,
    page: int,
    pagesize: int,
    orderby: str | None,
    desc: bool,
    keywords: str | None,
) -> tuple[int, list[dict[str, Any]]]:
    if await knowledge_service.get_knowledge(db, kb_id, principal) is None:
        raise _not_found("Knowledge resource not found")
    await require_parent_folder(db, kb_id, parent_id, principal)
    filters = [File.kb_id == kb_id, File.file_role == FILE_ROLE_SOURCE]
    if parent_id:
        filters.append(File.parent_id == parent_id)
    if keywords:
        filters.append(File.file_name.ilike(f"%{keywords}%"))
    total, files = await file_repository.get_files_paginated_async(
        db,
        filters,
        page,
        pagesize,
        orderby,
        desc,
    )
    return total, [file_to_data(file) for file in files]


async def create_folder(
    db: AsyncSession,
    kb_id: uuid.UUID,
    parent_id: uuid.UUID,
    folder_name: str,
    principal: Principal,
) -> File:
    if await knowledge_service.get_knowledge(db, kb_id, principal) is None:
        raise _not_found("Knowledge resource not found")
    await require_parent_folder(db, kb_id, parent_id, principal)
    return await file_repository.create_file_async(
        db,
        FileCreate(
            kb_id=kb_id,
            created_by=principal.actor_id,
            parent_id=parent_id,
            file_name=folder_name,
            file_ext="folder",
            file_size=0,
        ),
    )


def _document_parser_config(knowledge: Knowledge, *, inherit: bool) -> dict[str, Any]:
    config = {
        "layout_recognize": "mineru",
        "chunk_token_num": 128,
        "delimiter": "\n",
        "auto_keywords": 0,
        "auto_questions": 0,
        "html4excel": "false",
    }
    if inherit and knowledge.parser_config:
        config.update(dict(knowledge.parser_config))
    return config


async def upload_content(
    db: AsyncSession,
    _storage: KnowledgeFileStorage,
    *,
    kb_id: uuid.UUID,
    parent_id: uuid.UUID,
    file_name: str,
    file_ext: str,
    content: bytes,
    content_type: str | None,
    principal: Principal,
    inherit_parser_config: bool,
    parser_id: str = "naive",
    parser_config: dict[str, Any] | None = None,
) -> UploadPlan:
    """Validate an upload and return a scalar plan without writing rows."""

    del content_type
    knowledge = await knowledge_service.get_knowledge(db, kb_id, principal)
    if knowledge is None:
        raise _not_found("Knowledge resource not found")
    try:
        normalized_parser_config = normalize_document_parser_config(
            parser_config
            if parser_config is not None
            else _document_parser_config(knowledge, inherit=inherit_parser_config)
        )
    except (ValueError, GraphPipelineConfigError) as exc:
        raise KnowledgeError.from_code("KB_VALIDATION_ERROR", str(exc)) from exc
    await require_parent_folder(db, kb_id, parent_id, principal)
    file_id = uuid.uuid4()
    return UploadPlan(
        file_id=file_id,
        file_key=generate_kb_file_key(kb_id, file_id, file_ext),
        kb_id=kb_id,
        parent_id=parent_id,
        created_by=principal.actor_id,
        file_name=file_name,
        file_ext=file_ext,
        file_size=len(content),
        parser_id=parser_id,
        parser_config=deepcopy(normalized_parser_config),
    )


async def persist_uploaded_content(
    db: AsyncSession,
    plan: UploadPlan,
    principal: Principal,
) -> UploadOutcome:
    """Revalidate ownership and atomically persist a planned upload."""

    if plan.created_by != principal.actor_id:
        raise _not_found("Knowledge resource not found")
    if await knowledge_service.get_knowledge(db, plan.kb_id, principal) is None:
        raise _not_found("Knowledge resource not found")
    await require_parent_folder(db, plan.kb_id, plan.parent_id, principal)
    try:
        db_file = await file_repository.add_file_async(
            db,
            FileCreate(
                kb_id=plan.kb_id,
                created_by=plan.created_by,
                parent_id=plan.parent_id,
                file_name=plan.file_name,
                file_ext=plan.file_ext,
                file_size=plan.file_size,
                file_key=plan.file_key,
            ),
            file_id=plan.file_id,
        )
        document = await document_repository.add_document_async(
            db,
            DocumentCreate(
                kb_id=plan.kb_id,
                created_by=plan.created_by,
                file_id=db_file.id,
                file_name=plan.file_name,
                file_ext=plan.file_ext,
                file_size=plan.file_size,
                file_meta={},
                parser_id=plan.parser_id,
                parser_config=deepcopy(plan.parser_config),
            ),
        )
        await db.refresh(document)
        outcome = UploadOutcome(
            file_id=db_file.id,
            document_id=document.id,
            document_data=document_to_data(document),
        )
        await db.commit()
        return outcome
    except Exception:
        await db.rollback()
        raise


async def compensate_storage_upload(
    storage: KnowledgeFileStorage,
    file_key: str,
) -> None:
    try:
        await storage.delete(file_key)
    except Exception as exc:
        logger.warning(
            "Failed to compensate storage upload error_type=%s",
            type(exc).__name__,
        )


def stored_file_snapshot(file: Any) -> StoredFileSnapshot:
    return StoredFileSnapshot(
        file_id=file.id,
        kb_id=file.kb_id,
        file_key=file.file_key,
        file_name=file.file_name,
        file_ext=file.file_ext,
    )


async def prepare_file_deletion(
    db: AsyncSession,
    file_id: uuid.UUID,
    principal: Principal,
) -> FileDeletionPlan:
    target = await get_file(db, file_id, principal)
    if target is None:
        raise _not_found()
    files = [target]
    if target.file_ext == "folder":
        result = await db.execute(
            select(File).where(File.parent_id == target.id, File.kb_id == target.kb_id)
        )
        files.extend(result.scalars().all())

    source_ids = tuple(
        file.id for file in files if file.file_role == FILE_ROLE_SOURCE
    )
    document_ids: tuple[uuid.UUID, ...] = ()
    if source_ids:
        result = await db.execute(select(Document.id).where(Document.file_id.in_(source_ids)))
        document_ids = tuple(result.scalars().all())
    derived_files: list[File] = []
    if document_ids:
        result = await db.execute(
            select(File).where(
                File.source_document_id.in_(document_ids),
                File.file_role == FILE_ROLE_DERIVED_IMAGE,
            )
        )
        derived_files = list(result.scalars().all())
    return FileDeletionPlan(
        file_ids=tuple(file.id for file in files),
        storage_keys=tuple(file.file_key for file in files if file.file_key),
        derived_file_ids=tuple(file.id for file in derived_files),
        derived_storage_keys=tuple(
            file.file_key for file in derived_files if file.file_key
        ),
    )


async def delete_file_storage(
    storage: KnowledgeFileStorage,
    plan: FileDeletionPlan,
) -> None:
    for file_key in plan.storage_keys:
        try:
            await storage.delete(file_key)
        except Exception:
            logger.warning("Failed to delete file from storage: %s", file_key)
    for file_key in plan.derived_storage_keys:
        try:
            await storage.delete(file_key)
        except Exception:
            logger.warning("Failed to delete derived image: %s", file_key)


async def persist_file_deletion(
    db: AsyncSession,
    plan: FileDeletionPlan,
) -> None:
    try:
        await file_repository.delete_files_by_ids_async(db, plan.derived_file_ids)
        await file_repository.delete_files_by_ids_async(db, plan.file_ids)
        await db.commit()
    except Exception:
        await db.rollback()
        raise


async def update_file(
    db: AsyncSession,
    file_id: uuid.UUID,
    update_data: FileUpdate,
    principal: Principal,
) -> File:
    file = await get_file(db, file_id, principal)
    if file is None:
        raise _not_found()
    update_fields = update_data.model_dump(exclude_unset=True)
    if "parent_id" in update_fields:
        await require_parent_folder(db, file.kb_id, update_fields["parent_id"], principal)
    for field, value in update_fields.items():
        if hasattr(file, field):
            setattr(file, field, value)
    try:
        await db.commit()
        await db.refresh(file)
        return file
    except Exception:
        await db.rollback()
        raise


async def get_qa_export_spec(
    db: AsyncSession,
    file: File,
) -> QAExportSpec | None:
    result = await db.execute(select(Document).where(Document.file_id == file.id))
    document = result.scalars().first()
    if document is None or (document.parser_config or {}).get("doc_type") != "qa":
        return None
    return QAExportSpec(
        kb_id=file.kb_id,
        document_id=document.id,
        file_ext=file.file_ext,
        file_name=file.file_name,
    )


def build_zip_arcnames(files: list[Any]) -> list[tuple[str, str, str]]:
    seen: set[str] = set()
    result = []
    for file in files:
        original = file.file_name
        candidate = original
        counter = 1
        while candidate in seen:
            path = Path(original)
            candidate = f"{path.stem}_{counter}{path.suffix}"
            counter += 1
        seen.add(candidate)
        result.append((original, file.file_key, candidate))
    return result


def make_zip_filename(
    files: list[Any],
    custom_name: str | None = None,
    base_name: str | None = None,
) -> str:
    if custom_name:
        name = custom_name
    else:
        stem = base_name or files[0].file_name
        stem = stem.rsplit(".", 1)[0] if "." in stem else stem
        name = f"{stem}.zip" if len(files) == 1 else f"{stem}_等{len(files)}个文件.zip"
    return name if name.endswith(".zip") else f"{name}.zip"


async def stream_zip_files(
    entries: list[tuple[str, str, str]],
    storage: KnowledgeFileStorage,
    qa_exports: dict[str, QAExportFile] | None = None,
) -> AsyncIterator[bytes]:
    central_entries: list[tuple[bytes, int, int, int, int]] = []
    skipped: list[str] = []
    offset = 0
    cleanup_paths = {export.path for export in (qa_exports or {}).values()}
    try:
        for file_name, file_key, arc_name in entries:
            try:
                export = (qa_exports or {}).get(file_key)
                chunks = (
                    iter_export_file(export.path)
                    if export
                    else storage.download_stream(file_key, ZIP_STREAM_CHUNK_SIZE)
                )
                name = arc_name.encode("utf-8")
                header = struct.pack(
                    "<4sHHHHHIIIHH",
                    b"PK\x03\x04", 20, 0x0808, 8, 0, 0, 0, 0, 0, len(name), 0,
                ) + name
                yield header
                crc = compressed_size = raw_size = 0
                compressor = zlib.compressobj(zlib.Z_DEFAULT_COMPRESSION, zlib.DEFLATED, -15)
                async for chunk in chunks:
                    crc = zlib.crc32(chunk, crc) & 0xFFFFFFFF
                    raw_size += len(chunk)
                    compressed = compressor.compress(chunk)
                    compressed_size += len(compressed)
                    if compressed:
                        yield compressed
                tail = compressor.flush()
                compressed_size += len(tail)
                if tail:
                    yield tail
                yield struct.pack("<III", crc, compressed_size, raw_size)
                central_entries.append((name, crc, compressed_size, raw_size, offset))
                offset += len(header) + compressed_size + 12
            except Exception:
                logger.warning("Skipping file in ZIP: %s", file_name, exc_info=True)
                skipped.append(file_name)

        if skipped:
            content = (
                "以下文件下载失败，未包含在此ZIP包中：\n\n"
                + "\n".join(f"- {name}" for name in skipped)
                + "\n"
            ).encode()
            name = b"_skipped_files.txt"
            crc = zlib.crc32(content) & 0xFFFFFFFF
            compressor = zlib.compressobj(zlib.Z_DEFAULT_COMPRESSION, zlib.DEFLATED, -15)
            compressed = compressor.compress(content) + compressor.flush()
            header = struct.pack(
                "<4sHHHHHIIIHH",
                b"PK\x03\x04", 20, 0x0808, 8, 0, 0, 0, 0, 0, len(name), 0,
            ) + name
            yield header
            yield compressed
            yield struct.pack("<III", crc, len(compressed), len(content))
            central_entries.append((name, crc, len(compressed), len(content), offset))
            offset += len(header) + len(compressed) + 12

        central_offset = offset
        for name, crc, compressed_size, raw_size, local_offset in central_entries:
            entry = struct.pack(
                "<4sHHHHHHIIIHHHHHII",
                b"PK\x01\x02", 20, 20, 0x0808, 8, 0, 0, crc, compressed_size,
                raw_size, len(name), 0, 0, 0, 0, 0, local_offset,
            ) + name
            yield entry
            offset += len(entry)
        yield struct.pack(
            "<4sHHHHIIH",
            b"PK\x05\x06", 0, 0, len(central_entries), len(central_entries),
            offset - central_offset, central_offset, 0,
        )
    finally:
        for path in cleanup_paths:
            cleanup_export_file(path)
