from enum import StrEnum


class StorageType(StrEnum):
    NEO4J = 'neo4j'
    RAG = 'rag'


class Neo4jStorageStrategy(StrEnum):
    WINDOW = 'window'
    TIMELINE = 'timeline'
    AGGREGATE = "aggregate"


class SearchStrategy(StrEnum):
    DEEP = "0"
    NORMAL = "1"
    QUICK = "2"
