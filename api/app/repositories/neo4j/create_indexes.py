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
        # 创建 Entities 索引
        await connector.execute_query("""
            CREATE FULLTEXT INDEX entitiesFulltext IF NOT EXISTS FOR (e:ExtractedEntity) ON EACH [e.name]
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
    finally:
        await connector.close()


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

    finally:
        await connector.close()


async def create_all_indexes():
    """Create all indexes and constraints in one go."""
    await create_fulltext_indexes()
    await create_vector_indexes()
    await create_unique_constraints()
