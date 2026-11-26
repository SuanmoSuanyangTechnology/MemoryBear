"""Synchronous Web and third-party knowledge synchronization."""

from __future__ import annotations

import logging
import os
import shutil
import tempfile
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, cast

from sqlalchemy import select

from ..errors import KnowledgeError
from ..models.owned import (
    FILE_ROLE_DERIVED_IMAGE,
    FILE_ROLE_SOURCE,
    Document,
    File,
    Knowledge,
)
from ..parser_configuration import ConfigurationError, KnowledgeConfiguration
from ..parser_configuration.api import require_sync_source
from ..parser_configuration.legacy import document_configuration_from_legacy
from ..rag.integrations.feishu import FeishuAPIClient
from ..rag.integrations.feishu.models import FileInfo
from ..rag.integrations.web import WebCrawler
from ..rag.integrations.yuque import YuqueAPIClient
from ..rag.integrations.yuque.models import YuqueDocInfo
from ..rag.vdb.vector_store import TaskVectorStore
from ..repositories.parser_configuration import (
    ParserConfigurationRepository,
    knowledge_configuration,
)
from ..runtime import ProcessRuntime
from ..tasks.dispatch import TaskDispatcher
from ..tasks.observability import BusinessOutcome, TaskRun
from ..utils.datetime_utils import utcnow_naive
from .knowledge_file_storage import KnowledgeFileStorage, generate_kb_file_key

logger = logging.getLogger(__name__)

DEFAULT_SYNC_PARSER_CONFIG = {
    "layout_recognize": "plain",
    "chunk_token_num": 130,
    "delimiter": "\n",
    "auto_keywords": 0,
    "auto_questions": 0,
    "html4excel": "false",
}


@dataclass(frozen=True)
class _KnowledgeSnapshot:
    id: uuid.UUID
    name: str
    source: str | None
    created_by: uuid.UUID


@dataclass(frozen=True)
class _FileSnapshot:
    id: uuid.UUID
    kb_id: uuid.UUID
    created_by: uuid.UUID
    parent_id: uuid.UUID | None
    file_name: str
    file_ext: str
    file_size: int
    file_url: str | None
    file_key: str | None
    created_at: datetime | None
    file_role: str
    source_document_id: uuid.UUID | None


@dataclass(frozen=True)
class _DocumentSnapshot:
    id: uuid.UUID
    file_id: uuid.UUID
    file_name: str


@dataclass(frozen=True)
class _StaleFileSnapshot:
    file: _FileSnapshot
    document_id: uuid.UUID | None
    derived_files: tuple[_FileSnapshot, ...]


@dataclass
class KnowledgeSyncCounters:
    """Safe aggregate counts for one synchronization task."""

    discovered: int = 0
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    deleted: int = 0
    parse_dispatched: int = 0
    failed: int = 0

    def as_dict(self) -> dict[str, int]:
        return asdict(self)


def _snapshot_file(record: File) -> _FileSnapshot:
    return _FileSnapshot(
        id=record.id,
        kb_id=record.kb_id,
        created_by=record.created_by,
        parent_id=record.parent_id,
        file_name=record.file_name,
        file_ext=record.file_ext,
        file_size=record.file_size,
        file_url=record.file_url,
        file_key=record.file_key,
        created_at=record.created_at,
        file_role=record.file_role,
        source_document_id=record.source_document_id,
    )


def _snapshot_document(record: Document | None) -> _DocumentSnapshot | None:
    if record is None:
        return None
    return _DocumentSnapshot(
        id=record.id,
        file_id=record.file_id,
        file_name=record.file_name,
    )


def _load_knowledge(
    runtime: ProcessRuntime,
    kb_id: uuid.UUID,
) -> _KnowledgeSnapshot | None:
    with runtime.database.sync_session() as session:
        record = session.get(Knowledge, kb_id)
        if record is None:
            return None
        return _KnowledgeSnapshot(
            id=record.id,
            name=record.name,
            source=cast(str | None, record.source),
            created_by=record.created_by,
        )


def _load_configuration(
    runtime: ProcessRuntime, knowledge: _KnowledgeSnapshot,
) -> KnowledgeConfiguration:
    with runtime.database.sync_session() as session:
        record = session.get(Knowledge, knowledge.id)
        if record is None or record.configuration is None:
            raise ConfigurationError("configuration_not_migrated")
        if knowledge.source == "third_party" and record.configuration.third_party_platform not in {
            "yuque", "feishu",
        }:
            raise KnowledgeError.from_code("KB_CONNECTOR_SOURCE_INVALID")
        config = knowledge_configuration(record)
        if config.source_issues():
            logger.error(
                "Synchronization source requires repair: knowledge=%s source=%s fields=%s",
                knowledge.id, knowledge.source,
                ",".join(issue.field for issue in config.source_issues()),
            )
        require_sync_source(config)
        return config


def _list_file_records(runtime: ProcessRuntime, kb_id: uuid.UUID) -> list[_FileSnapshot]:
    with runtime.database.sync_session() as session:
        records = session.scalars(select(File).where(File.kb_id == kb_id)).all()
        return [_snapshot_file(record) for record in records]


def _list_source_files(runtime: ProcessRuntime, kb_id: uuid.UUID) -> list[_FileSnapshot]:
    return [
        record
        for record in _list_file_records(runtime, kb_id)
        if record.file_role == FILE_ROLE_SOURCE
    ]


def _find_source_file(
    runtime: ProcessRuntime,
    kb_id: uuid.UUID,
    file_url: str,
) -> _FileSnapshot | None:
    return next(
        (record for record in _list_source_files(runtime, kb_id) if record.file_url == file_url),
        None,
    )


def _create_file(
    runtime: ProcessRuntime,
    knowledge: _KnowledgeSnapshot,
    *,
    file_name: str,
    file_ext: str,
    file_size: int,
    file_url: str,
    created_at: datetime | None = None,
) -> _FileSnapshot:
    with runtime.database.sync_session() as session:
        values: dict[str, Any] = {
            "id": uuid.uuid4(),
            "kb_id": knowledge.id,
            "created_by": knowledge.created_by,
            "parent_id": knowledge.id,
            "file_name": file_name,
            "file_ext": file_ext,
            "file_size": file_size,
            "file_url": file_url,
            "file_role": FILE_ROLE_SOURCE,
        }
        if created_at is not None:
            values["created_at"] = created_at
        record = File(**values)
        session.add(record)
        session.commit()
        session.refresh(record)
        return _snapshot_file(record)


def _document_for_file(
    records: list[Document],
    kb_id: uuid.UUID,
    file_id: uuid.UUID,
) -> Document | None:
    return next(
        (
            record
            for record in records
            if record.kb_id == kb_id and record.file_id == file_id
        ),
        None,
    )


def _update_file(
    runtime: ProcessRuntime,
    kb_id: uuid.UUID,
    file_id: uuid.UUID,
    *,
    file_name: str,
    file_ext: str,
    file_size: int,
    file_key: str,
    created_at: datetime | None = None,
    sync_document_created_at: bool = False,
) -> tuple[_FileSnapshot | None, _DocumentSnapshot | None]:
    with runtime.database.sync_session() as session:
        record = session.get(File, file_id)
        if record is None:
            logger.warning("Synced file disappeared before update: file=%s", file_id)
            return None, None
        record.file_name = file_name
        record.file_ext = file_ext
        record.file_size = file_size
        record.file_key = file_key
        if created_at is not None:
            record.created_at = created_at

        documents = session.scalars(select(Document).where(Document.kb_id == kb_id)).all()
        document = _document_for_file(documents, kb_id, file_id)
        if document is not None:
            document.file_name = file_name
            document.file_ext = file_ext
            document.file_size = file_size
            if sync_document_created_at:
                document.created_at = record.created_at
            document.updated_at = utcnow_naive()
        session.commit()
        session.refresh(record)
        if document is not None:
            session.refresh(document)
        return _snapshot_file(record), _snapshot_document(document)


def _create_document(
    runtime: ProcessRuntime,
    knowledge: _KnowledgeSnapshot,
    file_record: _FileSnapshot,
    configuration: KnowledgeConfiguration,
) -> _DocumentSnapshot:
    with runtime.database.sync_session() as session:
        record = Document(
            id=uuid.uuid4(),
            kb_id=knowledge.id,
            created_by=knowledge.created_by,
            file_id=file_record.id,
            file_name=file_record.file_name,
            file_ext=file_record.file_ext,
            file_size=file_record.file_size,
            file_meta={},
            parser_id="naive",
        )
        ParserConfigurationRepository(session).write_document(
            knowledge.id,
            record,
            lambda _: document_configuration_from_legacy(DEFAULT_SYNC_PARSER_CONFIG, configuration),
        )
        session.commit()
        session.refresh(record)
        snapshot = _snapshot_document(record)
        assert snapshot is not None
        return snapshot


def _legacy_file_path(
    runtime: ProcessRuntime,
    kb_id: uuid.UUID,
    parent_id: uuid.UUID,
    file_id: uuid.UUID,
    file_ext: str,
) -> Path:
    return Path(
        runtime.settings.file_path,
        str(kb_id),
        str(parent_id),
        f"{file_id}{file_ext}",
    )


def _write_legacy_file(
    runtime: ProcessRuntime,
    file_record: _FileSnapshot,
    content: bytes,
    *,
    file_ext: str | None = None,
) -> Path:
    path = _legacy_file_path(
        runtime,
        file_record.kb_id,
        file_record.kb_id,
        file_record.id,
        file_ext or file_record.file_ext,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    path.write_bytes(content)
    return path


def _copy_legacy_file(
    runtime: ProcessRuntime,
    file_record: _FileSnapshot,
    source_path: str,
    *,
    file_ext: str | None = None,
) -> Path:
    path = _legacy_file_path(
        runtime,
        file_record.kb_id,
        file_record.kb_id,
        file_record.id,
        file_ext or file_record.file_ext,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    shutil.copyfile(source_path, path)
    return path


def _upload_content(
    runtime: ProcessRuntime,
    file_record: _FileSnapshot,
    content: bytes,
    *,
    file_ext: str | None = None,
) -> str:
    file_key = generate_kb_file_key(
        file_record.kb_id,
        file_record.id,
        file_ext or file_record.file_ext,
    )
    storage = KnowledgeFileStorage(runtime.storage)
    runtime.run_async(
        lambda: storage.upload(file_key, content, "application/octet-stream")
    )
    return file_key


def _dispatcher(runtime: ProcessRuntime) -> TaskDispatcher:
    configured = getattr(runtime, "task_dispatcher", None)
    return configured if configured is not None else TaskDispatcher(runtime=runtime)


def _dispatch_parse(
    runtime: ProcessRuntime,
    file_record: _FileSnapshot | None,
    document: _DocumentSnapshot | None,
) -> bool:
    if file_record is not None and document is not None and file_record.file_key:
        _dispatcher(runtime).send_sync(
            "app.core.rag.tasks.parse_document",
            args=[file_record.file_key, str(document.id), file_record.file_name],
        )
        return True
    elif file_record is not None and document is not None:
        logger.warning(
            "Skipping parse because synchronized file key is empty: document=%s",
            document.id,
        )
    elif file_record is not None:
        logger.warning(
            "Skipping parse because synchronized document is missing: file=%s",
            file_record.id,
        )
    return False


def _snapshot_stale_files(
    runtime: ProcessRuntime,
    kb_id: uuid.UUID,
    current_urls: set[str],
) -> tuple[_StaleFileSnapshot, ...]:
    with runtime.database.sync_session() as session:
        files = session.scalars(select(File).where(File.kb_id == kb_id)).all()
        documents = session.scalars(select(Document).where(Document.kb_id == kb_id)).all()
        stale = []
        for record in files:
            if (
                record.file_role != FILE_ROLE_SOURCE
                or (record.file_url is None and current_urls)
                or record.file_url in current_urls
            ):
                continue
            document = _document_for_file(documents, kb_id, record.id)
            derived = tuple(
                _snapshot_file(candidate)
                for candidate in files
                if document is not None
                and candidate.file_role == FILE_ROLE_DERIVED_IMAGE
                and candidate.source_document_id == document.id
            )
            stale.append(
                _StaleFileSnapshot(
                    file=_snapshot_file(record),
                    document_id=document.id if document is not None else None,
                    derived_files=derived,
                )
            )
        return tuple(stale)


def _delete_stale_records(
    runtime: ProcessRuntime,
    stale_files: tuple[_StaleFileSnapshot, ...],
) -> None:
    from ..repositories.wiki.state import withdraw
    from .wiki.execution import publish_pages

    cleanup: dict[str, set[str]] = {}
    with runtime.database.sync_session() as session:
        for stale in stale_files:
            if stale.document_id is not None:
                document = session.get(Document, stale.document_id)
                if document is not None:
                    pages = withdraw(session, document.kb_id, document.id, hard_delete=True)
                    cleanup.setdefault(str(document.kb_id), set()).update(pages)
                    session.delete(document)
            for derived in stale.derived_files:
                record = session.get(File, derived.id)
                if record is not None:
                    session.delete(record)
            record = session.get(File, stale.file.id)
            if record is not None:
                session.delete(record)
        session.commit()
    for kb_id, page_ids in cleanup.items():
        try:
            publish_pages(runtime, kb_id, sorted(page_ids))
        except Exception as exc:
            logger.error("Wiki cleanup dispatch failed kb_id=%s page_ids=%s error_type=%s",
                         kb_id, sorted(page_ids), type(exc).__name__)


def _delete_stale_files(
    runtime: ProcessRuntime,
    kb_id: uuid.UUID,
    current_urls: set[str],
) -> int:
    stale_files = _snapshot_stale_files(runtime, kb_id, current_urls)
    if not stale_files:
        return 0
    storage = KnowledgeFileStorage(runtime.storage)
    vector_store = TaskVectorStore(runtime.elasticsearch.sync_client(), kb_id, None)
    for stale in stale_files:
        if stale.document_id is not None:
            vector_store.delete_by_metadata_field(
                "document_id",
                str(stale.document_id),
            )
        for record in (*stale.derived_files, stale.file):
            if record.file_key:
                try:
                    runtime.run_async(lambda key=record.file_key: storage.delete(key))
                except Exception as exc:
                    logger.warning(
                        "Failed to delete synchronized storage object: "
                        "file=%s error_type=%s",
                        record.id,
                        type(exc).__name__,
                    )
        parent_id = stale.file.parent_id or stale.file.kb_id
        legacy_path = _legacy_file_path(
            runtime,
            stale.file.kb_id,
            parent_id,
            stale.file.id,
            stale.file.file_ext,
        )
        if legacy_path.exists():
            legacy_path.unlink()
    _delete_stale_records(runtime, stale_files)
    return len(stale_files)


def _sync_web(
    runtime: ProcessRuntime,
    knowledge: _KnowledgeSnapshot,
    run: TaskRun,
    counters: KnowledgeSyncCounters,
) -> None:
    config = _load_configuration(runtime, knowledge)
    assert config.web_entry_url is not None
    assert config.web_max_pages is not None
    assert config.web_delay_seconds is not None
    assert config.web_timeout_seconds is not None
    assert config.web_user_agent is not None
    crawler = WebCrawler(
        entry_url=config.web_entry_url,
        max_pages=config.web_max_pages,
        delay_seconds=config.web_delay_seconds,
        timeout_seconds=config.web_timeout_seconds,
        user_agent=config.web_user_agent,
    )
    current_urls: set[str] = set()
    for crawled in crawler.crawl():
        counters.discovered += 1
        current_urls.add(crawled.url)
        if not crawled.content_length:
            counters.unchanged += 1
            continue
        file_record = _find_source_file(runtime, knowledge.id, crawled.url)
        if file_record is not None and file_record.file_size == crawled.content_length:
            counters.unchanged += 1
            continue
        is_new = file_record is None
        if is_new:
            file_record = _create_file(
                runtime,
                knowledge,
                file_name=f"{crawled.title}.txt",
                file_ext=".txt",
                file_size=crawled.content_length,
                file_url=crawled.url,
            )
            counters.created += 1
        else:
            counters.updated += 1
        assert file_record is not None
        content = crawled.content.encode("utf-8")
        _write_legacy_file(runtime, file_record, content, file_ext=".txt")
        file_key = _upload_content(runtime, file_record, content, file_ext=".txt")
        file_record, existing_document = _update_file(
            runtime,
            knowledge.id,
            file_record.id,
            file_name=f"{crawled.title}.txt",
            file_ext=".txt",
            file_size=crawled.content_length,
            file_key=file_key,
        )
        if file_record is None:
            continue
        document = (
            _create_document(runtime, knowledge, file_record, config)
            if is_new
            else existing_document
        )
        if _dispatch_parse(runtime, file_record, document):
            counters.parse_dispatched += 1
        run.progress(
            stage="sync_web",
            fraction=None,
            detail="source_item_processed",
            counts=counters.as_dict(),
        )
    counters.deleted += _delete_stale_files(runtime, knowledge.id, current_urls)


async def _list_yuque_documents(client: YuqueAPIClient) -> list[YuqueDocInfo]:
    async with client as opened:
        documents = []
        for repository in await opened.get_user_repos():
            documents.extend(await opened.get_repo_docs(repository.id))
        return documents


async def _download_yuque_document(
    client: YuqueAPIClient,
    document: YuqueDocInfo,
    save_dir: str,
) -> str:
    async with client as opened:
        return await opened.download_document(document, save_dir)


def _sync_yuque(
    runtime: ProcessRuntime,
    knowledge: _KnowledgeSnapshot,
    run: TaskRun,
    counters: KnowledgeSyncCounters,
    config: KnowledgeConfiguration,
) -> None:
    assert config.yuque_user_id is not None and config.yuque_token is not None
    client = YuqueAPIClient(
        user_id=config.yuque_user_id,
        token=config.yuque_token,
    )
    with run.stage("list_remote_documents"):
        documents = runtime.run_async(lambda: _list_yuque_documents(client))
    counters.discovered += len(documents)
    current_urls: set[str] = set()
    for document in documents:
        current_urls.add(document.slug)
        file_record = _find_source_file(runtime, knowledge.id, document.slug)
        if file_record is not None and file_record.created_at == document.updated_at:
            counters.unchanged += 1
            continue
        save_dir = os.path.join(
            str(runtime.settings.file_path),
            str(knowledge.id),
            str(knowledge.id),
        )
        Path(save_dir).mkdir(parents=True, exist_ok=True)
        with run.stage("download_remote_document"):
            file_path = runtime.run_async(
                lambda doc=document, directory=save_dir: _download_yuque_document(
                    client,
                    doc,
                    directory,
                )
            )
        file_name = os.path.basename(file_path)
        file_ext = os.path.splitext(file_name)[1].lower()
        file_size = os.path.getsize(file_path)
        is_new = file_record is None
        if is_new:
            file_record = _create_file(
                runtime,
                knowledge,
                file_name=file_name,
                file_ext=file_ext,
                file_size=file_size,
                file_url=document.slug,
                created_at=document.updated_at,
            )
            counters.created += 1
        else:
            counters.updated += 1
        assert file_record is not None
        legacy_path = _copy_legacy_file(
            runtime,
            file_record,
            file_path,
            file_ext=file_ext,
        )
        content = legacy_path.read_bytes()
        file_key = _upload_content(runtime, file_record, content, file_ext=file_ext)
        file_record, existing_document = _update_file(
            runtime,
            knowledge.id,
            file_record.id,
            file_name=file_name,
            file_ext=file_ext,
            file_size=file_size,
            file_key=file_key,
            created_at=document.updated_at,
            sync_document_created_at=True,
        )
        if file_record is None:
            continue
        synced_document = (
            _create_document(runtime, knowledge, file_record, config)
            if is_new
            else existing_document
        )
        if _dispatch_parse(runtime, file_record, synced_document):
            counters.parse_dispatched += 1
        run.progress(
            stage="sync_yuque",
            fraction=None,
            detail="source_item_processed",
            counts=counters.as_dict(),
        )
    counters.deleted += _delete_stale_files(runtime, knowledge.id, current_urls)


async def _list_feishu_documents(
    client: FeishuAPIClient,
    folder_token: str,
) -> list[FileInfo]:
    async with client as opened:
        return await opened.list_all_folder_files(folder_token, recursive=True)


async def _download_feishu_document(
    client: FeishuAPIClient,
    document: FileInfo,
    save_dir: str,
) -> str:
    async with client as opened:
        return await opened.download_document(document, save_dir)


def _sync_feishu(
    runtime: ProcessRuntime,
    knowledge: _KnowledgeSnapshot,
    run: TaskRun,
    counters: KnowledgeSyncCounters,
    config: KnowledgeConfiguration,
) -> None:
    assert config.feishu_app_id is not None and config.feishu_app_secret is not None
    folder_token = config.feishu_folder_token
    assert folder_token is not None
    client = FeishuAPIClient(
        app_id=config.feishu_app_id,
        app_secret=config.feishu_app_secret,
    )
    with run.stage("list_remote_documents"):
        files = runtime.run_async(
            lambda: _list_feishu_documents(
                client,
                folder_token,
            )
        )
    documents = [
        record
        for record in files
        if record.type in {"doc", "docx", "sheet", "bitable", "file"}
    ]
    counters.discovered += len(documents)
    current_urls: set[str] = set()
    for document in documents:
        current_urls.add(document.url)
        file_record = _find_source_file(runtime, knowledge.id, document.url)
        if file_record is not None and file_record.created_at == document.modified_time:
            counters.unchanged += 1
            continue
        save_dir = tempfile.mkdtemp()
        with run.stage("download_remote_document"):
            file_path = runtime.run_async(
                lambda doc=document, directory=save_dir: _download_feishu_document(
                    client,
                    doc,
                    directory,
                )
            )
        file_name = os.path.basename(file_path)
        file_ext = os.path.splitext(file_name)[1].lower()
        file_size = os.path.getsize(file_path)
        is_new = file_record is None
        if is_new:
            file_record = _create_file(
                runtime,
                knowledge,
                file_name=file_name,
                file_ext=file_ext,
                file_size=file_size,
                file_url=document.url,
                created_at=document.modified_time,
            )
            counters.created += 1
        else:
            counters.updated += 1
        assert file_record is not None
        content = Path(file_path).read_bytes()
        file_key = _upload_content(runtime, file_record, content, file_ext=file_ext)
        try:
            if os.path.exists(file_path):
                os.remove(file_path)
        except Exception:
            pass
        file_record, existing_document = _update_file(
            runtime,
            knowledge.id,
            file_record.id,
            file_name=file_name,
            file_ext=file_ext,
            file_size=file_size,
            file_key=file_key,
            created_at=document.modified_time,
            sync_document_created_at=True,
        )
        if file_record is None:
            continue
        synced_document = (
            _create_document(runtime, knowledge, file_record, config)
            if is_new
            else existing_document
        )
        if _dispatch_parse(runtime, file_record, synced_document):
            counters.parse_dispatched += 1
        run.progress(
            stage="sync_feishu",
            fraction=None,
            detail="source_item_processed",
            counts=counters.as_dict(),
        )
    counters.deleted += _delete_stale_files(runtime, knowledge.id, current_urls)


def _sync_third_party(
    runtime: ProcessRuntime,
    knowledge: _KnowledgeSnapshot,
    run: TaskRun,
    counters: KnowledgeSyncCounters,
) -> int:
    try:
        config = _load_configuration(runtime, knowledge)
        if config.third_party_platform == "yuque":
            _sync_yuque(runtime, knowledge, run, counters, config)
        elif config.third_party_platform == "feishu":
            _sync_feishu(runtime, knowledge, run, counters, config)
    except Exception as exc:
        logger.error(
            "Third-party knowledge synchronization failed: knowledge=%s error_type=%s "
            "error_code=%s",
            knowledge.id,
            type(exc).__name__,
            exc.code if isinstance(exc, KnowledgeError) else "KB_SYNC_PROCESSING_FAILED",
        )
        return 1
    return 0


def process_knowledge_sync(
    runtime: ProcessRuntime,
    kb_id: uuid.UUID | str,
    *,
    run: TaskRun,
) -> str:
    """Synchronize one knowledge base while keeping slow work outside DB sessions."""

    knowledge: _KnowledgeSnapshot | None = None
    counters = KnowledgeSyncCounters()
    try:
        knowledge_id = kb_id if isinstance(kb_id, uuid.UUID) else uuid.UUID(str(kb_id))
        with run.stage("load_knowledge"):
            knowledge = _load_knowledge(runtime, knowledge_id)
        if knowledge is None:
            logger.error("Knowledge synchronization target not found: knowledge=%s", knowledge_id)
            counters.failed += 1
            run.finish(
                BusinessOutcome.FAILURE,
                error_code="KB_SYNC_KNOWLEDGE_NOT_FOUND",
                counts=counters.as_dict(),
            )
            return "sync knowledge failed: knowledge not found"
        match knowledge.source:
            case "website":
                try:
                    with run.stage("sync_web"):
                        _sync_web(runtime, knowledge, run, counters)
                except Exception as exc:
                    counters.failed += 1
                    logger.error(
                        "Web knowledge synchronization failed: error_type=%s",
                        type(exc).__name__,
                    )
            case "third_party":
                with run.stage("sync_third_party"):
                    counters.failed += _sync_third_party(runtime, knowledge, run, counters)
            case _:
                logger.info(
                    "Knowledge source requires no synchronization: knowledge=%s source=%s",
                    knowledge_id,
                    knowledge.source,
                )
                run.finish(
                    BusinessOutcome.SKIPPED,
                    detail="source_type_no_sync",
                    counts=counters.as_dict(),
                )
                return f"sync knowledge '{knowledge.name}' processed successfully."
        run.progress(
            stage="complete",
            fraction=None,
            detail="sync_summary",
            counts=counters.as_dict(),
            force=True,
        )
        if counters.failed:
            run.finish(
                BusinessOutcome.PARTIAL_FAILURE,
                error_code="KB_SYNC_SOURCE_PARTIAL_FAILURE",
                counts=counters.as_dict(),
            )
        else:
            run.finish(BusinessOutcome.SUCCESS, counts=counters.as_dict())
        return f"sync knowledge '{knowledge.name}' processed successfully."
    except Exception as exc:
        logger.error(
            "Knowledge synchronization failed: knowledge=%s error_type=%s",
            kb_id,
            type(exc).__name__,
        )
        name = knowledge.name if knowledge is not None else kb_id
        counters.failed += 1
        run.finish(
            BusinessOutcome.FAILURE,
            error_code="KB_SYNC_PROCESSING_FAILED",
            exc=exc,
            counts=counters.as_dict(),
        )
        return f"sync knowledge '{name}' failed: {exc}"


__all__ = ["process_knowledge_sync"]
