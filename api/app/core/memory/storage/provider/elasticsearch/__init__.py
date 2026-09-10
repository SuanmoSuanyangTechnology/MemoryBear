from app.core.memory.storage.enums import BackendType
from app.core.memory.storage.provider.elasticsearch.client import ElasticClient
from app.core.memory.storage.provider.factory import BackendFactory

BackendFactory.register(BackendType.ELASTIC, ElasticClient)
