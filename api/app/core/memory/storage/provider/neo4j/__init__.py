from app.core.memory.storage.enums import BackendType
from app.core.memory.storage.provider.factory import BackendFactory
from app.core.memory.storage.provider.neo4j.client import Neo4jClient

BackendFactory.register(BackendType.NEO4J, Neo4jClient)