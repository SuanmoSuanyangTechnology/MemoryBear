"""TODO"""
from app.core.memory.storage.provider.base import BaseClient
from app.core.memory.storage.enums import BackendType


class BackendFactory:
    BACKENDS: dict[BackendType,type[BaseClient]] = {}

    def get_write_client(self):
        return self.BACKENDS[BackendType.NEO4J]

    def get_read_client(self):
        pass

    @classmethod
    def register(cls, name: BackendType, obj: type[BaseClient]):
        cls.BACKENDS[name] = obj



