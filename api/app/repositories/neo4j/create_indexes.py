from app.repositories.neo4j.neo4j_connector import Neo4jConnector


async def create_fulltext_indexes():
    """Create full-text indexes for keyword search with BM25 scoring."""
    connector = Neo4jConnector()
    try:
        print("\n" + "=" * 70)
        print("Creating Full-Text Indexes (for keyword search)")
        print("=" * 70)
        
        # 创建 Statements 索引
        await connector.execute_query("""
            CREATE FULLTEXT INDEX statementsFulltext IF NOT EXISTS FOR (s:Statement) ON EACH [s.statement]
            OPTIONS { indexConfig: { `fulltext.analyzer`: 'cjk' } }
        """)
        print("✓ Created: statementsFulltext")
        
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
        print("✓ Created: entitiesFulltext")
        
        # 创建 Chunks 索引
        await connector.execute_query("""
            CREATE FULLTEXT INDEX chunksFulltext IF NOT EXISTS FOR (c:Chunk) ON EACH [c.content]
            OPTIONS { indexConfig: { `fulltext.analyzer`: 'cjk' } }
        """)
        print("✓ Created: chunksFulltext")
        
        # 创建 MemorySummary 索引
        await connector.execute_query("""
            CREATE FULLTEXT INDEX summariesFulltext IF NOT EXISTS FOR (m:MemorySummary) ON EACH [m.content]
            OPTIONS { indexConfig: { `fulltext.analyzer`: 'cjk' } }
        """)
        print("✓ Created: summariesFulltext")
        
        print("\nFull-text indexes created successfully with BM25 support.")
    except Exception as e:
        print(f"✗ Error creating full-text indexes: {e}")
    finally:
        await connector.close()


async def create_vector_indexes():
    """Create vector indexes for fast embedding similarity search.
    
    Vector indexes provide 10-100x faster similarity search compared to manual cosine calculation.
    This is critical for performance - reduces embedding search from ~1.4s to ~0.05-0.2s!
    """
    connector = Neo4jConnector()
    try:
        print("\n" + "=" * 70)
        print("Creating Vector Indexes (for embedding search)")
        print("=" * 70)
        print("Note: Adjust vector.dimensions if using different embedding model")
        print("      Current setting: 1024 dimensions (for bge-m3)")
        print()
        
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
        print("✓ Created: statement_embedding_index")
        
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
        print("✓ Created: chunk_embedding_index")
        
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
        print("✓ Created: entity_embedding_index")
        
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
        print("✓ Created: summary_embedding_index")
        
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
        print("✓ Created: dialogue_embedding_index")
        
        print("\nVector indexes created successfully!")
        print("\nExpected performance improvement:")
        print("  Before: ~1.4s for embedding search")
        print("  After:  ~0.05-0.2s for embedding search (10-30x faster!)")
        
    except Exception as e:
        print(f"✗ Error creating vector indexes: {e}")
    finally:
        await connector.close()


async def create_config_id_indexes():
    """Create indexes on config_id fields for improved query performance.
    
    These indexes enable fast filtering of nodes by configuration ID,
    which is essential for configuration isolation and multi-tenant scenarios.
    """
    connector = Neo4jConnector()
    try:
        print("\n" + "=" * 70)
        print("Creating Config ID Indexes")
        print("=" * 70)
        
        # Dialogue.config_id index
        await connector.execute_query("""
            CREATE INDEX dialogue_config_id_index IF NOT EXISTS
            FOR (d:Dialogue) ON (d.config_id)
        """)
        print("✓ Created: dialogue_config_id_index")
        
        # Statement.config_id index
        await connector.execute_query("""
            CREATE INDEX statement_config_id_index IF NOT EXISTS
            FOR (s:Statement) ON (s.config_id)
        """)
        print("✓ Created: statement_config_id_index")
        
        # ExtractedEntity.config_id index
        await connector.execute_query("""
            CREATE INDEX entity_config_id_index IF NOT EXISTS
            FOR (e:ExtractedEntity) ON (e.config_id)
        """)
        print("✓ Created: entity_config_id_index")
        
        # MemorySummary.config_id index
        await connector.execute_query("""
            CREATE INDEX summary_config_id_index IF NOT EXISTS
            FOR (m:MemorySummary) ON (m.config_id)
        """)
        print("✓ Created: summary_config_id_index")
        
        print("\nConfig ID indexes created successfully!")
        print("These indexes enable fast filtering by configuration ID.")
        
    except Exception as e:
        print(f"✗ Error creating config_id indexes: {e}")
    finally:
        await connector.close()


async def create_unique_constraints():
    """Create uniqueness constraints for core node identifiers.

    Ensures concurrent MERGE operations remain safe and prevents duplicates.
    """
    connector = Neo4jConnector()
    try:
        print("\n" + "=" * 70)
        print("Creating Unique Constraints")
        print("=" * 70)
        
        # Dialogue.id unique
        await connector.execute_query(
            """
            CREATE CONSTRAINT dialog_id_unique IF NOT EXISTS
            FOR (d:Dialogue) REQUIRE d.id IS UNIQUE
            """
        )
        print("✓ Created: dialog_id_unique")

        # Statement.id unique
        await connector.execute_query(
            """
            CREATE CONSTRAINT statement_id_unique IF NOT EXISTS
            FOR (s:Statement) REQUIRE s.id IS UNIQUE
            """
        )
        print("✓ Created: statement_id_unique")

        # Chunk.id unique
        await connector.execute_query(
            """
            CREATE CONSTRAINT chunk_id_unique IF NOT EXISTS
            FOR (c:Chunk) REQUIRE c.id IS UNIQUE
            """
        )
        print("✓ Created: chunk_id_unique")

        print("\nUnique constraints ensured for Dialogue, Statement, and Chunk.")
    except Exception as e:
        print(f"✗ Error creating unique constraints: {e}")
    finally:
        await connector.close()


async def create_all_indexes():
    """Create all indexes and constraints in one go."""
    print("\n" + "=" * 70)
    print("Neo4j Index & Constraint Setup")
    print("=" * 70)
    print("This will create:")
    print("  1. Full-text indexes (for keyword/BM25 search)")
    print("  2. Vector indexes (for embedding similarity search)")
    print("  3. Config ID indexes (for configuration isolation)")
    print("  4. Unique constraints (for data integrity)")
    print("=" * 70)
    
    await create_fulltext_indexes()
    await create_vector_indexes()
    await create_config_id_indexes()
    await create_unique_constraints()
    
    print("\n" + "=" * 70)
    print("✓ All indexes and constraints created successfully!")
    print("=" * 70)
    print("\nTo verify, run in Neo4j Browser:")
    print("  SHOW INDEXES")
    print("  SHOW CONSTRAINTS")
    print()


async def check_indexes():
    """Check what indexes currently exist."""
    connector = Neo4jConnector()
    
    try:
        print("\n" + "=" * 70)
        print("Checking Existing Indexes")
        print("=" * 70)
        
        query = "SHOW INDEXES"
        result = await connector.execute_query(query)
        
        fulltext_indexes = [idx for idx in result if idx.get('type') == 'FULLTEXT']
        vector_indexes = [idx for idx in result if idx.get('type') == 'VECTOR']
        range_indexes = [idx for idx in result if idx.get('type') == 'RANGE']
        
        print(f"\nFull-text indexes: {len(fulltext_indexes)}")
        for idx in fulltext_indexes:
            print(f"  ✓ {idx.get('name')}")
        
        print(f"\nVector indexes: {len(vector_indexes)}")
        for idx in vector_indexes:
            print(f"  ✓ {idx.get('name')}")
        
        print(f"\nRange indexes (including config_id): {len(range_indexes)}")
        for idx in range_indexes:
            print(f"  ✓ {idx.get('name')}")
        
        if not vector_indexes:
            print("\n⚠️  WARNING: No vector indexes found!")
            print("   Embedding search will be VERY SLOW (~1.4s)")
            print("   Run: python create_indexes.py")
        
        # Check for config_id indexes
        config_id_indexes = [idx for idx in range_indexes if 'config_id' in idx.get('name', '')]
        if len(config_id_indexes) < 4:
            print("\n⚠️  WARNING: Not all config_id indexes found!")
            print(f"   Expected 4, found {len(config_id_indexes)}")
            print("   Run: python create_indexes.py config_id")
        
        print("=" * 70)
        
    finally:
        await connector.close()


if __name__ == "__main__":
    import asyncio
    import sys
    
    if len(sys.argv) > 1:
        command = sys.argv[1]
        if command == "check":
            asyncio.run(check_indexes())
        elif command == "fulltext":
            asyncio.run(create_fulltext_indexes())
        elif command == "vector":
            asyncio.run(create_vector_indexes())
        elif command == "config_id":
            asyncio.run(create_config_id_indexes())
        elif command == "constraints":
            asyncio.run(create_unique_constraints())
        else:
            print(f"Unknown command: {command}")
            print("\nUsage:")
            print("  python create_indexes.py              # Create all indexes")
            print("  python create_indexes.py check        # Check existing indexes")
            print("  python create_indexes.py fulltext     # Create only full-text indexes")
            print("  python create_indexes.py vector       # Create only vector indexes")
            print("  python create_indexes.py config_id    # Create only config_id indexes")
            print("  python create_indexes.py constraints  # Create only constraints")
    else:
        asyncio.run(create_all_indexes())

