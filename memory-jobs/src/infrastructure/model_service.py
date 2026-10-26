"""Blocking model-service invoke channel used by the generation tasks.

Generation never holds a provider credential: it names a ``model_configs`` row
and the tenant it acts for, and the model service picks the channel, decrypts
the credential and emits usage. This is the synchronous twin of
``memory-profile/src/infrastructure/model_service.py``; the HTTP pool is a
thread-safe ``httpx.Client`` so a ``--pool=threads`` worker shares one pool.
"""

from __future__ import annotations

import logging
import threading
from typing import Any
from uuid import UUID

import httpx
import json_repair
from langchain_core.messages import AIMessage
from redbear_model.contracts import ModelType
from redbear_model.runtime.remote import (
    InvokeRequest,
    InvokeTarget,
    InvokeTimeouts,
    RemoteRedBearChatModel,
    SyncInvokeTransport,
)

from src.common.context import get_task_id
from src.config import JobsSettings

logger = logging.getLogger(__name__)

#: 与 api ``MODEL_SOURCE_INTERNAL_API`` 同值：模型服务的内部调用面。
MODEL_SOURCE_INTERNAL_API = "in_api"


class StructResponse:
    """Fallback structured-output parser (api ``StructResponse`` 同语义).

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


class SyncChatModel(RemoteRedBearChatModel):
    """Remote chat model with the blocking ``call_structured`` vocabulary."""

    def call_structured(
        self, input: Any, schema: dict[str, Any] | type, **kwargs: Any
    ) -> Any:
        """``with_structured_output`` first, repaired-JSON fallback second."""

        try:
            chain = self.with_structured_output(schema, **kwargs)
            result = chain.invoke(input)
            if result is not None:
                return result
        except Exception:
            logger.warning(
                "call_structured: with_structured_output failed; falling back to repaired JSON",
                exc_info=True,
            )
        response = self.invoke(input)
        return response | StructResponse(schema)


class ModelServiceManager:
    """Own the invoke HTTP pool and build chat models over it."""

    def __init__(self, settings: JobsSettings) -> None:
        self._settings = settings
        self._client: httpx.Client | None = None
        self._lock = threading.Lock()

    @property
    def initialized(self) -> bool:
        return self._client is not None

    def client(self) -> httpx.Client:
        if self._client is not None:
            return self._client
        with self._lock:
            if self._client is None:
                self._client = httpx.Client(
                    base_url=self._settings.model_service_base_url.rstrip("/") + "/",
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
            return self._client

    def _transport(self, timeout_seconds: float | None = None) -> SyncInvokeTransport:
        """Build a transport, optionally with one call's idle budget.

        Interest distribution runs under a whole-call time budget, so each LLM
        call gets its own ``idle_s``; everything else uses the process defaults.
        """

        return SyncInvokeTransport(
            self.client(),
            base_url=self._settings.model_service_base_url.rstrip("/"),
            timeouts=InvokeTimeouts(
                connect_s=self._settings.model_service_connect_timeout_seconds,
                idle_s=(
                    self._settings.model_service_idle_timeout_seconds
                    if timeout_seconds is None
                    else max(1.0, timeout_seconds)
                ),
                write_s=self._settings.model_service_write_timeout_seconds,
                pool_s=self._settings.model_service_pool_timeout_seconds,
            ),
        )

    @staticmethod
    def _target(tenant_id: UUID) -> InvokeTarget:
        """Attribution for one call: this service, this tenant, this task."""

        return InvokeTarget(
            tenant_id=tenant_id,
            source=MODEL_SOURCE_INTERNAL_API,
            trace_id=get_task_id() or None,
        )

    def chat_model(
        self,
        config_id: UUID,
        tenant_id: UUID,
        *,
        timeout_seconds: float | None = None,
    ) -> SyncChatModel:
        """Build a model bound to one configuration and tenant."""

        return SyncChatModel(
            config_id,
            transport=self._transport(timeout_seconds),
            target=self._target(tenant_id),
        )

    def embed_texts(
        self, config_id: UUID, tenant_id: UUID, texts: list[str]
    ) -> list[list[float]]:
        """Embed a text batch through the embedding family (kept alongside chat)."""

        if not texts:
            return []
        request = InvokeRequest(
            config_id=config_id,
            type=ModelType.EMBEDDING,
            params={"input": list(texts)},
            stream=False,
        )
        data = self._transport().call(request, self._target(tenant_id))
        return [[float(value) for value in vector] for vector in data["vectors"]]

    def reset_after_fork(self) -> None:
        """Drop a parent-owned pool without closing the parent's sockets."""

        self._client = None

    def close(self) -> None:
        client = self._client
        self._client = None
        if client is not None:
            client.close()
