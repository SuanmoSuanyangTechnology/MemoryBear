
from app.core.logging_config import get_api_logger
from app.db import get_db
from app.repositories.memory_short_repository import LongTermMemoryRepository
from app.repositories.memory_short_repository import ShortTermMemoryRepository


api_logger = get_api_logger()
db=next(get_db())
class ShortService:
    def __init__(self, end_user_id):
        self.short_repo = ShortTermMemoryRepository(db)
        self.end_user_id = end_user_id

    def get_short_databasets(self):
        short_memories = self.short_repo.get_latest_by_user_id(self.end_user_id, 3)
        short_result = []
        for memory in short_memories:
            deep_expanded = {}  # Create a new dictionary for each memory
            messages = memory.messages
            aimessages = memory.aimessages
            retrieved_content = memory.retrieved_content or []

            api_logger.debug(f"Retrieved content: {retrieved_content}")

            retrieval_source = []
            for item in retrieved_content:
                if isinstance(item, dict):
                    for key, values in item.items():
                        retrieval_source.append({"query": key, "retrieval": values,"source":"上下文记忆"})

            deep_expanded['retrieval'] = retrieval_source
            deep_expanded['message'] = messages  # 修正拼写错误
            deep_expanded['answer'] = aimessages
            short_result.append(deep_expanded)
        return short_result
    def get_short_count(self):
        short_count = self.short_repo.count_by_user_id(self.end_user_id)
        return short_count

class LongService:
    def __init__(self, end_user_id):
        self.long_repo = LongTermMemoryRepository(db)
        self.end_user_id = end_user_id
    def get_long_databasets(self):
        # 获取长期记忆数据
        long_memories = self.long_repo.get_by_user_id(self.end_user_id, 1)

        long_result = []
        for long_memory in long_memories:
            if long_memory.retrieved_content:
                for memory_item in long_memory.retrieved_content:
                    if isinstance(memory_item, dict):
                        for key, values in memory_item.items():
                            long_result.append({"query": key, "retrieval": values})
        return long_result
