from abc import ABC, abstractmethod

from ..context import ChunkContext, MergeResult, ParseResult


class ChunkMerger(ABC):
    @abstractmethod
    def merge(self, ctx: ChunkContext, parse_result: ParseResult) -> MergeResult:
        pass
