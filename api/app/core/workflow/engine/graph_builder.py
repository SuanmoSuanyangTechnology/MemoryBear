# -*- coding: UTF-8 -*-
# Author: Eternity
# @Email: 1533512157@qq.com
# @Time : 2026/2/10 13:33
import logging
import re
import uuid
from collections import defaultdict
from functools import lru_cache
from typing import Any, Iterable

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import START, END
from langgraph.graph.state import CompiledStateGraph, StateGraph
from langgraph.types import Send

from app.core.workflow.engine.state_manager import WorkflowState
from app.core.workflow.engine.stream_output_coordinator import OutputContent, StreamOutputConfig
from app.core.workflow.engine.variable_pool import VariablePool
from app.core.workflow.nodes import NodeFactory
from app.core.workflow.nodes.enums import NodeType, BRANCH_NODES
from app.core.workflow.utils.expression_evaluator import evaluate_condition
from app.core.workflow.validator import WorkflowValidator

logger = logging.getLogger(__name__)

# Regex to split output into:
#    - variable placeholders: {{ ... }}
#    - normal literal text
#
# Example:
#   "Hello {{user.name}}!" ->
#   ["Hello ", "{{user.name}}", "!"]
_OUTPUT_PATTERN = re.compile(r'\{\{.*?}}|[^{}]+')
# Strict variable format: {{ node_id.field_name }}
_VARIABLE_PATTERN = re.compile(r'\{\{\s*[a-zA-Z0-9_]+\.[a-zA-Z0-9_]+\s*}}')


class GraphBuilder:
    def __init__(
            self,
            workflow_config: dict[str, Any],
            stream: bool = False,
            subgraph: bool = False,
            variable_pool: VariablePool | None = None
    ):
        self.workflow_config = workflow_config

        self.stream = stream
        self.subgraph = subgraph

        self.start_node_id: str | None = None

        self.node_map = {node["id"]: node for node in self.nodes}
        self.end_node_map: dict[str, StreamOutputConfig] = {}
        self._find_upstream_activation_dep = lru_cache(
            maxsize=len(self.nodes) * 2
        )(self._find_upstream_activation_dep)
        if variable_pool:
            self.variable_pool = variable_pool
        else:
            self.variable_pool = VariablePool()

        self.graph = StateGraph(WorkflowState)
        self.add_nodes()
        self.reachable_nodes = WorkflowValidator.get_reachable_nodes(self.start_node_id, self.edges)
        self.end_nodes = [
            node
            for node in self.nodes
            if node.get("type") == "end" and node.get("id") in self.reachable_nodes
        ]
        self.add_edges()
        # EDGES MUST BE ADDED AFTER NODES ARE ADDED.

        self._reverse_adj: dict[str, list[dict]] = defaultdict(list)
        self._build_reverse_adj()
        self._analyze_end_node_output()

    @property
    def nodes(self) -> list[dict[str, Any]]:
        return self.workflow_config.get("nodes", [])

    @property
    def edges(self) -> list[dict[str, Any]]:
        return self.workflow_config.get("edges", [])

    def get_node_type(self, node_id: str) -> str:
        """Retrieve the type of node given its ID.

        Args:
            node_id (str): The unique identifier of the node.

        Returns:
            str: The type of the node.

        Raises:
            RuntimeError: If no node with the given `node_id` exists.
        """
        try:
            return self.node_map[node_id]["type"]
        except KeyError:
            raise RuntimeError(f"Node not found: Id={node_id}")

    @staticmethod
    def _merge_control_nodes(control_nodes: Iterable[tuple[str, str]]) -> dict[str, list]:
        result = defaultdict(list)
        for node in control_nodes:
            result[node[0]].append(node[1])
        return result

    def _build_reverse_adj(self):
        for edge in self.edges:
            if edge["source"] not in self.reachable_nodes:
                continue
            self._reverse_adj[edge.get("target")].append({
                "id": edge["source"], "branch": edge.get("label")
            })

    def _find_upstream_activation_dep(
            self,
            target_node: str
    ) -> tuple[tuple[tuple[str, str]], tuple[str]]:
        """Find upstream dependencies that affect the activation of a target node.

        Walks upstream along the workflow graph from the target node, collecting
        two types of dependencies:
            - Branch control nodes: upstream branch nodes (e.g. if-else) whose
              routing outcome determines whether the target node executes.
            - Output nodes: upstream END nodes that must complete their output
              before the target node can activate.

        The traversal terminates early and returns empty tuples if any upstream
        path reaches START/CYCLE_START without encountering a branch or output
        node, indicating the target node is directly reachable and should be
        activated immediately.

        Args:
            target_node: The ID of the node whose upstream activation
                dependencies are to be resolved.

        Returns:
            A tuple of two elements:
                - A deduplicated tuple of (branch_node_id, branch_label) pairs
                  representing upstream branch control dependencies. Empty if
                  any clean path to START exists.
                - A deduplicated tuple of upstream output node IDs that must
                  complete before this node activates.
        """
        source_nodes = self._reverse_adj[target_node]
        if not source_nodes and self.get_node_type(target_node) in [NodeType.START, NodeType.CYCLE_START]:
            return tuple(), tuple()

        branch_nodes = []
        output_nodes = []
        non_branch_nodes = []

        for node_info in source_nodes:
            if self.get_node_type(node_info["id"]) in BRANCH_NODES:
                branch_nodes.append(
                    (node_info["id"], node_info["branch"])
                )
            else:
                if self.get_node_type(node_info["id"]) == NodeType.END:
                    output_nodes.append(node_info["id"])
                non_branch_nodes.append(node_info["id"])

        has_branch = True
        for node_id in non_branch_nodes:
            upstream_control_nodes, upstream_output_nodes = self._find_upstream_activation_dep(node_id)
            if not upstream_control_nodes:
                if not upstream_output_nodes and node_id not in output_nodes:
                    return tuple(), tuple()
                branch_nodes = []
                has_branch = False
            if has_branch:
                branch_nodes.extend(upstream_control_nodes)
            output_nodes.extend(upstream_output_nodes)

        return tuple(set(branch_nodes)), tuple(set(output_nodes))

    def _analyze_end_node_output(self):
        """
        Analyze output templates of all End nodes and generate StreamOutputConfig.

        This method is responsible for parsing the `output` field of End nodes,
        splitting literal text and variable placeholders (e.g. {{ node.field }}),
        and determining whether each output segment should be activated immediately
        or controlled by upstream branch nodes.

        In stream mode:
        - If the End node is controlled by any upstream branch node, the output
          will be initially inactive and controlled by those branch nodes.
        - Otherwise, the output is activated immediately.

        In non-stream mode:
        - All outputs are activated by default.
        """

        # Collect all End nodes in the workflow
        logger.info(f"[Prefix Analysis] Found {len(self.end_nodes)} End nodes")

        # Iterate through each End node to analyze its output
        for end_node in self.end_nodes:
            end_node_id = end_node.get("id")
            config = end_node.get("config", {})
            output = config.get("output")

            # Skip End nodes without output configuration
            if not output:
                continue

            # Split output into ordered segments
            output_template = list(_OUTPUT_PATTERN.findall(output))

            # Determine whether each segment is literal text
            #    True  -> literal (can be directly output)
            #    False -> variable placeholder (needs runtime value)
            output_flag = [
                not bool(_VARIABLE_PATTERN.match(item))
                for item in output_template
            ]

            # Stream mode: output activation depends on upstream branch nodes
            if self.stream:
                # Find upstream branch nodes that can control this End node
                upstream_control_nodes, upstream_output_nodes = self._find_upstream_activation_dep(end_node_id)
                activate = not bool(upstream_control_nodes) and not bool(upstream_output_nodes)
                # Build StreamOutputConfig for this End node
                self.end_node_map[end_node_id] = StreamOutputConfig(
                    id=end_node_id,
                    # If there is no upstream branch, output is active immediately
                    activate=activate,

                    # Branch nodes that control activation of this End node
                    control_nodes=self._merge_control_nodes(upstream_control_nodes),
                    upstream_output_nodes=list(upstream_output_nodes),
                    control_resolved=not bool(upstream_control_nodes),
                    output_resolved=not bool(upstream_output_nodes),

                    # Convert output segments into OutputContent objects
                    outputs=list(
                        [
                            OutputContent(
                                literal=output_string,
                                # Literal text can be activated immediately unless blocked by branch
                                activate=activate,
                                # Variable segments are marked explicitly
                                is_variable=not activate
                            )
                            for output_string, activate in zip(output_template, output_flag)
                        ]
                    ),
                    # Cursor for streaming output (initially 0)
                    cursor=0
                )
                logger.info(f"[Stream Analysis] end_id: {end_node_id}, "
                            f"activate: {activate}, "
                            f"control_nodes: {upstream_control_nodes},"
                            f"ref_outputs: {upstream_output_nodes},"
                            f"output: {output_template},"
                            f"output_activate: {output_flag}")

            # Non-stream mode: all outputs are activated by default
            else:
                self.end_node_map[end_node_id] = StreamOutputConfig(
                    id=end_node_id,
                    activate=True,
                    control_nodes={},
                    outputs=list(
                        [
                            OutputContent(
                                literal=output_string,
                                activate=True,
                                is_variable=not activate
                            )
                            for output_string, activate in zip(output_template, output_flag)
                        ]
                    ),
                    cursor=0,
                    upstream_output_nodes=[],
                    control_resolved=True,
                    output_resolved=True,
                )

    def add_nodes(self):
        """Add all nodes from the workflow configuration to the state graph.

        This method handles:
        - Creation of node instances using NodeFactory.
        - Special handling for start, end, and cycle nodes.
        - Injection of End node prefixes for streaming mode.
        - Marking nodes as adjacent to End nodes if referenced.
        - Wrapping node run methods as async functions or async generators
          depending on streaming mode.

        Notes:
            Loop nodes (nodes with `cycle` property) are handled separately
            via CycleGraphNode when building subgraphs.

        Returns:
            None
        """
        for node in self.nodes:
            node_type = node.get("type")
            if node_type == NodeType.NOTES:
                continue
            node_id = node.get("id")
            cycle_node = node.get("cycle")
            if cycle_node:
                # Nodes within a loop subgraph are constructed by CycleGraphNode
                if not self.subgraph:
                    continue

            # Record start and end node IDs
            if node_type in [NodeType.START, NodeType.CYCLE_START]:
                self.start_node_id = node_id

            # Create node instance (start and end nodes are also created)
            # NOTE:Loop node creation automatically removes the nodes and edges of the subgraph from the current graph
            node_instance = NodeFactory.create_node(node, self.workflow_config)

            if node_type in BRANCH_NODES:

                # Find all edges whose source is the current node
                related_edge = [edge for edge in self.edges if edge.get("source") == node_id]

                # Iterate over each branch
                for idx in range(len(related_edge)):
                    # Generate a condition expression for each edge
                    # Used later to determine which branch to take based on the node's output
                    # Assumes node output `node.<node_id>.output` matches the edge's label
                    # For example, if node.123.output == 'CASE1', take the branch labeled 'CASE1'
                    related_edge[idx]['condition'] = f"node['{node_id}']['output'] == '{related_edge[idx]['label']}'"

            if node_instance:
                # Wrap node's run method to avoid closure issues
                if self.stream:
                    # Stream mode: create an async generator function
                    # LangGraph collects all yielded values; the last yielded dictionary is merged into the state
                    def make_stream_func(inst, variable_pool=self.variable_pool):
                        async def node_func(state: WorkflowState):
                            async for item in inst.run_stream(state, variable_pool):
                                yield item

                        return node_func

                    self.graph.add_node(node_id, make_stream_func(node_instance))
                else:
                    # Non-stream mode: create an async function
                    def make_func(inst, variable_pool=self.variable_pool):
                        async def node_func(state: WorkflowState):
                            return await inst.run(state, variable_pool)

                        return node_func

                    self.graph.add_node(node_id, make_func(node_instance))

                logger.debug(f"Added node: {node_id} (type={node_type}, stream={self.stream})")

    def add_edges(self):
        """Add all edges (normal, waiting, and conditional) to the state graph.

        This method handles:
        - Connecting the START node to the workflow's start node.
        - Collecting waiting edges for nodes with multiple sources.
        - Collecting conditional edges for routing to NOP nodes.
        - Adding NOP nodes for conditional branches to allow later merging.
        - Wrapping routing logic in a router function that evaluates conditions.
        - Connecting End nodes to the global END node.

        Notes:
            - NOP nodes are used to ensure that multiple branches can merge
              correctly without modifying the workflow state.
            - Waiting edges are automatically handled by LangGraph to schedule
              nodes only after all sources are activated.

        Returns:
            None
        """
        # Connect the START node to the workflow's start node
        if self.start_node_id:
            self.graph.add_edge(START, self.start_node_id)
            logger.debug(f"Added edge: START -> {self.start_node_id}")

        # Collect all sources for each target node for normal/waiting edges
        waiting_edges = defaultdict(list)
        # Collect all conditional edges for each source node to construct routing
        conditional_edges = defaultdict(list)

        for edge in self.edges:
            source = edge.get("source")
            target = edge.get("target")
            condition = edge.get("condition")
            edge_type = edge.get("type")

            # Skip error edges (handled within nodes)
            if edge_type == "error":
                continue

            if condition:
                # Conditional edges: group by source node
                conditional_edges[source].append({
                    "target": target,
                    "condition": condition,
                    "label": edge.get("label")
                })
            else:
                # Normal edges: group by target node (used for waiting edges)
                waiting_edges[target].append(source)

        # Add conditional edges
        for source_node, branches in conditional_edges.items():
            def make_router(src, branch_list):
                """reate a router function for each source node that routes to a NOP node for later merging."""

                def make_branch_node(node_name, targets):
                    def node(s):
                        # NOTE: NOP NODE MUST NOT MODIFY STATE
                        return {
                            "activate": {
                                node_id: s["activate"][node_name]
                                for node_id in targets
                            }
                        }

                    return node

                unique_branch = {}
                for branch in branch_list:
                    if branch.get("label") not in unique_branch.keys():
                        nop_node_name = f"nop_{uuid.uuid4().hex[:8]}"
                        logger.info(f"Binding NOP: {source_node} {branch.get('label')} -> {nop_node_name}")
                        unique_branch[branch["label"]] = {
                            "condition": branch["condition"],
                            "node": {
                                "name": nop_node_name,
                            },
                            "target": [branch["target"]]
                        }
                    else:
                        unique_branch[branch["label"]]["target"].append(branch["target"])

                # Add NOP nodes and connect them to downstream nodes
                for label, branch_info in unique_branch.items():
                    self.graph.add_node(
                        branch_info["node"]["name"],
                        make_branch_node(
                            branch_info["node"]["name"],
                            branch_info["target"]
                        )
                    )
                    for target in branch_info["target"]:
                        waiting_edges[target].append(branch_info["node"]["name"])

                def router_fn(state: WorkflowState, variable_pool: VariablePool = self.variable_pool) -> list[Send]:
                    branch_activate = []
                    new_state = state.copy()
                    new_state["activate"] = dict(state.get("activate", {}))  # deep copy of activate
                    node_output = variable_pool.get_node_output(src, defalut=dict(), strict=False)
                    for label, branch in unique_branch.items():
                        if node_output and evaluate_condition(
                                branch["condition"],
                                {},
                                {src: node_output},
                                {}
                        ):
                            logger.debug(f"Conditional routing {src}: selected branch {label}")
                            new_state["activate"][branch["node"]["name"]] = True
                            branch_activate.append(
                                Send(
                                    branch['node']['name'],
                                    new_state
                                )
                            )
                            continue
                        new_state["activate"][branch["node"]["name"]] = False
                        branch_activate.append(
                            Send(
                                branch['node']['name'],
                                new_state
                            )
                        )
                    return branch_activate

                # Dynamically set function name
                router_fn.__name__ = f"router_{uuid.uuid4().hex[:8]}_{src}"
                return router_fn

            router_fn = make_router(source_node, branches)
            self.graph.add_conditional_edges(source_node, router_fn)
            logger.debug(f"Added conditional edges: {source_node} -> {[b['target'] for b in branches]}")

        # Add normal/waiting edges
        for target, sources in waiting_edges.items():
            if len(sources) == 1:
                # Single source: normal edge
                self.graph.add_edge(sources[0], target)
                logger.debug(f"Added edge: {sources[0]} -> {target}")
            else:
                # Multiple sources: waiting edge
                self.graph.add_edge(sources, target)
                logger.debug(f"Added waiting edge: {sources} -> {target}")

        # Connect End nodes to the global END node
        for end_node in self.end_nodes:
            end_node_id = end_node.get("id")
            if end_node_id:
                self.graph.add_edge(end_node_id, END)
                logger.debug(f"Added edge: {end_node_id} -> END")
        return

    def build(self) -> CompiledStateGraph:
        checkpointer = InMemorySaver()
        self.graph = self.graph.compile(checkpointer=checkpointer)
        return self.graph
