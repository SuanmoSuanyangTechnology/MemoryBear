from abc import ABC, abstractmethod
from typing import Any


class DocumentParser(ABC):
    @abstractmethod
    def parse(self, ctx: Any):
        pass
