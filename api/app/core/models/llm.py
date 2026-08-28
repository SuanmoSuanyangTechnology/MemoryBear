from __future__ import annotations
import asyncio
import logging
import time
from typing import Any, Iterator, AsyncIterator, List, Optional, Literal, Type

from json_repair import json_repair
from langchain_core.callbacks import CallbackManagerForLLMRun, AsyncCallbackManagerForLLMRun
from langchain_core.language_models import BaseLLM
from langchain_core.messages import AIMessage
from langchain_core.outputs import LLMResult, GenerationChunk
from langchain_core.runnables import Runnable
from app.core.alert_metric_bridge import (
    report_model_gateway_failure,
    report_model_gateway_failure_async,
    report_model_gateway_success,
    report_model_gateway_success_async,
)
from app.core.models import RedBearModelConfig, RedBearModelFactory, get_provider_llm_class
from app.core.models.network_retry import (
    NETWORK_RETRYABLE,
    NETWORK_RETRY_ATTEMPTS,
    network_retry,
)
from app.models.models_model import ModelType


class StructResponse:
    """Fallback post-processor: extract text, repair JSON, convert to target format.

    The *schema* parameter mirrors ``with_structured_output``:
    - Pydantic class → returns Pydantic instance (``model_validate``)
    - ``dict`` (JSON Schema) → returns ``dict``
    - ``TypedDict`` → returns ``dict``

    Designed as a fallback when the provider does not implement
    ``with_structured_output`` (raises ``NotImplementedError``).

    Pipe usage::

        ai_msg | StructResponse(MyModel)      → MyModel instance
        ai_msg | StructResponse(json_schema)   → dict

    Direct parse::

        StructResponse.parse(text, MyModel)    → MyModel instance
        StructResponse.parse(text, schema)     → dict
    """

    def __init__(self, schema: dict[str, Any] | type):
        self.schema = schema

    # ── Pipe target ──────────────────────────────────────────────────

    def __ror__(self, other: AIMessage | str) -> Any:
        """``left | self`` — extract text, repair JSON, convert."""
        text = self.extract_text(other)
        return self._convert(text)

    # ── Direct parse ─────────────────────────────────────────────────

    @staticmethod
    def parse(text: str, schema: dict[str, Any] | type) -> Any:
        """Parse *text* directly without using the pipe operator."""
        return StructResponse(schema)._convert(text)

    # ── Internal helpers ─────────────────────────────────────────────

    @staticmethod
    def extract_text(other: AIMessage | str) -> str:
        if isinstance(other, str):
            return other
        if isinstance(other, AIMessage):
            content = getattr(other, "content", None)
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                text = ""
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        text += block.get("text", "")
                return text
            return str(content) if content else ""
        raise RuntimeError(f"Unsupported struct type {type(other)}")

    def _convert(self, text: str) -> Any:
        fixed_json = json_repair.repair_json(text, return_objects=True)
        schema = self.schema
        if isinstance(schema, type) and hasattr(schema, "model_validate"):
            return schema.model_validate(fixed_json)
        return fixed_json


class _ObservedRunnable(Runnable):
    """观察由 bind/bind_tools/with_structured_output 派生出的真实调用。"""

    def __init__(self, runnable: Any, config: RedBearModelConfig, operation: str):
        self._runnable = runnable
        self._model_config = config
        self._operation = operation

    def invoke(self, input: Any, config: Optional[dict] = None, **kwargs: Any) -> Any:
        started = time.perf_counter()
        try:
            result = self._runnable.invoke(input, config=config, **kwargs)
        except Exception as exc:
            report_model_gateway_failure(
                self._model_config, self._operation, exc, started
            )
            raise
        report_model_gateway_success(self._model_config, self._operation, started)
        return result

    async def ainvoke(self, input: Any, config: Optional[dict] = None, **kwargs: Any) -> Any:
        started = time.perf_counter()
        try:
            result = await self._runnable.ainvoke(input, config=config, **kwargs)
        except Exception as exc:
            await report_model_gateway_failure_async(
                self._model_config, self._operation, exc, started
            )
            raise
        await report_model_gateway_success_async(self._model_config, self._operation, started)
        return result

    def stream(self, input: Any, config: Optional[dict] = None, **kwargs: Any):
        started = time.perf_counter()
        try:
            yield from self._runnable.stream(input, config=config, **kwargs)
        except Exception as exc:
            report_model_gateway_failure(
                self._model_config, f"{self._operation}.stream", exc, started
            )
            raise
        # 流被完整消费后才算调用成功；中途被放弃（GeneratorExit）不会走到这里。
        report_model_gateway_success(self._model_config, f"{self._operation}.stream", started)

    async def astream(self, input: Any, config: Optional[dict] = None, **kwargs: Any):
        started = time.perf_counter()
        try:
            async for chunk in self._runnable.astream(input, config=config, **kwargs):
                yield chunk
        except Exception as exc:
            await report_model_gateway_failure_async(
                self._model_config, f"{self._operation}.astream", exc, started
            )
            raise
        await report_model_gateway_success_async(
            self._model_config, f"{self._operation}.astream", started
        )

    def __getattr__(self, name: str) -> Any:
        return getattr(self._runnable, name)


class RedBearLLM(BaseLLM):
    """
    RedBear LLM Model Wrapper
    
    This wrapper provides a unified interface to access different LLM providers,
    while maintaining all LangChain functionality, including streaming output.
    
    Features:
    - Support for multiple LLM providers (OpenAI, Qwen, Ollama, etc.)
    - Full streaming output support
    - Elegant error handling and fallback mechanism
    - Automatic proxying of all underlying model methods and attributes
    """

    def __init__(self, config: RedBearModelConfig, type: ModelType = ModelType.LLM):
        """Initialize RedBear LLM wrapper
        
        Args:
            config: Model configuration
            type: Model type (LLM or CHAT)
        """
        super().__init__()
        self._config = config
        self._model = self._create_model(config, type)

    @property
    def _llm_type(self) -> str:
        """Return LLM type identifier"""
        return getattr(self._model, '_llm_type', 'redbear_llm')

    # ==================== Core Methods (Required by BaseLLM) ====================

    def _generate(
            self,
            prompts: List[str],
            stop: Optional[List[str]] = None,
            run_manager: Optional[CallbackManagerForLLMRun] = None,
            **kwargs: Any
    ) -> LLMResult:
        """Synchronous text generation (required by BaseLLM)"""
        started = time.perf_counter()
        try:
            result = self._model._generate(prompts, stop=stop, run_manager=run_manager, **kwargs)
        except Exception as exc:
            report_model_gateway_failure(self._config, "generate", exc, started)
            raise
        report_model_gateway_success(self._config, "generate", started)
        return result

    async def _agenerate(
            self,
            prompts: List[str],
            stop: Optional[List[str]] = None,
            run_manager: Optional[AsyncCallbackManagerForLLMRun] = None,
            **kwargs: Any
    ) -> LLMResult:
        """Asynchronous text generation (required by BaseLLM)"""
        started = time.perf_counter()
        try:
            result = await self._model._agenerate(prompts, stop=stop, run_manager=run_manager, **kwargs)
        except Exception as exc:
            await report_model_gateway_failure_async(self._config, "agenerate", exc, started)
            raise
        await report_model_gateway_success_async(self._config, "agenerate", started)
        return result

    # ==================== Advanced Methods (Support Message Lists) ====================

    def invoke(self, input: Any, config: Optional[dict] = None, **kwargs: Any) -> Any:
        """Synchronous model invocation
        
        Supports various input formats including strings and message lists.
        Directly delegates to the underlying model to avoid BaseLLM's string conversion.
        
        Args:
            input: Input (string, message list, etc.)
            config: Runtime configuration
            **kwargs: Additional arguments
            
        Returns:
            Model response
        """
        started = time.perf_counter()
        try:
            return self._invoke_with_retry(input, config, kwargs)
        except AttributeError as e:
            if 'invoke' in str(e):
                # Underlying model doesn't support invoke, fallback to parent implementation
                return super().invoke(input, config=config, **kwargs)
            raise
        except Exception as exc:
            report_model_gateway_failure(self._config, "invoke", exc, started)
            raise

    @network_retry
    def _invoke_with_retry(self, input: Any, config: Optional[dict], kwargs: dict) -> Any:
        return self._model.invoke(input, config=config, **kwargs)

    async def ainvoke(self, input: Any, config: Optional[dict] = None, **kwargs: Any) -> Any:
        """Asynchronous model invocation
        
        Supports various input formats including strings and message lists.
        Directly delegates to the underlying model to avoid BaseLLM's string conversion.
        
        Args:
            input: Input (string, message list, etc.)
            config: Runtime configuration
            **kwargs: Additional arguments
            
        Returns:
            Model response
        """
        started = time.perf_counter()
        try:
            return await self._ainvoke_with_retry(input, config, kwargs)
        except AttributeError as e:
            if 'ainvoke' in str(e):
                # Underlying model doesn't support ainvoke, fallback to parent implementation
                result = await super().ainvoke(input, config=config, **kwargs)
                await report_model_gateway_success_async(self._config, "ainvoke", started)
                return result
            raise
        except Exception as exc:
            await report_model_gateway_failure_async(self._config, "ainvoke", exc, started)
            raise
        await report_model_gateway_success_async(self._config, "ainvoke", started)
        return result

    @network_retry
    async def _ainvoke_with_retry(self, input: Any, config: Optional[dict], kwargs: dict) -> Any:
        return await self._model.ainvoke(input, config=config, **kwargs)

    # ==================== Streaming Methods (Critical) ====================

    def stream(
            self,
            input: Any,
            config: Optional[dict] = None,
            *,
            stop: Optional[List[str]] = None,
            **kwargs: Any
    ) -> Iterator[GenerationChunk]:
        """Synchronous streaming model invocation
        
        Args:
            input: Input (string, message list, etc.)
            config: Runtime configuration
            stop: List of stop words
            **kwargs: Additional arguments
            
        Yields:
            GenerationChunk: Generated text chunks
        """
        started = time.perf_counter()
        try:
            yield from self._stream_with_retry(input, config, stop, kwargs)
        except AttributeError as e:
            if 'stream' in str(e):
                # Underlying model doesn't support stream, fallback to parent implementation
                try:
                    yield from super().stream(input, config=config, stop=stop, **kwargs)
                except Exception as fallback_exc:
                    report_model_gateway_failure(
                        self._config, "stream", fallback_exc, started
                    )
                    raise
            else:
                raise
        except Exception as exc:
            report_model_gateway_failure(self._config, "stream", exc, started)
            raise
        # 流被完整消费后才算调用成功（含回退路径）。
        report_model_gateway_success(self._config, "stream", started)

    def _stream_with_retry(
            self,
            input: Any,
            config: Optional[dict],
            stop: Optional[List[str]],
            kwargs: dict,
    ) -> Iterator[GenerationChunk]:
        """流式前置重试：仅在未产出任何 chunk 前失败时重试整个流，避免重复输出。"""
        attempt = 0
        while True:
            yielded = False
            try:
                for chunk in self._model.stream(input, config=config, stop=stop, **kwargs):
                    yielded = True
                    yield chunk
                return
            except NETWORK_RETRYABLE as exc:
                if yielded or attempt >= NETWORK_RETRY_ATTEMPTS:
                    raise
                attempt += 1
                logging.getLogger("business").warning(
                    "stream 网络错误，第 %d 次重试: %s", attempt, exc
                )
                time.sleep(min(2 ** (attempt - 1), 8))

    async def astream(
            self,
            input: Any,
            config: Optional[dict] = None,
            *,
            stop: Optional[List[str]] = None,
            **kwargs: Any
    ) -> AsyncIterator[GenerationChunk]:
        """Asynchronous streaming model invocation
        
        This is the core method for streaming output. It directly proxies to the
        underlying model's astream method, maintaining generator characteristics
        to ensure each chunk is delivered in real-time.
        
        Args:
            input: Input (string, message list, etc.)
            config: Runtime configuration
            stop: List of stop words
            **kwargs: Additional arguments
            
        Yields:
            GenerationChunk: Generated text chunks
        """
        started = time.perf_counter()
        try:
            async for chunk in self._astream_with_retry(input, config, stop, kwargs):
                yield chunk
        except AttributeError as e:
            if 'astream' in str(e):
                # Underlying model doesn't support astream, fallback to parent implementation
                try:
                    async for chunk in super().astream(input, config=config, stop=stop, **kwargs):
                        yield chunk
                except Exception as fallback_exc:
                    await report_model_gateway_failure_async(
                        self._config, "astream", fallback_exc, started
                    )
                    raise
            else:
                raise
        except Exception as exc:
            await report_model_gateway_failure_async(self._config, "astream", exc, started)
            raise
        await report_model_gateway_success_async(self._config, "astream", started)

    async def _astream_with_retry(
            self,
            input: Any,
            config: Optional[dict],
            stop: Optional[List[str]],
            kwargs: dict,
    ) -> AsyncIterator[GenerationChunk]:
        """流式前置重试：仅在未产出任何 chunk 前失败时重试整个流，避免重复输出。"""
        attempt = 0
        while True:
            yielded = False
            try:
                async for chunk in self._model.astream(input, config=config, stop=stop, **kwargs):
                    yielded = True
                    yield chunk
                return
            except NETWORK_RETRYABLE as exc:
                if yielded or attempt >= NETWORK_RETRY_ATTEMPTS:
                    raise
                attempt += 1
                logging.getLogger("business").warning(
                    "astream 网络错误，第 %d 次重试: %s", attempt, exc
                )
                await asyncio.sleep(min(2 ** (attempt - 1), 8))

    # ==================== Structured Output ====================

    def with_structured_output(self, schema: dict[str, Any] | type, **kwargs: Any) -> Any:
        """Delegate to the underlying model's with_structured_output.

        Must be explicitly overridden because RedBearLLM inherits from BaseLLM →
        BaseLanguageModel, which already defines with_structured_output as a stub
        that raises NotImplementedError.  Without this override, Python's MRO
        lookup finds the stub and __getattr__ is never invoked.
        """
        with_so = getattr(self._model, "with_structured_output", None)
        if callable(with_so):
            return _ObservedRunnable(
                with_so(schema, **kwargs), self._config, "structured_output"
            )
        raise NotImplementedError(
            f"Underlying model {type(self._model).__name__} does not implement "
            f"with_structured_output"
        )

    async def call_structured(
            self,
            input: Any,
            schema: dict[str, Any] | type,
            **kwargs: Any,
    ) -> Any:
        """Shortcut: build a structured-output chain and invoke it in one step.

        Primary path uses the provider's native ``with_structured_output``.
        If the provider does not support it (``NotImplementedError``), falls
        back to a normal ``ainvoke`` + :class:`StructResponse` which repairs
        JSON via ``json_repair`` and validates against the schema.

        Equivalent to::

            chain = llm.with_structured_output(schema, **kwargs)
            result = await chain.ainvoke(input)

        Args:
            input: Messages (list of dicts) or a string prompt.
            schema: A Pydantic class, TypedDict, or JSON Schema dict.
            **kwargs: Forwarded to ``with_structured_output`` (e.g. ``method``, ``strict``).

        Returns:
            A Pydantic instance (when *schema* is a Pydantic class) or a dict.
        """
        import logging
        _logger = logging.getLogger(__name__)

        try:
            chain = self.with_structured_output(schema, **kwargs)
            result = await chain.ainvoke(input)
            if result is not None:
                return result
        except NotImplementedError:
            _logger.warning(
                "call_structured: with_structured_output not supported by %s, "
                "falling back to ainvoke + StructResponse",
                type(self._model).__name__, exc_info=True
            )
        except Exception:
            _logger.warning(
                "call_structured: with_structured_output failed for %s, "
                "falling back to ainvoke + StructResponse",
                type(self._model).__name__, exc_info=True
            )

        response = await self.ainvoke(input)
        return response | StructResponse(schema)

    # ==================== Dynamic Proxy ====================

    def __getattr__(self, name: str) -> Any:
        """Dynamic proxy: delegate undefined attributes and method calls to internal model
        
        This method allows RedBearLLM to transparently access all attributes and methods
        of the underlying model without explicitly defining each one.
        
        Args:
            name: Attribute or method name
            
        Returns:
            Attribute value or method
            
        Raises:
            AttributeError: If attribute doesn't exist
        """
        # Avoid recursion: raise error directly for special attributes
        if name in ('__isabstractmethod__', '__dict__', '__class__', '_model', '_config'):
            raise AttributeError(f"'{type(self).__name__}' object has no attribute '{name}'")

        # Try to get attribute from internal model
        try:
            attr = object.__getattribute__(self._model, name)

            # If it's callable (a method)
            if callable(attr):
                # Streaming methods are returned directly to maintain generator characteristics
                # Note: Although we've explicitly implemented stream/astream,
                # this is kept to handle internal methods like _stream/_astream
                if name in ('_stream', '_astream'):
                    return attr

                # Wrap other methods for easier debugging and error handling
                def method_wrapper(*args, **kwargs):
                    try:
                        result = attr(*args, **kwargs)
                        if name in {"bind", "bind_tools"} and hasattr(result, "invoke"):
                            return _ObservedRunnable(result, self._config, name)
                        return result
                    except Exception:
                        # Can add logging or error handling here
                        raise

                # Preserve method metadata
                method_wrapper.__name__ = name
                method_wrapper.__doc__ = getattr(attr, '__doc__', f"Delegated method: {name}")
                return method_wrapper

            # If it's a regular attribute, return directly
            return attr

        except AttributeError:
            # Internal model doesn't have this attribute either
            pass

        # Check if there's a fallback method
        fallback_name = f'_fallback_{name}'
        try:
            return object.__getattribute__(self, fallback_name)
        except AttributeError:
            pass

        # Nothing found, raise error
        raise AttributeError(
            f"'{type(self).__name__}' object has no attribute '{name}'. "
            f"The underlying model '{type(self._model).__name__}' also doesn't have this attribute."
        )

    # ==================== Helper Methods ====================

    def _create_model(self, config: RedBearModelConfig, type: ModelType) -> BaseLLM:
        """Create internal model instance
        
        Args:
            config: Model configuration
            type: Model type
            
        Returns:
            Created model instance
        """
        llm_class = get_provider_llm_class(config, type)
        model_params = RedBearModelFactory.get_model_params(config)

        # ===== 调试日志：追踪惩罚参数是否真正传入 =====
        penalty_keys = {"repetition_penalty", "frequency_penalty", "presence_penalty"}
        penalty_in_params = {k: v for k, v in model_params.items() if k in penalty_keys}
        import logging
        _penalty_logger = logging.getLogger("business")
        _penalty_logger.info(
            f"[LLM惩罚参数追踪] provider={config.provider}, model={config.model_name}, "
            f"llm_class={llm_class.__name__}, "
            f"penalty_params={penalty_in_params or '无'}"
        )
        # ===== 调试日志 END =====

        return llm_class(**model_params)

    def get_config(self) -> RedBearModelConfig:
        """Get model configuration
        
        Returns:
            Model configuration object
        """
        return self._config

    def get_underlying_model(self) -> BaseLLM:
        """Get underlying model instance
        
        Returns:
            Underlying model instance
        """
        return self._model

    def __repr__(self) -> str:
        """Return string representation of the object"""
        return (
            f"RedBearLLM("
            f"provider={self._config.provider}, "
            f"model={self._config.model_name}, "
            f"type={type(self._model).__name__}"
            f")"
        )
