from app.repositories.neo4j.neo4j_connector import Neo4jConnector


async def create_fulltext_indexes():
    """Create full-text indexes for keyword search with BM25 scoring."""
    connector = Neo4jConnector()
    try:

        # 创建 Statements 索引
        await connector.execute_query("""
            CREATE FULLTEXT INDEX statementsFulltext IF NOT EXISTS FOR (s:Statement) ON EACH [s.statement]
            OPTIONS { indexConfig: { `fulltext.analyzer`: 'cjk' } }
        """)

        # # 创建 Dialogues 索引
        # await connector.execute_query("""
        #     CREATE FULLTEXT INDEX dialoguesFulltext IF NOT EXISTS FOR (d:Dialogue) ON EACH [d.content]
        #     OPTIONS { indexConfig: { `fulltext.analyzer`: 'cjk' } }
        # """)
        # 创建 Entities 索引 (name + description + aliases)
        await connector.execute_query("""
            CREATE FULLTEXT INDEX entitiesFulltext IF NOT EXISTS FOR (e:ExtractedEntity) ON EACH [e.name, e.description, e.aliases]
            OPTIONS { indexConfig: { `fulltext.analyzer`: 'cjk' } }
        """)

        # 创建 Chunks 索引
        await connector.execute_query("""
            CREATE FULLTEXT INDEX chunksFulltext IF NOT EXISTS FOR (c:Chunk) ON EACH [c.content]
            OPTIONS { indexConfig: { `fulltext.analyzer`: 'cjk' } }
        """)

        # 创建 MemorySummary 索引
        await connector.execute_query("""
            CREATE FULLTEXT INDEX summariesFulltext IF NOT EXISTS FOR (m:MemorySummary) ON EACH [m.content]
            OPTIONS { indexConfig: { `fulltext.analyzer`: 'cjk' } }
        """)
        # 创建 Community 索引
        await connector.execute_query("""
            CREATE FULLTEXT INDEX communitiesFulltext IF NOT EXISTS FOR (c:Community) ON EACH [c.name, c.summary]
            OPTIONS { indexConfig: { `fulltext.analyzer`: 'cjk' } }
        """)

        # 创建 Perceptual 感知记忆索引
        await connector.execute_query("""
            CREATE FULLTEXT INDEX perceptualFulltext IF NOT EXISTS FOR (p:Perceptual) ON EACH [p.summary, p.topic, p.domain]
            OPTIONS { indexConfig: { `fulltext.analyzer`: 'cjk' } }
        """)

        # 创建 AssistantPruned 剪枝文本全文索引
        await connector.execute_query("""
            CREATE FULLTEXT INDEX assistantPrunedFulltext IF NOT EXISTS FOR (p:AssistantPruned) ON EACH [p.text]
            OPTIONS { indexConfig: { `fulltext.analyzer`: 'cjk' } }
        """)

    finally:
        await connector.close()


async def create_vector_indexes():
    """Create vector indexes for fast embedding similarity search.
    
    Vector indexes provide 10-100x faster similarity search compared to manual cosine calculation.
    This is critical for performance - reduces embedding search from ~1.4s to ~0.05-0.2s!
    """
    connector = Neo4jConnector()
    try:

        # Statement embedding index
        await connector.execute_query("""
            CREATE VECTOR INDEX statement_embedding_index IF NOT EXISTS
            FOR (s:Statement)
            ON s.statement_embedding
            OPTIONS {indexConfig: {
              `vector.dimensions`: 1024,
              `vector.similarity_function`: 'cosine'
            }}
        """)

        # Chunk embedding index
        await connector.execute_query("""
            CREATE VECTOR INDEX chunk_embedding_index IF NOT EXISTS
            FOR (c:Chunk)
            ON c.chunk_embedding
            OPTIONS {indexConfig: {
              `vector.dimensions`: 1024,
              `vector.similarity_function`: 'cosine'
            }}
        """)

        # Entity name embedding index
        await connector.execute_query("""
            CREATE VECTOR INDEX entity_embedding_index IF NOT EXISTS
            FOR (e:ExtractedEntity)
            ON e.name_embedding
            OPTIONS {indexConfig: {
              `vector.dimensions`: 1024,
              `vector.similarity_function`: 'cosine'
            }}
        """)

        # Memory summary embedding index
        await connector.execute_query("""
            CREATE VECTOR INDEX summary_embedding_index IF NOT EXISTS
            FOR (m:MemorySummary)
            ON m.summary_embedding
            OPTIONS {indexConfig: {
              `vector.dimensions`: 1024,
              `vector.similarity_function`: 'cosine'
            }}
        """)

        # Community summary embedding index
        await connector.execute_query("""
            CREATE VECTOR INDEX community_summary_embedding_index IF NOT EXISTS
            FOR (c:Community)
            ON c.summary_embedding
            OPTIONS {indexConfig: {
              `vector.dimensions`: 1024,
              `vector.similarity_function`: 'cosine'
            }}
        """)

        # Dialogue embedding index (optional)
        await connector.execute_query("""
            CREATE VECTOR INDEX dialogue_embedding_index IF NOT EXISTS
            FOR (d:Dialogue)
            ON d.dialog_embedding
            OPTIONS {indexConfig: {
              `vector.dimensions`: 1024,
              `vector.similarity_function`: 'cosine'
            }}
        """)

        # Perceptual summary embedding index
        await connector.execute_query("""
            CREATE VECTOR INDEX perceptual_summary_embedding_index IF NOT EXISTS
            FOR (p:Perceptual)
            ON p.summary_embedding
            OPTIONS {indexConfig: {
              `vector.dimensions`: 1024,
              `vector.similarity_function`: 'cosine'
            }}
        """)

        # AssistantPruned text embedding index (optional, for semantic search on pruned hints)
        await connector.execute_query("""
            CREATE VECTOR INDEX assistant_pruned_embedding_index IF NOT EXISTS
            FOR (p:AssistantPruned)
            ON p.text_embedding
            OPTIONS {indexConfig: {
              `vector.dimensions`: 1024,
              `vector.similarity_function`: 'cosine'
            }}
        """)
    finally:
        await connector.close()


async def create_user_indexes():
    connector = Neo4jConnector()
    await connector.execute_query(
        """
        CREATE INDEX user_perceptual IF NOT EXISTS
        FOR (p:Perceptual) ON (p.end_user_id);
        """
    )


async def create_unique_constraints():
    """Create uniqueness constraints for core node identifiers.
    Ensures concurrent MERGE operations remain safe and prevents duplicates.
    """
    connector = Neo4jConnector()
    try:
        # Dialogue.id unique
        await connector.execute_query(
            """
            CREATE CONSTRAINT dialog_id_unique IF NOT EXISTS
            FOR (d:Dialogue) REQUIRE d.id IS UNIQUE
            """
        )

        # Statement.id unique
        await connector.execute_query(
            """
            CREATE CONSTRAINT statement_id_unique IF NOT EXISTS
            FOR (s:Statement) REQUIRE s.id IS UNIQUE
            """
        )

        # Chunk.id unique
        await connector.execute_query(
            """
            CREATE CONSTRAINT chunk_id_unique IF NOT EXISTS
            FOR (c:Chunk) REQUIRE c.id IS UNIQUE
            """
        )

        # AssistantOriginal.id unique
        await connector.execute_query(
            """
            CREATE CONSTRAINT assistant_original_id_unique IF NOT EXISTS
            FOR (o:AssistantOriginal) REQUIRE o.id IS UNIQUE
            """
        )

        # AssistantPruned.id unique
        await connector.execute_query(
            """
            CREATE CONSTRAINT assistant_pruned_id_unique IF NOT EXISTS
            FOR (p:AssistantPruned) REQUIRE p.id IS UNIQUE
            """
        )

        # Phase 4: ExtractedEntity (end_user_id, name_lower) 唯一约束
        # 杜绝同一 end_user_id 下出现重复的特殊实体（"用户"/"AI助手"）
        # 注意：此约束要求所有 ExtractedEntity 节点都有 name_lower 属性，
        # 首次创建前需确保已有数据已补充该字段（通过迁移脚本）。
        import os
        if os.getenv("NEO4J_ENTITY_UNIQUE_CONSTRAINT", "false").lower() == "true":
            try:
                await connector.execute_query(
                    """
                    CREATE CONSTRAINT entity_user_name_unique IF NOT EXISTS
                    FOR (e:ExtractedEntity) REQUIRE (e.end_user_id, e.name_lower) IS UNIQUE
                    """
                )
                import logging
                logging.getLogger(__name__).info(
                    "[Indexes] entity_user_name_unique constraint created"
                )
            except Exception as e:
                import logging
                logging.getLogger(__name__).warning(
                    f"[Indexes] Failed to create entity unique constraint "
                    f"(may need data migration first): {e}"
                )

    finally:
        await connector.close()


async def create_all_indexes():
    """Create all indexes and constraints in one go."""
    await create_fulltext_indexes()
    await create_vector_indexes()
    await create_unique_constraints()
