"""G4b 探针：mem-knowledge 接缝端到端（真实模型服务 + 真实模型）。

覆盖（计划 F21 八项）：
1. 文本入库 + 检索：TaskVectorStore 文本栈写入读回；AsyncChunkStore 文本栈写 ES +
   search_by_vector 向量检索
2. 多模态 LB：qwen3-vl-embedding 结构化单向量融合（text-only / text+image）+
   qwen3-vl-rerank 原生多模态 rerank（views）
3. parse_document 全链等价：vision 图→文（preview_with_vision）+ 结构化 embedding
   写 ES（build_chunk_store 多模态 unit 布局）
4. qa_import：qa chunk 文本 / 结构化 embedding 写入与读回
5. asr 音频：asr 壳直调（轮询收敛在服务侧）
6. graph pipeline：call_structured（json_schema 主路径 + 文本回退）+ aembed_documents
7. usage 双落点：Redis 事件 + model_usage_records（config_id / source_service=
   mem-knowledge / resource_type=kb / resource_id 归因）
8. 凭据泄漏扫描：进程输出 / 日志 / 结构断言无密钥材料

用法（在仓库根执行；两行分别为独立命令）：
    core/mem-knowledge/.venv/bin/python core/mem-knowledge/scripts/probe_g4b_km_invoke_e2e.py --list
    core/mem-knowledge/.venv/bin/python core/mem-knowledge/scripts/probe_g4b_km_invoke_e2e.py

参数：
    --tenant NAME|UUID   调用方租户（默认 redbearai.com；名称经 tenants 表解析）
    --base-url URL       模型服务地址（覆盖 MODEL_SERVICE_BASE_URL）
    --asr-url URL        asr 腿音频公网 URL（默认 DashScope 官方样例）
    --skip a,b           跳过组：embed_text, es, mm, rerank, qa, vision, graph, asr, negative
    --list               只盘点候选与窗口计划（不调用、不写 ES）
    --keep-index         保留 scratch ES 索引（默认前置/收尾清理）

退出码：0=全过；1=存在 FAIL；2=前置/环境问题（服务未起、缺族候选、usage 消费停摆等）。
只读约定：数据库连接 default_transaction_read_only=on；仅写 4 个专用 scratch ES 索引。
"""
# ruff: noqa: E402

from __future__ import annotations

import asyncio
import base64
import builtins
import json
import logging
import os
import struct
import sys
import time
import uuid
import zlib
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx
import redis
from dotenv import dotenv_values
from langchain_core.documents import Document
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

_KM_ROOT = Path(__file__).resolve().parents[1]
_ENTERPRISE_ROOT = _KM_ROOT.parents[1]
_ROOT_ENV = _ENTERPRISE_ROOT / ".env"
_MS_ENV = _ENTERPRISE_ROOT / "core" / "model-service" / ".env"

sys.path.insert(0, str(_KM_ROOT))

from redbear_model import (  # noqa: E402
    QWEN3_VL_EMBEDDING_DIMENSION,
    EmbeddingPurpose,
    EmbeddingRequest,
    ImageEmbeddingContent,
    Modality,
    ModelConfigDeprecatedError,
    ModelConfigInactiveError,
    ModelConfigNotFoundError,
    ModelType,
    RerankCandidateView,
    TextEmbeddingContent,
)

from src.bootstrap import BootstrapPaths, load_settings  # noqa: E402
from src.error_mapping import map_model_error  # noqa: E402
from src.integrations.model import (  # noqa: E402
    ModelInvokeFailedError,
    ModelInvokeTimeoutError,
    ModelInvokeUnavailableError,
    RemoteInvokeRef,
    ref_from_view,
)
from src.integrations.model.asr_video import AudioTranscriptionChunkModel  # noqa: E402
from src.integrations.model.chat import RedBearChatModel, message_text  # noqa: E402
from src.integrations.model.embedding import RedBearEmbeddings  # noqa: E402
from src.integrations.model.rerank import RedBearRerank  # noqa: E402
from src.integrations.model.views import (  # noqa: E402
    is_qwen3_vl_embedding_view,
    is_qwen3_vl_rerank_view,
)
from src.rag.models.chunk import DocumentChunk  # noqa: E402
from src.rag.retrieval.async_elasticsearch import (  # noqa: E402
    AsyncChunkStore,
    AsyncElasticSearchRetrieval,
    collection_name_for_knowledge,
)
from src.rag.retrieval.models import RetrievalSearchOptions  # noqa: E402
from src.rag.vdb.vector_store import TaskVectorStore  # noqa: E402
from src.repositories.model_registry import SyncSQLModelRegistry  # noqa: E402
from src.runtime import ProcessRuntime  # noqa: E402
from src.services.chunk import (  # noqa: E402
    ChunkDocumentSnapshot,
    build_chunk_store,
    preview_with_vision,
)
from src.usage_context import bind_usage_context  # noqa: E402

_OK = "OK"
_WARN = "WARN"
_FAIL = "FAIL"
_ENV = "ENV"

_counts = {_OK: 0, _WARN: 0, _FAIL: 0, _ENV: 0}

MODEL_USAGE_STREAM = "model:usage"
MODEL_USAGE_CONSUMER_GROUP = "model-usage-consumers"
_CONSUMER_ONLINE_IDLE_MS = 30_000
MODEL_USAGE_COLUMNS = (
    "event_id, status, capability, stream, channel_id, attempts, source_service, "
    "resource_type, resource_id, latency_ms, config_id, provider, model_name, "
    "input_tokens, output_tokens"
)

_RESOURCE_TYPE = "kb"
_EXPECTED_SOURCE = "mem-knowledge"
_DEFAULT_TENANT = "redbearai.com"
_DEFAULT_ASR_URL = "https://dashscope.oss-cn-beijing.aliyuncs.com/audios/welcome.mp3"

_P1 = "MemoryBear 探针文本一：向量化端到端校验。"
_P2 = "MemoryBear 探针文本二：检索命中校验。"
_QUERY_TEXT = "向量化端到端校验"
_MM_TEXT = "探针多模态融合文本：一张红色方块图片的说明。"
_RERANK_QUERY = "红色方块图片"
_RERANK_DOCS = ("一张红色方块图片。", "一张蓝色圆形图片。", "一份与图片无关的文本说明。")
_LLM_PROMPT = "用一句话说明你在做什么。"
_STRUCT_PROMPT = "请以 JSON 输出一个人的姓名与年龄。"
_QA_Q = "探针问题：什么是 MemoryBear？"
_QA_A = "MemoryBear 是探针测试用的答案。"

_TENANT_SQL = (
    "SELECT id, name FROM tenants "
    "WHERE id::text = :value OR name = :value "
    "ORDER BY is_active DESC NULLS LAST LIMIT 1"
)
_CANDIDATE_SQL = (
    "SELECT c.id, c.tenant_id, c.name, c.provider, c.is_public "
    "FROM model_configs c "
    "LEFT JOIN model_bases b ON b.id = c.model_id "
    "WHERE c.is_active "
    "  AND (c.tenant_id = CAST(:tenant AS uuid) OR c.is_public) "
    "  AND (b.is_deprecated IS NULL OR b.is_deprecated = false) "
    "ORDER BY c.name"
)

_MAX_TRIALS = {
    "embedding_text": 3,
    "embedding_mm": 2,
    "rerank_text": 2,
    "rerank_mm": 2,
    "asr": 3,
    "llm": 3,
    "vision": 3,
}

_ENV_REMOTE_CODES = {
    "MODEL_NOT_FOUND",
    4003,
    "MODEL_DEPRECATED",
    4010,
    "CHANNEL_DISABLED",
    4011,
    "NO_AVAILABLE_CHANNEL",
    4012,
    "SPEEDBEAR_CHANNEL_MISSING",
    4013,
    "CREDENTIAL_DECRYPT_ERROR",
    4014,
    "API_KEY_INVALID",
    3009,
    "MODEL_SERVICE_UNAVAILABLE",
    5002,
    "MODEL_SERVICE_TIMEOUT",
    5003,
    "RATE_LIMITED",
    4029,
}

_PREFERRED = {
    "embedding_text": ("text-embedding-v4", "text-embedding-v3", "bge", "qwen", "gte"),
    "embedding_mm": ("qwen3-vl-embedding",),
    "rerank_text": ("gte-rerank-v2", "gte-rerank", "rerank"),
    "rerank_mm": ("qwen3-vl-rerank",),
    "asr": ("paraformer", "sensevoice", "whisper", "asr"),
    "llm": ("qwen3.5-flash", "qwen-flash", "gpt-4o-mini", "qwen", "gpt"),
    "vision": ("qwen3-vl", "qwen-vl", "vl", "gpt-4o"),
}

_CAPTURED: list[tuple[str, str]] = []
_LEAK_TOKENS: set[str] = set()


class _Person(BaseModel):
    name: str
    age: int


class _ProbeAssertionError(Exception):
    """契约断言失败：候选自身不满足调用契约（区别于环境性问题）。"""


def _require(condition: Any, message: str) -> None:
    if not condition:
        raise _ProbeAssertionError(message)


def _report(status: str, scope: str, detail: str) -> None:
    _counts[status] += 1
    print(f"[{status}] {scope}: {detail}")


def _info(message: str) -> None:
    print(f"       {message}")


def _section(title: str) -> None:
    print(f"\n=== {title} ===")


def _arg_value(argv: list[str], name: str, default: str | None = None) -> str | None:
    prefix = name + "="
    for index, item in enumerate(argv):
        if item.startswith(prefix):
            return item[len(prefix):]
        if item == name and index + 1 < len(argv):
            return argv[index + 1]
    return default


def _env_file_values(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    return {key: value for key, value in dotenv_values(path).items() if value is not None}


def _load_settings():
    paths = BootstrapPaths(
        repository_root=_ENTERPRISE_ROOT,
        service_root=_KM_ROOT,
        root_env_file=_ROOT_ENV,
        service_env_file=_KM_ROOT / ".env",
    )
    return load_settings(paths)


def _engine(settings):
    return create_engine(
        settings.database_url_sync,
        connect_args={
            "options": "-c default_transaction_read_only=on",
            "connect_timeout": 5,
        },
        pool_pre_ping=True,
    )


def _trust_env_for(url: str) -> bool:
    host = urlsplit(url).hostname or ""
    return host not in {"127.0.0.1", "localhost", "::1"}


def _resolve_tenant(engine, value: str):
    with engine.connect() as conn:
        row = conn.execute(text(_TENANT_SQL), {"value": value}).first()
    if row is None:
        return None
    return row[0], row[1]


def _resolve_tenant_uuid(engine, tenant) -> uuid.UUID:
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT id FROM tenants WHERE id = :tenant"), {"tenant": tenant}
        ).first()
    _require(row is not None, "调用方租户不存在")
    return row[0]


def _err_detail(exc: BaseException) -> str:
    head = type(exc).__name__
    if isinstance(exc, ModelInvokeFailedError):
        head += f"(remote_code={exc.remote_code!r}, http_status={exc.http_status!r})"
    return f"{head}: {_scrub(str(exc))[:200]}"


def _is_env_error(exc: BaseException) -> bool:
    if isinstance(
        exc,
        (
            ModelInvokeUnavailableError,
            ModelInvokeTimeoutError,
            ModelConfigNotFoundError,
            ModelConfigDeprecatedError,
            ModelConfigInactiveError,
        ),
    ):
        return True
    if isinstance(exc, ModelInvokeFailedError):
        return exc.remote_code in _ENV_REMOTE_CODES
    return False


# ---------------- 候选盘点 ----------------


@dataclass(frozen=True)
class _Candidate:
    family: str
    tenant_id: uuid.UUID
    config_id: uuid.UUID
    name: str
    provider: str
    is_public: bool
    profile: Any

    @property
    def label(self) -> str:
        visibility = "public" if self.is_public else "tenant"
        return f"{self.family}:{self.name}({self.provider},{visibility})"


def _pref_rank(candidate: _Candidate) -> int:
    tokens = _PREFERRED.get(candidate.family, ())
    name = candidate.name.lower()
    for index, token in enumerate(tokens):
        if token in name:
            return index
    return len(tokens)


def _collect_candidates(engine, tenant) -> list[_Candidate]:
    candidates: list[_Candidate] = []
    with engine.connect() as conn:
        rows = conn.execute(text(_CANDIDATE_SQL), {"tenant": str(tenant)}).all()
    with Session(engine) as session:
        registry = SyncSQLModelRegistry(session)
        for config_id, tenant_id, name, provider, is_public in rows:
            view = registry.get_model_config(config_id, tenant)
            if view is None:
                continue
            profile = view.profile
            base = _Candidate(
                family="",
                tenant_id=tenant_id,
                config_id=config_id,
                name=name,
                provider=provider,
                is_public=bool(is_public),
                profile=profile,
            )
            if is_qwen3_vl_embedding_view(view):
                candidates.append(replace(base, family="embedding_mm"))
            elif profile.type is ModelType.EMBEDDING:
                candidates.append(replace(base, family="embedding_text"))
            elif is_qwen3_vl_rerank_view(view):
                candidates.append(replace(base, family="rerank_mm"))
            elif profile.type is ModelType.RERANK:
                candidates.append(replace(base, family="rerank_text"))
            elif profile.type is ModelType.ASR:
                candidates.append(replace(base, family="asr"))
            elif profile.type is ModelType.LLM:
                candidates.append(replace(base, family="llm"))
                if Modality.IMAGE in profile.input_modalities:
                    candidates.append(replace(base, family="vision"))
    return candidates


# ---------------- 窗口 ----------------


@dataclass
class _Window:
    key: str
    label: str
    capability: str | None
    group: str
    attempted: set[str] = field(default_factory=set)
    chosen: str | None = None

    @property
    def rid(self) -> str:
        return str(
            uuid.uuid5(uuid.NAMESPACE_URL, f"m8-g4b-km-e2e-{self.key.replace('_', '-')}")
        )


_WINDOWS = (
    _Window("embed_text", "文本 embedding（sync 文档+查询）", "embedding", "embed_text"),
    _Window("es_text", "文本 embedding 写 ES + 向量检索（async）", "embedding", "es"),
    _Window("embed_mm", "结构化单向量融合（text-only / text+image）", "embedding", "mm"),
    _Window("rerank_mm", "多模态 rerank（views）", "rerank", "mm"),
    _Window("rerank_text", "文本 rerank（acompress_documents）", "rerank", "rerank"),
    _Window("qa_text", "qa 文本 embedding 入库+读回", "embedding", "qa"),
    _Window("qa_mm", "qa 结构化 embedding 入库+读回", "embedding", "qa"),
    _Window("asr", "asr 音频转写（服务侧阻塞轮询）", "asr", "asr"),
    _Window("vision", "vision 图→文（llm 多模态）", "llm", "vision"),
    _Window("es_mm", "结构化 embedding 写 ES（unit 布局）", "embedding", "es"),
    _Window("graph_llm", "call_structured + ainvoke", "llm", "graph"),
    _Window("graph_embed", "graph aembed_documents", "embedding", "graph"),
    _Window("negative", "负例：不存在 config 响亮拒止", None, "negative"),
)
_WINDOW_BY_KEY = {window.key: window for window in _WINDOWS}


def _scratch_id(key: str) -> uuid.UUID:
    return uuid.uuid5(uuid.NAMESPACE_URL, f"m8-g4b-km-scratch-{key}")


KID_ES_TEXT = _scratch_id("es-text")
KID_ES_MM = _scratch_id("es-mm")
KID_QA_TEXT = _scratch_id("qa-text")
KID_QA_MM = _scratch_id("qa-mm")
KID_VISION = _scratch_id("vision")
_SCRATCH_KIDS = (KID_ES_TEXT, KID_ES_MM, KID_QA_TEXT, KID_QA_MM)


# ---------------- 测试素材 ----------------


def _png_chunk(tag: bytes, payload: bytes) -> bytes:
    return struct.pack(">I", len(payload)) + tag + payload + struct.pack(
        ">I", zlib.crc32(tag + payload) & 0xFFFFFFFF
    )


def _png_bytes() -> bytes:
    width = height = 64
    raw = b"".join(b"\x00" + b"\xff\x00\x00" * width for _ in range(height))
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", ihdr)
        + _png_chunk(b"IDAT", zlib.compress(raw))
        + _png_chunk(b"IEND", b"")
    )


@dataclass
class _Context:
    settings: Any
    tenant: uuid.UUID
    tenant_name: str
    asr_url: str
    keep_index: bool
    skip: set[str]
    engine: Any
    runtime: Any
    candidates: list[_Candidate]
    started_ms: int = 0
    png: bytes = field(default_factory=_png_bytes)
    png_data_uri: str = ""


def _view_for(ctx: _Context, candidate: _Candidate):
    with Session(ctx.engine) as session:
        view = SyncSQLModelRegistry(session).get_model_config(candidate.config_id, ctx.tenant)
    _require(view is not None, f"候选 {candidate.label} 视图解析失败")
    return view


def _candidate_ref(ctx: _Context, candidate: _Candidate):
    return ref_from_view(_view_for(ctx, candidate), ctx.tenant)


def _emb_shell(ctx: _Context, candidate: _Candidate, *, multimodal: bool = False):
    return RedBearEmbeddings.for_invoke_ref(
        _candidate_ref(ctx, candidate),
        pool=ctx.runtime.model_runtime,
        multimodal=multimodal,
    )


def _plain_chunk(knowledge_id: uuid.UUID, doc_id: uuid.UUID, content: str) -> DocumentChunk:
    return DocumentChunk(
        page_content=content,
        metadata={
            "doc_id": str(doc_id),
            "document_id": str(doc_id),
            "knowledge_id": str(knowledge_id),
            "sort_id": 0,
            "status": 1,
            "chunk_type": "chunk",
            "file_id": str(uuid.uuid4()),
            "file_name": "probe-g4b.txt",
            "file_created_at": int(time.time() * 1000),
        },
        children=[],
    )


def _qa_chunk(
    knowledge_id: uuid.UUID, doc_id: uuid.UUID, question: str, answer: str
) -> DocumentChunk:
    return DocumentChunk(
        page_content=f"question: {question}\nanswer: {answer}",
        metadata={
            "doc_id": str(doc_id),
            "document_id": str(doc_id),
            "knowledge_id": str(knowledge_id),
            "sort_id": 0,
            "status": 1,
            "chunk_type": "qa",
            "question": question,
            "answer": answer,
            "file_id": str(uuid.uuid4()),
            "file_name": "probe-g4b-qa.txt",
            "file_created_at": int(time.time() * 1000),
        },
        children=[],
    )


def _chunk_snapshot(knowledge_id: uuid.UUID, file_name: str) -> ChunkDocumentSnapshot:
    doc_id = uuid.uuid4()
    return ChunkDocumentSnapshot(
        knowledge_id=knowledge_id,
        document_id=doc_id,
        file_id=uuid.uuid4(),
        file_name=file_name,
        file_created_at=int(time.time() * 1000),
        parent_child_mode=False,
        parser_config={},
        embedding_id=None,
    )


# ---------------- 通用试调驱动 ----------------


async def _trial_core(
    ctx: _Context, family: str, window: _Window, trial, *, prefer=None, note=""
) -> None:
    if window.group in ctx.skip:
        _report(_WARN, window.label, f"跳过（--skip={window.group}）")
        return
    pool = [candidate for candidate in ctx.candidates if candidate.family == family]
    if prefer is not None:
        pool.sort(
            key=lambda c: (
                0 if str(c.config_id) == prefer else 1,
                _pref_rank(c),
                c.is_public,
                c.name,
            )
        )
    else:
        pool.sort(key=lambda c: (_pref_rank(c), c.is_public, c.name))
    if not pool:
        _report(_ENV, window.label, f"无 {family} 候选（dev 库缺族）")
        return
    cap = _MAX_TRIALS.get(family, 2)
    failures: list[str] = []
    env_only = True
    attempted_here = 0
    for candidate in pool:
        key = str(candidate.config_id)
        if key in window.attempted:
            continue
        if attempted_here >= cap:
            _info(f"成本护栏：本窗口仅试调 {cap} 个候选，余下未尝试")
            break
        window.attempted.add(key)
        attempted_here += 1
        _info(f"试调 {candidate.label} …")
        try:
            await trial(candidate)
        except _ProbeAssertionError as exc:
            _report(_FAIL, window.label, f"{candidate.label} 契约断言失败: {exc}")
            return
        except Exception as exc:  # noqa: BLE001
            if not _is_env_error(exc):
                env_only = False
            failures.append(f"{candidate.label} -> {_err_detail(exc)}")
            continue
        window.chosen = key
        suffix = f" {note}" if note else ""
        _report(_OK, window.label, f"{candidate.label}{suffix}")
        return
    detail = " | ".join(failures)[:400] or "无可用候选"
    if env_only:
        _report(_ENV, window.label, f"候选均环境性失败: {detail}")
    else:
        _report(_FAIL, window.label, f"候选全部失败: {detail}")


# ---------------- 同步腿 ----------------


async def _trial_embed_text(ctx: _Context, window: _Window, candidate: _Candidate) -> None:
    with bind_usage_context(_RESOURCE_TYPE, window.rid):
        shell = _emb_shell(ctx, candidate)
        vectors = shell.embed_documents([_P1, "   ", _P2])
        _require(len(vectors) == 3, f"返回条数 {len(vectors)} != 3")
        _require(vectors[1] is None, "空白位未回填 None")
        _require(
            vectors[0] and vectors[2] and len(vectors[0]) == len(vectors[2]),
            "批量向量维度不一致",
        )
        query_vector = shell.embed_query(_QUERY_TEXT)
        _require(len(query_vector) == len(vectors[0]), "查询向量维度与文档不一致")
    _info(f"维度={len(vectors[0])}")


async def _trial_qa_text(ctx: _Context, window: _Window, candidate: _Candidate) -> None:
    with bind_usage_context(_RESOURCE_TYPE, window.rid):
        shell = _emb_shell(ctx, candidate)
        client = ctx.runtime.elasticsearch.sync_client()
        store = TaskVectorStore(client, KID_QA_TEXT, shell)
        doc_id = uuid.uuid4()
        store.add_chunks([_qa_chunk(KID_QA_TEXT, doc_id, _QA_Q, _QA_A)])
        client.indices.refresh(index=collection_name_for_knowledge(KID_QA_TEXT))
        total, hits = store.search_by_segment(document_id=str(doc_id), query=_QA_Q)
    _require(total >= 1, "qa 文本检索 total=0")
    _require(
        any(_QA_Q in (hit.page_content or "") for hit in hits),
        "qa 文本读回内容不含问题文本",
    )


async def _trial_qa_mm(ctx: _Context, window: _Window, candidate: _Candidate) -> None:
    with bind_usage_context(_RESOURCE_TYPE, window.rid):
        shell = _emb_shell(ctx, candidate, multimodal=True)
        client = ctx.runtime.elasticsearch.sync_client()
        store = TaskVectorStore(
            client,
            KID_QA_MM,
            shell,
            structured_multimodal=True,
            embedding_dimension=QWEN3_VL_EMBEDDING_DIMENSION,
        )
        doc_id = uuid.uuid4()
        store.add_chunks([_qa_chunk(KID_QA_MM, doc_id, _QA_Q, _QA_A)])
        client.indices.refresh(index=collection_name_for_knowledge(KID_QA_MM))
        total, hits = store.search_by_segment(document_id=str(doc_id), query=_QA_Q)
    _require(total >= 1, "qa 结构化检索 total=0")
    _require(
        any(_QA_Q in (hit.page_content or "") for hit in hits),
        "qa 结构化读回内容不含问题文本",
    )


async def _trial_asr(ctx: _Context, window: _Window, candidate: _Candidate) -> None:
    with bind_usage_context(_RESOURCE_TYPE, window.rid):
        shell = AudioTranscriptionChunkModel.for_invoke_sync_ref(
            _candidate_ref(ctx, candidate),
            pool=ctx.runtime.model_runtime,
            file_url=ctx.asr_url,
        )
        asr_text, tokens = shell.transcription("")
    _require(isinstance(asr_text, str) and asr_text.strip(), "asr 转写结果为空")
    _require(isinstance(tokens, int) and tokens >= 0, "asr tokens 非法")
    _info(f"chars={len(asr_text)}, tokens={tokens}")


async def _run_negative(ctx: _Context, window: _Window) -> None:
    if window.group in ctx.skip:
        _report(_WARN, window.label, f"跳过（--skip={window.group}）")
        return
    ghost = uuid.uuid4()
    with Session(ctx.engine) as session:
        view = SyncSQLModelRegistry(session).get_model_config(ghost, ctx.tenant)
    if view is not None:
        _report(_FAIL, window.label, "幽灵 config 本地视图非 None")
        return
    ref = RemoteInvokeRef(config_id=ghost, tenant_id=ctx.tenant)
    shell = RedBearEmbeddings.for_invoke_ref(ref, pool=ctx.runtime.model_runtime)
    try:
        with bind_usage_context(_RESOURCE_TYPE, window.rid):
            shell.embed_documents(["probe negative leg"])
    except (ModelInvokeUnavailableError, ModelInvokeTimeoutError):
        _report(_ENV, window.label, "模型服务不可达/超时（负例无法验证）")
        return
    except ModelInvokeFailedError as exc:
        if exc.remote_code not in ("MODEL_NOT_FOUND", 4003):
            _report(_FAIL, window.label, f"拒止码不符: {exc.remote_code!r}")
            return
        mapped = map_model_error(exc)
        if mapped.code != "KB_RETRIEVAL_MODEL_NOT_FOUND":
            _report(_FAIL, window.label, f"错误映射不符: {mapped.code}")
            return
        window.chosen = "ghost"
        _report(_OK, window.label, f"remote_code={exc.remote_code!r} → {mapped.code}")
        return
    _report(_FAIL, window.label, "幽灵 config 调用未抛错")


async def _run_sync_phase(ctx: _Context) -> None:
    _section("[3] 同步腿：文本 embedding / qa / asr / 负例")
    window = _WINDOW_BY_KEY["embed_text"]
    await _trial_core(
        ctx, "embedding_text", window, lambda c: _trial_embed_text(ctx, window, c)
    )
    window = _WINDOW_BY_KEY["qa_text"]
    await _trial_core(ctx, "embedding_text", window, lambda c: _trial_qa_text(ctx, window, c))
    window = _WINDOW_BY_KEY["qa_mm"]
    await _trial_core(ctx, "embedding_mm", window, lambda c: _trial_qa_mm(ctx, window, c))
    window = _WINDOW_BY_KEY["asr"]
    await _trial_core(ctx, "asr", window, lambda c: _trial_asr(ctx, window, c))
    await _run_negative(ctx, _WINDOW_BY_KEY["negative"])


# ---------------- 异步腿 ----------------


async def _trial_es_text(ctx: _Context, window: _Window, candidate: _Candidate) -> None:
    shell = _emb_shell(ctx, candidate)
    client = await ctx.runtime.elasticsearch.client()
    store = AsyncChunkStore(client, KID_ES_TEXT, embed=shell.aembed_documents)
    doc_id = uuid.uuid4()
    with bind_usage_context(_RESOURCE_TYPE, window.rid):
        await store.add_chunks([_plain_chunk(KID_ES_TEXT, doc_id, _P1)])
    index = collection_name_for_knowledge(KID_ES_TEXT)
    await client.indices.refresh(index=index)
    mapping = await client.indices.get_mapping(index=index)
    props = mapping[index]["mappings"]["properties"]
    _require(props["vector"].get("index") is True, "文本索引 vector.index 应为 True")
    retrieval = AsyncElasticSearchRetrieval(client)
    options = RetrievalSearchOptions(
        indices=index,
        top_k=5,
        score_threshold=0.0,
        file_names_filter=(),
        document_ids_include=None,
    )
    with bind_usage_context(_RESOURCE_TYPE, window.rid):
        query_vector = await shell.aembed_query(_QUERY_TEXT)
        hits = await retrieval.search_by_query_vector(query_vector, options)
    _require(
        props["vector"].get("dims") == len(query_vector),
        f"dims={props['vector'].get('dims')} != 查询向量维度 {len(query_vector)}",
    )
    _require(
        any(str(hit.metadata.get("doc_id")) == str(doc_id) for hit in hits),
        "向量检索未命中写入文档",
    )


async def _trial_embed_mm(ctx: _Context, window: _Window, candidate: _Candidate) -> None:
    _require(
        is_qwen3_vl_embedding_view(_view_for(ctx, candidate)),
        "候选视图非 qwen3-vl-embedding（与分类逻辑不一致）",
    )
    shell = _emb_shell(ctx, candidate, multimodal=True)
    with bind_usage_context(_RESOURCE_TYPE, window.rid):
        text_result = await shell.aembed_contents(
            EmbeddingRequest(
                purpose=EmbeddingPurpose.INDEX,
                contents=(TextEmbeddingContent(text=_MM_TEXT),),
            )
        )
        fused_result = await shell.aembed_contents(
            EmbeddingRequest(
                purpose=EmbeddingPurpose.INDEX,
                contents=(
                    TextEmbeddingContent(text="描述这张图片的内容"),
                    ImageEmbeddingContent(
                        media_type="image/png",
                        data_uri=ctx.png_data_uri,
                        decoded_bytes=len(ctx.png),
                    ),
                ),
            )
        )
    _require(
        len(text_result.vector) == QWEN3_VL_EMBEDDING_DIMENSION,
        f"text-only 向量维度 {len(text_result.vector)} != {QWEN3_VL_EMBEDDING_DIMENSION}",
    )
    _require(
        text_result.dimension == QWEN3_VL_EMBEDDING_DIMENSION,
        f"text-only dimension={text_result.dimension}",
    )
    _require(
        len(fused_result.vector) == QWEN3_VL_EMBEDDING_DIMENSION,
        f"text+image 向量维度 {len(fused_result.vector)} != {QWEN3_VL_EMBEDDING_DIMENSION}",
    )


async def _trial_rerank_mm(ctx: _Context, window: _Window, candidate: _Candidate) -> None:
    shell = RedBearRerank.for_invoke_ref(
        _candidate_ref(ctx, candidate), pool=ctx.runtime.model_runtime
    )
    views = (
        RerankCandidateView(chunk_index=0, kind="text", content="一张红色方块图片。"),
        RerankCandidateView(
            chunk_index=1, kind="image", image_index=0, content=ctx.png_data_uri
        ),
        RerankCandidateView(chunk_index=2, kind="text", content="一份与图片无关的文本说明。"),
    )
    with bind_usage_context(_RESOURCE_TYPE, window.rid):
        scores = await shell.arerank_multimodal(
            TextEmbeddingContent(text=_RERANK_QUERY), views, top_n=3
        )
    _require(1 <= len(scores) <= 3, f"rerank 返回条数 {len(scores)} 越界")
    indices = [score.input_index for score in scores]
    _require(len(set(indices)) == len(indices), "input_index 重复")
    _require(all(0 <= index < 3 for index in indices), f"input_index 越界: {indices}")


async def _trial_rerank_text(ctx: _Context, window: _Window, candidate: _Candidate) -> None:
    shell = RedBearRerank.for_invoke_ref(
        _candidate_ref(ctx, candidate), pool=ctx.runtime.model_runtime
    )
    documents = [Document(page_content=text) for text in _RERANK_DOCS]
    with bind_usage_context(_RESOURCE_TYPE, window.rid):
        compressed = await shell.acompress_documents(documents, _RERANK_QUERY)
    _require(len(compressed) == len(_RERANK_DOCS), f"重排返回 {len(compressed)} 条")
    _require(
        all(document.metadata.get("relevance_score") is not None for document in compressed),
        "重排结果缺 relevance_score",
    )


async def _trial_vision(ctx: _Context, window: _Window, candidate: _Candidate) -> None:
    snapshot = _chunk_snapshot(KID_VISION, "probe-g4b-vision.png")
    with bind_usage_context(_RESOURCE_TYPE, window.rid):
        chunks = await preview_with_vision(
            ctx.runtime,
            snapshot,
            ctx.png,
            _view_for(ctx, candidate),
            tenant_id=ctx.tenant,
        )
    _require(len(chunks) >= 1, "vision 未产出 chunk")
    text = chunks[0].page_content or ""
    _require(text.strip(), "vision 输出为空")
    _require(
        chunks[0].metadata.get("vision_text") == text, "vision_text 与 page_content 不一致"
    )
    _info(f"chars={len(text)}")


async def _trial_es_mm(ctx: _Context, window: _Window, candidate: _Candidate) -> None:
    client = await ctx.runtime.elasticsearch.client()
    snapshot = _chunk_snapshot(KID_ES_MM, "probe-g4b-es-mm.txt")
    store = build_chunk_store(
        ctx.runtime, client, snapshot, _view_for(ctx, candidate), tenant_id=ctx.tenant
    )
    _require(store.multimodal is True, "结构化栈 multimodal 标记缺失")
    doc_id = uuid.uuid4()
    with bind_usage_context(_RESOURCE_TYPE, window.rid):
        await store.add_chunks([_plain_chunk(KID_ES_MM, doc_id, _MM_TEXT)])
    index = collection_name_for_knowledge(KID_ES_MM)
    await client.indices.refresh(index=index)
    mapping = await client.indices.get_mapping(index=index)
    props = mapping[index]["mappings"]["properties"]
    _require(props["vector"].get("index") is False, "unit 索引 vector.index 应为 False")
    _require(
        props["vector"].get("dims") == QWEN3_VL_EMBEDDING_DIMENSION,
        f"unit 索引 dims={props['vector'].get('dims')} != {QWEN3_VL_EMBEDDING_DIMENSION}",
    )
    _require("unit_kind" in props, "unit 字段 unit_kind 缺失")
    total, hits = await store.search_by_segment(document_id=str(doc_id))
    _require(total >= 1, "单元布局 chunk_record 检索 total=0")
    _require(
        any((hit.page_content or "").strip() == _MM_TEXT for hit in hits),
        "chunk_record 读回内容不一致",
    )
    shell = _emb_shell(ctx, candidate, multimodal=True)
    with bind_usage_context(_RESOURCE_TYPE, window.rid):
        query_result = await shell.aembed_contents(
            EmbeddingRequest(
                purpose=EmbeddingPurpose.RETRIEVAL,
                contents=(TextEmbeddingContent(text=_MM_TEXT),),
            )
        )
    retrieval = AsyncElasticSearchRetrieval(client)
    options = RetrievalSearchOptions(
        indices=index,
        top_k=5,
        score_threshold=0.0,
        file_names_filter=(),
        document_ids_include=None,
    )
    unit_candidates = await retrieval.search_units_by_vector(query_result.vector, options)
    _require(
        any(candidate_hit.chunk_id == str(doc_id) for candidate_hit in unit_candidates),
        "unit 检索未命中",
    )


async def _trial_graph_llm(ctx: _Context, window: _Window, candidate: _Candidate) -> None:
    shell = RedBearChatModel.for_invoke_ref(
        _candidate_ref(ctx, candidate), pool=ctx.runtime.model_runtime
    )
    with bind_usage_context(_RESOURCE_TYPE, window.rid):
        response = await shell.ainvoke(_LLM_PROMPT)
        parsed = await shell.call_structured(_STRUCT_PROMPT, _Person)
    text = message_text(response)
    _require(text.strip(), "llm 输出为空")
    _require(isinstance(parsed, _Person), f"结构化输出类型 {type(parsed).__name__}")
    _require(str(parsed.name).strip(), "结构化输出 name 为空")
    _info(f"call_structured ok: name={parsed.name!r} age={parsed.age!r}")


async def _trial_graph_embed(ctx: _Context, window: _Window, candidate: _Candidate) -> None:
    shell = _emb_shell(ctx, candidate)
    with bind_usage_context(_RESOURCE_TYPE, window.rid):
        vectors = await shell.aembed_documents([_P1, _P2])
    _require(len(vectors) == 2, f"返回条数 {len(vectors)} != 2")
    _require(
        vectors[0] and vectors[1] and len(vectors[0]) == len(vectors[1]),
        "异步批量维度不一致",
    )


async def _run_async_phase(ctx: _Context) -> None:
    _section("[4] 异步腿：ES 写读 / 多模态 / rerank / vision / graph")
    window = _WINDOW_BY_KEY["es_text"]
    await _trial_core(ctx, "embedding_text", window, lambda c: _trial_es_text(ctx, window, c))
    window = _WINDOW_BY_KEY["embed_mm"]
    await _trial_core(ctx, "embedding_mm", window, lambda c: _trial_embed_mm(ctx, window, c))
    window = _WINDOW_BY_KEY["rerank_mm"]
    await _trial_core(ctx, "rerank_mm", window, lambda c: _trial_rerank_mm(ctx, window, c))
    window = _WINDOW_BY_KEY["rerank_text"]
    await _trial_core(ctx, "rerank_text", window, lambda c: _trial_rerank_text(ctx, window, c))
    window = _WINDOW_BY_KEY["vision"]
    await _trial_core(ctx, "vision", window, lambda c: _trial_vision(ctx, window, c))
    window = _WINDOW_BY_KEY["es_mm"]
    await _trial_core(ctx, "embedding_mm", window, lambda c: _trial_es_mm(ctx, window, c))
    window = _WINDOW_BY_KEY["graph_llm"]
    await _trial_core(ctx, "llm", window, lambda c: _trial_graph_llm(ctx, window, c))
    window = _WINDOW_BY_KEY["graph_embed"]
    prefer = _WINDOW_BY_KEY["embed_text"].chosen
    await _trial_core(
        ctx, "embedding_text", window, lambda c: _trial_graph_embed(ctx, window, c), prefer=prefer
    )


async def _delete_scratch(ctx: _Context, *, reason: str) -> None:
    client = await ctx.runtime.elasticsearch.client()
    for knowledge_id in _SCRATCH_KIDS:
        index = collection_name_for_knowledge(knowledge_id)
        try:
            await client.indices.delete(index=index, ignore_unavailable=True)
        except Exception as exc:  # noqa: BLE001
            _info(f"{reason} 删除 {index} 失败（忽略）: {type(exc).__name__}")


async def _run_all_invokes(ctx: _Context) -> None:
    if not ctx.keep_index:
        try:
            await _delete_scratch(ctx, reason="前置清理")
        except Exception as exc:  # noqa: BLE001
            _info(f"前置清理异常（继续）: {type(exc).__name__}")
    try:
        await _run_sync_phase(ctx)
        await _run_async_phase(ctx)
    finally:
        if not ctx.keep_index:
            try:
                await _delete_scratch(ctx, reason="收尾清理")
            except Exception as exc:  # noqa: BLE001
                _info(f"收尾清理异常（忽略）: {type(exc).__name__}")


# ---------------- 结构断言 ----------------


_CREDENTIAL_ATTRS = ("api_key", "api_base", "api_token", "credential", "credential_encrypted")


def _shell_credential_attrs(shell: Any) -> list[str]:
    found = [name for name in _CREDENTIAL_ATTRS if hasattr(shell, name)]
    fields = getattr(type(shell), "model_fields", None) or {}
    found += [name for name in ("api_key", "api_base") if name in fields]
    return sorted(set(found))


def _structure_check(ctx: _Context) -> None:
    _section("[2] 结构断言（解密面撤出 / 壳与视图无凭据字段）")
    if hasattr(ctx.settings, "model_credentials_key"):
        _report(_FAIL, "结构", "KnowledgeSettings 仍含 model_credentials_key")
    else:
        _report(_OK, "结构", "settings 无 model_credentials_key")
    ghost_ref = RemoteInvokeRef(config_id=uuid.uuid4(), tenant_id=ctx.tenant)
    shells = {
        "chat(async)": RedBearChatModel.for_invoke_ref(
            ghost_ref, pool=ctx.runtime.model_runtime
        ),
        "chat(sync)": RedBearChatModel.for_invoke_sync_ref(
            ghost_ref, pool=ctx.runtime.model_runtime
        ),
        "embedding": RedBearEmbeddings.for_invoke_ref(ghost_ref, pool=ctx.runtime.model_runtime),
        "rerank": RedBearRerank.for_invoke_ref(ghost_ref, pool=ctx.runtime.model_runtime),
        "asr": AudioTranscriptionChunkModel.for_invoke_sync_ref(
            ghost_ref, pool=ctx.runtime.model_runtime, file_url=ctx.asr_url
        ),
    }
    for name, shell in shells.items():
        leaked = _shell_credential_attrs(shell)
        if leaked:
            _report(_FAIL, "结构", f"{name} 壳暴露凭据属性 {leaked}")
        else:
            _report(_OK, "结构", f"{name} 壳无凭据属性")
    bad_views = []
    for candidate in ctx.candidates:
        dumped = str(_view_for(ctx, candidate).model_dump())
        if any(token in dumped for token in ("api_key", "api_base", "credential")):
            bad_views.append(candidate.label)
    if bad_views:
        _report(_FAIL, "结构", f"视图含凭据字段: {bad_views[:3]}")
    else:
        _report(_OK, "结构", f"{len(ctx.candidates)} 个候选视图无凭据字段")


# ---------------- 泄漏扫描 ----------------


def _scrub(value: str) -> str:
    for token in _LEAK_TOKENS:
        if token and token in value:
            value = value.replace(token, "***MASKED***")
    return value


def _add_leak_token(value: str) -> None:
    value = value.strip()
    if len(value) >= 12:
        _LEAK_TOKENS.add(value)


def _collect_leak_tokens(engine) -> None:
    _LEAK_TOKENS.clear()
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT credential_encrypted FROM model_channels")).all()
    for (envelope,) in rows:
        if not envelope:
            continue
        _add_leak_token(str(envelope))
        for part in str(envelope).split(":"):
            _add_leak_token(part)
    for path in (_ROOT_ENV, _MS_ENV):
        key = _env_file_values(path).get("MODEL_CREDENTIALS_KEY")
        if key:
            _add_leak_token(key)
    key = os.environ.get("MODEL_CREDENTIALS_KEY")
    if key:
        _add_leak_token(key)


def _install_capture() -> None:
    handler = logging.Handler()

    def emit(record: logging.LogRecord) -> None:
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001
            message = str(record.msg)
        _CAPTURED.append((record.name or "root", message))

    handler.emit = emit  # type: ignore[method-assign]
    handler.setLevel(logging.DEBUG)
    logging.getLogger().addHandler(handler)

    original_print = builtins.print

    def captured_print(*args: Any, **kwargs: Any) -> None:
        try:
            rendered = " ".join(str(item) for item in args)
        except Exception:  # noqa: BLE001
            rendered = "<unprintable>"
        _CAPTURED.append(("print", rendered))
        original_print(*args, **kwargs)

    builtins.print = captured_print


def _scan_capture() -> None:
    hits = 0
    for where, value in _CAPTURED:
        for token in _LEAK_TOKENS:
            if token and token in value:
                hits += 1
                _report(_FAIL, "泄漏扫描", f"{where} 输出命中敏感串 #{hits}")
                break
    if hits == 0:
        _report(
            _OK,
            "泄漏扫描",
            f"{len(_CAPTURED)} 条进程输出/日志无密钥材料（{len(_LEAK_TOKENS)} 个 token 比对）",
        )


# ---------------- usage 双落点 ----------------


def _service_redis(ctx: _Context) -> redis.Redis:
    env = {**_env_file_values(_ROOT_ENV), **_env_file_values(_MS_ENV)}
    host = env.get("REDIS_HOST") or ctx.settings.redis_host
    port = int(env.get("REDIS_PORT") or ctx.settings.redis_port or 6379)
    db = int(env.get("REDIS_DB") or 1)
    password = env.get("REDIS_PASSWORD")
    if not password:
        secret = ctx.settings.redis_password
        password = secret.get_secret_value() if hasattr(secret, "get_secret_value") else None
        if password == "":
            password = None
    return redis.Redis(
        host=host,
        port=port,
        db=db,
        password=password,
        decode_responses=True,
        socket_connect_timeout=5,
    )


def _wait_usage_events(
    redis_client: redis.Redis,
    *,
    capability: str,
    resource_id: str,
    started_ms: int,
    timeout_s: float = 20.0,
) -> dict[str, dict[str, Any]]:
    def collect() -> dict[str, dict[str, Any]]:
        found: dict[str, dict[str, Any]] = {}
        raw = redis_client.xrevrange(MODEL_USAGE_STREAM, max="+", min="-", count=2000)
        for entry_id, fields in raw:
            payload = fields.get("payload")
            if not payload:
                continue
            try:
                event = json.loads(payload)
            except (TypeError, ValueError):
                continue
            if not isinstance(event, dict):
                continue
            if event.get("capability") != capability:
                continue
            if str(event.get("resource_id") or "") != resource_id:
                continue
            try:
                ts_ms = int(event.get("ts_ms") or 0)
            except (TypeError, ValueError):
                ts_ms = 0
            if ts_ms >= started_ms:
                found[entry_id] = event
        return found

    deadline = time.monotonic() + timeout_s
    while True:
        found = collect()
        if found:
            time.sleep(0.5)
            found.update(collect())
            return found
        if time.monotonic() > deadline:
            return {}
        time.sleep(0.5)


def _wait_usage_rows(ctx: _Context, event_ids: list[str], *, timeout_s: float = 20.0):
    if not event_ids:
        return []
    # psycopg 适配：uuid[] 需显式 CAST，列表参数按数组绑定
    sql = text(
        f"SELECT {MODEL_USAGE_COLUMNS} FROM model_usage_records "
        "WHERE event_id = ANY(CAST(:ids AS uuid[]))"
    )
    deadline = time.monotonic() + timeout_s
    rows = []
    while True:
        with ctx.engine.connect() as conn:
            rows = conn.execute(sql, {"ids": event_ids}).mappings().all()
        if len(rows) >= len(event_ids):
            return rows
        if time.monotonic() > deadline:
            return rows
        time.sleep(0.5)


def _diagnose_usage_consumer(redis_client: redis.Redis) -> str:
    try:
        groups = redis_client.xinfo_groups(MODEL_USAGE_STREAM)
    except Exception as exc:  # noqa: BLE001
        return f"xinfo_groups 失败: {type(exc).__name__}"
    target = next(
        (group for group in groups if group.get("name") == MODEL_USAGE_CONSUMER_GROUP), None
    )
    if target is None:
        return "消费者组不存在"
    lag = int(target.get("lag") or 0)
    idle = int(target.get("idle") or 0)
    if lag > 0 and idle > _CONSUMER_ONLINE_IDLE_MS:
        return f"消费者疑似停摆（lag={lag}, idle={idle}ms）"
    return f"消费者在线（lag={lag}, idle={idle}ms）"


def _verify_event(window: _Window, entry_id: str, event: dict[str, Any]) -> bool:
    event_config = str(event.get("config_id") or "")
    if event_config not in window.attempted:
        kind = "成功" if str(event.get("status") or "") in ("ok", "fallback_succeeded") else "失败"
        _report(
            _FAIL, "usage 事件", f"{window.label}({entry_id}): {kind}事件 config 不在本窗口试调集"
        )
        return False
    status = str(event.get("status") or "")
    if status in ("ok", "fallback_succeeded"):
        checks = {
            "source_service": event.get("source_service") == _EXPECTED_SOURCE,
            "resource_type": event.get("resource_type") == _RESOURCE_TYPE,
            "stream": str(event.get("stream")).lower() in ("false", "0"),
            "channel_id": bool(str(event.get("channel_id") or "").strip()),
        }
        try:
            attempts = int(event.get("attempts") or 0)
        except (TypeError, ValueError):
            attempts = 0
        checks["attempts"] = attempts >= 1
        bad = [name for name, ok in checks.items() if not ok]
        if bad:
            _report(_FAIL, "usage 事件", f"{window.label}({entry_id}): 成功事件字段不符 {bad}")
            return False
    return True


def _check_negative_window(redis_client: redis.Redis, window: _Window, started_ms: int) -> None:
    events = _wait_usage_events(
        redis_client,
        capability="embedding",
        resource_id=window.rid,
        started_ms=started_ms,
        timeout_s=3.0,
    )
    if not events:
        _report(_OK, "usage 事件", f"{window.label}: 3s 窗口零事件（前置拒止不落 usage）")
        return
    for entry_id, event in events.items():
        if str(event.get("status") or "") in ("ok", "fallback_succeeded"):
            _report(_FAIL, "usage 事件", f"{window.label}({entry_id}): 幽灵 config 出现成功事件")
            return
    _report(_OK, "usage 事件", f"{window.label}: {len(events)} 条失败事件均属幽灵 config")


def _usage_row_problems(events: dict[str, dict[str, Any]], rows) -> list[str]:
    by_event = {str(row["event_id"]): row for row in rows}
    problems: list[str] = []
    for entry_id, event in events.items():
        row = by_event.get(str(event.get("event_id") or ""))
        if row is None:
            problems.append(f"{entry_id}: 事件存在但落表缺失")
            continue
        if str(row["status"]) != str(event.get("status")):
            problems.append(f"{entry_id}: status {row['status']} != {event.get('status')}")
        if str(row["config_id"]) != str(event.get("config_id")):
            problems.append(f"{entry_id}: config_id 不一致")
        if row["source_service"] != _EXPECTED_SOURCE:
            problems.append(f"{entry_id}: source_service={row['source_service']}")
        if row["resource_type"] != _RESOURCE_TYPE:
            problems.append(f"{entry_id}: resource_type={row['resource_type']}")
        if str(row["resource_id"]) != str(event.get("resource_id")):
            problems.append(f"{entry_id}: resource_id 不一致")
        try:
            row_attempts = int(row["attempts"] or 0)
        except (TypeError, ValueError):
            row_attempts = -1
        try:
            event_attempts = int(event.get("attempts") or 0)
        except ValueError:
            event_attempts = -1
        if row_attempts != event_attempts:
            problems.append(f"{entry_id}: attempts 不一致")
    return problems


def _check_usage(ctx: _Context) -> None:
    _section("[5] usage 双落点核对（Redis 事件 + model_usage_records）")
    chosen_windows = [window for window in _WINDOWS if window.chosen]
    if not chosen_windows:
        _report(_ENV, "usage", "无成功调用窗口，跳过 usage 核对")
        return
    try:
        redis_client = _service_redis(ctx)
        redis_client.ping()
    except Exception as exc:  # noqa: BLE001
        _report(_ENV, "usage", f"模型服务 Redis 不可达: {type(exc).__name__}")
        return
    all_events: dict[str, dict[str, Any]] = {}
    for window in chosen_windows:
        if window.key == "negative":
            _check_negative_window(redis_client, window, ctx.started_ms)
            continue
        if window.capability is None:
            continue
        events = _wait_usage_events(
            redis_client,
            capability=window.capability,
            resource_id=window.rid,
            started_ms=ctx.started_ms,
        )
        if not events:
            diagnose = _diagnose_usage_consumer(redis_client)
            status = _FAIL if "在线" in diagnose else _ENV
            _report(status, "usage 事件", f"{window.label}: 20s 无事件（{diagnose}）")
            continue
        ok = True
        for entry_id, event in events.items():
            ok = _verify_event(window, entry_id, event) and ok
        if ok:
            _report(_OK, "usage 事件", f"{window.label}: {len(events)} 条事件字段正确")
        all_events.update(events)
    if not all_events:
        return
    event_uuids = [str(event.get("event_id") or "") for event in all_events.values()]
    event_uuids = [event_id for event_id in event_uuids if event_id]
    rows = _wait_usage_rows(ctx, event_uuids)
    if not rows:
        diagnose = _diagnose_usage_consumer(redis_client)
        status = _FAIL if "在线" in diagnose else _ENV
        _report(status, "usage 落表", f"事件 {len(all_events)} 条但落表 0 行（{diagnose}）")
        return
    problems = _usage_row_problems(all_events, rows)
    if problems:
        _report(_FAIL, "usage 落表", f"{len(problems)} 处不一致: {problems[:4]}")
    else:
        _report(_OK, "usage 落表", f"{len(rows)}/{len(all_events)} 行落表且字段互证")


# ---------------- 前置检查 ----------------


def _preflight(base_url: str) -> bool:
    _section("[0] 模型服务前置检查")
    base = base_url.rstrip("/")
    try:
        health = httpx.get(
            f"{base}/internal/v1/health/live", timeout=5.0, trust_env=_trust_env_for(base)
        )
    except httpx.HTTPError as exc:
        _report(_FAIL, "preflight", f"/internal/v1/health/live 不可达: {type(exc).__name__}")
        return False
    if health.status_code != 200:
        _report(_FAIL, "preflight", f"/internal/v1/health/live HTTP {health.status_code}")
        return False
    _report(_OK, "preflight", "/internal/v1/health/live 200")
    try:
        ready = httpx.get(
            f"{base}/internal/v1/health/ready", timeout=10.0, trust_env=_trust_env_for(base)
        )
    except httpx.HTTPError as exc:
        _report(_FAIL, "preflight", f"/internal/v1/health/ready 不可达: {type(exc).__name__}")
        return False
    if ready.status_code != 200:
        _report(_FAIL, "preflight", f"/internal/v1/health/ready HTTP {ready.status_code}")
        return False
    _report(_OK, "preflight", "/internal/v1/health/ready 200")
    try:
        noauth = httpx.post(
            f"{base}/internal/v1/invoke",
            json={},
            timeout=5.0,
            trust_env=_trust_env_for(base),
        )
        if noauth.status_code == 401:
            _report(_OK, "preflight", "invoke 无身份头 401（fail-closed）")
        else:
            _report(_WARN, "preflight", f"invoke 无身份头返回 {noauth.status_code}（预期 401）")
    except httpx.HTTPError as exc:
        _report(_WARN, "preflight", f"invoke 探测异常: {type(exc).__name__}")
    return True


# ---------------- main ----------------


def main() -> int:
    argv = sys.argv[1:]
    if "--help" in argv or "-h" in argv:
        print(__doc__)
        return 0
    base_url = _arg_value(argv, "--base-url")
    if base_url:
        os.environ["MODEL_SERVICE_BASE_URL"] = base_url
    skip_arg = _arg_value(argv, "--skip")
    skip = {item.strip() for item in (skip_arg or "").split(",") if item.strip()}
    tenant_arg = _arg_value(argv, "--tenant", _DEFAULT_TENANT) or _DEFAULT_TENANT
    asr_url = _arg_value(argv, "--asr-url", _DEFAULT_ASR_URL) or _DEFAULT_ASR_URL
    list_only = "--list" in argv
    keep_index = "--keep-index" in argv

    settings = _load_settings()
    engine = _engine(settings)
    runtime = ProcessRuntime(settings)
    try:
        if not _preflight(settings.model_service_base_url):
            return 2
        _section("[1] 租户与候选盘点")
        resolved = _resolve_tenant(engine, tenant_arg)
        if resolved is None:
            _report(_FAIL, "租户", f"未找到租户 {tenant_arg}")
            return 2
        tenant, tenant_name = resolved
        _report(_OK, "租户", f"{tenant_name} ({tenant})")
        candidates = _collect_candidates(engine, tenant)
        if not candidates:
            _report(_ENV, "候选", "0 个可见候选（dev 库缺模型配置）")
            return 2
        for family in sorted({candidate.family for candidate in candidates}):
            entries = [c for c in candidates if c.family == family]
            names = ", ".join(entry.name for entry in entries[:4])
            _info(f"{family}: {len(entries)} 个（{names}…）")
        if list_only:
            _section("[list] 窗口计划（不执行调用）")
            for window in _WINDOWS:
                capability = window.capability or "-"
                print(
                    f"  {window.key:<11} group={window.group:<9} cap={capability:<9} "
                    f"rid={window.rid[:8]}  {window.label}"
                )
            return 0
        ctx = _Context(
            settings=settings,
            tenant=tenant,
            tenant_name=tenant_name,
            asr_url=asr_url,
            keep_index=keep_index,
            skip=skip,
            engine=engine,
            runtime=runtime,
            candidates=candidates,
        )
        ctx.png_data_uri = "data:image/png;base64," + base64.b64encode(ctx.png).decode("ascii")
        _install_capture()
        _collect_leak_tokens(engine)
        _structure_check(ctx)
        ctx.started_ms = int(time.time() * 1000)
        try:
            asyncio.run(_run_all_invokes(ctx))
        except Exception as exc:  # noqa: BLE001
            _report(_FAIL, "执行", f"总调度异常: {_err_detail(exc)}")
        _check_usage(ctx)
        _section("[6] 泄漏扫描")
        _scan_capture()
        _section("[7] 总结")
        for window in _WINDOWS:
            if window.key == "negative":
                state = "达到预期拒止" if window.chosen else "未验证"
            else:
                state = "成功" if window.chosen else "未验证"
            _info(f"{window.key:<11} {state}")
        _info(
            f"统计：OK={_counts[_OK]} WARN={_counts[_WARN]} "
            f"FAIL={_counts[_FAIL]} ENV={_counts[_ENV]}"
        )
        if _counts[_FAIL]:
            return 1
        if _counts[_ENV]:
            return 2
        return 0
    finally:
        engine.dispose()
        try:
            asyncio.run(runtime.aclose())
        except Exception as exc:  # noqa: BLE001
            print(f"[{_WARN}] shutdown: runtime.aclose 异常（忽略）: {type(exc).__name__}")


if __name__ == "__main__":
    sys.exit(main())
