import logging
import re
from typing import Callable

from app.core.memory.models.service_models import QuestionSplit
from app.core.memory.exceptions import (
    MemoryModelType,
    MemoryRetrievalBusinessError,
    MemoryRetrievalStage,
)
from app.core.utils.datetime_utils import utcnow_naive
from app.core.memory.prompt import prompt_manager
from app.core.models.llm import StructResponse
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
    async def split(
        query: str,
        history: list,
        memory_l0_str: str,
        llm_client: RedBearLLM,
        on_error: Callable[[MemoryRetrievalBusinessError], None] | None = None,
    ) -> list:
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
            response = await llm_client.ainvoke(messages, config={
                "callbacks": []
            })
        except Exception as e:
            logger.error(f"[QueryPreprocessor] Sub-question segmentation failed - {e}")
            if on_error is not None:
                on_error(
                    MemoryRetrievalBusinessError.model_call_failed(
                        MemoryRetrievalStage.QUERY_PROCESS,
                        e,
                        model_type=MemoryModelType.LLM,
                    )
                )
            return [query]

        try:
            sub_queries = response | StructResponse(QuestionSplit)
            queries = sub_queries.questions
        except Exception as e:
            logger.error(f"[QueryPreprocessor] Sub-question segmentation failed - {e}")
            if on_error is not None:
                on_error(
                    MemoryRetrievalBusinessError.structured_result_parse_failed(
                        MemoryRetrievalStage.QUERY_PROCESS,
                        e,
                        model_type=MemoryModelType.LLM,
                    )
                )
            queries = [query]
        return queries
