import logging

from app.core.models import RedBearLLM
from app.core.memory.prompt import prompt_manager
from app.core.memory.utils.llm.llm_utils import StructResponse

logger = logging.getLogger(__name__)


class RetrievalSummaryProcessor:
    @staticmethod
    async def summary(query, content: str, memory_l0_str: str, llm_client: RedBearLLM):
        system_prompt = prompt_manager.render(
            name="retrieval_summary"
        )
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user",
             "content": f"<query>{query}</query>"
                        f"<content>{content}{memory_l0_str}</content>"},
        ]
        try:
            summary = await llm_client.ainvoke(messages) | StructResponse(mode='str')
            return summary
        except:
            logger.error("Failed to generate reply summary, returning original content", exc_info=True)
            return content

    @staticmethod
    async def verify(query, content: str, llm_client: RedBearLLM):
        return
