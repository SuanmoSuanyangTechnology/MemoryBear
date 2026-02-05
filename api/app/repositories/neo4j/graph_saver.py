from typing import List

# 使用新的仓储层
from app.repositories.neo4j.neo4j_connector import Neo4jConnector
from app.repositories.neo4j.add_nodes import add_dialogue_nodes, add_statement_nodes, add_chunk_nodes
from app.repositories.neo4j.cypher_queries import (
    STATEMENT_ENTITY_EDGE_SAVE,
    ENTITY_RELATIONSHIP_SAVE,
    EXTRACTED_ENTITY_NODE_SAVE,
    CHUNK_STATEMENT_EDGE_SAVE,
    STATEMENT_ENTITY_EDGE_SAVE,
    ENTITY_RELATIONSHIP_SAVE,
    EXTRACTED_ENTITY_NODE_SAVE,
)
from app.core.memory.models.graph_models import (
    DialogueNode,
    ChunkNode,
    StatementChunkEdge,
    StatementEntityEdge,
    StatementNode,
    ExtractedEntityNode,
    EntityEntityEdge,
)
import logging
logger = logging.getLogger(__name__)
async def save_entities_and_relationships(
    entity_nodes: List[ExtractedEntityNode],
    entity_entity_edges: List[EntityEntityEdge],
    connector: Neo4jConnector
):
    """Save entities and their relationships using graph models"""
    all_entities = [entity.model_dump() for entity in entity_nodes]
    all_relationships = []

    for edge in entity_entity_edges:
        relationship = {
            'source_id': edge.source,
            'target_id': edge.target,
            'predicate': edge.relation_type,
            'statement_id': edge.source_statement_id,
            'value': edge.relation_value,
            'statement': edge.statement,
            'valid_at': edge.valid_at.isoformat() if edge.valid_at else None,
            'invalid_at': edge.invalid_at.isoformat() if edge.invalid_at else None,
            'created_at': edge.created_at.isoformat(),
            'expired_at': edge.expired_at.isoformat(),
            'run_id': edge.run_id,
            'end_user_id': edge.end_user_id,
        }
        all_relationships.append(relationship)

    # Save entities
    if all_entities:
        entity_uuids = await connector.execute_query(EXTRACTED_ENTITY_NODE_SAVE, entities=all_entities)
        if entity_uuids:
            print(f"Successfully saved {len(entity_uuids)} entity nodes to Neo4j")
        else:
            print("Failed to save entity nodes to Neo4j")
    else:
        print("No entity nodes to save")

    # Create relationships
    if all_relationships:
        relationship_uuids = await connector.execute_query(ENTITY_RELATIONSHIP_SAVE, relationships=all_relationships)
        if relationship_uuids:
            print(f"Successfully saved {len(relationship_uuids)} entity relationships (edges) to Neo4j")
        else:
            print("Failed to save entity relationships to Neo4j")
    else:
        print("No entity relationships to save")


async def save_chunk_nodes(
    chunk_nodes: List[ChunkNode],
    connector: Neo4jConnector
):
    """Save chunk nodes using graph models"""
    if not chunk_nodes:
        print("No chunk nodes to save")
        return

    chunk_uuids = await add_chunk_nodes(chunk_nodes, connector)
    if chunk_uuids:
        print(f"Successfully saved {len(chunk_uuids)} chunk nodes to Neo4j")
    else:
        print("Failed to save chunk nodes to Neo4j")


async def save_statement_chunk_edges(
    statement_chunk_edges: List[StatementChunkEdge],
    connector: Neo4jConnector
):
    """Save statement-chunk edges using graph models"""
    if not statement_chunk_edges:
        return

    all_sc_edges = []
    for edge in statement_chunk_edges:
        all_sc_edges.append({
            "id": edge.id,
            "source": edge.source,
            "target": edge.target,
            "end_user_id": edge.end_user_id,
            "run_id": edge.run_id,
            "created_at": edge.created_at.isoformat() if edge.created_at else None,
            "expired_at": edge.expired_at.isoformat() if edge.expired_at else None,
        })

    try:
        await connector.execute_query(
            CHUNK_STATEMENT_EDGE_SAVE,
            chunk_statement_edges=all_sc_edges
        )
    except Exception:
        pass


async def save_statement_entity_edges(
    statement_entity_edges: List[StatementEntityEdge],
    connector: Neo4jConnector
):
    """Save statement-entity edges using graph models"""
    if not statement_entity_edges:
        print("No statement-entity edges to save")
        return

    all_se_edges = []
    for edge in statement_entity_edges:
        edge_data = {
            "source": edge.source,
            "target": edge.target,
            "end_user_id": edge.end_user_id,
            "run_id": edge.run_id,
            "connect_strength": edge.connect_strength,
            "created_at": edge.created_at.isoformat() if edge.created_at else None,
            "expired_at": edge.expired_at.isoformat() if edge.expired_at else None,
        }
        all_se_edges.append(edge_data)

    if all_se_edges:
        try:
            await connector.execute_query(
                STATEMENT_ENTITY_EDGE_SAVE, 
                relationships=all_se_edges
            )
        except Exception:
            pass


async def save_dialog_and_statements_to_neo4j(
        dialogue_nodes: List[DialogueNode],
        chunk_nodes: List[ChunkNode],
        statement_nodes: List[StatementNode],
        entity_nodes: List[ExtractedEntityNode],
        entity_edges: List[EntityEntityEdge],
        statement_chunk_edges: List[StatementChunkEdge],
        statement_entity_edges: List[StatementEntityEdge],
        connector: Neo4jConnector
) -> bool:
    """Save dialogue nodes, chunk nodes, statement nodes, entities, and all relationships to Neo4j using graph models.

    Args:
        dialogue_nodes: List of DialogueNode objects to save
        chunk_nodes: List of ChunkNode objects to save
        statement_nodes: List of StatementNode objects to save
        entity_nodes: List of ExtractedEntityNode objects to save
        entity_edges: List of EntityEntityEdge objects to save
        statement_chunk_edges: List of StatementChunkEdge objects to save
        statement_entity_edges: List of StatementEntityEdge objects to save
        connector: Neo4j connector instance

    Returns:
        bool: True if successful, False otherwise
    """

    # 定义事务函数，将所有写操作放在一个事务中
    async def _save_all_in_transaction(tx):
        """在单个事务中执行所有保存操作，避免死锁"""
        results = {}

        # 1. Save all dialogue nodes in batch
        if dialogue_nodes:
            from app.repositories.neo4j.cypher_queries import DIALOGUE_NODE_SAVE
            dialogue_data = [node.model_dump() for node in dialogue_nodes]
            result = await tx.run(DIALOGUE_NODE_SAVE, dialogues=dialogue_data)
            dialogue_uuids = [record["uuid"] async for record in result]
            results['dialogues'] = dialogue_uuids
            print(f"Dialogues saved to Neo4j with UUIDs: {dialogue_uuids}")

        # 2. Save all chunk nodes in batch
        if chunk_nodes:
            from app.repositories.neo4j.cypher_queries import CHUNK_NODE_SAVE
            chunk_data = [node.model_dump() for node in chunk_nodes]
            result = await tx.run(CHUNK_NODE_SAVE, chunks=chunk_data)
            chunk_uuids = [record["uuid"] async for record in result]
            results['chunks'] = chunk_uuids
            logger.info(f"Successfully saved {len(chunk_uuids)} chunk nodes to Neo4j")

        # 3. Save all statement nodes in batch
        if statement_nodes:
            from app.repositories.neo4j.cypher_queries import STATEMENT_NODE_SAVE
            statement_data = [node.model_dump() for node in statement_nodes]
            result = await tx.run(STATEMENT_NODE_SAVE, statements=statement_data)
            statement_uuids = [record["uuid"] async for record in result]
            results['statements'] = statement_uuids
            logger.info(f"Successfully saved {len(statement_uuids)} statement nodes to Neo4j")

        # 4. Save entities
        if entity_nodes:
            from app.repositories.neo4j.cypher_queries import EXTRACTED_ENTITY_NODE_SAVE
            entity_data = [entity.model_dump() for entity in entity_nodes]
            result = await tx.run(EXTRACTED_ENTITY_NODE_SAVE, entities=entity_data)
            entity_uuids = [record["uuid"] async for record in result]
            results['entities'] = entity_uuids
            logger.info(f"Successfully saved {len(entity_uuids)} entity nodes to Neo4j")

        # 5. Create entity relationships
        if entity_edges:
            from app.repositories.neo4j.cypher_queries import ENTITY_RELATIONSHIP_SAVE
            relationship_data = []
            for edge in entity_edges:
                relationship_data.append({
                    'source_id': edge.source,
                    'target_id': edge.target,
                    'predicate': edge.relation_type,
                    'statement_id': edge.source_statement_id,
                    'value': edge.relation_value,
                    'statement': edge.statement,
                    'valid_at': edge.valid_at.isoformat() if edge.valid_at else None,
                    'invalid_at': edge.invalid_at.isoformat() if edge.invalid_at else None,
                    'created_at': edge.created_at.isoformat() if edge.created_at else None,
                    'expired_at': edge.expired_at.isoformat() if edge.expired_at else None,
                    'run_id': edge.run_id,
                    'end_user_id': edge.end_user_id,
                })
            result = await tx.run(ENTITY_RELATIONSHIP_SAVE, relationships=relationship_data)
            rel_uuids = [record["uuid"] async for record in result]
            results['entity_relationships'] = rel_uuids
            logger.info(f"Successfully saved {len(rel_uuids)} entity relationships to Neo4j")

        # 6. Save statement-chunk edges
        if statement_chunk_edges:
            from app.repositories.neo4j.cypher_queries import CHUNK_STATEMENT_EDGE_SAVE
            sc_edge_data = []
            for edge in statement_chunk_edges:
                sc_edge_data.append({
                    "id": edge.id,
                    "source": edge.source,
                    "target": edge.target,
                    "created_at": edge.created_at.isoformat() if edge.created_at else None,
                    "expired_at": edge.expired_at.isoformat() if edge.expired_at else None,
                    "run_id": edge.run_id,
                    "end_user_id": edge.end_user_id,
                })
            result = await tx.run(CHUNK_STATEMENT_EDGE_SAVE, chunk_statement_edges=sc_edge_data)
            sc_uuids = [record["uuid"] async for record in result]
            results['statement_chunk_edges'] = sc_uuids
            logger.info(f"Successfully saved {len(sc_uuids)} statement-chunk edges to Neo4j")

        # 7. Save statement-entity edges
        if statement_entity_edges:
            from app.repositories.neo4j.cypher_queries import STATEMENT_ENTITY_EDGE_SAVE
            se_edge_data = []
            for edge in statement_entity_edges:
                se_edge_data.append({
                    "source": edge.source,
                    "target": edge.target,
                    "created_at": edge.created_at.isoformat() if edge.created_at else None,
                    "expired_at": edge.expired_at.isoformat() if edge.expired_at else None,
                    "run_id": edge.run_id,
                    "end_user_id": edge.end_user_id,
                    "connect_strength": edge.connect_strength if hasattr(edge, 'connect_strength') else 'strong',
                })
            result = await tx.run(STATEMENT_ENTITY_EDGE_SAVE, relationships=se_edge_data)
            se_uuids = [record["uuid"] async for record in result]
            results['statement_entity_edges'] = se_uuids
            logger.info(f"Successfully saved {len(se_uuids)} statement-entity edges to Neo4j")

        return results

    try:
        # 使用显式写事务执行所有操作，避免死锁
        results = await connector.execute_write_transaction(_save_all_in_transaction)
        logger.info(f"Transaction completed, results: {results}")
        print(f"Successfully saved all data to Neo4j in a single transaction. Results: {results}")
        return True

    except Exception as e:
        logger.error(f"Neo4j integration error: {e}", exc_info=True)
        print(f"Neo4j integration error: {e}")
        print("Continuing without database storage...")
        return False

