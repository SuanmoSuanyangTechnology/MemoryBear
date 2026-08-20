"""Node labels owned exclusively by storage tests."""

from app.core.memory.storage.enums import MemoryNodeLabel


class TestMemoryNodeType(MemoryNodeLabel):
    __test__ = False
    TEST = "Test"
