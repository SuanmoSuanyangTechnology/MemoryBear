import asyncio
import logging
from abc import ABC, abstractmethod
from functools import cached_property
from typing import Any, AsyncGenerator

from langgraph.config import get_stream_writer
from typing_extensions import TypedDict, Annotated

from app.core.config import settings
from app.core.workflow.nodes.enums import BRANCH_NODES
from app.core.workflow.variable.base_variable import VariableType
from app.core.workflow.variable_pool import VariablePool
from app.services.multimodal_service import PROVIDER_STRATEGIES

logger = logging.getLogger(__name__)


def merge_activate_state(x, y):
    return {
        k: x.get(k, False) or y.get(k, False)
        for k in set(x) | set(y)
    }


def merge_looping_state(x, y):
    return y if y > x else x


class WorkflowState(TypedDict):
    """Workflow state

    The state object passed between nodes in a workflow, containing messages, variables, node outputs, etc.
    """
    # List of messages (append mode)
    messages: Annotated[list[dict[str, str]], lambda x, y: y]

    # Set of loop node IDs, used for assigning values in loop nodes
    cycle_nodes: list
    looping: Annotated[int, merge_looping_state]

    # Node outputs (stores execution results of each node for variable references)
    # Uses a custom merge function to combine new node outputs into the existing dictionary
    node_outputs: Annotated[dict[str, Any], lambda x, y: {**x, **y}]

    # Execution context
    execution_id: str
    workspace_id: str
    user_id: str

    # Error information (for error edges)
    error: str | None
    error_node: str | None

    # node activate status
    activate: Annotated[dict[str, bool], merge_activate_state]


class BaseNode(ABC):
    """Base class for workflow nodes.

    All node types should inherit from this class and implement the `execute` method.
    """

    def __init__(self, node_config: dict[str, Any], workflow_config: dict[str, Any]):
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
        # 使用 or 运算符处理 None 值
        self.config = node_config.get("config") or {}
        self.error_handling = node_config.get("error_handling") or {}

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
        """
        return state["activate"][self.node_id]

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
        edges = self.workflow_config.get("edges")
        under_stream_nodes = [
            edge.get("target")
            for edge in edges
            if edge.get("source") == self.node_id and self.node_type not in BRANCH_NODES
        ]
        return {
            "activate": {
                            node_id: self.check_activate(state)
                            for node_id in under_stream_nodes
                        } | {self.node_id: self.check_activate(state)}
        }

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

        import time

        start_time = time.time()

        timeout = self.get_timeout()

        try:
            # Invoke the node business logic.
            business_result = await asyncio.wait_for(
                self.execute(state, variable_pool),
                timeout=timeout
            )

            elapsed_time = time.time() - start_time

            # Extract processed outputs using subclass-defined logic.
            extracted_output = self._extract_output(business_result)

            # Wrap the business result into the standard output format.
            wrapped_output = self._wrap_output(business_result, elapsed_time, state, variable_pool)

            # Store extracted outputs as runtime variables for downstream nodes.
            if extracted_output is not None:
                runtime_vars = extracted_output
                if not isinstance(extracted_output, dict):
                    runtime_vars = {"output": extracted_output}
                for k, v in runtime_vars.items():
                    await variable_pool.new(self.node_id, k, v, self.output_types[k], mut=self.variable_change_able)

            # Return the wrapped output along with activation state updates.
            return {
                **wrapped_output,
                "looping": state["looping"]
            } | self.trans_activate(state)

        except TimeoutError:
            elapsed_time = time.time() - start_time
            logger.error(
                f"Node {self.node_id} execution timed out ({timeout} seconds)."
            )
            return self._wrap_error(
                f"Node execution timed out ({timeout} seconds).",
                elapsed_time,
                state,
                variable_pool,
            )
        except Exception as e:
            elapsed_time = time.time() - start_time
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

        import time

        start_time = time.time()

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
                    chunks.append(content)

                    # Send chunks for all nodes (including End nodes for suffix)
                    logger.debug(f"Node {self.node_id} sent chunk #{chunk_count}: {content[:50]}...")

                    # 1. Send via stream writer (for real-time client updates)
                    writer({
                        "type": "node_chunk",
                        "node_id": self.node_id,
                        "chunk": content,
                        "done": done
                    })

            elapsed_time = time.time() - start_time

            logger.info(f"Node {self.node_id} streaming execution finished, "
                        f"time elapsed: {elapsed_time:.2f}s, chunks: {chunk_count}")

            # Extract processed output (call subclass's _extract_output)
            extracted_output = self._extract_output(final_result)

            # Wrap final result
            final_output = self._wrap_output(final_result, elapsed_time, state, variable_pool)

            # Store extracted output in runtime variables (for quick access by subsequent nodes)
            if extracted_output is not None:
                runtime_vars = extracted_output
                if not isinstance(extracted_output, dict):
                    runtime_vars = {"output": extracted_output}
                for k, v in runtime_vars.items():
                    await variable_pool.new(self.node_id, k, v, self.output_types[k], mut=self.variable_change_able)

            # Build complete state update (including node_outputs, runtime_vars, and final streaming buffer)
            state_update = {
                **final_output,
                "looping": state["looping"]
            }

            # Finally yield state update
            # LangGraph will merge this into state
            yield state_update | self.trans_activate(state)

        except TimeoutError:
            elapsed_time = time.time() - start_time
            logger.error(f"Node {self.node_id} execution timed out ({timeout}s)")
            error_output = self._wrap_error(
                f"Node execution timed out ({timeout}s)",
                elapsed_time,
                state,
                variable_pool
            )
            yield error_output
        except Exception as e:
            elapsed_time = time.time() - start_time
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
            "error": None
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
        # Check if the node has an error edge defined
        error_edge = self._find_error_edge()

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
            "error": error_message
        }

        if error_edge:
            # If an error edge exists, log a warning and continue to error node
            logger.warning(
                f"Node {self.node_id} execution failed, redirecting to error node: {error_edge['target']}"
            )
            return {
                "node_outputs": {
                    self.node_id: node_output
                },
                "error": error_message,
                "error_node": self.node_id
            }
        else:
            # If no error edge, send the error via stream writer and stop the workflow
            writer = get_stream_writer()
            writer({
                "type": "node_error",
                **node_output
            })
            logger.error(f"Node {self.node_id} execution failed, stopping workflow: {error_message}")
            raise Exception(f"Node {self.node_id} execution failed: {error_message}")

    def _extract_input(self, state: WorkflowState, variable_pool: VariablePool) -> dict[str, Any]:
        """Extracts the input data for this node (used for logging or audit).

        Subclasses may override this method to customize what input data
        should be recorded.

        Args:
            state: The current workflow state.
            variable_pool: The variable pool used for reading and writing variables.

        Returns:
            A dictionary containing the node's input data.
        """
        # Default implementation returns the node configuration
        return {"config": self.config}

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
        from app.core.workflow.template_renderer import render_template

        return render_template(
            template=template,
            conv_vars=variable_pool.get_all_conversation_vars(),
            node_outputs=variable_pool.get_all_node_outputs(),
            system_vars=variable_pool.get_all_system_vars(),
            strict=strict
        )

    @staticmethod
    def _evaluate_condition(expression: str, variable_pool: VariablePool) -> bool:
        """Evaluates a conditional expression using the provided variable pool.

        Supported variable namespaces:
          - sys.xxx: System variables
          - conv.xxx: Conversation variables
          - node_id.xxx: Node outputs

        Args:
            expression: The conditional expression to evaluate.
            variable_pool: The variable pool containing system, conversation, and
                node variables.

        Returns:
            The boolean result of evaluating the expression.
        """
        from app.core.workflow.expression_evaluator import evaluate_condition

        return evaluate_condition(
            expression=expression,
            conv_var=variable_pool.get_all_conversation_vars(),
            node_outputs=variable_pool.get_all_node_outputs(),
            system_vars=variable_pool.get_all_system_vars()
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
    async def process_message(provider, content, enable_file=False) -> dict | str | None:
        if isinstance(content, str):
            if enable_file:
                return {"text": content}
            return content
        elif isinstance(content, dict):
            trans_tool = PROVIDER_STRATEGIES[provider]()
            result = await trans_tool.format_image(content["url"])
            return result
        raise TypeError('Unexpect input value type')

    @staticmethod
    def process_model_output(content) -> str:
        if isinstance(content, dict):
            return content.get("text")
        return content
