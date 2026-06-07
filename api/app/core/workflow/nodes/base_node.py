import asyncio
import logging
import re
import time
import threading
import uuid
from abc import ABC, abstractmethod
from datetime import datetime
from functools import cached_property
from typing import Any, AsyncGenerator

from langgraph.config import get_stream_writer
from langgraph.errors import GraphInterrupt

from app.core.config import settings
from app.core.workflow.node_cache import DEFAULT_CACHEABLE_NODE_TYPES, WorkflowNodeCacheManager
from app.core.workflow.engine.state_manager import WorkflowState
from app.core.workflow.engine.variable_pool import VariablePool
from app.core.workflow.nodes.enums import BRANCH_NODES
from app.core.workflow.variable.base_variable import VariableType, FileObject
from app.db import get_db_read
from app.models import ModelConfig, ModelApiKey, LoadBalanceStrategy
from app.schemas import FileInput
from app.schemas.model_schema import ModelInfo
from app.services.multimodal_service import MultimodalService

logger = logging.getLogger(__name__)

# 匹配模板变量 {{xxx}} 的正则
_TEMPLATE_PATTERN = re.compile(r"\{\{.*?\}\}")
_MAX_DB_INTEGER = 2_147_483_647
_execution_order = 0
_execution_order_lock = threading.Lock()


def _next_execution_order() -> int:
    """Return a DB-safe monotonic order value for workflow node logs."""
    global _execution_order
    with _execution_order_lock:
        _execution_order = (_execution_order % _MAX_DB_INTEGER) + 1
        return _execution_order


class NodeExecutionError(Exception):
    """节点执行失败异常。

    携带失败节点的完整 node_output，供 executor 兜底注入 node_outputs，
    保证 workflow_executions.output_data 里能看到失败节点的日志记录。
    """

    def __init__(self, node_id: str, node_output: dict[str, Any], error_message: str):
        super().__init__(f"Node {node_id} execution failed: {error_message}")
        self.node_id = node_id
        self.node_output = node_output
        self.error_message = error_message


class BaseNode(ABC):
    """Base class for workflow nodes.

    All node types should inherit from this class and implement the `execute` method.
    """

    def __init__(self, node_config: dict[str, Any], workflow_config: dict[str, Any], down_stream_nodes: list[str]):
        """Initialize the node.

        Args:
            node_config: Configuration of the node.
            workflow_config: Configuration of the workflow.
        """
        self.node_config = node_config
        self.workflow_config = workflow_config
        self.node_id = node_config["id"]
        self.node_type = node_config["type"]
        self.cycle = node_config.get("cycle")
        self.node_name = node_config.get("name", self.node_id)
        self.down_stream_nodes = down_stream_nodes
        # 使用 or 运算符处理 None 值
        self.config = node_config.get("config") or {}
        self.error_handling = node_config.get("error_handling") or {}
        self.cache_config = node_config.get("cache") or {}

        self.variable_change_able = False

    @cached_property
    def output_types(self) -> dict[str, VariableType]:
        """Returns the output variable types of the node.

        This property is cached to avoid recomputation.
        """
        return self._output_types()

    @abstractmethod
    def _output_types(self) -> dict[str, VariableType]:
        """Defines output variable types for the node.

        Subclasses must override this method to declare the variables
        produced by the node and their corresponding types.

        Returns:
            A mapping from output variable names to ``VariableType``.
        """
        return {}

    def check_activate(self, state: WorkflowState):
        """Check if the current node is activated in the workflow state.

        Args:
            state (WorkflowState): The current workflow state containing the 'activate' dict.

        Returns:
            bool: True if the node is activated, False otherwise.
            Returns False if the node_id is not present in the activate dict
            (e.g. during LangGraph resume when nodes that completed before the
            interrupt are re-scheduled but have no activation signal).
        """
        activate = state.get("activate", {})
        if self.node_id not in activate:
            # During resume, LangGraph may re-schedule nodes that completed
            # before the interrupt. These nodes have no activation signal in
            # the checkpoint state because their trans_activate already ran.
            # Treat them as deactivated so they skip execution and propagate
            # False to downstream nodes (which are also already completed).
            return False
        return activate[self.node_id]

    def trans_activate(self, state: WorkflowState):
        """Transform the activation state for downstream nodes.

        This method collects all downstream nodes (excluding branch nodes)
        connected to the current node and returns a dict indicating whether
        each of these nodes should be activated based on the current node's state.
        The current node itself is also included in the returned activation dict.

        Args:
            state (WorkflowState): The current workflow state.

        Returns:
            dict: A dict with a single key 'activate', mapping node IDs to
                  their activation status (True/False).
        """
        activate_flag = self.check_activate(state)

        if self.node_type not in BRANCH_NODES:
            activate = {node_id: activate_flag for node_id in self.down_stream_nodes}
        else:
            activate = {}

        activate[self.node_id] = activate_flag

        return {"activate": activate}

    @abstractmethod
    async def execute(self, state: WorkflowState, variable_pool: VariablePool) -> Any:
        """Executes the node business logic (non-streaming).

        The node implementation should only return the business result.
        It does not need to handle output formatting, timing, or statistics.
        The ``BaseNode`` will automatically wrap the result into a standard
        response format.

        Args:
            state: The current workflow state.
            variable_pool: The variable pool used for reading and writing variables.

        Returns:
            The business result produced by the node. The return value can be
            of any type.
        """
        pass

    async def execute_stream(self, state: WorkflowState, variable_pool: VariablePool):
        """Executes the node business logic in streaming mode.

        Subclasses may override this method to support streaming output.
        The default implementation executes the non-streaming method and
        yields a single final result.

        For streaming execution, a node implementation should:
          1. Yield intermediate results (e.g. text chunks).
          2. Yield a final completion marker in the following format:
             ``{"__final__": True, "result": final_result}``.

        Args:
            state: The current workflow state.
            variable_pool: The variable pool used for reading and writing variables.

        Yields:
            Business data chunks or a final completion marker.
        """
        result = await self.execute(state, variable_pool)
        # Default implementation: yield a single final completion marker.
        yield {"__final__": True, "result": result}

    def supports_streaming(self) -> bool:
        """Returns whether the node supports streaming output.

        A node is considered to support streaming if its class overrides
        the ``execute_stream`` method. If the default implementation from
        ``BaseNode`` is used, streaming is not supported.

        Returns:
            True if the node supports streaming output, False otherwise.
        """
        # Check whether the subclass overrides the execute_stream method.
        return self.__class__.execute_stream is not BaseNode.execute_stream

    @staticmethod
    def get_timeout() -> int:
        """Returns the execution timeout in seconds.

        Returns:
            The timeout duration, in seconds.
        """
        return settings.WORKFLOW_NODE_TIMEOUT

    def _get_cache_manager(self) -> WorkflowNodeCacheManager:
        return WorkflowNodeCacheManager(
            app_id=self.workflow_config.get("app_id"),
            workflow_config_id=self.workflow_config.get("workflow_config_id"),
            node_id=self.node_id,
            node_type=self.node_type,
            node_name=self.node_name,
        )

    def _get_runtime_options(self) -> dict[str, Any]:
        return self.workflow_config.get("runtime_options") or {}

    def _get_cache_source(self) -> str:
        return self._get_runtime_options().get("cache_source", "workflow_execution")

    def _is_cache_enabled(self) -> bool:
        if self._get_runtime_options().get("bypass_node_cache"):
            return False
        execution_config = self.workflow_config.get("execution_config") or {}
        if not execution_config.get("enable_cache", True):
            return False
        explicit = self.cache_config.get("enabled")
        if explicit is not None:
            return bool(explicit)
        return self.node_type in DEFAULT_CACHEABLE_NODE_TYPES

    def _get_cache_ttl_seconds(self) -> int | None:
        raw_value = (
            self.cache_config.get("ttl_seconds")
            if "ttl_seconds" in self.cache_config
            else self.cache_config.get("ttl")
        )
        if raw_value in (None, "", False):
            return None
        try:
            ttl_seconds = int(raw_value)
        except (TypeError, ValueError):
            return None
        return ttl_seconds if ttl_seconds > 0 else None

    async def _store_runtime_variables(self, extracted_output: Any, variable_pool: VariablePool) -> None:
        if extracted_output is None:
            return
        runtime_vars = extracted_output
        if not isinstance(extracted_output, dict):
            runtime_vars = {"output": extracted_output}
        for key, value in runtime_vars.items():
            if key not in self.output_types:
                continue
            await variable_pool.new(self.node_id, key, value, self.output_types[key], mut=self.variable_change_able)

    def _build_cache_input_snapshot(self, state: WorkflowState, variable_pool: VariablePool) -> dict[str, Any]:
        return self._extract_input(state, variable_pool)

    def _build_state_update_from_cache(
            self,
            *,
            cache_entry: dict[str, Any],
            state: WorkflowState,
            variable_pool: VariablePool,
            lookup_started_at: float,
    ) -> dict[str, Any]:
        cached_result = dict(cache_entry.get("result_data") or {})
        cached_result["input"] = self._build_cache_input_snapshot(state, variable_pool)
        original_elapsed = cached_result.get("elapsed_time")
        cached_result["status"] = "completed"
        cached_result["error"] = None
        cached_result["elapsed_time"] = (time.time() - lookup_started_at) * 1000
        cached_result["execution_order"] = _next_execution_order()
        cached_result["cache_hit"] = True
        cached_result["cache_key"] = cache_entry.get("cache_key")
        cached_result["cache_id"] = str(cache_entry.get("id")) if cache_entry.get("id") else None
        cached_result["cache_status"] = cache_entry.get("status")
        cached_result["cache_hit_count"] = cache_entry.get("hit_count", 0)
        if original_elapsed is not None:
            cached_result["cache_origin_elapsed_time"] = original_elapsed
        return {
            "node_outputs": {
                self.node_id: cached_result
            },
            "looping": state["looping"],
        } | self.trans_activate(state)

    async def _try_use_cache(
            self,
            state: WorkflowState,
            variable_pool: VariablePool,
            lookup_started_at: float,
    ) -> dict[str, Any] | None:
        if not self._is_cache_enabled():
            return None
        manager = self._get_cache_manager()
        if not manager.is_available():
            return None
        cache_input = self._build_cache_input_snapshot(state, variable_pool)
        cache_key = manager.build_cache_key(cache_input)
        cache_entry = manager.get_active_cache(cache_key)
        if not cache_entry:
            return None
        await self._store_runtime_variables((cache_entry.get("result_data") or {}).get("output"), variable_pool)
        return self._build_state_update_from_cache(
            cache_entry=cache_entry,
            state=state,
            variable_pool=variable_pool,
            lookup_started_at=lookup_started_at,
        )

    def _save_cache(
            self,
            *,
            state: WorkflowState,
            variable_pool: VariablePool,
            node_output: dict[str, Any],
    ) -> None:
        if not self._is_cache_enabled():
            return
        if not isinstance(node_output, dict) or node_output.get("status") != "completed":
            return
        manager = self._get_cache_manager()
        if not manager.is_available():
            return
        cache_input = self._build_cache_input_snapshot(state, variable_pool)
        cache_key = manager.build_cache_key(cache_input)
        cache_payload = dict(node_output)
        cache_payload.pop("execution_order", None)
        cache_payload.pop("cache_hit", None)
        cache_payload.pop("cache_id", None)
        cache_payload.pop("cache_status", None)
        cache_payload.pop("cache_hit_count", None)
        cache_payload.pop("cache_origin_elapsed_time", None)
        manager.save_cache(
            cache_key=cache_key,
            input_data=cache_input,
            result_data=cache_payload,
            source=self._get_cache_source(),
            ttl_seconds=self._get_cache_ttl_seconds(),
            meta_data={
                "execution_id": state.get("execution_id"),
            },
        )

    async def run(self, state: WorkflowState, variable_pool: VariablePool) -> dict[str, Any]:
        """Runs the node with error handling and output wrapping (non-streaming).

        This method is invoked by the Executor and is responsible for:
          1. Execution time measurement.
          2. Invoking the node's ``execute()`` method.
          3. Wrapping the business result into a standardized output format.
          4. Handling execution errors.

        Args:
            state: The current workflow state.
            variable_pool: The variable pool used for reading and writing variables.

        Returns:
            A standardized state update dictionary.
        """
        if not self.check_activate(state):
            return self.trans_activate(state)

        start_time = time.time()
        cached_state_update = await self._try_use_cache(state, variable_pool, start_time)
        if cached_state_update:
            return cached_state_update

        timeout = self.get_timeout()

        try:
            # Invoke the node business logic.
            business_result = await asyncio.wait_for(
                self.execute(state, variable_pool),
                timeout=timeout
            )

            elapsed_time = (time.time() - start_time) * 1000

            # Extract processed outputs using subclass-defined logic.
            extracted_output = self._extract_output(business_result)

            # Wrap the business result into the standard output format.
            wrapped_output = self._wrap_output(business_result, elapsed_time, state, variable_pool)

            # Store extracted outputs as runtime variables for downstream nodes.
            if extracted_output is not None:
                await self._store_runtime_variables(extracted_output, variable_pool)

            # Return the wrapped output along with activation state updates.
            state_update = {
                **wrapped_output,
                "looping": state["looping"]
            } | self.trans_activate(state)
            self._save_cache(
                state=state,
                variable_pool=variable_pool,
                node_output=wrapped_output.get("node_outputs", {}).get(self.node_id, {}),
            )
            return state_update

        except TimeoutError:
            elapsed_time = (time.time() - start_time) * 1000
            logger.error(
                f"Node {self.node_id} execution timed out ({timeout} seconds)."
            )
            return self._wrap_error(
                f"Node execution timed out ({timeout} seconds).",
                elapsed_time,
                state,
                variable_pool,
            )
        except GraphInterrupt:
            raise
        except Exception as e:
            elapsed_time = (time.time() - start_time) * 1000
            logger.error(
                f"Node {self.node_id} execution failed: {e}",
                exc_info=True,
            )
            return self._wrap_error(str(e), elapsed_time, state, variable_pool)

    async def run_stream(
            self, state: WorkflowState,
            variable_pool: VariablePool
    ) -> AsyncGenerator[dict[str, Any], Any]:
        """Executes the node with error handling and output wrapping (streaming).

        This method is called by the Executor and is responsible for:
          1. Tracking execution time.
          2. Calling the node's ``execute_stream()`` method.
          3. Sending streaming chunks via LangGraph's stream writer.
          4. Updating activation-related state for downstream nodes.
          5. Wrapping business data into a standardized output format.
          6. Handling execution errors.

        Args:
            state: The current workflow state.
            variable_pool: The variable pool used for reading and writing variables.

        Yields:
            Incremental state updates, including activation state changes and
            the final wrapped result.
        """
        if not self.check_activate(state):
            yield self.trans_activate(state)
            logger.debug(f"jump node: {self.node_id}")
            return

        start_time = time.time()
        cached_state_update = await self._try_use_cache(state, variable_pool, start_time)
        if cached_state_update:
            yield cached_state_update
            return

        timeout = self.get_timeout()

        try:
            # Get LangGraph's stream writer for sending custom data
            writer = get_stream_writer()

            # Accumulate complete result (for final wrapping)
            chunks = []
            final_result = None
            chunk_count = 0

            # Stream chunks in real-time
            loop_start = asyncio.get_event_loop().time()

            async for item in self.execute_stream(state, variable_pool):
                # Check timeout
                if asyncio.get_event_loop().time() - loop_start > timeout:
                    raise TimeoutError()

                # Check if it's a completion marker
                if item.get("__final__"):
                    final_result = item["result"]
                else:
                    chunk_count += 1
                    content = str(item.get("chunk"))
                    done = item.get("done", False)
                    field = item.get("field", "output")
                    chunks.append(content)

                    # Send chunks for all nodes (including End nodes for suffix)
                    logger.debug(f"Node {self.node_id} sent chunk #{chunk_count}: {content[:50]}...")

                    # 1. Send via stream writer (for real-time client updates)
                    writer({
                        "type": "node_chunk",
                        "node_id": self.node_id,
                        "chunk": content,
                        "done": done,
                        "field": field
                    })

            elapsed_time = (time.time() - start_time) * 1000

            logger.debug(f"Node {self.node_id} streaming execution finished, "
                         f"time elapsed: {elapsed_time:.2f}ms, chunks: {chunk_count}")

            # Extract processed output (call subclass's _extract_output)
            extracted_output = self._extract_output(final_result)

            # Wrap final result
            final_output = self._wrap_output(final_result, elapsed_time, state, variable_pool)

            # Store extracted output in runtime variables (for quick access by subsequent nodes)
            if extracted_output is not None:
                await self._store_runtime_variables(extracted_output, variable_pool)

            # Build complete state update (including node_outputs, runtime_vars, and final streaming buffer)
            state_update = {
                **final_output,
                "looping": state["looping"]
            }
            self._save_cache(
                state=state,
                variable_pool=variable_pool,
                node_output=final_output.get("node_outputs", {}).get(self.node_id, {}),
            )

            # Finally yield state update
            # LangGraph will merge this into state
            yield state_update | self.trans_activate(state)

        except TimeoutError:
            elapsed_time = (time.time() - start_time) * 1000
            logger.error(f"Node {self.node_id} execution timed out ({timeout}s)")
            error_output = self._wrap_error(
                f"Node execution timed out ({timeout}s)",
                elapsed_time,
                state,
                variable_pool
            )
            yield error_output
        except GraphInterrupt:
            raise
        except Exception as e:
            elapsed_time = (time.time() - start_time) * 1000
            logger.error(f"Node {self.node_id} execution failed: {e}", exc_info=True)
            error_output = self._wrap_error(str(e), elapsed_time, state, variable_pool)
            yield error_output

    def _wrap_output(
            self,
            business_result: Any,
            elapsed_time: float,
            state: WorkflowState,
            variable_pool: VariablePool
    ) -> dict[str, Any]:
        """Wraps the business result into a standardized node output format.

        Args:
            business_result: The result returned by the node's business logic.
            elapsed_time: Time elapsed during node execution (in seconds).
            state: The current workflow state.
            variable_pool: The variable pool used for reading and writing variables.

        Returns:
            A dictionary representing the standardized state update for this node,
            including node outputs, input, output, elapsed time, token usage, and status.
        """
        # Extract input data (for logging or audit purposes)
        input_data = self._extract_input(state, variable_pool)

        # Extract token usage information (if applicable)
        token_usage = self._extract_token_usage(business_result)

        # Extract actual output (strip any metadata)
        output = self._extract_output(business_result)

        # Construct standardized node output
        node_output = {
            "node_id": self.node_id,
            "node_type": self.node_type,
            "node_name": self.node_name,
            "status": "completed",
            "input": input_data,
            "output": output,
            "elapsed_time": elapsed_time,
            "token_usage": token_usage,
            "error": None,
            # 单调递增序号，用于日志按执行顺序排序（JSONB 不保证 key 顺序）
            "execution_order": _next_execution_order(),
            **self._extract_extra_fields(business_result),
        }
        final_output = {
            "node_outputs": {self.node_id: node_output},
        }

        return final_output

    def _wrap_error(
            self,
            error_message: str,
            elapsed_time: float,
            state: WorkflowState,
            variable_pool: VariablePool
    ) -> dict[str, Any]:
        """Wraps an error into a standardized node output format.

        This method handles both cases:
          - If an error edge is defined, the workflow can continue to the error handling node.
          - If no error edge exists, the workflow is stopped by raising an exception.

        Args:
            error_message: The error message describing the failure.
            elapsed_time: Time elapsed during node execution (in seconds).
            state: The current workflow state.
            variable_pool: The variable pool used for reading and writing variables.

        Returns:
            A dictionary representing the standardized state update for this node
            when an error edge exists. If no error edge exists, this method
            raises an exception to stop the workflow.
        """
        # # Check if the node has an error edge defined
        # error_edge = self._find_error_edge()

        # Extract input data (for logging or audit purposes)
        input_data = self._extract_input(state, variable_pool)

        # Construct the standardized node output for the error
        node_output = {
            "node_id": self.node_id,
            "node_type": self.node_type,
            "node_name": self.node_name,
            "status": "failed",
            "input": input_data,
            "output": None,
            "elapsed_time": elapsed_time,
            "token_usage": None,
            "error": error_message,
            # 单调递增序号，用于日志按执行顺序排序
            "execution_order": _next_execution_order(),
        }

        # if error_edge:
        #     # If an error edge exists, log a warning and continue to error node
        #     logger.warning(
        #         f"Node {self.node_id} execution failed, redirecting to error node: {error_edge['target']}"
        #     )
        #     return {
        #         "node_outputs": {
        #             self.node_id: node_output
        #         },
        #         "error": error_message,
        #         "error_node": self.node_id
        #     }
        # else:
        writer = get_stream_writer()
        writer({
            "type": "node_error",
            **node_output
        })
        logger.error(f"Node {self.node_id} execution failed, stopping workflow: {error_message}")
        # 抛出自定义异常，把 node_output 带给 executor，供其写入 node_outputs
        raise NodeExecutionError(
            node_id=self.node_id,
            node_output=node_output,
            error_message=error_message,
        )

    def _extract_input(self, state: WorkflowState, variable_pool: VariablePool) -> dict[str, Any]:
        """Extracts the input data for this node (used for logging or audit).

        Subclasses may override this method to customize what input data
        should be recorded.

        Args:
            state: The current workflow state.
            variable_pool: The variable pool used for reading and writing variables.

        Returns:
            A dictionary containing the node's input data with all template
            variables resolved to their actual runtime values.
        """
        return {"config": self._resolve_config(self.config, variable_pool)}

    @staticmethod
    def _resolve_config(config: Any, variable_pool: VariablePool) -> Any:
        """递归解析 config 中的模板变量，将 {{xxx}} 替换为实际值。

        Args:
            config: 节点的原始配置（可能包含模板变量）。
            variable_pool: 变量池，用于解析模板变量。

        Returns:
            解析后的配置，所有字符串中的 {{变量}} 已被替换为真实值。
        """
        if isinstance(config, str) and _TEMPLATE_PATTERN.search(config):
            return BaseNode._render_template(config, variable_pool, strict=False)
        elif isinstance(config, dict):
            return {k: BaseNode._resolve_config(v, variable_pool) for k, v in config.items()}
        elif isinstance(config, list):
            return [BaseNode._resolve_config(item, variable_pool) for item in config]
        return config

    def _extract_output(self, business_result: Any) -> Any:
        """Extracts the actual output from the business result.

        Subclasses may override this method to customize how the node's
        output is extracted.

        Args:
            business_result: The result returned by the node's business logic.

        Returns:
            The actual output extracted from the business result.
        """
        # Default implementation returns the business result directly
        return business_result

    def _extract_extra_fields(self, business_result: Any) -> dict:
        """Extracts extra fields to merge into node_output (e.g. citations).

        Subclasses may override to inject additional metadata.
        """
        return {}

    def _extract_token_usage(self, business_result: Any) -> dict[str, int] | None:
        """Extracts token usage information from the business result.

        Subclasses may override this method to extract token usage statistics
        (e.g., for LLM nodes).

        Args:
            business_result: The result returned by the node's business logic.

        Returns:
            A dictionary mapping token types to counts, or None if not applicable.
        """
        # Default implementation returns None
        return None

    def _find_error_edge(self) -> dict[str, Any] | None:
        """Finds the error edge for this node, if any.

        An error edge is used to redirect workflow execution when this node
        fails.

        Returns:
            A dictionary representing the error edge configuration if it exists,
            or None if no error edge is defined.
        """
        for edge in self.workflow_config.get("edges", []):
            if edge.get("source") == self.node_id and edge.get("type") == "error":
                return edge
        return None

    @staticmethod
    def _render_template(template: str, variable_pool: VariablePool, strict: bool = True) -> str:
        """Renders a template string using the provided variable pool.

        Supported variable namespaces:
          - sys.xxx: System variables (e.g., message, execution_id, workspace_id,
            user_id, conversation_id)
          - conv.xxx: Conversation variables (persist across multiple turns)
          - env.xxx: Environment variables (workflow-level, read-only)
          - node_id.xxx: Node outputs

        Args:
            template: The template string to render.
            variable_pool: The variable pool containing system, conversation, and
                node variables.
            strict: If True, missing variables will raise an error; if False,
                missing variables are ignored.

        Returns:
            The rendered string with all variables substituted.
        """
        from app.core.workflow.utils.template_renderer import render_template

        return render_template(
            template=template,
            conv_vars=variable_pool.lazy_namespace("conv", literal=True),
            node_outputs=variable_pool.lazy_all_node_outputs(literal=True),
            system_vars=variable_pool.lazy_namespace("sys", literal=True),
            env_vars=variable_pool.lazy_namespace("env", literal=True),
            strict=strict
        )

    @staticmethod
    def _evaluate_condition(expression: str, variable_pool: VariablePool) -> bool:
        """Evaluates a conditional expression using the provided variable pool.

        Supported variable namespaces:
          - sys.xxx: System variables
          - conv.xxx: Conversation variables
          - env.xxx: Environment variables
          - node_id.xxx: Node outputs

        Args:
            expression: The conditional expression to evaluate.
            variable_pool: The variable pool containing system, conversation, and
                node variables.

        Returns:
            The boolean result of evaluating the expression.
        """
        from app.core.workflow.utils.expression_evaluator import evaluate_condition

        return evaluate_condition(
            expression=expression,
            conv_var=variable_pool.lazy_namespace("conv"),
            node_outputs=variable_pool.lazy_all_node_outputs(),
            system_vars=variable_pool.lazy_namespace("sys"),
            env_vars=variable_pool.lazy_namespace("env"),
        )

    @staticmethod
    def get_variable(
            selector: str,
            variable_pool: VariablePool,
            default: Any = None,
            strict: bool = True
    ) -> Any:
        """Retrieves a variable value from the variable pool (convenience method).

        Args:
            selector: The variable selector (can be namespaced, e.g., sys.xxx, conv.xxx, node_id.xxx).
            variable_pool: The variable pool from which to fetch the value.
            default: The default value to return if the variable does not exist.
            strict: If True, raise an error when the variable is missing; if False, return the default.

        Returns:
            The value of the selected variable, or the default if not found and strict is False.
        """
        return variable_pool.get_value(selector, default, strict=strict)

    @staticmethod
    def has_variable(selector: str, variable_pool: VariablePool) -> bool:
        """Checks whether a variable exists in the variable pool (convenience method).

        Args:
            selector: The variable selector (can be namespaced, e.g., sys.xxx, conv.xxx, node_id.xxx).
            variable_pool: The variable pool to check.

        Returns:
            True if the variable exists in the pool, False otherwise.
        """
        return variable_pool.has(selector)

    @staticmethod
    async def process_message(
            api_config: ModelInfo,
            content: str | dict | FileObject,
            enable_file=False
    ) -> list | str | None:
        provider = api_config.provider
        if isinstance(content, dict):
            content = FileObject(
                type=content.get("type"),
                url=content.get("url"),
                transfer_method=content.get("transfer_method"),
                origin_file_type=content.get("origin_file_type"),
                file_id=content.get("file_id"),
                is_file=True
            )
        if isinstance(content, str):
            if enable_file:
                return [{"type": "text", "text": content}]
            return content

        elif isinstance(content, FileObject):
            if content.content_cache.get(f"{provider}_{api_config.is_omni}"):
                return content.content_cache[f"{provider}_{api_config.is_omni}"]
            with get_db_read() as db:
                multimodal_service = MultimodalService(db, api_config=api_config)
                file_obj = FileInput(
                    type=content.type,
                    url=content.url,
                    transfer_method=content.transfer_method,
                    origin_file_type=content.origin_file_type,
                    upload_file_id=uuid.UUID(content.file_id) if content.file_id else None,
                )
                file_obj.set_content(content.get_content())
                message = await multimodal_service.process_files(
                    [file_obj],
                )
                content.set_content(file_obj.get_content())
                if message:
                    content.content_cache[f"{provider}_{api_config.is_omni}"] = message
                    return message
                return None
        raise TypeError(f'Unexpected input value type - {type(content)}')

    @staticmethod
    def process_model_output(content) -> str:
        result = ""
        if isinstance(content, list):
            for msg in content:
                if isinstance(msg, dict):
                    result += msg.get("text")
                elif isinstance(msg, str):
                    result += msg
        elif isinstance(content, dict):
            result = content.get("text")
        elif isinstance(content, str):
            return content
        return result

    @staticmethod
    def model_balance(model_config: ModelConfig) -> ModelApiKey:
        api_keys = [key for key in model_config.api_keys if key.is_active]
        if not api_keys:
            raise ValueError("No active API keys available for model")
        if model_config.load_balance_strategy == LoadBalanceStrategy.ROUND_ROBIN:
            return min(api_keys, key=lambda x: (int(x.usage_count or "0"), x.last_used_at or datetime.min))
        return api_keys[0]
