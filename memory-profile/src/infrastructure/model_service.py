"""Model-service invoke channel shared by the modules that call models.

Generation needs the chat family today (memory insight, user summary, interest
distribution); the same seam also exposes the embedding and rerank families so a
later module does not need a second client. Credentials, channel selection and
failover stay in the model service — this process never holds a provider key, it
only names the configuration and the tenant it acts for, exactly like the API
host does.

The HTTP pool is per event loop (httpx ``AsyncClient`` connections are bound to
the loop that created them) and is created lazily on first use.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import httpx
import json_repair
from langchain_core.messages import AIMessage
from redbear_model.contracts import ModelType
from redbear_model.runtime.remote import (
    AsyncInvokeTransport,
    InvokeRequest,
    InvokeTarget,
    InvokeTimeouts,
    RemoteRedBearChatModel,
)

from src.common.observability.context import get_trace_id
from src.config import ProfileSettings

logger = logging.getLogger(__name__)

#: 与 api ``MODEL_SOURCE_INTERNAL_API`` 同值：模型服务的内部调用面。
MODEL_SOURCE_INTERNAL_API = "in_api"


@dataclass(frozen=True, slots=True)
class RerankHit:
    """One reranked document: its position in the input batch plus the score."""

    index: int
    relevance_score: float


class StructResponse:
    """Fallback structured-output parser (api ``StructResponse`` 同语义)。

    Used when ``with_structured_output`` is unavailable for a provider: the raw
    completion text is repaired into JSON and validated against the schema.
    """

    def __init__(self, schema: dict[str, Any] | type) -> None:
        self.schema = schema

    def __ror__(self, other: AIMessage | str) -> Any:
        return self._convert(self.extract_text(other))

    @staticmethod
    def extract_text(other: AIMessage | str) -> str:
        if isinstance(other, str):
            return other
        if isinstance(other, AIMessage):
            return other.text
        raise RuntimeError(f"Unsupported struct type {type(other).__name__}")

    def _convert(self, text: str) -> Any:
        fixed_json = json_repair.repair_json(text, return_objects=True)
        schema = self.schema
        if isinstance(schema, type) and hasattr(schema, "model_validate"):
            return schema.model_validate(fixed_json)
        return fixed_json


class ProfileChatModel(RemoteRedBearChatModel):
    """Remote chat model with the ``call_structured`` vocabulary generation uses."""

    async def call_structured(
        self, input: Any, schema: dict[str, Any] | type, **kwargs: Any
    ) -> Any:
        """``with_structured_output`` first, repaired-JSON fallback second.

        Same two-step contract as the API host: a provider that cannot enforce a
        tool schema still yields a validated object, and the first failure is not
        propagated.
        """

        try:
            chain = self.with_structured_output(schema, **kwargs)
            result = await chain.ainvoke(input)
            if result is not None:
                return result
        except Exception:
            logger.warning(
                "call_structured: with_structured_output failed; falling back to repaired JSON",
                exc_info=True,
            )
        response = await self.ainvoke(input)
        return response | StructResponse(schema)


class ModelServiceManager:
    """Lazily own the invoke HTTP pool and build chat models over it."""

    def __init__(self, settings: ProfileSettings) -> None:
        self._settings = settings
        self._client: httpx.AsyncClient | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._lock = asyncio.Lock()

    async def client(self) -> httpx.AsyncClient:
        current_loop = asyncio.get_running_loop()
        if self._client is not None and self._loop is not current_loop:
            raise RuntimeError("Model service client cannot be shared across event loops")
        async with self._lock:
            if self._client is None:
                base_url = self._settings.model_service_base_url.rstrip("/") + "/"
                self._client = httpx.AsyncClient(
                    base_url=base_url,
                    timeout=httpx.Timeout(
                        connect=self._settings.model_service_connect_timeout_seconds,
                        read=self._settings.model_service_idle_timeout_seconds,
                        write=self._settings.model_service_write_timeout_seconds,
                        pool=self._settings.model_service_pool_timeout_seconds,
                    ),
                    limits=httpx.Limits(
                        max_connections=self._settings.model_service_max_connections,
                        max_keepalive_connections=(
                            self._settings.model_service_max_keepalive_connections
                        ),
                    ),
                    trust_env=False,
                )
                self._loop = current_loop
            return self._client

    def _transport(self, client: httpx.AsyncClient) -> AsyncInvokeTransport:
        return AsyncInvokeTransport(
            client,
            base_url=self._settings.model_service_base_url.rstrip("/"),
            timeouts=InvokeTimeouts(
                connect_s=self._settings.model_service_connect_timeout_seconds,
                idle_s=self._settings.model_service_idle_timeout_seconds,
                write_s=self._settings.model_service_write_timeout_seconds,
                pool_s=self._settings.model_service_pool_timeout_seconds,
            ),
        )

    @staticmethod
    def _target(tenant_id: UUID) -> InvokeTarget:
        """Attribution for one call: this service, this tenant, this trace."""

        return InvokeTarget(
            tenant_id=tenant_id,
            source=MODEL_SOURCE_INTERNAL_API,
            trace_id=get_trace_id() or None,
        )

    async def chat_model(self, config_id: UUID, tenant_id: UUID) -> ProfileChatModel:
        """Build a model bound to one configuration and tenant.

        ``config_id`` names a ``model_configs`` row; the model service resolves
        its credentials and channels. Attribution carries this service's own
        request trace id so model-service usage stays traceable end to end.
        """

        return ProfileChatModel(
            config_id,
            transport=self._transport(await self.client()),
            target=self._target(tenant_id),
        )

    async def embed_texts(
        self, config_id: UUID, tenant_id: UUID, texts: Sequence[str]
    ) -> list[list[float]]:
        """Embed a text batch through the embedding family.

        An empty batch short-circuits: the invoke contract rejects an empty
        ``input`` list, and "no texts" means "no vectors", not an error.
        """

        if not texts:
            return []
        request = InvokeRequest(
            config_id=config_id,
            type=ModelType.EMBEDDING,
            params={"input": list(texts)},
            stream=False,
        )
        data = await self._transport(await self.client()).call(
            request, self._target(tenant_id)
        )
        return [[float(value) for value in vector] for vector in data["vectors"]]

    async def rerank(
        self,
        config_id: UUID,
        tenant_id: UUID,
        *,
        query: str,
        documents: Sequence[str],
        top_n: int | None = None,
    ) -> list[RerankHit]:
        """Score ``documents`` against ``query`` through the rerank family."""

        if not documents:
            return []
        params: dict[str, Any] = {"query": query, "documents": list(documents)}
        if top_n is not None:
            params["top_n"] = top_n
        request = InvokeRequest(
            config_id=config_id,
            type=ModelType.RERANK,
            params=params,
            stream=False,
        )
        data = await self._transport(await self.client()).call(
            request, self._target(tenant_id)
        )
        return [
            RerankHit(
                index=int(item["index"]),
                relevance_score=float(item["relevance_score"]),
            )
            for item in data["results"]
        ]

    async def aclose(self) -> None:
        client = self._client
        self._client = None
        self._loop = None
        if client is not None:
            await client.aclose()
