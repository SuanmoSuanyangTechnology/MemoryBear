"""Synchronous document parsing orchestration for the document worker."""

# ruff: noqa: E501

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from io import BytesIO
from typing import Any

from jinja2 import Environment
from pypdf import PdfReader

from ..models.owned import Document, Knowledge
from ..models.references import Workspace
from ..rag.chunk.context import ChunkOutputMode, build_chunk_context
from ..rag.chunk.hierarchy import GroupedChildChunks, validate_parent_child_result
from ..rag.chunk.llm_cache import get_llm_cache, set_llm_cache
from ..rag.chunk.metadata import merge_parser_metadata
from ..rag.chunk.router import FileTypeRouter
from ..rag.chunk.token_utils import num_tokens_from_string, truncate
from ..rag.knowledge_graph import GraphPipeline, is_graph_enabled, resolve_graph_pipeline
from ..rag.models.chunk import DocumentChunk
from ..rag.models.media import QWenCV as MediaQWenCV
from ..rag.models.media import QWenSeq2txt
from ..rag.models.task_runtime import TaskModelFactory
from ..rag.models.vision import QWenCV as ImageQWenCV
from ..rag.vdb.vector_store import TaskVectorStore
from ..runtime import ProcessRuntime
from ..tasks.dispatch import TaskDispatcher
from ..tasks.state import PARSE_CANCEL_KEY, PARSE_TASK_KEY
from ..utils.datetime_utils import to_iso_z, to_timestamp_ms, utcnow, utcnow_naive
from .knowledge_file_storage import KnowledgeFileStorage

logger = logging.getLogger(__name__)

DEFAULT_PARSE_TO_PAGE = 100_000
NON_VECTORIZED_CHUNK_TYPES = {"source", "parent"}
_AUDIO_PATTERN = re.compile(
    r"\.(da|wave|wav|mp3|aac|flac|ogg|aiff|au|midi|wma|"
    r"realaudio|vqf|oggvorbis|ape?)$",
    re.IGNORECASE,
)
_VIDEO_PATTERN = re.compile(
    r"\.(mp4|mov|avi|flv|mpeg|mpg|webm|wmv|3gp|3gpp|mkv?)$",
    re.IGNORECASE,
)
_THINK_PREFIX = re.compile(r"^.*</think>", re.DOTALL)
_QA_LINE = re.compile(r"^Q:\s*(.+?)\s+A:\s*(.+)$", re.IGNORECASE)
_QA_TEMPLATE = """## Role
You are a text analyzer and knowledge extraction expert.

## Task
Generate question-answer pairs from the given text content.

## Requirements
- Understand and summarize the text content, then generate up to {{ topn }} important question-answer pairs.
- Each question-answer pair MUST be on a single line, formatted as: Q: <question> A: <answer>
- The questions SHOULD NOT have overlapping meanings.
- The questions SHOULD cover the main content of the text as much as possible.
- The answers MUST be concise, accurate, and directly derived from the text content.
- The answers SHOULD be self-contained and understandable without additional context.
- Both questions and answers MUST be in the same language as the given text content.
- If the text is too short or lacks substantive content, generate fewer pairs rather than padding.
- Output question-answer pairs ONLY, no extra explanation or commentary.

## Example Output
Q: What is the capital of France? A: The capital of France is Paris.
Q: When was the Eiffel Tower built? A: The Eiffel Tower was built in 1889.
"""
_QA_ENV = Environment(autoescape=False, trim_blocks=True, lstrip_blocks=True)


@dataclass(frozen=True)
class ParseDocumentSnapshot:
    document_id: uuid.UUID
    knowledge_id: uuid.UUID
    workspace_id: uuid.UUID
    tenant_id: uuid.UUID
    file_id: uuid.UUID
    file_name: str
    source_file_name: str
    file_created_at_ms: int | None
    parser_config: dict[str, Any]
    parent_child_mode: bool
    embedding_id: uuid.UUID | None
    llm_id: uuid.UUID | None
    image2text_id: uuid.UUID | None


class _ParseAborted(RuntimeError):
    pass


def _progress_ts() -> str:
    return str(to_iso_z(utcnow()))


def _progress_message(lines: list[str]) -> str:
    return "\n".join(lines) + "\n"


def _mark_running(
    runtime: ProcessRuntime,
    document_id: uuid.UUID,
    file_name: str,
    progress_lines: list[str],
) -> ParseDocumentSnapshot:
    with runtime.database.sync_session() as session:
        document = session.get(Document, document_id)
        if document is None:
            raise ValueError(f"Document {document_id} not found")
        knowledge = session.get(Knowledge, document.kb_id)
        if knowledge is None:
            raise ValueError(f"Knowledge {document.kb_id} not found")
        workspace = session.get(Workspace, knowledge.workspace_id)
        if workspace is None:
            raise ValueError(f"Workspace {knowledge.workspace_id} not found")

        effective_file_name = file_name or document.file_name
        progress_lines.append(f"{_progress_ts()} Start to parse.")
        document.progress = 0.0
        document.progress_msg = _progress_message(progress_lines)
        document.process_begin_at = utcnow_naive()
        document.process_duration = 0.0
        document.run = 1
        session.commit()
        return ParseDocumentSnapshot(
            document_id=document.id,
            knowledge_id=knowledge.id,
            workspace_id=knowledge.workspace_id,
            tenant_id=workspace.tenant_id,
            file_id=document.file_id,
            file_name=effective_file_name,
            source_file_name=document.file_name,
            file_created_at_ms=to_timestamp_ms(document.created_at),
            parser_config=dict(document.parser_config or {}),
            parent_child_mode=bool(document.is_parent_child_mode),
            embedding_id=knowledge.embedding_id,
            llm_id=knowledge.llm_id,
            image2text_id=knowledge.image2text_id,
        )


def _update_document(
    runtime: ProcessRuntime,
    document_id: uuid.UUID,
    updater,
) -> bool:
    with runtime.database.sync_session() as session:
        document = session.get(Document, document_id)
        if document is None:
            logger.warning(
                "Document missing while updating parse state: document=%s",
                document_id,
            )
            return False
        updater(document)
        session.commit()
        return True


def _clear_parse_state(runtime: ProcessRuntime, document_id: object) -> None:
    try:
        redis = runtime.redis.sync_client()
        redis.delete(PARSE_TASK_KEY.format(doc_id=document_id))
        redis.delete(PARSE_CANCEL_KEY.format(doc_id=document_id))
    except Exception as exc:  # noqa: BLE001 - cleanup must not replace task results.
        logger.warning(
            "Failed to clear parse state: document=%s error_type=%s",
            document_id,
            type(exc).__name__,
        )


def _document_exists(runtime: ProcessRuntime, document_id: uuid.UUID) -> bool:
    with runtime.database.sync_session() as session:
        return session.get(Document, document_id) is not None


def _should_abort(runtime: ProcessRuntime, document_id: uuid.UUID) -> bool:
    try:
        redis = runtime.redis.sync_client()
    except Exception as exc:  # noqa: BLE001 - DB presence is the legacy fallback.
        logger.warning(
            "Parse cancellation client unavailable: document=%s error_type=%s",
            document_id,
            type(exc).__name__,
        )
        return not _document_exists(runtime, document_id)
    try:
        if redis.get(PARSE_CANCEL_KEY.format(doc_id=document_id)) is not None:
            logger.info("Document parsing cancelled: document=%s", document_id)
            return True
    except Exception as exc:  # noqa: BLE001 - RedisDB.get was best effort.
        logger.warning(
            "Parse cancellation state unavailable: document=%s error_type=%s",
            document_id,
            type(exc).__name__,
        )
    return False


def _download_file(runtime: ProcessRuntime, file_key: str) -> bytes:
    storage = KnowledgeFileStorage(runtime.storage)
    return runtime.run_async(lambda: storage.download(file_key))


def _estimate_pages(file_name: str, file_binary: bytes) -> int | None:
    if not file_name.lower().endswith(".pdf"):
        return None
    try:
        return len(PdfReader(BytesIO(file_binary)).pages)
    except Exception:  # noqa: BLE001 - page estimation must never block parsing.
        return None


def _build_vision_model(runtime: ProcessRuntime, snapshot: ParseDocumentSnapshot):
    if snapshot.image2text_id is None:
        raise RuntimeError("image2text model config is unavailable")
    config = TaskModelFactory(runtime).resolve_image(
        snapshot.image2text_id,
        snapshot.tenant_id,
    )
    image_model = ImageQWenCV(
        config,
        client_pool=runtime.model_runtime.pool,
        lang="Chinese",
    )
    if _AUDIO_PATTERN.search(snapshot.file_name):
        return QWenSeq2txt(lang="Chinese")
    if _VIDEO_PATTERN.search(snapshot.file_name):
        return MediaQWenCV(lang="Chinese")
    return image_model


def _parse_chunks(
    runtime: ProcessRuntime,
    snapshot: ParseDocumentSnapshot,
    file_binary: bytes,
    progress_callback,
    vision_model,
):
    output_mode = (
        ChunkOutputMode.PARENT_CHILD if snapshot.parent_child_mode else ChunkOutputMode.NORMAL
    )
    context = build_chunk_context(
        filename=snapshot.file_name,
        binary=file_binary,
        from_page=0,
        to_page=DEFAULT_PARSE_TO_PAGE,
        callback=progress_callback,
        vision_model=vision_model,
        parser_config=snapshot.parser_config,
        is_root=False,
        chunk_output_mode=output_mode,
        tenant_id=str(snapshot.tenant_id),
        workspace_id=str(snapshot.workspace_id),
        knowledge_id=str(snapshot.knowledge_id),
        document_id=str(snapshot.document_id),
        source_file_id=str(snapshot.file_id),
        source_file_name=snapshot.source_file_name,
        runtime=runtime,
    )
    return FileTypeRouter().route(snapshot.file_name).run(context)


def _base_metadata(snapshot: ParseDocumentSnapshot, sort_id: int) -> dict[str, Any]:
    return {
        "file_id": str(snapshot.file_id),
        "file_name": snapshot.source_file_name,
        "file_created_at": snapshot.file_created_at_ms,
        "document_id": str(snapshot.document_id),
        "knowledge_id": str(snapshot.knowledge_id),
        "sort_id": sort_id,
        "status": 1,
    }


def _chunk_requires_vector(chunk: DocumentChunk) -> bool:
    return (chunk.metadata or {}).get("chunk_type", "chunk") not in (NON_VECTORIZED_CHUNK_TYPES)


def _prioritize_vectorized_chunks(chunks: list[DocumentChunk]) -> list[DocumentChunk]:
    vectorized = [chunk for chunk in chunks if _chunk_requires_vector(chunk)]
    non_vectorized = [chunk for chunk in chunks if not _chunk_requires_vector(chunk)]
    return vectorized + non_vectorized


def _pop_vectorized_bootstrap_batch(
    batches: list[list[DocumentChunk]],
) -> tuple[int | None, list[DocumentChunk] | None]:
    for index, batch in enumerate(batches):
        if any(_chunk_requires_vector(chunk) for chunk in batch):
            return index, batches.pop(index)
    return None, None


def _parent_child_chunks(
    snapshot: ParseDocumentSnapshot,
    child_result: list[dict],
    parent_result: list[dict],
    parent_id_map: dict[int, int],
) -> list[DocumentChunk]:
    parent_chunks: list[DocumentChunk] = []
    parent_doc_ids: dict[int, str] = {}
    for index, item in enumerate(parent_result):
        parent_doc_id = uuid.uuid4().hex
        parent_doc_ids[index] = parent_doc_id
        metadata = {
            **_base_metadata(snapshot, index),
            "doc_id": parent_doc_id,
            "chunk_type": "parent",
        }
        parent_chunks.append(
            DocumentChunk(
                page_content=item["content_with_weight"],
                metadata=merge_parser_metadata(metadata, item),
            )
        )

    child_chunks: list[DocumentChunk] = []
    for index, item in enumerate(child_result):
        metadata = {
            **_base_metadata(snapshot, index),
            "doc_id": uuid.uuid4().hex,
            "chunk_type": "child",
            "parent_id": parent_doc_ids.get(parent_id_map.get(index), ""),
        }
        child_chunks.append(
            DocumentChunk(
                page_content=item["content_with_weight"],
                metadata=merge_parser_metadata(metadata, item),
            )
        )
    return _prioritize_vectorized_chunks(parent_chunks + child_chunks)


def _model_name(model: Any) -> str:
    direct = getattr(model, "model_name", None)
    if direct:
        return str(direct)
    return str(getattr(getattr(model, "_config", None), "model_name", "unknown"))


def _fit_qa_messages(system_prompt: str, content: str, max_length: int) -> tuple[str, str]:
    system_count = num_tokens_from_string(system_prompt)
    content_count = num_tokens_from_string(content)
    if system_count + content_count < max_length:
        return system_prompt, content
    if system_count / max(system_count + content_count, 1) > 0.8:
        return truncate(system_prompt, max_length - content_count), content
    return system_prompt, truncate(content, max_length - content_count)


def _response_text(response: Any) -> str:
    if isinstance(response, tuple):
        response = response[0]
    content = getattr(response, "content", response)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            str(block.get("text", ""))
            for block in content
            if isinstance(block, dict) and block.get("text")
        )
    return str(content or "")


def _parse_qa_pairs(text: str) -> list[dict[str, str]]:
    pairs: list[dict[str, str]] = []
    for line in text.strip().splitlines():
        match = _QA_LINE.match(line.strip())
        if not match:
            continue
        question, answer = match.group(1).strip(), match.group(2).strip()
        if question and answer:
            pairs.append({"question": question, "answer": answer})
    return pairs


def _propose_qa(
    model: Any,
    content: str,
    topn: int,
    custom_prompt: str | None,
) -> list[dict[str, str]]:
    system_prompt = (
        _QA_ENV.from_string(custom_prompt).render(topn=topn) if custom_prompt else _QA_TEMPLATE
    )
    system_prompt, fitted_content = _fit_qa_messages(
        system_prompt,
        content,
        int(getattr(model, "max_length", 8096)),
    )
    if hasattr(model, "chat"):
        response = model.chat(
            system_prompt,
            [{"role": "user", "content": fitted_content}],
            {"temperature": 0.2},
        )
    else:
        response = model.invoke(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": fitted_content},
            ],
            temperature=0.2,
        )
    text = _THINK_PREFIX.sub("", _response_text(response))
    if "**ERROR**" in text:
        return []
    return _parse_qa_pairs(text)


def _generate_qa_map(
    runtime: ProcessRuntime,
    model: Any,
    items: list[dict],
    topn: int,
    custom_prompt: str | None,
) -> dict[int, list[dict[str, str]]]:
    redis = runtime.redis.sync_client()
    model_name = _model_name(model)

    def generate(index_and_item: tuple[int, dict]) -> tuple[int, list[dict[str, str]]]:
        index, item = index_and_item
        content = item["content_with_weight"]
        cache_params: dict[str, Any] = {"topn": topn}
        if custom_prompt:
            cache_params["prompt_hash"] = hashlib.md5(custom_prompt.encode()).hexdigest()[:8]
        cached = get_llm_cache(redis, model_name, content, "qa", cache_params)
        if cached:
            if isinstance(cached, bytes):
                cached = cached.decode("utf-8")
            if isinstance(cached, str):
                try:
                    parsed = json.loads(cached)
                except (json.JSONDecodeError, TypeError):
                    parsed = None
                if isinstance(parsed, list):
                    return index, parsed
                return index, _parse_qa_pairs(cached)
            return index, cached if isinstance(cached, list) else []
        try:
            pairs = _propose_qa(model, content, topn, custom_prompt)
        except Exception as exc:  # noqa: BLE001 - one QA chunk is best effort.
            logger.error(
                "Automatic QA generation failed: chunk=%s error_type=%s",
                index,
                type(exc).__name__,
            )
            return index, []
        set_llm_cache(
            redis,
            model_name,
            content,
            json.dumps(pairs, ensure_ascii=False),
            "qa",
            cache_params,
        )
        return index, pairs

    qa_map: dict[int, list[dict[str, str]]] = {}
    indexed_items = list(enumerate(items))
    with ThreadPoolExecutor(max_workers=runtime.settings.auto_questions_max_workers) as executor:
        futures = [executor.submit(generate, item) for item in indexed_items]
        for future in futures:
            index, pairs = future.result()
            qa_map[index] = pairs
    return qa_map


def _auto_qa_chunks(
    runtime: ProcessRuntime,
    snapshot: ParseDocumentSnapshot,
    items: list[dict],
    topn: int,
    custom_prompt: str | None,
) -> list[DocumentChunk]:
    if snapshot.llm_id is None:
        raise RuntimeError("auto_questions is enabled but LLM config is unavailable")
    model = TaskModelFactory(runtime).create_llm(snapshot.llm_id, snapshot.tenant_id)
    qa_map = _generate_qa_map(runtime, model, items, topn, custom_prompt)
    source_chunks: list[DocumentChunk] = []
    qa_chunks: list[DocumentChunk] = []
    qa_sort_id = 0
    for index, item in enumerate(items):
        source_chunk_id = uuid.uuid4().hex
        source_metadata = {
            **_base_metadata(snapshot, index),
            "doc_id": source_chunk_id,
            "chunk_type": "source",
        }
        source_chunks.append(
            DocumentChunk(
                page_content=item["content_with_weight"],
                metadata=merge_parser_metadata(source_metadata, item),
            )
        )
        for pair in qa_map.get(index, []):
            qa_metadata = {
                **_base_metadata(snapshot, qa_sort_id),
                "doc_id": uuid.uuid4().hex,
                "chunk_type": "qa",
                "question": pair["question"],
                "answer": pair["answer"],
                "source_chunk_id": source_chunk_id,
            }
            qa_chunks.append(DocumentChunk(page_content=pair["question"], metadata=qa_metadata))
            qa_sort_id += 1
    return _prioritize_vectorized_chunks(source_chunks + qa_chunks)


def _normal_chunks(
    snapshot: ParseDocumentSnapshot,
    items: list[dict],
) -> list[DocumentChunk]:
    chunks = []
    for index, item in enumerate(items):
        metadata = {
            **_base_metadata(snapshot, index),
            "doc_id": uuid.uuid4().hex,
        }
        chunks.append(
            DocumentChunk(
                page_content=item["content_with_weight"],
                metadata=merge_parser_metadata(metadata, item),
            )
        )
    return chunks


def _write_chunks_with_retry(
    runtime: ProcessRuntime,
    vector_store: TaskVectorStore,
    batches: list[list[DocumentChunk]],
) -> None:
    batch_errors: dict[int, Exception] = {}
    total_batches = len(batches)

    def write(batch_index: int, chunks: list[DocumentChunk]) -> None:
        try:
            vector_store.add_chunks(chunks)
        except Exception as exc:  # noqa: BLE001 - exactly one retry is required.
            logger.warning(
                "Document vector batch failed; retrying: batch=%s error_type=%s",
                batch_index,
                type(exc).__name__,
            )
            try:
                vector_store.add_chunks(chunks)
            except Exception as retry_exc:  # noqa: BLE001 - aggregate batch failures.
                logger.error(
                    "Document vector batch retry failed: batch=%s error_type=%s",
                    batch_index,
                    type(retry_exc).__name__,
                )
                batch_errors[batch_index] = retry_exc

    bootstrap_index, bootstrap_batch = _pop_vectorized_bootstrap_batch(batches)
    if bootstrap_batch is not None and bootstrap_index is not None:
        write(bootstrap_index, bootstrap_batch)
        if bootstrap_index in batch_errors:
            error = batch_errors[bootstrap_index]
            raise RuntimeError(
                "Embedding failed for "
                f"{len(batch_errors)}/{total_batches} batch(es). "
                f"batch {bootstrap_index}: {type(error).__name__}: {error}"
            )

    with ThreadPoolExecutor(max_workers=runtime.settings.embedding_max_workers) as executor:
        futures = [executor.submit(write, index, chunks) for index, chunks in enumerate(batches)]
        for future in futures:
            future.result()
    if batch_errors:
        details = "; ".join(
            f"batch {index}: {type(error).__name__}: {error}"
            for index, error in sorted(batch_errors.items())
        )
        raise RuntimeError(
            f"Embedding failed for {len(batch_errors)}/{total_batches} batch(es). {details}"
        )


def _dispatcher(runtime: ProcessRuntime) -> TaskDispatcher:
    configured = getattr(runtime, "task_dispatcher", None)
    return configured if configured is not None else TaskDispatcher()


def _current_graph_config(
    runtime: ProcessRuntime,
    knowledge_id: uuid.UUID,
) -> dict[str, Any] | None:
    with runtime.database.sync_session() as session:
        knowledge = session.get(Knowledge, knowledge_id)
        return dict(knowledge.parser_config or {}) if knowledge is not None else None


def _dispatch_graph(
    runtime: ProcessRuntime,
    snapshot: ParseDocumentSnapshot,
    progress_lines: list[str],
) -> None:
    graph_config = _current_graph_config(runtime, snapshot.knowledge_id)
    if graph_config is None or not is_graph_enabled(graph_config):
        return
    pipeline = resolve_graph_pipeline(graph_config)
    if pipeline is GraphPipeline.LEGACY:
        logger.warning(
            "Legacy graph document sync removed; skipping: knowledge=%s document=%s",
            snapshot.knowledge_id,
            snapshot.document_id,
        )
        return
    if _should_abort(runtime, snapshot.document_id):
        raise _ParseAborted
    progress_lines.append(f"{_progress_ts()} Knowledge graph enabled, dispatching async task.")
    _update_document(
        runtime,
        snapshot.document_id,
        lambda document: setattr(
            document,
            "progress_msg",
            _progress_message(progress_lines),
        ),
    )
    _dispatcher(runtime).send_sync(
        "app.core.rag.tasks.sync_evidence_graph_document",
        args=[str(snapshot.knowledge_id), str(snapshot.document_id)],
        queue="graphrag_tasks",
    )


def process_document(
    runtime: ProcessRuntime,
    file_key: str,
    document_id: str | uuid.UUID,
    file_name: str = "",
) -> str:
    """Parse, vectorize, and persist one document with legacy task semantics."""

    progress_lines = [f"{_progress_ts()} Task has been received."]
    started_at = time.time()
    document_label = file_name or str(document_id)
    normalized_document_id: uuid.UUID | None = None
    try:
        normalized_document_id = uuid.UUID(str(document_id))
        snapshot = _mark_running(
            runtime,
            normalized_document_id,
            file_name,
            progress_lines,
        )
        document_label = snapshot.file_name or str(normalized_document_id)
        if _should_abort(runtime, normalized_document_id):
            raise _ParseAborted

        file_binary = _download_file(runtime, file_key)
        if not file_binary:
            raise OSError(f"Downloaded empty file from storage: {file_key}")
        estimated_pages = _estimate_pages(snapshot.file_name, file_binary)
        if estimated_pages is None:
            progress_lines.append(
                f"{_progress_ts()} parse document '{document_label}' page number unavailable."
            )
        elif estimated_pages > runtime.settings.max_document_pages:
            progress_lines.append(
                f"{_progress_ts()} parse document '{document_label}' failed: page limit exceeded"
            )

            def mark_page_limit_failed(document: Document) -> None:
                document.progress = -1.0
                document.run = 0
                document.progress_msg = _progress_message(progress_lines)

            _update_document(runtime, normalized_document_id, mark_page_limit_failed)
            return f"parse document '{document_label}' failed: page limit exceeded"

        def progress_callback(prog=None, msg=None):
            progress_lines.append(f"{_progress_ts()} parse progress: {prog} msg: {msg}.")

        if _should_abort(runtime, normalized_document_id):
            raise _ParseAborted
        parsed = _parse_chunks(
            runtime,
            snapshot,
            file_binary,
            progress_callback,
            _build_vision_model(runtime, snapshot),
        )
        if snapshot.parent_child_mode:
            child_result, parent_result, parent_id_map = parsed
            if isinstance(child_result, GroupedChildChunks):
                validate_parent_child_result(
                    child_result,
                    parent_result,
                    parent_id_map,
                    str(snapshot.parser_config.get("parent_chunk_mode") or "paragraph"),
                )
        else:
            child_result = parsed
            parent_result = []
            parent_id_map = {}
        progress_lines.append(f"{_progress_ts()} Finish parsing.")

        def mark_parsed(document: Document) -> None:
            document.progress = 0.8
            document.progress_msg = _progress_message(progress_lines)

        _update_document(runtime, normalized_document_id, mark_parsed)
        if _should_abort(runtime, normalized_document_id):
            raise _ParseAborted

        total_chunks = len(child_result)
        progress_lines.append(f"{_progress_ts()} Generate {total_chunks} chunks.")
        if total_chunks == 0:
            logger.warning(
                "Document parser returned no chunks; vectorization skipped: document=%s",
                normalized_document_id,
            )
            progress_lines.append(f"{_progress_ts()} No chunks generated, skipping vectorization.")
        else:
            factory = TaskModelFactory(runtime)
            if snapshot.embedding_id is None:
                raise RuntimeError("embedding model is unavailable")
            embeddings = factory.create_embeddings(
                snapshot.embedding_id,
                snapshot.tenant_id,
            )
            vector_store = TaskVectorStore(
                runtime.elasticsearch.sync_client(),
                snapshot.knowledge_id,
                embeddings,
            )
            vector_store.delete_by_metadata_field(
                "document_id",
                str(normalized_document_id),
            )
            auto_questions_topn = int(snapshot.parser_config.get("auto_questions", 0) or 0)
            if snapshot.parent_child_mode:
                all_chunks = _parent_child_chunks(
                    snapshot,
                    child_result,
                    parent_result,
                    parent_id_map,
                )
                progress_lines.append(
                    f"{_progress_ts()} Parent-child mode: {len(parent_result)} parent "
                    f"chunks + {len(child_result)} child chunks prepared."
                )
            elif auto_questions_topn:
                all_chunks = _auto_qa_chunks(
                    runtime,
                    snapshot,
                    child_result,
                    auto_questions_topn,
                    snapshot.parser_config.get("qa_prompt"),
                )
                qa_count = sum(chunk.metadata.get("chunk_type") == "qa" for chunk in all_chunks)
                progress_lines.append(
                    f"{_progress_ts()} QA pairs generated for {total_chunks} chunks "
                    f"(workers={runtime.settings.auto_questions_max_workers})."
                )
                progress_lines.append(
                    f"{_progress_ts()} QA mode: {total_chunks} source chunks + "
                    f"{qa_count} QA chunks prepared."
                )
            else:
                all_chunks = _normal_chunks(snapshot, child_result)
            batch_size = runtime.settings.embedding_batch_size
            batches = [
                all_chunks[start : start + batch_size]
                for start in range(0, len(all_chunks), batch_size)
            ]
            total_batches = len(batches)
            _write_chunks_with_retry(runtime, vector_store, batches)
            progress_lines.append(
                f"{_progress_ts()} All {total_batches} batches embedded "
                f"(workers={runtime.settings.embedding_max_workers})."
            )

            def mark_vectorized(document: Document) -> None:
                document.progress = 1.0
                document.progress_msg = _progress_message(progress_lines)
                document.process_duration = time.time() - started_at
                document.run = 0

            _update_document(runtime, normalized_document_id, mark_vectorized)

        progress_lines.append(f"{_progress_ts()} Indexing done.")
        process_duration = time.time() - started_at
        progress_lines.append(f"{_progress_ts()} Task done ({process_duration}s).")

        def mark_done(document: Document) -> None:
            document.chunk_num = total_chunks
            document.progress = 1.0
            document.process_duration = process_duration
            document.progress_msg = _progress_message(progress_lines)
            document.run = 0

        _update_document(runtime, normalized_document_id, mark_done)
        _dispatch_graph(runtime, snapshot, progress_lines)
        logger.info(
            "Document parsing completed: document=%s duration=%.1f chunks=%s",
            normalized_document_id,
            process_duration,
            total_chunks,
        )
        return f"parse document '{snapshot.source_file_name}' processed successfully."
    except _ParseAborted:
        logger.info("Document parsing aborted: document=%s", normalized_document_id)
        return f"parse document '{document_label}' aborted (deleted or cancelled)."
    except Exception as exc:  # noqa: BLE001 - task returns a legacy failure string.
        logger.error(
            "Document parsing failed: document=%s error_type=%s",
            normalized_document_id or document_id,
            type(exc).__name__,
        )
        progress_lines.append(
            f"{_progress_ts()} Failed to vectorize and import the parsed document:{exc}"
        )
        if normalized_document_id is not None:
            try:

                def mark_failed(document: Document) -> None:
                    document.progress = -1.0
                    document.progress_msg = _progress_message(progress_lines)
                    document.run = 0

                _update_document(runtime, normalized_document_id, mark_failed)
            except Exception as state_exc:  # noqa: BLE001 - preserve task result.
                logger.warning(
                    "Failed to persist parse failure state: document=%s error_type=%s",
                    normalized_document_id,
                    type(state_exc).__name__,
                )
        return f"parse document '{document_label}' failed."
    finally:
        _clear_parse_state(runtime, normalized_document_id or document_id)


__all__ = ["ParseDocumentSnapshot", "process_document"]
