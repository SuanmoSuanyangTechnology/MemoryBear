"""Unified LangChain-compatible LLM runtime."""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator, Iterator
from typing import Any

from json_repair import json_repair
from langchain_core.callbacks import (
    AsyncCallbackManagerForLLMRun,
    CallbackManagerForLLMRun,
)
from langchain_core.language_models import BaseLLM
from langchain_core.messages import AIMessage
from langchain_core.outputs import GenerationChunk, LLMResult
from langchain_core.runnables import Runnable

from redbear_model.contracts import ModelProvider, ModelType, ResolvedModelConfig
from redbear_model.errors import (
    UnsupportedModelProviderError,
    is_provider_rate_limit_error,
)
from redbear_model.providers.bedrock import (
    build_bedrock_params,
    load_bedrock_chat_class,
)
from redbear_model.providers.dashscope import (
    build_dashscope_params,
    load_dashscope_chat_class,
)
from redbear_model.providers.ollama import build_ollama_params, load_ollama_llm_class
from redbear_model.providers.openai import (
    CompatibleChatOpenAI,
    build_openai_compatible_params,
)
from redbear_model.telemetry import (
    ModelTelemetry,
    NoOpModelTelemetry,
    report_failure_safely,
)

from .client_pool import ModelClientPool

logger = logging.getLogger(__name__)


class StructResponse:
    """Repair provider JSON and convert it to a requested schema."""

    def __init__(self, schema: dict[str, Any] | type):
        self.schema = schema

    def __ror__(self, other: AIMessage | str) -> Any:
        return self._convert(self.extract_text(other))

    @staticmethod
    def parse(text: str, schema: dict[str, Any] | type) -> Any:
        return StructResponse(schema)._convert(text)

    @staticmethod
    def extract_text(other: AIMessage | str) -> str:
        if isinstance(other, str):
            return other
        if isinstance(other, AIMessage):
            content = other.content
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                return "".join(
                    str(block.get("text", ""))
                    for block in content
                    if isinstance(block, dict) and block.get("type") == "text"
                )
            return str(content) if content else ""
        raise RuntimeError(f"Unsupported structured response type: {type(other)}")

    def _convert(self, text: str) -> Any:
        repaired = json_repair.repair_json(text, return_objects=True)
        if isinstance(self.schema, type) and hasattr(self.schema, "model_validate"):
            return self.schema.model_validate(repaired)
        return repaired


class _ObservedRunnable(Runnable):
    def __init__(
        self,
        runnable: Any,
        config: ResolvedModelConfig,
        operation: str,
        telemetry: ModelTelemetry,
    ):
        self._runnable = runnable
        self._config = config
        self._operation = operation
        self._telemetry = telemetry

    def invoke(self, input: Any, config: dict | None = None, **kwargs: Any) -> Any:
        started = time.perf_counter()
        try:
            return self._runnable.invoke(input, config=config, **kwargs)
        except Exception as exc:
            report_failure_safely(
                self._telemetry,
                self._config,
                operation=self._operation,
                exc=exc,
                started_at=started,
            )
            raise

    async def ainvoke(
        self,
        input: Any,
        config: dict | None = None,
        **kwargs: Any,
    ) -> Any:
        started = time.perf_counter()
        try:
            return await self._runnable.ainvoke(input, config=config, **kwargs)
        except Exception as exc:
            report_failure_safely(
                self._telemetry,
                self._config,
                operation=self._operation,
                exc=exc,
                started_at=started,
            )
            raise

    def stream(self, input: Any, config: dict | None = None, **kwargs: Any):
        started = time.perf_counter()
        try:
            yield from self._runnable.stream(input, config=config, **kwargs)
        except Exception as exc:
            report_failure_safely(
                self._telemetry,
                self._config,
                operation=f"{self._operation}.stream",
                exc=exc,
                started_at=started,
            )
            raise

    async def astream(
        self,
        input: Any,
        config: dict | None = None,
        **kwargs: Any,
    ):
        started = time.perf_counter()
        try:
            async for chunk in self._runnable.astream(
                input,
                config=config,
                **kwargs,
            ):
                yield chunk
        except Exception as exc:
            report_failure_safely(
                self._telemetry,
                self._config,
                operation=f"{self._operation}.astream",
                exc=exc,
                started_at=started,
            )
            raise

    def __getattr__(self, name: str) -> Any:
        return getattr(self._runnable, name)


def _create_provider_model(
    config: ResolvedModelConfig,
    pool: ModelClientPool,
):
    provider = config.provider
    if provider in {
        ModelProvider.OPENAI,
        ModelProvider.XINFERENCE,
        ModelProvider.GPUSTACK,
        ModelProvider.SPEEDBEAR,
        ModelProvider.VOLCANO,
    } or (provider is ModelProvider.DASHSCOPE and config.is_omni):
        return CompatibleChatOpenAI(
            **build_openai_compatible_params(config, pool.get_http_clients())
        )
    if provider is ModelProvider.DASHSCOPE:
        return load_dashscope_chat_class()(**build_dashscope_params(config))
    if provider is ModelProvider.OLLAMA:
        return load_ollama_llm_class()(**build_ollama_params(config))
    if provider is ModelProvider.BEDROCK:
        return load_bedrock_chat_class()(**build_bedrock_params(config))
    raise UnsupportedModelProviderError(provider.value)


class RedBearLLM(BaseLLM):
    """Delegate LangChain LLM behavior to the resolved provider runtime."""

    def __init__(
        self,
        config: ResolvedModelConfig,
        model_type: ModelType | None = None,
        *,
        model: Any | None = None,
        telemetry: ModelTelemetry | None = None,
        client_pool: ModelClientPool | None = None,
    ):
        super().__init__()
        pool = client_pool or ModelClientPool(config.runtime)
        object.__setattr__(self, "_config", config)
        object.__setattr__(self, "_telemetry", telemetry or NoOpModelTelemetry())
        object.__setattr__(self, "_client_pool", pool)
        object.__setattr__(self, "_owns_pool", client_pool is None)
        object.__setattr__(self, "_model_type", model_type or config.model_type)
        object.__setattr__(
            self,
            "_model",
            model if model is not None else _create_provider_model(config, pool),
        )

    @property
    def _llm_type(self) -> str:
        return getattr(self._model, "_llm_type", "redbear_llm")

    def _observe(self, operation: str, call):
        started = time.perf_counter()
        try:
            return call()
        except Exception as exc:
            report_failure_safely(
                self._telemetry,
                self._config,
                operation=operation,
                exc=exc,
                started_at=started,
            )
            raise

    async def _observe_async(self, operation: str, call):
        started = time.perf_counter()
        try:
            return await call()
        except Exception as exc:
            report_failure_safely(
                self._telemetry,
                self._config,
                operation=operation,
                exc=exc,
                started_at=started,
            )
            raise

    def _generate(
        self,
        prompts: list[str],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> LLMResult:
        method = getattr(self._model, "_generate", None) or self._model.generate
        return self._observe(
            "generate",
            lambda: method(
                prompts,
                stop=stop,
                run_manager=run_manager,
                **kwargs,
            ),
        )

    async def _agenerate(
        self,
        prompts: list[str],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> LLMResult:
        method = getattr(self._model, "_agenerate", None) or self._model.agenerate
        return await self._observe_async(
            "agenerate",
            lambda: method(
                prompts,
                stop=stop,
                run_manager=run_manager,
                **kwargs,
            ),
        )

    def invoke(self, input: Any, config: dict | None = None, **kwargs: Any) -> Any:
        started = time.perf_counter()
        try:
            return self._model.invoke(input, config=config, **kwargs)
        except AttributeError as exc:
            if "invoke" in str(exc):
                return super().invoke(input, config=config, **kwargs)
            raise
        except Exception as exc:
            report_failure_safely(
                self._telemetry,
                self._config,
                operation="invoke",
                exc=exc,
                started_at=started,
            )
            raise

    async def ainvoke(
        self,
        input: Any,
        config: dict | None = None,
        **kwargs: Any,
    ) -> Any:
        started = time.perf_counter()
        try:
            return await self._model.ainvoke(input, config=config, **kwargs)
        except AttributeError as exc:
            if "ainvoke" in str(exc):
                return await super().ainvoke(input, config=config, **kwargs)
            raise
        except Exception as exc:
            report_failure_safely(
                self._telemetry,
                self._config,
                operation="ainvoke",
                exc=exc,
                started_at=started,
            )
            raise

    def stream(
        self,
        input: Any,
        config: dict | None = None,
        *,
        stop: list[str] | None = None,
        **kwargs: Any,
    ) -> Iterator[GenerationChunk]:
        started = time.perf_counter()
        try:
            stream_kwargs = dict(kwargs)
            if stop is not None:
                stream_kwargs["stop"] = stop
            yield from self._model.stream(input, config=config, **stream_kwargs)
        except Exception as exc:
            report_failure_safely(
                self._telemetry,
                self._config,
                operation="stream",
                exc=exc,
                started_at=started,
            )
            raise

    async def astream(
        self,
        input: Any,
        config: dict | None = None,
        *,
        stop: list[str] | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[GenerationChunk]:
        started = time.perf_counter()
        try:
            stream_kwargs = dict(kwargs)
            if stop is not None:
                stream_kwargs["stop"] = stop
            async for chunk in self._model.astream(
                input,
                config=config,
                **stream_kwargs,
            ):
                yield chunk
        except Exception as exc:
            report_failure_safely(
                self._telemetry,
                self._config,
                operation="astream",
                exc=exc,
                started_at=started,
            )
            raise

    def with_structured_output(
        self,
        schema: dict[str, Any] | type,
        **kwargs: Any,
    ) -> Any:
        method = getattr(self._model, "with_structured_output", None)
        if not callable(method):
            raise NotImplementedError(
                f"Underlying model {type(self._model).__name__} does not implement "
                "with_structured_output"
            )
        return _ObservedRunnable(
            method(schema, **kwargs),
            self._config,
            "structured_output",
            self._telemetry,
        )

    async def call_structured(
        self,
        input: Any,
        schema: dict[str, Any] | type,
        **kwargs: Any,
    ) -> Any:
        try:
            result = await self.with_structured_output(schema, **kwargs).ainvoke(input)
            if result is not None:
                return result
        except Exception as exc:
            if is_provider_rate_limit_error(exc):
                raise
            logger.warning(
                "Structured output fallback activated for provider=%s model=%s",
                self._config.provider.value,
                self._config.model_name,
                exc_info=True,
            )
        return (await self.ainvoke(input)) | StructResponse(schema)

    def get_config(self) -> ResolvedModelConfig:
        return self._config

    def get_underlying_model(self) -> Any:
        return self._model

    def close(self) -> None:
        if self._owns_pool:
            self._client_pool.close()

    async def aclose(self) -> None:
        if self._owns_pool:
            await self._client_pool.aclose()

    def __getattr__(self, name: str) -> Any:
        if name.startswith("__"):
            raise AttributeError(name)
        model = object.__getattribute__(self, "_model")
        attribute = getattr(model, name)
        if not callable(attribute):
            return attribute
        if name in {"_stream", "_astream"}:
            return attribute

        def delegated(*args: Any, **kwargs: Any) -> Any:
            result = attribute(*args, **kwargs)
            if name in {"bind", "bind_tools"} and hasattr(result, "invoke"):
                return _ObservedRunnable(
                    result,
                    self._config,
                    name,
                    self._telemetry,
                )
            return result

        return delegated

    def __repr__(self) -> str:
        return (
            "RedBearLLM("
            f"provider={self._config.provider.value}, "
            f"model={self._config.model_name}, "
            f"type={type(self._model).__name__})"
        )
