from typing import List, Optional
import hashlib
from datetime import datetime
from uuid import uuid4
from app.repositories.neo4j.cypher_queries import CHUNK_STATEMENT_EDGE_SAVE, MEMORY_SUMMARY_STATEMENT_EDGE_SAVE
from app.core.memory.models.message_models import Chunk
# 使用新的仓储层
from app.repositories.neo4j.neo4j_connector import Neo4jConnector
from app.core.memory.models.graph_models import MemorySummaryNode

async def add_chunk_statement_edges(chunks: List[Chunk], connector: Neo4jConnector) -> Optional[List[str]]:
    """Add edges between chunk nodes and their statement nodes in Neo4j.

    Args:
        chunks: List of Chunk objects containing the statements
        connector: Neo4j connector instance

    Returns:
        List of created edge UUIDs or None if failed
    """
    if not chunks:
        print("No chunks provided to create edges")
        return []

    try:
        # Build edges deterministically per (chunk, statement) pair
        edges: List[dict] = []
        for chunk in chunks:
            for stmt in getattr(chunk, "statements", []) or []:
                stable_edge_id = hashlib.sha1(f"{chunk.id}|{stmt.id}".encode()).hexdigest()
                edge = {
                    "id": stable_edge_id,
                    "source": chunk.id,
                    "target": stmt.id,
                    "group_id": getattr(stmt, 'group_id', None),
                    "user_id":getattr(stmt, 'user_id', None),
                    "apply_id": getattr(stmt, 'apply_id', None),
                    "run_id": getattr(stmt, 'run_id', None) or getattr(chunk, 'run_id', None),
                    "created_at": getattr(stmt, 'created_at', None),
                    "expired_at": getattr(stmt, 'expired_at', None),
                    # "created_at": getattr(statement, 'created_at', None),
                    # "expired_at": None  # Set to None or appropriate default
                }
                edges.append(edge)

        if not edges:
            print("No statements found in chunks to create edges")
            return []

        # Execute the query to create edges
        result = await connector.execute_query(
            CHUNK_STATEMENT_EDGE_SAVE,
            chunk_statement_edges=edges
        )
        created_uuids = [record.get("uuid") for record in result] if result else []
        print(f"Successfully created {len(created_uuids)} chunk-statement edges")
        return created_uuids
    except Exception as e:
        print(f"Error creating chunk-statement edges: {e}")
        return None

async def add_memory_summary_statement_edges(summaries: List[MemorySummaryNode], connector: Neo4jConnector) -> Optional[List[str]]:
    """Create edges from MemorySummary to Statements via their chunk_ids.

    For each summary and each chunk_id in it, this links the summary to all statements
    contained in that chunk using DERIVED_FROM_STATEMENT. This supports queries like
    summary -> statement -> entity with minimal hops.

    Args:
        summaries: List of MemorySummaryNode objects
        connector: Neo4j connector instance

    Returns:
        List of created edge elementIds or None if failed
    """
    if not summaries:
        return []

    try:
        edges: List[dict] = []
        for s in summaries:
            for chunk_id in getattr(s, "chunk_ids", []) or []:
                edges.append({
                    "summary_id": s.id,
                    "chunk_id": chunk_id,
                    "group_id": s.group_id,
                    "run_id": s.run_id,
                    "created_at": s.created_at.isoformat() if s.created_at else None,
                    "expired_at": s.expired_at.isoformat() if s.expired_at else None,
                })

        if not edges:
            return []

        result = await connector.execute_query(
            MEMORY_SUMMARY_STATEMENT_EDGE_SAVE,
            edges=edges
        )
        created = [record.get("uuid") for record in result] if result else []
        return created
    except Exception:
        return None
