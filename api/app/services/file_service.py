import asyncio
import struct
import uuid
import zlib
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from typing import Any, AsyncGenerator
from logging import Logger

from sqlalchemy.orm import Session
from app.models.user_model import User
from app.models.file_model import File
from app.schemas.file_schema import FileCreate, FileUpdate
from app.repositories import file_repository
from app.core.logging_config import get_business_logger
from app.services.qa_export_service import (
    cleanup_qa_export_file,
    write_qa_document_export_file,
)

# Obtain a dedicated logger for business logic
business_logger = get_business_logger()

ZIP_STREAM_CHUNK_SIZE = 1024 * 1024


@dataclass(frozen=True)
class QAExportSpec:
    kb_id: uuid.UUID | str
    document_id: uuid.UUID | str
    file_ext: str | None
    file_name: str


@dataclass(frozen=True)
class QAExportFile:
    path: str
    filename: str
    media_type: str


def get_files_paginated(
        db: Session,
        current_user: User,
        filters: list,
        page: int,
        pagesize: int,
        orderby: str = None,
        desc: bool = False
) -> tuple[int, list]:
    business_logger.debug(f"Query file in pages: username={current_user.username}, page={page}, pagesize={pagesize}, orderby={orderby}, desc={desc}")

    try:
        total, items = file_repository.get_files_paginated(
            db=db,
            filters=filters,
            page=page,
            pagesize=pagesize,
            orderby=orderby,
            desc=desc
        )
        business_logger.info(f"The file paging query has been successful: username={current_user.username}, total={total}, Number of current page={len(items)}")
        return total, items
    except Exception as e:
        business_logger.error(f"Querying file pagination failed: username={current_user.username} - {str(e)}")
        raise


def create_file(
        db: Session, file: FileCreate, current_user: User
) -> File:
    business_logger.info(f"Create a file: {file.file_name}, creator: {current_user.username}")

    try:
        file.created_by = current_user.id
        if file.parent_id is None:
            file.parent_id = file.kb_id
        db_file = file_repository.create_file(
            db=db, file=file
        )
        business_logger.info(f"The file has been successfully created: {file.file_name} (ID: {db_file.id}), creator: {current_user.username}")
        return db_file
    except Exception as e:
        business_logger.error(f"Failed to create a file: {file.file_name} - {str(e)}")
        raise


def get_file_by_id(
        db: Session,
        file_id: uuid.UUID,
        current_user: User | None = None,
        kb_id: uuid.UUID | None = None,
) -> File | None:
    business_logger.debug(f"Query file based on ID: file_id={file_id}")

    try:
        workspace_id = getattr(current_user, "current_workspace_id", None)
        if current_user is not None:
            if workspace_id is None:
                return None
            file = file_repository.get_file_by_id_in_workspace(
                db=db,
                file_id=file_id,
                workspace_id=workspace_id,
                kb_id=kb_id,
            )
        else:
            file = file_repository.get_file_by_id(db=db, file_id=file_id)
        if file:
            business_logger.info(f"file query successful: {file.file_name} (ID: {file_id})")
        else:
            business_logger.warning(f"file does not exist: file_id={file_id}")
        return file
    except Exception as e:
        business_logger.error(f"Failed to query the file based on the ID: file_id={file_id} - {str(e)}")
        raise


def get_files_by_parent_id(db: Session, parent_id: uuid.UUID | None, current_user: User) -> list | None:
    business_logger.debug(f"Query file based on folder ID: parent_id={parent_id}, username: {current_user.username}")
    return file_repository.get_files_by_parent_id(db=db, parent_id=parent_id)


def delete_file_by_id(db: Session, file_id: uuid.UUID, current_user: User) -> None:
    business_logger.info(f"Delete file: file_id={file_id}, operator: {current_user.username}")

    try:
        file_repository.delete_file_by_id(db=db, file_id=file_id)
        business_logger.info(f"file_id deleted successfully: file_id={file_id}, operator: {current_user.username}")
    except Exception as e:
        business_logger.error(f"Failed to delete file: file_id={file_id} - {str(e)}")
        raise


def _is_qa_doc(db: Session, file_id: uuid.UUID) -> bool:
    """文件关联的文档是否为 QA CSV/XLSX 导入类型"""
    from app.models.document_model import Document
    doc = db.query(Document).filter(Document.file_id == file_id).first()
    if not doc or not doc.parser_config:
        return False
    return doc.parser_config.get("doc_type") == "qa"


def get_qa_export_spec(db: Session, file_id: uuid.UUID, kb_id: uuid.UUID) -> QAExportSpec | None:
    from app.models.document_model import Document
    from app.models.knowledge_model import Knowledge

    db_file = db.query(File).filter(File.id == file_id).first()
    doc = db.query(Document).filter(Document.file_id == file_id).first()
    if not db_file or not doc:
        return None

    db_knowledge = db.query(Knowledge).filter(Knowledge.id == kb_id).first()
    if not db_knowledge:
        return None

    if (doc.parser_config or {}).get("doc_type") != "qa":
        return None

    return QAExportSpec(
        kb_id=kb_id,
        document_id=doc.id,
        file_ext=db_file.file_ext,
        file_name=db_file.file_name,
    )


def build_qa_export_file(spec: QAExportSpec) -> QAExportFile | None:
    result = write_qa_document_export_file(
        kb_id=spec.kb_id,
        document_id=spec.document_id,
        file_ext=spec.file_ext,
    )
    if result is None:
        return None

    path, media_type = result
    return QAExportFile(
        path=path,
        filename=spec.file_name,
        media_type=media_type,
    )


def build_zip_arcnames(files: list[Any]) -> list[tuple[str, str, str]]:
    """同名文件自动去重，返回 [(file_name, file_key, arc_name), ...]"""
    seen: set[str] = set()
    result: list[tuple[str, str, str]] = []

    def unique_name(original: str) -> str:
        if original not in seen:
            seen.add(original)
            return original
        stem, *ext_parts = original.rsplit(".", 1)
        ext = f".{ext_parts[0]}" if ext_parts else ""
        counter = 1
        while True:
            candidate = f"{stem}_{counter}{ext}"
            if candidate not in seen:
                seen.add(candidate)
                return candidate
            counter += 1

    for f in files:
        result.append((f.file_name, f.file_key, unique_name(f.file_name)))
    return result


def make_zip_filename(files: list[Any], custom_name: str | None = None, base_name: str | None = None) -> str:
    """生成 ZIP 文件名：自定义 / base_name / 基于首个文件名自动生成"""
    if custom_name:
        zip_name = custom_name
    else:
        stem = base_name or files[0].file_name
        stem = stem.rsplit(".", 1)[0] if "." in stem else stem
        count = len(files)
        zip_name = f"{stem}.zip" if count == 1 else f"{stem}_等{count}个文件.zip"
    if not zip_name.endswith(".zip"):
        zip_name += ".zip"
    return zip_name


async def stream_zip_files(
    entries: list[tuple[str, str, str]],  # [(file_name, file_key, arc_name), ...]
    storage_service: Any,
    logger: Logger,
    pre_fetched: dict[str, bytes] | None = None,
    qa_export_specs: Mapping[str, QAExportSpec] | None = None,
) -> AsyncGenerator[bytes, None]:
    """逐文件下载 → 实时 deflate 压缩 → yield ZIP 数据块，全程不落盘。
    单个文件下载失败时跳过并记录，最终 ZIP 内含 _skipped_files.txt 清单。

    pre_fetched: key=file_key 的预取内容字典，QA 文档命中时直接用，不走 storage 下载。
    """

    central_entries: list[tuple[bytes, int, int, int, int]] = []
    skipped_files: list[str] = []
    offset = 0
    cleanup_paths: set[str] = set()

    try:
        for file_name, file_key, arc_name in entries:
            try:
                export_file: QAExportFile | None = None
                if qa_export_specs and file_key in qa_export_specs:
                    export_file = await asyncio.to_thread(
                        build_qa_export_file,
                        qa_export_specs[file_key],
                    )
                    if export_file is not None:
                        cleanup_paths.add(export_file.path)

                if export_file is not None:
                    chunk_source = _iter_qa_export_chunks(export_file.path)
                elif pre_fetched and file_key in pre_fetched:
                    chunk_source = _iter_bytes_content(pre_fetched[file_key])
                else:
                    async with asyncio.timeout(120):
                        content = await storage_service.download_file(file_key)
                    chunk_source = _iter_bytes_content(content)

                arc_name_bytes = arc_name.encode("utf-8")
                local_header = struct.pack(
                    "<4sHHHHHIIIHH",
                    b"PK\x03\x04", 20, 0x0808, 8, 0, 0, 0, 0, 0, len(arc_name_bytes), 0,
                ) + arc_name_bytes

                yield local_header

                crc = 0
                uncompressed_size = 0
                compressed_size = 0
                compressor = zlib.compressobj(zlib.Z_DEFAULT_COMPRESSION, zlib.DEFLATED, -15)
                async for chunk in chunk_source:
                    crc = zlib.crc32(chunk, crc) & 0xFFFFFFFF
                    uncompressed_size += len(chunk)
                    compressed_chunk = compressor.compress(chunk)
                    if compressed_chunk:
                        compressed_size += len(compressed_chunk)
                        yield compressed_chunk

                compressed_tail = compressor.flush()
                if compressed_tail:
                    compressed_size += len(compressed_tail)
                    yield compressed_tail

                descriptor = struct.pack("<III", crc, compressed_size, uncompressed_size)
                yield descriptor

                header_data_len = len(local_header) + compressed_size
                central_entries.append((arc_name_bytes, crc, compressed_size, uncompressed_size, offset))
                offset += header_data_len + 12
                if export_file is not None:
                    cleanup_qa_export_file(export_file.path)
                    cleanup_paths.discard(export_file.path)
            except Exception as e:
                logger.warning(f"跳过文件 {file_name}: {e}")
                skipped_files.append(file_name)
                continue

        # --- skipped_files manifest ---
        if skipped_files:
            lines = "\n".join(f"- {name}" for name in skipped_files)
            content = f"以下文件下载失败，未包含在此ZIP包中：\n\n{lines}\n".encode("utf-8")
            crc = zlib.crc32(content) & 0xFFFFFFFF
            uncompressed_size = len(content)
            compressor = zlib.compressobj(zlib.Z_DEFAULT_COMPRESSION, zlib.DEFLATED, -15)
            compressed_data = compressor.compress(content) + compressor.flush()
            compressed_size = len(compressed_data)
            name_bytes = "_skipped_files.txt".encode("utf-8")

            local_header = struct.pack(
                "<4sHHHHHIIIHH",
                b"PK\x03\x04", 20, 0x0808, 8, 0, 0, 0, 0, 0, len(name_bytes), 0,
            ) + name_bytes
            descriptor = struct.pack("<III", crc, compressed_size, uncompressed_size)

            yield local_header
            yield compressed_data
            yield descriptor

            header_data_len = len(local_header) + compressed_size
            central_entries.append((name_bytes, crc, compressed_size, uncompressed_size, offset))
            offset += header_data_len + 12

        # --- Central Directory ---
        central_offset = offset
        for arc_name_bytes, crc, compressed_size, uncompressed_size, local_offset in central_entries:
            entry = struct.pack(
                "<4sHHHHHHIIIHHHHHII",
                b"PK\x01\x02", 20, 20, 0x0808, 8, 0, 0,
                crc, compressed_size, uncompressed_size,
                len(arc_name_bytes), 0, 0, 0, 0, 0, local_offset,
            ) + arc_name_bytes
            yield entry
            offset += len(entry)

        central_size = offset - central_offset

        eocd = struct.pack(
            "<4sHHHHIIH",
            b"PK\x05\x06", 0, 0,
            len(central_entries), len(central_entries),
            central_size, central_offset, 0,
        )
        yield eocd
    finally:
        for path in cleanup_paths:
            cleanup_qa_export_file(path)


async def _iter_bytes_content(content: bytes) -> AsyncIterator[bytes]:
    yield content


async def _iter_qa_export_chunks(path: str) -> AsyncIterator[bytes]:
    with open(path, "rb") as file:
        while True:
            chunk = await asyncio.to_thread(file.read, ZIP_STREAM_CHUNK_SIZE)
            if not chunk:
                break
            yield chunk
