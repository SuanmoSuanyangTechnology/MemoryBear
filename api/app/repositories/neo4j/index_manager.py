# -*- coding: utf-8 -*-
"""Neo4j 索引管理模块

负责检查和创建 Neo4j 全文索引与向量索引。
支持多环境（通过 .env 中的 NEO4J_URI/USERNAME/PASSWORD 区分）。

用法：
    # 作为模块调用（应用启动时）
    from app.repositories.neo4j.index_manager import ensure_indexes
    await ensure_indexes()

    # 作为独立脚本执行（手动建索引）
    python -m app.repositories.neo4j.index_manager
"""

import asyncio
import logging
from dataclasses import dataclass
from typing import List

from app.core.config import settings
from app.repositories.neo4j.neo4j_connector import Neo4jConnector

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────
# 索引定义表
# ─────────────────────────────────────────────────────────────

@dataclass
class FulltextIndexDef:
    name: str
    label: str
    properties: List[str]


@dataclass
class VectorIndexDef:
    name: str
    label: str
    property: str
    dimensions: int
    similarity: str = "cosine"


# 全文索引清单（现有 + 新增 communities）
FULLTEXT_INDEXES: List[FulltextIndexDef] = [
    FulltextIndexDef("statementsFulltext",  "Statement",      ["statement"]),
    FulltextIndexDef("entitiesFulltext",    "ExtractedEntity", ["name"]),
    FulltextIndexDef("chunksFulltext",      "Chunk",          ["content"]),
    FulltextIndexDef("summariesFulltext",   "MemorySummary",  ["content"]),
    FulltextIndexDef("communitiesFulltext", "Community",      ["name", "summary"]),  # 第五检索源
]

# 向量索引清单（预留 community 二期）
VECTOR_INDEXES: List[VectorIndexDef] = [
    VectorIndexDef("statement_embedding_index", "Statement",      "statement_embedding", 1536),
    VectorIndexDef("chunk_embedding_index",     "Chunk",          "chunk_embedding",     1536),
    VectorIndexDef("entity_embedding_index",    "ExtractedEntity","name_embedding",      1536),
    VectorIndexDef("summary_embedding_index",   "MemorySummary",  "summary_embedding",   1536),
    # 二期：社区向量索引
    VectorIndexDef("community_summary_embedding_index", "Community", "summary_embedding", 1536),
]


# ─────────────────────────────────────────────────────────────
# 核心检查 / 创建逻辑
# ─────────────────────────────────────────────────────────────

async def _get_existing_indexes(connector: Neo4jConnector) -> set:
    """查询 Neo4j 中已存在的索引名称集合"""
    rows = await connector.execute_query("SHOW INDEXES YIELD name RETURN name")
    return {row["name"] for row in rows}


async def _ensure_fulltext_index(
    connector: Neo4jConnector,
    idx: FulltextIndexDef,
    existing: set,
) -> str:
    """检查并按需创建全文索引，返回操作状态描述"""
    if idx.name in existing:
        return f"[SKIP]   全文索引已存在: {idx.name}"

    props = ", ".join(f"n.{p}" for p in idx.properties)
    cypher = (
        f'CREATE FULLTEXT INDEX {idx.name} IF NOT EXISTS '
        f'FOR (n:{idx.label}) ON EACH [{props}]'
    )
    await connector.execute_query(cypher)
    return f"[CREATE] 全文索引已创建: {idx.name}  ({idx.label} → {idx.properties})"


async def _ensure_vector_index(
    connector: Neo4jConnector,
    idx: VectorIndexDef,
    existing: set,
) -> str:
    """检查并按需创建向量索引，返回操作状态描述"""
    if idx.name in existing:
        return f"[SKIP]   向量索引已存在: {idx.name}"

    cypher = (
        f"CREATE VECTOR INDEX {idx.name} IF NOT EXISTS "
        f"FOR (n:{idx.label}) ON n.{idx.property} "
        f"OPTIONS {{indexConfig: {{"
        f"`vector.dimensions`: {idx.dimensions}, "
        f"`vector.similarity_function`: '{idx.similarity}'"
        f"}}}}"
    )
    await connector.execute_query(cypher)
    return (
        f"[CREATE] 向量索引已创建: {idx.name}  "
        f"({idx.label}.{idx.property}, dim={idx.dimensions})"
    )


async def ensure_indexes(connector: Neo4jConnector | None = None) -> dict:
    """
    检查并创建所有必要的 Neo4j 索引（幂等，可重复调用）。

    Args:
        connector: 可选，传入已有连接器；为 None 时自动创建。

    Returns:
        dict: {
            "uri": 当前连接的 Neo4j URI,
            "fulltext": [操作日志列表],
            "vector":   [操作日志列表],
            "errors":   [错误信息列表],
        }
    """
    own_connector = connector is None
    if own_connector:
        connector = Neo4jConnector()

    report = {
        "uri": settings.NEO4J_URI,
        "fulltext": [],
        "vector": [],
        "errors": [],
    }

    try:
        # 一次性拉取所有已有索引名
        existing = await _get_existing_indexes(connector)
        logger.info(f"[IndexManager] 当前环境: {settings.NEO4J_URI}")
        logger.info(f"[IndexManager] 已有索引数量: {len(existing)}")

        # 处理全文索引
        for idx in FULLTEXT_INDEXES:
            try:
                msg = await _ensure_fulltext_index(connector, idx, existing)
                report["fulltext"].append(msg)
                logger.info(f"[IndexManager] {msg}")
            except Exception as e:
                err = f"[ERROR]  全文索引 {idx.name} 创建失败: {e}"
                report["errors"].append(err)
                logger.error(f"[IndexManager] {err}")

        # 处理向量索引
        for idx in VECTOR_INDEXES:
            try:
                msg = await _ensure_vector_index(connector, idx, existing)
                report["vector"].append(msg)
                logger.info(f"[IndexManager] {msg}")
            except Exception as e:
                err = f"[ERROR]  向量索引 {idx.name} 创建失败: {e}"
                report["errors"].append(err)
                logger.error(f"[IndexManager] {err}")

    finally:
        if own_connector:
            await connector.close()

    return report


async def check_indexes(connector: Neo4jConnector | None = None) -> dict:
    """
    仅检查索引状态，不创建任何索引。

    Returns:
        dict: {
            "uri": ...,
            "present":  [已存在的索引名],
            "missing_fulltext": [缺失的全文索引名],
            "missing_vector":   [缺失的向量索引名],
        }
    """
    own_connector = connector is None
    if own_connector:
        connector = Neo4jConnector()

    try:
        existing = await _get_existing_indexes(connector)
        missing_ft = [i.name for i in FULLTEXT_INDEXES if i.name not in existing]
        missing_vec = [i.name for i in VECTOR_INDEXES if i.name not in existing]

        return {
            "uri": settings.NEO4J_URI,
            "present": sorted(existing),
            "missing_fulltext": missing_ft,
            "missing_vector": missing_vec,
        }
    finally:
        if own_connector:
            await connector.close()


# ─────────────────────────────────────────────────────────────
# 独立脚本入口
# ─────────────────────────────────────────────────────────────

async def _main():
    import sys

    print(f"\n{'='*60}")
    print(f"Neo4j 索引管理工具")
    print(f"环境: {settings.NEO4J_URI}")
    print(f"{'='*60}\n")

    # 先检查
    print(">>> 检查当前索引状态...\n")
    status = await check_indexes()
    print(f"  已存在索引数: {len(status['present'])}")
    if status["missing_fulltext"]:
        print(f"  缺失全文索引: {status['missing_fulltext']}")
    if status["missing_vector"]:
        print(f"  缺失向量索引: {status['missing_vector']}")

    if not status["missing_fulltext"] and not status["missing_vector"]:
        print("\n  所有索引均已存在，无需操作。")
        return

    # 再创建
    print("\n>>> 开始创建缺失索引...\n")
    report = await ensure_indexes()

    for msg in report["fulltext"] + report["vector"]:
        print(f"  {msg}")

    if report["errors"]:
        print("\n[!] 以下索引创建失败：")
        for err in report["errors"]:
            print(f"  {err}")
        sys.exit(1)
    else:
        print("\n  全部索引处理完成。")


if __name__ == "__main__":
    asyncio.run(_main())
