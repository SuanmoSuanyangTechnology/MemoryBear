from __future__ import annotations

from pydantic import BaseModel

from app.core.memory.llm_tools.llm_client import LLMClient


class StructuredLLM:
    def __init__(self, client: LLMClient) -> None:
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
        self._calls += 1
        kwargs = {"temperature": temperature}
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        return await self._client.response_structured_with_retry(
            [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
            response_model,
            **kwargs,
        )

    def usage(self) -> dict[str, int]:
        return {"calls": self._calls, "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
