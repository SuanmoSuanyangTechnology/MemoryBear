from app.core.memory.storage.enums import BackendType, MemoryNodeLabel


class UnsupportedQueryError(Exception):
    def __init__(self, backend: BackendType, label: MemoryNodeLabel, query_type: str):
        super().__init__(f"DB {backend} Unsupported query {label} by {query_type}")