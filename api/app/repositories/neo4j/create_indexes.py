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


async def create_end_user_id_indexes():
    """为受 graph_data 接口查询的 8 类节点建立 ``end_user_id`` 范围索引。

    所有 graph_data 系列查询（Q1 / Q4）都按 ``n.end_user_id = $end_user_id AND
    labels(n)[0] = $label`` 过滤；如果不建索引，对每个类型都要全表扫描，
    在大库下会成为性能瓶颈。

    本函数对全部 8 类受支持节点统一建立单字段范围索引（``IF NOT EXISTS``
    保证幂等）；首次创建时 Neo4j 会异步在后台填充索引，不阻塞在线查询。
    """
    connector = Neo4jConnector()
    try:
        for label, idx_name in [
            ("Dialogue",          "user_dialogue"),
            ("Chunk",             "user_chunk"),
            ("Statement",         "user_statement"),
            ("ExtractedEntity",   "user_extracted_entity"),
            ("MemorySummary",     "user_memory_summary"),
            ("Perceptual",        "user_perceptual"),
            ("AssistantOriginal", "user_assistant_original"),
            ("AssistantPruned",   "user_assistant_pruned"),
        ]:
            await connector.execute_query(
                f"""
                CREATE INDEX {idx_name} IF NOT EXISTS
                FOR (n:{label}) ON (n.end_user_id);
                """
            )
    finally:
        await connector.close()


async def create_user_indexes():
    """Deprecated: 历史保留入口；新代码请直接调用 :func:`create_end_user_id_indexes`。

    早期版本只建了 ``Perceptual.end_user_id`` 一条；本函数现已并入到
    :func:`create_end_user_id_indexes`，统一覆盖 8 类受支持节点。保留本函数
    名是为了避免破坏外部调用方（如运维脚本）。
    """
    await create_end_user_id_indexes()


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

    finally:
        await connector.close()


async def create_all_indexes():
    """Create all indexes and constraints in one go."""
    await create_fulltext_indexes()
    await create_vector_indexes()
    await create_end_user_id_indexes()
    await create_unique_constraints()
