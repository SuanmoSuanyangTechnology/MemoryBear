import logging
import re

from app.core.utils.datetime_utils import utcnow_naive
from app.core.memory.prompt import prompt_manager
from app.core.memory.utils.llm.llm_utils import StructResponse
from app.core.models import RedBearLLM
from app.schemas.memory_agent_schema import AgentMemoryDataset

logger = logging.getLogger(__name__)


class QueryPreprocessor:
    @staticmethod
    def process(query: str) -> str:
        text = query.strip()
        if not text:
            return text

        text = re.sub(rf'{"|".join(AgentMemoryDataset.PRONOUN)}', AgentMemoryDataset.NAME, text)
        return text

    @staticmethod
    async def split(query: str, history: list, memory_l0_str: str, llm_client: RedBearLLM) -> tuple[list, str]:
        system_prompt = prompt_manager.render(
            name="problem_split",
            datetime=utcnow_naive().strftime("%Y-%m-%d"),
        )
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"<history>{history}</history>"
                                        f"<memory>{memory_l0_str}</memory>"
                                        f"<query>{query}</query>"},
        ]
        try:
            sub_queries = await llm_client.ainvoke(messages, config={
                "callbacks": []
            }) | StructResponse(mode='json')
            queries = sub_queries["questions"]
            answer = sub_queries.get("answer") or ""
        except Exception as e:
            logger.error(f"[QueryPreprocessor] Sub-question segmentation failed - {e}")
            queries = [query]
            answer = ""
        return queries, answer
