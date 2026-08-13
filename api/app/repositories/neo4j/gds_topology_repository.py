from __future__ import annotations

import logging
from typing import Any, Dict

from app.repositories.neo4j.cypher_queries import (
    GDS_GARPH_BUILD,
    G_SCORE,
    CLEAR_GRAPH,
)
from app.repositories.neo4j.neo4j_connector import Neo4jConnector

logger = logging.getLogger(__name__)


async def compute_topology_score(end_user_id: str) -> Dict[str, Any]:
    connector = Neo4jConnector()
    try:
        await connector.execute_query(GDS_GARPH_BUILD, end_user_id=end_user_id)

        try:
            score_rows = await connector.execute_query(G_SCORE, end_user_id=end_user_id)
        except Exception as e:
            # 活跃用户可能尚无记忆节点，投影得到空图，eigenvector 对空图会报错。
            # 这种情况记 skipped（非失败），避免每次 scan 都产生一条 FAILURE 任务。
            logger.warning(
                f"GDS eigenvector.write 失败（可能为空图） user={end_user_id}: {e}"
            )
            return {"status": "skipped", "reason": "eigenvector_failed", "error": str(e)}

        summary = score_rows[0] if score_rows else {}
        return {
            "status": "success",
            "node_properties_written": summary.get("nodePropertiesWritten", 0),
            "did_converge": summary.get("didConverge"),
            "ran_iterations": summary.get("ranIterations"),
        }
    finally:
        # 无论成败都释放内存图，避免残留投影图占用 Neo4j 内存
        try:
            await connector.execute_query(CLEAR_GRAPH, end_user_id=end_user_id)
        except Exception as e:
            logger.warning(f"GDS graph.drop 失败 user={end_user_id}: {e}")
        await connector.close()