from src.storage.enums import BackendType
from src.storage.provider.elasticsearch.client import ElasticClient
from src.storage.provider.factory import BackendFactory

BackendFactory.register(BackendType.ELASTIC, ElasticClient)
