"""记录一次分块存储操作实际成功的写入，供调用方完成派生数据交接。"""

from dataclasses import dataclass


@dataclass
class ChunkWriteResult:
    changed: bool = False

    def record(self, count: int) -> None:
        self.changed = self.changed or count > 0
