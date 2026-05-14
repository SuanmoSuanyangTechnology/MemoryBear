"""ExtractionStep abstract base class and StepContext.

Provides the unified paradigm for all LLM extraction stages:
    render_prompt → call_llm → parse_response → post_process

Critical steps retry on failure with exponential backoff.
Sidecar (non-critical) steps return a default output on failure without retry.
"""

import asyncio
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Generic, Optional, TypeVar

logger = logging.getLogger(__name__)

InputT = TypeVar("InputT")
OutputT = TypeVar("OutputT")


async def call_structured(llm_client, messages: list[dict], response_model: type):
    """兼容所有 provider 的结构化输出工具函数。

    优先调用 llm_client.response_structured()；若 provider 不支持
    with_structured_output（如 DashScope ChatTongyi），捕获
    NotImplementedError 后降级到 chat + StructResponse（json_repair 兜底）。

    Args:
        llm_client: LLM 客户端实例
        messages: 消息列表，格式 [{"role": ..., "content": ...}]
        response_model: 目标 Pydantic 模型类

    Returns:
        解析后的 Pydantic 模型实例
    """
    from app.core.memory.utils.llm.llm_utils import StructResponse

    try:
        return await llm_client.response_structured(
            messages=messages,
            response_model=response_model,
        )
    except NotImplementedError:
        logger.warning("call_structured: 降级到 chat + StructResponse")
    except Exception as e:
        if isinstance(e.__cause__, NotImplementedError) or "NotImplementedError" in str(e):
            logger.warning(f"call_structured: 降级到 chat + StructResponse: {e}")
        else:
            raise

    ai_message = await llm_client.chat(messages=messages)
    return ai_message | StructResponse("pydantic", response_model)


@dataclass
class StepContext:
    """Shared context injected into every ExtractionStep by the orchestrator.

    Attributes:
        llm_client: LLM client instance for generating completions.
        language: Target language code (e.g. "en", "zh").
        config: Pipeline configuration object (ExtractionPipelineConfig).
        is_pilot_run: When True, run in lightweight preview mode.
        progress_callback: Optional callable for reporting progress.
    """

    llm_client: Any
    language: str
    config: Any
    is_pilot_run: bool = False
    progress_callback: Optional[Any] = None


class ExtractionStep(ABC, Generic[InputT, OutputT]):
    """Abstract base class for all LLM extraction stages.

    Lifecycle:
        1. ``__init__(context)`` — receive shared context, bind config params
        2. ``should_skip()`` — check whether to skip (config-driven / pilot mode)
        3. ``run(input_data)`` — execute full flow (with retry for critical steps)
           Internally: render_prompt → call_llm → parse_response → post_process
        4. ``on_failure(error)`` — critical steps raise; sidecar steps return default

    Type Parameters:
        InputT: The Pydantic model type accepted by this step.
        OutputT: The Pydantic model type produced by this step.
    """

    def __init__(self, context: StepContext) -> None:
        self.context = context
        self.llm_client = context.llm_client
        self.language = context.language
        self.config = context.config

    # ── Structured output helper ──

    async def call_structured(
        self,
        messages: list[dict],
        response_model: type,
    ):
        """兼容所有 provider 的结构化输出辅助方法。

        代理到模块级 call_structured()，子类的 call_llm 应使用此方法
        代替直接调用 self.llm_client.response_structured()。
        """
        return await call_structured(self.llm_client, messages, response_model)

    # ── Subclasses must implement ──

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable step name for logging."""
        ...

    @abstractmethod
    async def render_prompt(self, input_data: InputT) -> Any:
        """Build the prompt from *input_data* and bound config."""
        ...

    @abstractmethod
    async def call_llm(self, prompt: Any) -> Any:
        """Send *prompt* to the LLM and return the raw response."""
        ...

    @abstractmethod
    async def parse_response(self, raw_response: Any, input_data: InputT) -> OutputT:
        """Parse *raw_response* into a typed OutputT (Pydantic model)."""
        ...

    @abstractmethod
    def get_default_output(self) -> OutputT:
        """Return a safe default when the step is skipped or fails gracefully."""
        ...

    # ── Overridable properties ──

    @property
    def is_critical(self) -> bool:
        """``True`` = critical step (failure aborts pipeline).

        ``False`` = sidecar step (failure degrades gracefully).
        """
        return True

    @property
    def max_retries(self) -> int:
        """Maximum retry attempts (only effective for critical steps)."""
        return 2

    @property
    def retry_backoff_base(self) -> float:
        """Backoff base in seconds.  Actual wait = base × 2^attempt."""
        return 1.0

    # ── Overridable hooks ──

    def should_skip(self) -> bool:
        """Config-driven skip check.  Subclasses may override."""
        return False

    async def post_process(self, parsed_data: OutputT, input_data: InputT) -> OutputT:
        """Post-processing hook.  Default is identity (returns *parsed_data* unchanged)."""
        return parsed_data

    # ── Core execution logic ──

    async def run(self, input_data: InputT) -> OutputT:
        """Execute the full step lifecycle with retry logic.

        For critical steps (``is_critical=True``):
            Attempt up to ``max_retries + 1`` times with exponential backoff.
            If all attempts fail, delegate to ``on_failure`` which raises.

        For sidecar steps (``is_critical=False``):
            Attempt exactly once.  On failure, delegate to ``on_failure``
            which returns ``get_default_output()``.
        """
        if self.should_skip():
            logger.info("Step '%s' skipped", self.name)
            return self.get_default_output()

        last_error: Optional[Exception] = None
        attempts = self.max_retries + 1 if self.is_critical else 1

        for attempt in range(attempts):
            try:
                prompt = await self.render_prompt(input_data)
                raw_response = await self.call_llm(prompt)
                parsed = await self.parse_response(raw_response, input_data)
                result = await self.post_process(parsed, input_data)
                return result
            except Exception as exc:
                last_error = exc
                logger.warning(
                    "Step '%s' attempt %d/%d failed: %s",
                    self.name,
                    attempt + 1,
                    attempts,
                    exc,
                )
                if attempt < attempts - 1:
                    wait = self.retry_backoff_base * (2 ** attempt)
                    logger.info(
                        "Step '%s' retrying in %.1fs …", self.name, wait
                    )
                    await asyncio.sleep(wait)

        # All attempts exhausted — delegate to failure handler
        return self.on_failure(last_error)  # type: ignore[arg-type]

    def on_failure(self, error: Exception) -> OutputT:
        """Handle step failure.

        Critical steps: re-raise the exception to abort the pipeline.
        Sidecar steps: return ``get_default_output()`` for graceful degradation.
        """
        if self.is_critical:
            logger.error(
                "Critical step '%s' failed after retries: %s", self.name, error
            )
            raise error
        logger.warning(
            "Sidecar step '%s' failed, returning default output: %s",
            self.name,
            error,
        )
        return self.get_default_output()
