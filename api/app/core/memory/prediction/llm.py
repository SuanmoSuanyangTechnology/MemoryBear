from __future__ import annotations

from pydantic import BaseModel

from app.core.models import RedBearChatModel


class StructuredLLM:
    """预测引擎的结构化调用壳：包一层现版本的统一模型客户端（RedBearChatModel）。

    历史实现走 ``LLMClient.response_structured_with_retry``，该抽象与配套的
    ``openai_client`` 模块已在新版本中下线；此处改为直接调用模型壳的
    ``call_structured``。
    """

    def __init__(self, client: RedBearChatModel) -> None:
        self._client = client
        self._calls = 0

    async def structured(
        self,
        system: str,
        prompt: str,
        response_model: type[BaseModel],
        *,
        temperature: float = 0.2,
        max_tokens: int | None = None,
    ) -> BaseModel:
        """结构化调用。

        说明：``temperature`` 与 ``max_tokens`` 由模型配置（memory_config）决定，
        本壳不下发，仅保留形参以兼容既有调用点。
        """
        self._calls += 1
        return await self._client.call_structured(
            [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
            response_model,
        )

    def usage(self) -> dict[str, int]:
        return {"calls": self._calls, "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
