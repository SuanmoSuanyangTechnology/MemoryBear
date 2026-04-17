# -*- coding: UTF-8 -*-
# Author: Eternity
# @Email: 1533512157@qq.com
# @Time : 2026/2/10 13:33
from app.core.workflow.engine.runtime_schema import ExecutionContext
from app.core.workflow.engine.variable_pool import VariablePool


class WorkflowResultBuilder:
    def build_final_output(
            self,
            result: dict,
            execution_context: ExecutionContext,
            variable_pool: VariablePool,
            elapsed_time: float,
            final_output: str,
            success: bool
    ):
        """Construct the final standardized output of the workflow execution.

        This method aggregates node outputs, token usage, conversation and system
        variables, messages, and other metadata into a consistent dictionary
        structure suitable for returning from workflow execution.

        Args:
            result (dict): The runtime state returned by the workflow graph execution.
                Expected keys include:
                    - "node_outputs" (dict): Outputs of executed nodes.
                    - "messages" (list): Conversation messages exchanged during execution.
                    - "error" (str, optional): Error message if any node failed.
            execution_context (ExecutionContext): The execution context containing metadata like
                execution ID, workspace ID, and user ID.)
            variable_pool (VariablePool): Variable Pool
            elapsed_time (float): Total execution time in seconds.
            final_output (Any): The aggregated or final output content of the workflow
                (e.g., combined messages from all End nodes).
            success (bool): Whether the execution was successful.

        Returns:
            dict: A dictionary containing the final workflow execution result with keys:
                - "status": Execution status ("completed")
                - "output": Aggregated final output content
                - "variables": Namespace dictionary with:
                    - "conv": Conversation variables
                    - "sys": System variables
                - "node_outputs": Outputs from all executed nodes
                - "messages": Conversation messages exchanged
                - "conversation_id": ID of the current conversation
                - "elapsed_time": Total execution time in seconds
                - "token_usage": Aggregated token usage across nodes (if available)
                - "error": Error message if any occurred during execution
        """
        node_outputs = result.get("node_outputs", {})
        token_usage = self.aggregate_token_usage(node_outputs)
        conversation_vars = {}
        sys_vars = {}

        if variable_pool:
            conversation_vars = variable_pool.get_all_conversation_vars()
            sys_vars = variable_pool.get_all_system_vars()

        # 汇总所有 knowledge 节点的 citations
        citations = self.aggregate_citations(node_outputs)

        return {
            "status": "completed" if success else "failed",
            "output": final_output,
            "variables": {
                "conv": conversation_vars,
                "sys": sys_vars
            },
            "node_outputs": node_outputs,
            "messages": result.get("messages", []),
            "conversation_id": execution_context.conversation_id,
            "elapsed_time": elapsed_time,
            "token_usage": token_usage,
            "citations": citations,
            "error": result.get("error"),
        }

    @staticmethod
    def aggregate_citations(node_outputs: dict) -> list:
        """从所有 knowledge 节点的输出中汇总 citations，去重"""
        seen = set()
        citations = []
        for node_output in node_outputs.values():
            if not isinstance(node_output, dict):
                continue
            for c in node_output.get("citations", []):
                key = c.get("document_id")
                if key and key not in seen:
                    seen.add(key)
                    citations.append(c)
        return citations

    @staticmethod
    def aggregate_token_usage(node_outputs: dict) -> dict[str, int] | None:
        """
        Aggregate token usage statistics across all nodes.

        Args:
            node_outputs (dict): A dictionary of all node outputs.

        Returns:
            dict | None: Aggregated token usage in the format:
                         {
                             "prompt_tokens": int,
                             "completion_tokens": int,
                             "total_tokens": int
                         }
                         Returns None if no token usage information is available.
        """
        total_prompt_tokens = 0
        total_completion_tokens = 0
        total_tokens = 0
        has_token_info = False

        for node_output in node_outputs.values():
            if isinstance(node_output, dict):
                token_usage = node_output.get("token_usage")
                if token_usage and isinstance(token_usage, dict):
                    has_token_info = True
                    total_prompt_tokens += token_usage.get("prompt_tokens", 0)
                    total_completion_tokens += token_usage.get("completion_tokens", 0)
                    total_tokens += token_usage.get("total_tokens", 0)

        if not has_token_info:
            return None

        return {
            "prompt_tokens": total_prompt_tokens,
            "completion_tokens": total_completion_tokens,
            "total_tokens": total_tokens
        }
