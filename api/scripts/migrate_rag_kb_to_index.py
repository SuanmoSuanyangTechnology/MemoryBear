#!/usr/bin/env python3
"""把旧知识库中的 RAG 记忆迁移到独立索引 ``rag_memory_current``。

旧链路把每个宿主的历史消息写成 ``documents`` 表里的 ``{end_user_id}.txt``，
再切分为知识库 chunk。本脚本按 document_id 顺序拼接 chunk 文本，复用旧展示层
的角色切分规则还原消息，逐条写入独立索引，标记 ``source="legacy"``。

幂等：消息 id 与写入链一致（uuid5 = 命名空间 + end_user_id + dialog_at + content + 文档内序号），
重复执行覆盖同一 ES 文档，不会产生重复数据；同文重复消息也不会互相覆盖。

向量：默认复用旧 chunk 的向量（旧 chunk 与消息为 1:N，同一 chunk 内的消息共享该向量），
或用 ``--reembed`` 重新按当前 embedding 模型批量计算。两者都不可用时才留空。

清理（仅 ``--purge`` 显式开启时执行，且逐 KB 校验通过才删）：
- ``knowledges.status = 2``（列表不再显示）
- 删除该 KB 下 ``documents`` 记录
- 删除旧 chunk 索引 ``vector_index_{kb_id}_node``

独立运行：PostgreSQL / Elasticsearch / 模型服务连接信息全部从环境变量读取，
不读取项目 .env，也不 import app 包。

Usage::

    python scripts/migrate_rag_kb_to_index.py --dry-run
    python scripts/migrate_rag_kb_to_index.py --purge --dry-run
    python scripts/migrate_rag_kb_to_index.py
    python scripts/migrate_rag_kb_to_index.py --reembed
    python scripts/migrate_rag_kb_to_index.py --purge
    python scripts/migrate_rag_kb_to_index.py --workspace-id <uuid>
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import sys
import time
import uuid
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, Iterable

# 实时输出：管道/重定向下 print 默认块缓冲，逐 KB 进度会攒到进程结束才刷出
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(line_buffering=True)

# =====================================================================
# 配置区（全部从环境变量读取，不依赖项目 .env 或 app 包）
# =====================================================================


def _required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ValueError(f"缺少必填环境变量: {name}")
    return value


APP_ENV = _required_env("APP_ENV").lower()
if APP_ENV not in {"test", "pre", "prod"}:
    raise ValueError("APP_ENV 必须是 test、pre 或 prod")

POSTGRES_CONFIG = {
    "host": _required_env("DB_HOST"),
    "port": int(os.getenv("DB_PORT", "5432")),
    "database": _required_env("DB_NAME"),
    "user": _required_env("DB_USER"),
    "password": _required_env("DB_PASSWORD"),
}

ELASTICSEARCH_HOST_RAW = _required_env("ELASTICSEARCH_HOST").strip()
ELASTICSEARCH_CONFIG = {
    "hosts": [
        ELASTICSEARCH_HOST_RAW
        if "://" in ELASTICSEARCH_HOST_RAW
        else f"https://{ELASTICSEARCH_HOST_RAW}"
    ],
    "basic_auth": (
        os.getenv("ELASTICSEARCH_USERNAME", ""),
        os.getenv("ELASTICSEARCH_PASSWORD", ""),
    ),
    "request_timeout": float(os.getenv("ELASTICSEARCH_REQUEST_TIMEOUT", "100")),
    "retry_on_timeout": os.getenv("ELASTICSEARCH_RETRY_ON_TIMEOUT", "true").lower() == "true",
    "max_retries": 3,
    # 与 api 侧默认一致：自签名证书环境不校验
    "verify_certs": os.getenv("ELASTICSEARCH_VERIFY_CERTS", "false").lower() == "true",
}
_ES_CA_CERTS = os.getenv("ELASTICSEARCH_CA_CERTS", "").strip()
if _ES_CA_CERTS:
    ELASTICSEARCH_CONFIG["ca_certs"] = _ES_CA_CERTS

# 显式给端口时拼接（host 里自带端口则优先用自带的）
if os.getenv("ELASTICSEARCH_PORT") and ":" not in ELASTICSEARCH_HOST_RAW.split("://")[-1]:
    ELASTICSEARCH_CONFIG["hosts"] = [
        f"{ELASTICSEARCH_CONFIG['hosts'][0].rstrip('/')}:{os.getenv('ELASTICSEARCH_PORT')}"
    ]

MODEL_SERVICE_BASE_URL = os.getenv("MODEL_SERVICE_BASE_URL", "http://127.0.0.1:8080").rstrip("/")


def _validate_connection_config() -> None:
    """检查必填连接项，避免空配置发起难以定位的连接。"""
    required = {
        "POSTGRES_CONFIG": (POSTGRES_CONFIG, ("host", "database", "user", "password")),
        "ELASTICSEARCH_CONFIG": (ELASTICSEARCH_CONFIG, ("hosts",)),
    }
    missing = [
        f"{section}.{key}"
        for section, (config, keys) in required.items()
        for key in keys
        if not config.get(key)
    ]
    if missing:
        raise ValueError(f"连接配置缺少必填项: {', '.join(missing)}")


def _print_target_config() -> None:
    es_host = ELASTICSEARCH_CONFIG["hosts"][0]
    print("=" * 72)
    print(f"APP_ENV          : {APP_ENV}")
    print(f"PostgreSQL       : {POSTGRES_CONFIG['host']}:{POSTGRES_CONFIG['port']}/{POSTGRES_CONFIG['database']}")
    print(f"Elasticsearch    : {es_host}")
    print(f"Model service    : {MODEL_SERVICE_BASE_URL}")
    print(f"目标索引         : {_RAG_INDEX}")
    print(f"断点文件         : {CHECKPOINT_PATH}")
    print("=" * 72)


# =====================================================================
# 常量
# =====================================================================

#: 与 api/app/core/memory/pipelines/dispatcher.py 的 _RAG_MEMORY_NAMESPACE 必须一致；
#: 两边不一致会导致同一内容算出两个 id，迁移数据与在线写入互相重复。
_RAG_MEMORY_NAMESPACE = uuid.UUID("8f3f1a52-6c1e-5b7a-9d4e-2a7c6b1f0e93")
_RAG_INDEX = "rag_memory_current"
_ROLE_SPLIT_RE = re.compile(r"(user|assistant):")
_LEGACY_SOURCE = "legacy"
_CHUNK_SCAN_SIZE = 500
#: 与索引定义一致：默认维度用不带后缀的向量字段名 text_embedding
DEFAULT_EMBEDDING_DIMENSION = 1024

DEFAULT_EMBED_BATCH = 10
MAX_RETRY_ROUNDS = 3
RETRY_ROUND_INTERVAL_SECONDS = 5

CHECKPOINT_PATH = pathlib.Path(
    os.getenv("MIGRATE_CHECKPOINT_PATH", ".migrate_rag_kb_checkpoint.json")
)
CHECKPOINT_VERSION = 1


# =====================================================================
# 连接（第三方库直连，不使用 app 封装）
# =====================================================================


def _pg_engine():
    from sqlalchemy import create_engine

    url = (
        f"postgresql+psycopg2://{POSTGRES_CONFIG['user']}:{POSTGRES_CONFIG['password']}"
        f"@{POSTGRES_CONFIG['host']}:{POSTGRES_CONFIG['port']}/{POSTGRES_CONFIG['database']}"
    )
    return create_engine(url, pool_pre_ping=True)


def _es_client():
    from elasticsearch import Elasticsearch

    kwargs = dict(ELASTICSEARCH_CONFIG)
    user, password = kwargs.get("basic_auth", ("", ""))
    if not user:
        kwargs.pop("basic_auth", None)
    return Elasticsearch(**kwargs)


# =====================================================================
# 工具
# =====================================================================


def _rag_memory_id(
        end_user_id: str,
        dialog_at: str,
        content: str,
        unique_key: str,
) -> str:
    """迁移 id：在运行时幂等键之外拼入文档内序号，避免同文重复消息被覆盖。"""
    return str(
        uuid.uuid5(
            _RAG_MEMORY_NAMESPACE,
            f"{end_user_id}:{dialog_at}:{content}:{unique_key}",
        )
    )


def _iter_chunks(
        client,
        kb_id: Any,
        document_id: str,
) -> Iterable[tuple[str, list[float] | None]]:
    """按 metadata.sort_id 升序读取一个 document 的全部 chunk，同时带出旧向量。"""
    index = f"vector_index_{kb_id}_node".lower()
    if not client.indices.exists(index=index):
        return
    query = {"term": {"metadata.document_id": document_id}}
    search_after: list[Any] | None = None
    while True:
        body: dict[str, Any] = {
            "query": query,
            "size": _CHUNK_SCAN_SIZE,
            "_source": ["page_content", "vector"],
            "sort": [
                {"metadata.sort_id": {"order": "asc", "unmapped_type": "long",
                                      "missing": "_last"}},
                {"metadata.doc_id": {"order": "asc", "unmapped_type": "keyword",
                                     "missing": "_last"}},
            ],
        }
        if search_after is not None:
            body["search_after"] = search_after
        result = client.search(index=index, **body)
        hits = (result.get("hits") or {}).get("hits", [])
        if not hits:
            return
        for hit in hits:
            source = hit.get("_source") or {}
            text = source.get("page_content")
            if isinstance(text, str) and text.strip():
                vector = source.get("vector")
                yield text, vector if isinstance(vector, list) else None
        if len(hits) < _CHUNK_SCAN_SIZE:
            return
        search_after = hits[-1].get("sort")


def split_messages_by_chunk(chunks: list[tuple[str, list[float] | None]]) -> list[tuple[str, str, list[float] | None]]:
    """逐 chunk 切分：同一 chunk 内的消息共享该 chunk 的旧向量。

    实测旧链路 1 chunk = 1 轮对话（user + assistant），没有消息跨 chunk，
    因此逐 chunk 切分与整体拼接切分的消息数一致，可安全承载向量复用。
    """
    messages: list[tuple[str, str, list[float] | None]] = []
    for text, vector in chunks:
        parts = _ROLE_SPLIT_RE.split(text)
        index = 1
        while index < len(parts) - 1:
            role = parts[index].strip()
            content = parts[index + 1].strip().replace("\\n", "\n")
            if role in ("user", "assistant") and content:
                messages.append((role, content, vector))
            index += 2
    return messages


# =====================================================================
# 断点续传
# =====================================================================


def _checkpoint_context(workspace_id: str | None, end_user_id: str | None) -> dict[str, Any]:
    return {
        "version": CHECKPOINT_VERSION,
        "app_env": APP_ENV,
        "pg": f"{POSTGRES_CONFIG['host']}/{POSTGRES_CONFIG['database']}",
        "rag_index": _RAG_INDEX,
        "workspace_id": workspace_id or "",
        "end_user_id": end_user_id or "",
    }


def _load_checkpoint(expected: dict[str, Any]) -> dict[str, Any]:
    if not CHECKPOINT_PATH.exists():
        return {}
    try:
        data = json.loads(CHECKPOINT_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"[WARN] 断点文件不可读，忽略: {exc}")
        return {}
    if data.get("context") != expected:
        print("[WARN] 断点上下文与本次运行不一致（环境/库/过滤参数变了），已忽略旧断点")
        return {}
    return data


def _save_checkpoint(expected: dict[str, Any], data: dict[str, Any]) -> None:
    payload = {"context": expected, **data}
    CHECKPOINT_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _clear_checkpoint() -> None:
    if CHECKPOINT_PATH.exists():
        CHECKPOINT_PATH.unlink()


# =====================================================================
# 数据加载
# =====================================================================

_LOAD_DOCUMENTS_SQL = """
SELECT d.id, d.created_at, d.file_name, d.kb_id, k.workspace_id
FROM documents d
JOIN knowledges k ON k.id = d.kb_id
WHERE k.permission_id = 'Memory' AND d.status = 1
{extra}
ORDER BY k.id, d.file_name
"""


def _load_documents(engine, workspace_id: str | None, end_user_id: str | None) -> list[dict[str, Any]]:
    from sqlalchemy import text as sql_text

    extra = ""
    params: dict[str, Any] = {}
    if workspace_id:
        extra += " AND k.workspace_id = :ws"
        params["ws"] = workspace_id
    if end_user_id:
        extra += " AND d.file_name = :fname"
        params["fname"] = f"{end_user_id}.txt"

    with engine.connect() as conn:
        rows = conn.execute(sql_text(_LOAD_DOCUMENTS_SQL.format(extra=extra)), params).all()

    documents: list[dict[str, Any]] = []
    for document_id, created_at, file_name, kb_id, ws_id in rows:
        file_name = str(file_name or "")
        user_id = file_name[:-4] if file_name.endswith(".txt") else ""
        if not user_id:
            continue
        try:
            uuid.UUID(user_id)
        except ValueError:
            continue
        documents.append(
            {
                "document_id": str(document_id),
                "created_at": created_at,
                "kb_id": str(kb_id),
                "end_user_id": user_id,
                "workspace_id": str(ws_id),
            }
        )
    return documents


def _load_embedding_binding(engine, workspace_ids: list[str]) -> dict[str, dict[str, str]]:
    """返回 {workspace_id: {config_id, embedding_id, tenant_id}}（取该空间首个有效 embedding 配置）。"""
    from sqlalchemy import text as sql_text

    if not workspace_ids:
        return {}
    sql = sql_text(
        "SELECT config_id, embedding_id, tenant_id FROM memory_config "
        "WHERE workspace_id = :ws AND embedding_id IS NOT NULL LIMIT 1"
    )
    result: dict[str, dict[str, str]] = {}
    with engine.connect() as conn:
        for ws in workspace_ids:
            row = conn.execute(sql, {"ws": ws}).fetchone()
            if row is None:
                continue
            result[ws] = {
                "config_id": str(row[0]),
                "embedding_id": str(row[1]),
                "tenant_id": str(row[2]),
            }
    return result


# =====================================================================
# 向量化（自写 HTTP，不 import app）
# =====================================================================


def _invoke_embedding(texts: list[str], *, embedding_id: str, tenant_id: str,
                      actor_id: str) -> list[list[float]]:
    """POST /internal/v1/invoke，返回与 texts 等长的向量列表。"""
    import httpx

    payload = {
        "config_id": embedding_id,
        "stream": False,
        "type": "embedding",
        "params": {"input": texts},
    }
    headers = {
        "Content-Type": "application/json",
        "X-Model-Actor-ID": actor_id,
        "X-Model-Tenant-ID": tenant_id,
        "X-Model-Source": "migrate_script",
    }
    url = f"{MODEL_SERVICE_BASE_URL}/internal/v1/invoke"
    with httpx.Client(timeout=120.0) as client:
        response = client.post(url, json=payload, headers=headers)

    if response.status_code != 200:
        raise RuntimeError(
            f"invoke 失败 status={response.status_code} body={response.text[:300]}"
        )
    data = response.json()
    vectors = data.get("vectors") if isinstance(data, dict) else None
    if not isinstance(vectors, list) or len(vectors) != len(texts):
        raise RuntimeError(
            f"向量条数不符：期望 {len(texts)}，实际 "
            f"{len(vectors) if isinstance(vectors, list) else 'N/A'}"
        )
    return [list(vector) for vector in vectors]


def _embed_texts(texts: list[str], *, embedding_id: str, tenant_id: str,
                 actor_id: str, batch_size: int = DEFAULT_EMBED_BATCH) -> tuple[list[list[float]], int]:
    """按批向量化，返回 (vectors, dimension)；条数与维度不一致即失败。"""
    vectors: list[list[float]] = []
    dimension = 0
    for start in range(0, len(texts), batch_size):
        batch = texts[start:start + batch_size]
        batch_vectors = _invoke_embedding(
            batch, embedding_id=embedding_id, tenant_id=tenant_id, actor_id=actor_id
        )
        if len(batch_vectors) != len(batch):
            raise RuntimeError("批量向量条数不一致")
        for vector in batch_vectors:
            if dimension == 0:
                dimension = len(vector)
            elif len(vector) != dimension:
                raise RuntimeError("批量向量维度不一致")
            vectors.append(vector)
    return vectors, dimension


# =====================================================================
# 主流程
# =====================================================================


@dataclass
class MigrationStats:
    documents: int = 0
    chunks: int = 0
    messages: int = 0
    written: int = 0
    skipped_empty: int = 0
    failures: int = 0
    reused_vectors: int = 0
    reembedded_users: int = 0
    reembedded_nodes: int = 0
    purged_kbs: int = 0
    per_user: dict[str, int] = field(default_factory=dict)
    per_kb: dict[str, dict[str, int]] = field(default_factory=dict)


def _bulk_index(client, actions: list[dict[str, Any]]) -> tuple[int, int]:
    """写 ES，失败按轮重试；返回 (成功条数, 失败条数)。"""
    from elasticsearch import helpers

    last_errors = 0
    for attempt in range(1, MAX_RETRY_ROUNDS + 1):
        try:
            success, errors = helpers.bulk(
                client, actions, refresh=False, raise_on_error=False
            )
            if not errors:
                return int(success), 0
            last_errors = len(errors)
            print(f"[WARN] bulk 第 {attempt} 轮有 {last_errors} 条失败，重试中")
        except Exception as exc:
            last_errors = len(actions)
            print(f"[WARN] bulk 第 {attempt} 轮异常: {exc}")
        if attempt < MAX_RETRY_ROUNDS:
            time.sleep(RETRY_ROUND_INTERVAL_SECONDS)
    return 0, last_errors


def migrate(
        *,
        engine,
        client,
        documents: list[dict[str, Any]],
        dry_run: bool,
        skip_documents: set[str],
        stats: MigrationStats,
        reuse_vectors: bool,
) -> None:
    for doc in documents:
        document_id = doc["document_id"]
        if document_id in skip_documents:
            continue

        stats.documents += 1
        kb_id = doc["kb_id"]
        user_id = doc["end_user_id"]
        created_at = doc["created_at"]

        chunks = list(_iter_chunks(client, kb_id, document_id))
        stats.chunks += len(chunks)
        messages = split_messages_by_chunk(chunks)
        if not messages:
            stats.skipped_empty += 1
            continue

        per_kb = stats.per_kb.setdefault(kb_id, {"documents": 0, "written": 0, "failed": 0})
        per_kb["documents"] += 1

        actions: list[dict[str, Any]] = []
        for ordinal, (role, content, vector) in enumerate(messages):
            # 旧文档只有创建时间：按文档内序号加毫秒，保证同文档消息有稳定先后
            message_dt = (
                created_at + timedelta(milliseconds=ordinal)
                if created_at is not None
                else None
            )
            dialog_at = message_dt.isoformat() if message_dt is not None else ""
            memory_id = _rag_memory_id(
                user_id, dialog_at, content, f"{document_id}:{ordinal}"
            )
            stats.messages += 1
            stats.per_user[user_id] = stats.per_user.get(user_id, 0) + 1

            source: dict[str, Any] = {
                "id": memory_id,
                "role": role,
                "content": content,
                "end_user_id": user_id,
                "dialog_at": dialog_at,
                "source": _LEGACY_SOURCE,
            }
            if reuse_vectors and vector:
                source["text_embedding"] = vector
                stats.reused_vectors += 1
            actions.append(
                {
                    "_op_type": "index",
                    "_index": _RAG_INDEX,
                    "_id": memory_id,
                    "_source": source,
                }
            )

        if dry_run:
            continue

        success, failed = _bulk_index(client, actions)
        stats.written += success
        per_kb["written"] += success
        if failed:
            stats.failures += failed
            per_kb["failed"] += failed
            print(f"[ERROR] document={document_id} 写入失败 {failed} 条")


def _scan_pending(client, end_user_id: str) -> list[tuple[str, str]]:
    """按 search_after 分页扫该 end_user 在新索引里的全部消息（id + content）。"""
    page_size = 500
    body: dict[str, Any] = {
        "query": {"term": {"end_user_id": end_user_id}},
        "size": page_size,
        "_source": ["id", "content"],
        "sort": [{"id": {"order": "asc", "unmapped_type": "keyword"}}],
    }
    out: list[tuple[str, str]] = []
    search_after: list[Any] | None = None
    while True:
        if search_after is not None:
            body["search_after"] = search_after
        result = client.search(index=_RAG_INDEX, **body)
        hits = (result.get("hits") or {}).get("hits", [])
        if not hits:
            return out
        for hit in hits:
            source = hit.get("_source") or {}
            node_id = source.get("id") or hit.get("_id")
            content = source.get("content")
            if node_id and isinstance(content, str) and content.strip():
                out.append((str(node_id), content))
        if len(hits) < page_size:
            return out
        search_after = hits[-1].get("sort")


def reembed(
        *,
        client,
        end_users: list[str],
        bindings: dict[str, dict[str, str]],
        user_workspace: dict[str, str],
        stats: MigrationStats,
        reuse_vectors: bool,
) -> None:
    """按 end_user 补算向量（仅当未复用旧向量时执行）。"""
    if reuse_vectors:
        print("已复用旧 chunk 向量，跳过 reembed")
        return

    for end_user_id in end_users:
        ws_id = user_workspace.get(end_user_id, "")
        binding = bindings.get(ws_id)
        if not binding:
            print(f"[WARN] end_user={end_user_id} 所属空间没有可用 embedding 配置，跳过")
            continue

        pending = _scan_pending(client, end_user_id)
        if not pending:
            continue

        try:
            vectors, dimension = _embed_texts(
                [text for _, text in pending],
                embedding_id=binding["embedding_id"],
                tenant_id=binding["tenant_id"],
                actor_id=end_user_id,
            )
        except Exception as exc:
            print(f"[ERROR] end_user={end_user_id} 向量化失败: {exc}")
            stats.failures += 1
            continue

        # 与索引 mapping 对齐：默认维度用不带后缀的字段名
        field = (
            "text_embedding"
            if dimension == DEFAULT_EMBEDDING_DIMENSION
            else f"text_embedding_{dimension}"
        )
        body = "".join(
            json.dumps({
                "update": {"_index": _RAG_INDEX, "_id": node_id},
                "_source": {"doc": {field: vector}},
            }, ensure_ascii=False) + "\n"
            for (node_id, _), vector in zip(pending, vectors)
        )
        try:
            resp = client.bulk(body=body, refresh=False)
            if resp.get("errors"):
                failed = sum(1 for item in resp.get("items", []) if item.get("error"))
                print(f"[WARN] end_user={end_user_id} 向量写回失败 {failed} 条")
                stats.failures += failed
            else:
                stats.reembedded_users += 1
                stats.reembedded_nodes += len(pending)
                print(f"[OK] end_user={end_user_id} 补向量 {len(pending)} 条 dim={dimension}")
        except Exception as exc:
            print(f"[ERROR] end_user={end_user_id} 向量写回异常: {exc}")
            stats.failures += 1


def purge(
        *,
        engine,
        client,
        stats: MigrationStats,
        dry_run: bool,
) -> None:
    """删除迁移已完成的旧记忆库（三层：knowledges / documents / 旧 chunk 索引）。"""
    from sqlalchemy import text as sql_text

    candidates = [
        kb_id for kb_id, v in stats.per_kb.items()
        if v.get("documents", 0) > 0
        and v.get("failed", 0) == 0
        and (dry_run or v.get("written", 0) > 0)
    ]
    if not candidates:
        print("没有可清理的 KB（无成功迁移的文档）")
        return

    for kb_id in candidates:
        if dry_run:
            print(f"[DRY] 将清理 KB {kb_id}（status=2 + 删 documents + 删 chunk 索引）")
            stats.purged_kbs += 1
            continue
        try:
            with engine.begin() as conn:
                conn.execute(
                    sql_text("UPDATE knowledges SET status = 2 WHERE id = :k"),
                    {"k": kb_id},
                )
                conn.execute(
                    sql_text("DELETE FROM documents WHERE kb_id = :k"),
                    {"k": kb_id},
                )
            index = f"vector_index_{kb_id}_node".lower()
            if client.indices.exists(index=index):
                client.indices.delete(index=index)
            stats.purged_kbs += 1
            print(f"[OK] 已清理 KB {kb_id}")
        except Exception as exc:
            print(f"[ERROR] 清理 KB {kb_id} 失败: {exc}")
            stats.failures += 1


def run(
        *,
        workspace_id: str | None,
        end_user_id: str | None,
        dry_run: bool,
        reuse_vectors: bool,
        do_purge: bool,
) -> MigrationStats:
    _validate_connection_config()
    _print_target_config()

    stats = MigrationStats()
    engine = _pg_engine()
    client = _es_client()
    context = _checkpoint_context(workspace_id, end_user_id)
    checkpoint = _load_checkpoint(context)

    try:
        documents = _load_documents(engine, workspace_id, end_user_id)
        if not documents:
            print("未找到 permission_id='Memory' 的文档，无需迁移")
            return stats

        skip_documents = set(checkpoint.get("done_documents", []))
        if skip_documents:
            print(f"断点续传：跳过已完成 {len(skip_documents)} 个 document")

        # ---------- ① 迁移写正文（可选复用旧向量）----------
        migrate(
            engine=engine,
            client=client,
            documents=documents,
            dry_run=dry_run,
            skip_documents=skip_documents,
            stats=stats,
            reuse_vectors=reuse_vectors,
        )
        if not dry_run:
            client.indices.refresh(index=_RAG_INDEX)
            done = sorted(skip_documents | {
                d["document_id"] for d in documents
                if d["document_id"] not in skip_documents
            })
            _save_checkpoint(context, {
                "done_documents": done,
                "reembedded_users": checkpoint.get("reembedded_users", []),
                "purged_kbs": checkpoint.get("purged_kbs", []),
            })

        # ---------- ② 补算向量 ----------
        if not dry_run and not reuse_vectors:
            ws_ids = sorted({d["workspace_id"] for d in documents})
            bindings = _load_embedding_binding(engine, ws_ids)
            user_workspace = {d["end_user_id"]: d["workspace_id"] for d in documents}
            done_users = set(checkpoint.get("reembedded_users", []))
            targets = [u for u in stats.per_user if u not in done_users]
            reembed(
                client=client,
                end_users=targets,
                bindings=bindings,
                user_workspace=user_workspace,
                stats=stats,
                reuse_vectors=reuse_vectors,
            )
            if not dry_run:
                client.indices.refresh(index=_RAG_INDEX)
                _save_checkpoint(context, {
                    "done_documents": sorted(skip_documents | {
                        d["document_id"] for d in documents
                    }),
                    "reembedded_users": sorted(done_users | set(stats.per_user)),
                    "purged_kbs": checkpoint.get("purged_kbs", []),
                })

        # ---------- ③ 清理旧库 ----------
        if do_purge:
            purge(engine=engine, client=client, stats=stats, dry_run=dry_run)

        if not dry_run and stats.failures == 0:
            _clear_checkpoint()

    finally:
        client.close()
        engine.dispose()

    print("迁移完成" + ("（dry-run，未写入）" if dry_run else ""))
    print(
        f"documents={stats.documents} chunks={stats.chunks} messages={stats.messages} "
        f"written={stats.written} skipped_empty={stats.skipped_empty} "
        f"failures={stats.failures} reused_vectors={stats.reused_vectors}"
    )
    if stats.reembedded_users:
        print(
            f"reembedded_users={stats.reembedded_users} "
            f"reembedded_nodes={stats.reembedded_nodes}"
        )
    if stats.purged_kbs:
        print(f"purged_kbs={stats.purged_kbs}")
    for kb_id, v in sorted(stats.per_kb.items()):
        print(
            f"  kb={kb_id} documents={v['documents']} "
            f"written={v['written']} failed={v['failed']}"
        )
    return stats


def main() -> int:
    parser = argparse.ArgumentParser(
        description="把旧知识库中的 RAG 记忆迁移到独立索引 rag_memory_current"
    )
    parser.add_argument("--workspace-id", help="只迁移指定工作空间")
    parser.add_argument("--end-user-id", help="只迁移指定宿主")
    parser.add_argument("--dry-run", action="store_true", help="只统计，不写 ES / 不改库")
    parser.add_argument(
        "--reembed",
        action="store_true",
        help="不复用旧 chunk 向量，改为按当前 embedding 模型重新计算",
    )
    parser.add_argument(
        "--purge",
        action="store_true",
        help="迁移成功的 KB 一并清理（status=2 + 删 documents + 删 chunk 索引），不可回滚",
    )
    args = parser.parse_args()

    if args.purge and args.dry_run:
        print("[INFO] --purge 与 --dry-run 同时开启：只打印将要清理的 KB，不执行")

    start = time.time()
    stats = run(
        workspace_id=args.workspace_id,
        end_user_id=args.end_user_id,
        dry_run=args.dry_run,
        reuse_vectors=not args.reembed,
        do_purge=args.purge,
    )
    print(f"elapsed={time.time() - start:.2f}s")
    return 0 if stats.failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
