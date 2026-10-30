#!/usr/bin/env python3
"""把旧知识库中的 RAG 记忆迁移到独立索引 ``rag_memory_current``。

旧链路把每个宿主的历史消息写成 ``documents`` 表里的 ``{end_user_id}.txt``，
再切分为知识库 chunk。本脚本按 document_id 顺序拼接 chunk 文本，复用旧展示层
的角色切分规则还原消息，逐条写入独立索引，标记 ``source="legacy"``。

幂等：消息 id = uuid5（命名空间 + end_user_id + dialog_at + content + 文档内序号），
重复执行覆盖同一 ES 文档，不会产生重复数据；同文重复消息也不会互相覆盖。
对话时间取自 document.created_at，重跑时稳定，避免重放产生重复记忆。

向量：仅复用旧 chunk 的向量（旧 chunk 与消息为 1:N，同一 chunk 内的消息共享该向量）。
本脚本不发起任何外部模型请求；需要按当前模型重算向量时走平台的重嵌入任务
（``/workspace_reembed``），那里有配额与续跑管理。

清理（仅 ``--purge --yes`` 显式开启时执行，且逐 KB 校验通过才删）：
- ``knowledges.status = 2``（列表不再显示）
- 删除该 KB 下 ``documents`` 记录
- 删除旧 chunk 索引 ``vector_index_{kb_id}_node``

独立运行：PostgreSQL / Elasticsearch 连接信息全部从环境变量读取，
不读取项目 .env，也不 import app 包。

Usage::

    python scripts/migrate_rag_kb_to_index.py --dry-run
    python scripts/migrate_rag_kb_to_index.py --purge --dry-run
    python scripts/migrate_rag_kb_to_index.py
    python scripts/migrate_rag_kb_to_index.py --purge --yes
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


_MISSING_ENV: list[str] = []


def _required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        _MISSING_ENV.append(name)
    return value


def _validate_env() -> None:
    """运行期校验环境变量，避免导入模块（--help / pytest / IDE）时直接崩溃。"""
    if _MISSING_ENV:
        raise ValueError(
            "缺少必填环境变量: " + ", ".join(sorted(set(_MISSING_ENV)))
        )
    if APP_ENV not in {"test", "pre", "prod"}:
        raise ValueError("APP_ENV 必须是 test、pre 或 prod")


APP_ENV = _required_env("APP_ENV").lower()

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
    print(f"目标索引         : {_RAG_INDEX}")
    print(f"断点文件         : {CHECKPOINT_PATH}")
    print("=" * 72)


# =====================================================================
# 常量
# =====================================================================

#: 与 api/app/core/memory/pipelines/dispatcher.py 的 _RAG_MEMORY_NAMESPACE 共用
#: 同一命名空间，但 key 结构刻意不同：在线链路是
#: ``end_user_id:dialog_at:role:content``，本脚本是
#: ``end_user_id:dialog_at:content:document_id:序号``——额外拼序号是为了
#: 保留旧数据中的同文重复消息（避免互相覆盖），且旧数据没有可精确还原的
#: 原始 dialog_at。因此 legacy 文档与在线写入永远不会互相覆盖：脚本重跑
#: 的幂等由本 key 结构保证；同一条旧消息若再经在线链路写入会另存一份
#: （source=legacy 与正常记录并存），属已知取舍
_RAG_MEMORY_NAMESPACE = uuid.UUID("8f3f1a52-6c1e-5b7a-9d4e-2a7c6b1f0e93")
_RAG_INDEX = "rag_memory_current"
#: 锚定行首切分：旧 chunk 为一轮对话（user + assistant 两条算一条 chunk），
#: 轮次标记在行首、正文内换行存为字面量 \n（切分后还原）。正文里出现的
#: "user:" / "assistant:"（引用对话、代码、URL 等）不锚定行首会被误拆
_ROLE_SPLIT_RE = re.compile(r"^(user|assistant):", re.M)
_LEGACY_SOURCE = "legacy"
_CHUNK_SCAN_SIZE = 500

#: 与平台侧 ``definitions.py`` 的维度注册保持一致（本脚本离线运行、不 import app 包）：
#: 默认维度写无后缀字段 ``text_embedding``，其它受支持维度写
#: ``text_embedding_{dim}``。复用的旧向量必须按自身长度选字段——维度与目标
#: 字段不匹配会让整条 bulk 被 ES 拒绝，该文档重试到耗尽也迁不过去，
#: 进而永久阻塞其所属 KB 的 purge 校验
_DEFAULT_EMBEDDING_DIMENSION = 1024
_SUPPORTED_EMBEDDING_DIMENSIONS = (768, 1024, 1536, 2048, 3072, 4096)
_TEXT_EMBEDDING_FIELD = "text_embedding"


def _embedding_field_for(dimension: int) -> str | None:
    """按向量维度返回目标字段名；维度未注册时返回 None（该向量不写入）。"""
    if dimension == _DEFAULT_EMBEDDING_DIMENSION:
        return _TEXT_EMBEDDING_FIELD
    if dimension not in _SUPPORTED_EMBEDDING_DIMENSIONS:
        return None
    return f"{_TEXT_EMBEDDING_FIELD}_{dimension}"


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

    # extra 只由本函数内的固定字面量拼接（不含任何外部输入），参数值统一走
    # params 绑定；二者必须成对传给 execute，否则筛选条件不会生效/缺参会直接报错
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
    purged_kbs: int = 0
    per_kb: dict[str, dict[str, int]] = field(default_factory=dict)


def _bulk_index(client, actions: list[dict[str, Any]]) -> tuple[int, int]:
    """写 ES，失败按轮重试；返回 (成功条数, 失败条数)。"""
    from elasticsearch import helpers

    last_errors = 0
    last_success = 0
    for attempt in range(1, MAX_RETRY_ROUNDS + 1):
        try:
            success, errors = helpers.bulk(
                client, actions, refresh=False, raise_on_error=False
            )
            # 每轮都整体重放 actions（_op_type=index 幂等覆盖写），
            # 最后一轮的 success 就是当前真实已写入条数，取它而非跨轮累加
            last_success = int(success)
            if not errors:
                return last_success, 0
            last_errors = len(errors)
            print(f"[WARN] bulk 第 {attempt} 轮有 {last_errors} 条失败，重试中")
        except Exception as exc:
            last_errors = len(actions)
            # 本轮整批结果不可知：置 0 从严计数（宁少勿多，purge 以 written
            # 判定，少计只会推迟清理、不会误放行）
            last_success = 0
            print(f"[WARN] bulk 第 {attempt} 轮异常: {exc}")
        if attempt < MAX_RETRY_ROUNDS:
            time.sleep(RETRY_ROUND_INTERVAL_SECONDS)
    return last_success, last_errors


def migrate(
        *,
        client,
        documents: list[dict[str, Any]],
        dry_run: bool,
        skip_documents: set[str],
        stats: MigrationStats,
) -> set[str]:
    """迁移文档正文，返回本次「真正完整写入」的 document_id 集合。

    解析失败、缺 created_at、bulk 部分失败的文档都不计入返回值；断点只应
    记录成功集合，否则重跑时这些文档会被跳过且永久不再重试。
    """
    completed: set[str] = set()
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

        per_kb = stats.per_kb.setdefault(
            kb_id, {"documents": 0, "messages": 0, "written": 0, "failed": 0}
        )
        per_kb["documents"] += 1

        if not messages:
            # 解析不出消息同样计入失败，否则该 KB 会被 purge 误判为「全部迁移成功」
            stats.skipped_empty += 1
            per_kb["failed"] += 1
            stats.failures += 1
            continue

        if created_at is None:
            # dialog_at 在索引里是 date 类型，写空串会让整条 bulk 失败；
            # 缺创建时间时也无法确定消息先后，跳过并计入失败，避免后续 purge 误删
            print(f"[WARN] document 缺少 created_at，跳过: document_id={document_id}")
            per_kb["failed"] += 1
            stats.failures += 1
            continue

        actions: list[dict[str, Any]] = []
        unsupported_dim_warned = False
        for ordinal, (role, content, vector) in enumerate(messages):
            # 旧文档只有创建时间：按文档内序号加毫秒，保证同文档消息有稳定先后
            message_dt = created_at + timedelta(milliseconds=ordinal)
            dialog_at = message_dt.isoformat()
            memory_id = _rag_memory_id(
                user_id, dialog_at, content, f"{document_id}:{ordinal}"
            )
            stats.messages += 1
            per_kb["messages"] += 1

            source: dict[str, Any] = {
                "id": memory_id,
                "role": role,
                "content": content,
                "end_user_id": user_id,
                "dialog_at": dialog_at,
                "source": _LEGACY_SOURCE,
            }
            if vector:
                # 复用旧 chunk 向量：同一 chunk 内的消息共享该向量。
                # 字段名按向量自身维度解析：旧库模型维度未必是默认 1024，
                # 写死 text_embedding 会因 dims 不匹配被 ES 拒绝整条 action
                vector_field = _embedding_field_for(len(vector))
                if vector_field is None:
                    # 维度未在索引注册：留空由重嵌入任务按当前模型补齐，
                    # 比让整条文档反复写入失败、永久阻塞 purge 更可控
                    if not unsupported_dim_warned:
                        print(
                            f"[WARN] document={document_id} 存在未注册的向量维度 "
                            f"{len(vector)}，已跳过向量（留待重嵌入）"
                        )
                        unsupported_dim_warned = True
                else:
                    source[vector_field] = vector
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
        else:
            completed.add(document_id)
    return completed


def purge(
        *,
        engine,
        client,
        stats: MigrationStats,
        documents: list[dict[str, Any]],
        done_documents: set[str],
        dry_run: bool,
) -> None:
    """删除迁移已完成的旧记忆库（三层：chunk 索引 / documents / knowledges）。

    逐 KB 校验：该 KB 本次加载到的**每个** document 都必须已完整写入
    （``done_documents`` 含本次成功与历史断点成功的文档），且本次写入条数
    与解析出的消息数一致，才允许清理。断点收尾轮（该 KB 全部文档都已在前轮
    成功、本轮被 skip_documents 跳过、per_kb 无条目）仅按 ``all_done`` 判定，
    否则已完整迁移的 KB 永远无法通过清理校验。
    """
    from sqlalchemy import text as sql_text

    docs_by_kb: dict[str, list[str]] = {}
    for doc in documents:
        docs_by_kb.setdefault(doc["kb_id"], []).append(doc["document_id"])

    purged = 0
    for kb_id, doc_ids in docs_by_kb.items():
        v = stats.per_kb.get(kb_id)
        if v is not None and (v.get("documents", 0) <= 0 or v.get("failed", 0) != 0):
            continue

        if dry_run:
            # dry-run 下 written 恒为 0，不能作为判定依据；这里只按解析结果预览，
            # 真正的清理判定与 dry_run 完全解耦
            if v is not None and v.get("messages", 0) > 0:
                print(f"[DRY] 将清理 KB {kb_id}（status=2 + 删 documents + 删 chunk 索引）")
            continue

        all_done = all(document_id in done_documents for document_id in doc_ids)
        # written_ok 只对本轮实际处理的文档有意义；断点收尾轮（v is None）
        # 没有可比数据，仅按 all_done 判定
        written_ok = (
            v is None
            or v.get("written", 0) == v.get("messages", 0)
        )
        if not all_done or not written_ok:
            print(
                f"[WARN] 跳过 KB {kb_id}: 未全部迁移成功 "
                f"(documents={len(doc_ids)}, all_done={all_done}, "
                f"written={v.get('written', 0)}/{v.get('messages', 0)}, "
                f"failed={v.get('failed', 0)})"
            )
            continue

        try:
            # 先删 ES 索引、成功后再提交 PG：反过来一旦 ES 删除失败，
            # documents 已删导致重跑查不到该 KB，旧 chunk 索引将永久残留
            index = f"vector_index_{kb_id}_node".lower()
            if client.indices.exists(index=index):
                client.indices.delete(index=index)
            with engine.begin() as conn:
                conn.execute(
                    sql_text("UPDATE knowledges SET status = 2 WHERE id = :k"),
                    {"k": kb_id},
                )
                conn.execute(
                    sql_text("DELETE FROM documents WHERE kb_id = :k"),
                    {"k": kb_id},
                )
            purged += 1
            print(f"[OK] 已清理 KB {kb_id}")
        except Exception as exc:
            print(f"[ERROR] 清理 KB {kb_id} 失败: {exc}")
            stats.failures += 1
    stats.purged_kbs += purged


def run(
        *,
        workspace_id: str | None,
        end_user_id: str | None,
        dry_run: bool,
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

        # ---------- ① 迁移写正文（复用旧 chunk 向量）----------
        completed = migrate(
            client=client,
            documents=documents,
            dry_run=dry_run,
            skip_documents=skip_documents,
            stats=stats,
        )
        done_documents = set(skip_documents)
        if not dry_run:
            client.indices.refresh(index=_RAG_INDEX)
            # 断点只记录真正完整写入的文档：解析失败/缺时间/部分失败的文档
            # 必须留在待迁移集合里，否则重跑会被跳过且永久不再重试
            done_documents |= completed
            _save_checkpoint(context, {
                "done_documents": sorted(done_documents),
            })

        # ---------- ② 清理旧库 ----------
        if do_purge:
            purge(
                engine=engine,
                client=client,
                stats=stats,
                documents=documents,
                done_documents=done_documents,
                dry_run=dry_run,
            )

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
        "--purge",
        action="store_true",
        help="迁移成功的 KB 一并清理（status=2 + 删 documents + 删 chunk 索引），不可回滚",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="配合 --purge 使用，确认执行不可回滚的删除操作",
    )
    args = parser.parse_args()

    _validate_env()

    if args.purge and not args.yes:
        print("[ERROR] --purge 会删除 documents 与 chunk 索引且不可回滚，需同时传 --yes 确认")
        return 2

    if args.purge and args.dry_run:
        print("[INFO] --purge 与 --dry-run 同时开启：只打印将要清理的 KB，不执行")

    start = time.time()
    stats = run(
        workspace_id=args.workspace_id,
        end_user_id=args.end_user_id,
        dry_run=args.dry_run,
        do_purge=args.purge,
    )
    print(f"elapsed={time.time() - start:.2f}s")
    return 0 if stats.failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
