from src.storage.enums import BackendType
from src.storage.provider.factory import BackendFactory
from src.storage.provider.neo4j.client import Neo4jClient

BackendFactory.register(BackendType.NEO4J, Neo4jClient)

__all__ = ["Neo4jClient"]
