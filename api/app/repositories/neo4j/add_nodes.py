from typing import List, Optional

from app.repositories.neo4j.cypher_queries import DIALOGUE_NODE_SAVE, STATEMENT_NODE_SAVE, CHUNK_NODE_SAVE,MEMORY_SUMMARY_NODE_SAVE
from app.core.memory.models.graph_models import DialogueNode, StatementNode, ChunkNode, MemorySummaryNode
# 使用新的仓储层
from app.repositories.neo4j.neo4j_connector import Neo4jConnector


async def delete_all_nodes(group_id: str, connector: Neo4jConnector):
    """Delete all nodes in the database."""
    result = await connector.execute_query(f"MATCH (n {{group_id: '{group_id}'}}) DETACH DELETE n")
    print(f"All group_id: {group_id} node and edge deleted successfully")
    return result

async def add_dialogue_nodes(dialogues: List[DialogueNode], connector: Neo4jConnector) -> Optional[List[str]]:
    """Add dialogue nodes to Neo4j database.

    Args:
        dialogues: List of DialogueNode objects to save
        connector: Neo4j connector instance

    Returns:
        List of created node UUIDs or None if failed
    """
    if not dialogues:
        print("No dialogues to save")
        return []

    try:
        # Flatten DialogueNode objects to match Cypher expected fields
        flattened_dialogues = []
        for dialogue in dialogues:
            flattened_dialogues.append({
                "id": dialogue.id,
                "group_id": dialogue.group_id,
                "user_id": dialogue.user_id,
                "apply_id": dialogue.apply_id,
                "run_id": dialogue.run_id,
                "ref_id": dialogue.ref_id,
                "name": dialogue.name,
                "created_at": dialogue.created_at.isoformat() if dialogue.created_at else None,
                "expired_at": dialogue.expired_at.isoformat() if dialogue.expired_at else None,
                "content": dialogue.content,
                "dialog_embedding": dialogue.dialog_embedding
            })

        result = await connector.execute_query(
            DIALOGUE_NODE_SAVE,
            dialogues=flattened_dialogues
        )

        created_uuids = [record["uuid"] for record in result]
        print(f"Successfully created {len(created_uuids)} dialogue nodes: {created_uuids}")
        return created_uuids

    except Exception as e:
        print(f"Error creating dialogue nodes: {e}")
        return None


async def add_statement_nodes(statements: List[StatementNode], connector: Neo4jConnector) -> Optional[List[str]]:
    """Add statement nodes to Neo4j database.

    Args:
        statements: List of StatementNode objects to save
        connector: Neo4j connector instance

    Returns:
        List of created node UUIDs or None if failed
    """
    if not statements:
        print("No statements to save")
        return []

    try:
        # Flatten StatementNode objects to only include primitive types
        flattened_statements = []
        for statement in statements:
            flattened_statement = {
                "id": statement.id,
                "name": statement.name,
                "group_id": statement.group_id,
                "user_id": statement.user_id,
                "apply_id": statement.apply_id,
                "run_id": statement.run_id,
                "chunk_id": statement.chunk_id,
                # "created_at": statement.created_at.isoformat(),
                "created_at": statement.created_at.isoformat() if statement.created_at else None,
                "expired_at": statement.expired_at.isoformat() if statement.expired_at else None,
                "stmt_type": statement.stmt_type,
                "temporal_info": statement.temporal_info.value,
                "statement": statement.statement,
                "connect_strength": statement.connect_strength,
                "chunk_embedding": statement.chunk_embedding if statement.chunk_embedding else None,
                # "temporal_validity_valid_at": statement.temporal_validity_valid_at.isoformat() if statement.temporal_validity_valid_at else None,
                # "temporal_validity_invalid_at": statement.temporal_validity_invalid_at.isoformat() if statement.temporal_validity_invalid_at else None,
                "valid_at": statement.valid_at.isoformat() if statement.valid_at else None,
                "invalid_at": statement.invalid_at.isoformat() if statement.invalid_at else None,
                # "triplet_extraction_info": json.dumps({
                #     "triplets": [triplet.model_dump() for triplet in statement.triplet_extraction_info.triplets] if statement.triplet_extraction_info else [],
                #     "entities": [entity.model_dump() for entity in statement.triplet_extraction_info.entities] if statement.triplet_extraction_info else []
                # }) if statement.triplet_extraction_info else json.dumps({"triplets": [], "entities": []}),
                "statement_embedding": statement.statement_embedding if statement.statement_embedding else None,
                # 添加情绪字段处理
                "emotion_type": statement.emotion_type,
                "emotion_intensity": statement.emotion_intensity,
                "emotion_keywords": statement.emotion_keywords if statement.emotion_keywords else [],
                "emotion_subject": statement.emotion_subject,
                "emotion_target": statement.emotion_target
            }
            flattened_statements.append(flattened_statement)

        result = await connector.execute_query(
            STATEMENT_NODE_SAVE,
            statements=flattened_statements
        )

        created_uuids = [record["uuid"] for record in result]
        print(f"Successfully created {len(created_uuids)} statement nodes")
        return created_uuids

    except Exception as e:
        print(f"Error creating statement nodes: {e}")
        return None

async def add_chunk_nodes(chunks: List[ChunkNode], connector: Neo4jConnector) -> Optional[List[str]]:
    """Add chunk nodes to Neo4j in batch.

    Args:
        chunks: List of ChunkNode objects to add
        connector: Neo4j connector instance

    Returns:
        List of created chunk UUIDs or None if failed
    """
    if not chunks:
        print("No chunk nodes to add")
        return []

    try:
        # Convert chunk nodes to dictionaries for the query
        flattened_chunks = []
        for chunk in chunks:
            # Flatten metadata properties to avoid Neo4j Map type issues
            metadata = chunk.metadata if chunk.metadata else {}
            flattened_chunk = {
                "id": chunk.id,
                "name": chunk.name,
                "group_id": chunk.group_id,
                "user_id": chunk.user_id,
                "apply_id": chunk.apply_id,
                "run_id": chunk.run_id,
                "created_at": chunk.created_at.isoformat() if chunk.created_at else None,
                "expired_at": chunk.expired_at.isoformat() if chunk.expired_at else None,
                "dialog_id": chunk.dialog_id,
                "content": chunk.content,
                "chunk_embedding": chunk.chunk_embedding if chunk.chunk_embedding else None,
                "sequence_number": chunk.sequence_number,
                "start_index": metadata.get("start_index"),
                "end_index": metadata.get("end_index")
            }
            flattened_chunks.append(flattened_chunk)

        result = await connector.execute_query(
            CHUNK_NODE_SAVE,
            chunks=flattened_chunks
        )

        created_uuids = [record["uuid"] for record in result]
        print(f"Successfully created {len(created_uuids)} chunk nodes")
        return created_uuids

    except Exception as e:
        print(f"Error creating chunk nodes: {e}")
        return None



async def add_memory_summary_nodes(summaries: List[MemorySummaryNode], connector: Neo4jConnector) -> Optional[List[str]]:
    """Add memory summary nodes to Neo4j in batch.

    Args:
        summaries: List of MemorySummaryNode objects to add
        connector: Neo4j connector instance

    Returns:
        List of created summary node ids or None if failed
    """
    if not summaries:
        print("No memory summary nodes to add")
        return []

    try:
        flattened = []
        for s in summaries:
            flattened.append({
                "id": s.id,
                "name": s.name,
                "group_id": s.group_id,
                "user_id": s.user_id,
                "apply_id": s.apply_id,
                "run_id": s.run_id,
                "created_at": s.created_at.isoformat() if s.created_at else None,
                "expired_at": s.expired_at.isoformat() if s.expired_at else None,
                "dialog_id": s.dialog_id,
                "chunk_ids": s.chunk_ids,
                "content": s.content,
                "summary_embedding": s.summary_embedding if s.summary_embedding else None,
                "config_id": s.config_id,  # 添加 config_id
            })
        
        result = await connector.execute_query(
            MEMORY_SUMMARY_NODE_SAVE,
            summaries=flattened
        )
        created_ids = [record.get("uuid") for record in result]
        return created_ids
    except Exception:
        return None


