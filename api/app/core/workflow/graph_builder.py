import logging
import re
import uuid
from collections import defaultdict
from functools import lru_cache
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import START, END
from langgraph.graph.state import CompiledStateGraph, StateGraph
from langgraph.types import Send
from pydantic import BaseModel, Field

from app.core.workflow.expression_evaluator import evaluate_condition
from app.core.workflow.nodes import WorkflowState, NodeFactory
from app.core.workflow.nodes.enums import NodeType, BRANCH_NODES

logger = logging.getLogger(__name__)


class OutputContent(BaseModel):
    """
    Represents a single output segment of an End node.

    An output segment can be either:
    - literal text (static string)
    - a variable placeholder (e.g. {{ node.field }})

    Each segment has its own activation state, which is especially
    important in stream mode.
    """

    literal: str = Field(
        ...,
        description="Raw output content. Can be literal text or a variable placeholder."
    )

    activate: bool = Field(
        ...,
        description=(
            "Whether this output segment is currently active.\n"
            "- True: allowed to be emitted/output\n"
            "- False: blocked until activated by branch control"
        )
    )

    is_variable: bool = Field(
        ...,
        description=(
            "Whether this segment represents a variable placeholder.\n"
            "True  -> variable (e.g. {{ node.field }})\n"
            "False -> literal text"
        )
    )

    def depends_on_node(self, node_id: str) -> bool:
        """
        Check if this output segment depends on a specific node's variable.

        This method examines the `literal` of the output segment to see if it
        contains a variable placeholder referencing the given node in the form:

            {{ node_id.field_name }}

        It uses a regular expression to match the exact node ID, avoiding
        false positives from substring matches (e.g., 'node1' should not match 'node10').

        Args:
            node_id (str): The ID of the node to check for in this segment's variable placeholders.

        Returns:
            bool:
                - True if the segment contains a variable referencing the given node.
                - False otherwise.

        Example:
            literal = "{{node1.name}}"

            depends_on_node("node1") -> True
            depends_on_node("node2") -> False

        Usage:
            This method is primarily used in stream mode to determine whether
            a particular variable output segment should be activated when a
            specific upstream node completes execution.
        """
        variable_pattern = rf"\{{\{{\s*{re.escape(node_id)}\.[a-zA-Z0-9_]+\s*\}}\}}"
        pattern = re.compile(variable_pattern)
        match = pattern.search(self.literal)
        if match:
            return True
        return False


class StreamOutputConfig(BaseModel):
    """
    Streaming output configuration for an End node.

    This structure controls:
    - whether the End node output is globally active
    - which upstream branch nodes are responsible for activation
    - how each output segment behaves in streaming mode
    """

    activate: bool = Field(
        ...,
        description=(
            "Global activation state of the End node output.\n"
            "If False, no output should be emitted until all control nodes are resolved."
        )
    )

    control_nodes: list[str] = Field(
        ...,
        description=(
            "List of upstream branch node IDs that control this End node.\n"
            "Each node must signal completion before output becomes active."
        )
    )

    outputs: list[OutputContent] = Field(
        ...,
        description="Ordered list of output segments parsed from the output template."
    )

    cursor: int = Field(
        ...,
        description=(
            "Streaming cursor index.\n"
            "Indicates how many output segments have already been emitted."
        )
    )

    def update_activate(self, node_id):
        """
        Update activation state based on an upstream node completion.

        This method is typically called when a branch/control node finishes execution.

        Behavior:
        1. If the node is a control node:
           - Remove it from `control_nodes`
           - If all control nodes are resolved, activate the entire output

        2. Activate variable output segments that depend on this node:
           - If an output segment is a variable
           - And its literal references the completed node_id
           - Mark that segment as active
        """

        # Case 1: resolve control branch dependency
        if node_id in self.control_nodes:
            self.control_nodes.remove(node_id)

            # All branch constraints resolved → enable output
            if not self.control_nodes:
                self.activate = True

        # Case 2: activate variable segments related to this node
        for i in range(len(self.outputs)):
            if (
                    self.outputs[i].is_variable
                    and self.outputs[i].depends_on_node(node_id)
            ):
                self.outputs[i].activate = True


class GraphBuilder:
    def __init__(
            self,
            workflow_config: dict[str, Any],
            stream: bool = False,
            subgraph: bool = False,
    ):
        self.workflow_config = workflow_config

        self.stream = stream
        self.subgraph = subgraph

        self.start_node_id = None
        self.end_node_ids = []
        self.node_map = {node["id"]: node for node in self.nodes}
        self.end_node_map: dict[str, StreamOutputConfig] = {}
        self._find_upstream_branch_node = lru_cache(
            maxsize=len(self.nodes) * 2
        )(self._find_upstream_branch_node)
        self._analyze_end_node_output()

        self.graph = StateGraph(WorkflowState)
        self.add_nodes()
        self.add_edges()
        # EDGES MUST BE ADDED AFTER NODES ARE ADDED.

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

    def _find_upstream_branch_node(self, target_node: str) -> tuple[bool, tuple[str]]:
        """Find upstream branch nodes for a given target node in the workflow graph.

        This method identifies all upstream control (branch) nodes that can affect
        the execution of `target_node`. If `target_node` is reachable from a start
        node (i.e., a node with no upstream nodes), the method returns an empty tuple.

        The function distinguishes between branch nodes (defined in `BRANCH_NODES`)
        and non-branch nodes, recursively traversing upstream through non-branch
        nodes. If any non-branch upstream path does not lead to a branch node,
        the result will indicate that no valid upstream branch node exists.

        Args:
            target_node (str): The identifier of the target node.

        Returns:
            tuple[bool, tuple[str]]:
                - has_branch (bool): True if all upstream non-branch paths lead to at least
                  one branch node; False if any path reaches a start node without a branch.
                - branch_nodes (tuple[str]): A deduplicated tuple of upstream branch node IDs
                  affecting `target_node`. Returns an empty tuple if `has_branch` is False.
        """
        source_nodes = [
            edge.get("source")
            for edge in self.edges
            if edge.get("target") == target_node
        ]
        if not source_nodes and self.get_node_type(target_node) in [NodeType.START, NodeType.CYCLE_START]:
            return False, tuple()

        branch_nodes = []
        non_branch_nodes = []

        for node_id in source_nodes:
            if self.get_node_type(node_id) in BRANCH_NODES:
                branch_nodes.append(node_id)
            else:
                non_branch_nodes.append(node_id)

        has_branch = True
        for node_id in non_branch_nodes:
            node_has_branch, nodes = self._find_upstream_branch_node(node_id)
            has_branch = has_branch and node_has_branch
            if not has_branch:
                break
            branch_nodes.extend(nodes)
        if not has_branch:
            branch_nodes = []

        return has_branch, tuple(set(branch_nodes))

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
        end_nodes = [node for node in self.nodes if node.get("type") == "end"]
        logger.info(f"[Prefix Analysis] Found {len(end_nodes)} End nodes")

        # Iterate through each End node to analyze its output
        for end_node in end_nodes:
            end_node_id = end_node.get("id")
            config = end_node.get("config", {})
            output = config.get("output")

            # Skip End nodes without output configuration
            if not output:
                continue

            # Regex to split output into:
            #    - variable placeholders: {{ ... }}
            #    - normal literal text
            #
            # Example:
            #   "Hello {{user.name}}!" ->
            #   ["Hello ", "{{user.name}}", "!"]
            pattern = r'\{\{.*?\}\}|[^{}]+'

            # Strict variable format: {{ node_id.field_name }}
            variable_pattern_string = r'\{\{\s*[a-zA-Z0-9_]+\.[a-zA-Z0-9_]+\s*\}\}'
            variable_pattern = re.compile(variable_pattern_string)

            # Split output into ordered segments
            output_template = list(re.findall(pattern, output))

            # Determine whether each segment is literal text
            #    True  -> literal (can be directly output)
            #    False -> variable placeholder (needs runtime value)
            output_flag = [
                not bool(variable_pattern.match(item))
                for item in output_template
            ]

            # Stream mode: output activation depends on upstream branch nodes
            if self.stream:
                # Find upstream branch nodes that can control this End node
                has_branch, control_nodes = self._find_upstream_branch_node(end_node_id)

                # Build StreamOutputConfig for this End node
                self.end_node_map[end_node_id] = StreamOutputConfig(
                    # If there is no upstream branch, output is active immediately
                    activate=not has_branch,

                    # Branch nodes that control activation of this End node
                    control_nodes=list(control_nodes),

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
                            f"activate: {not has_branch}, "
                            f"control_nodes: {control_nodes},"
                            f"output: {output_template},"
                            f"output_activate: {output_flag}")

            # Non-stream mode: all outputs are activated by default
            else:
                self.end_node_map[end_node_id] = StreamOutputConfig(
                    activate=True,
                    control_nodes=[],
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
                    cursor=0
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
            node_id = node.get("id")
            cycle_node = node.get("cycle")
            if cycle_node:
                # Nodes within a loop subgraph are constructed by CycleGraphNode
                if not self.subgraph:
                    continue

            # Record start and end node IDs
            if node_type in [NodeType.START, NodeType.CYCLE_START]:
                self.start_node_id = node_id
            elif node_type == NodeType.END:
                self.end_node_ids.append(node_id)

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
                    related_edge[idx]['condition'] = f"node.{node_id}.output == '{related_edge[idx]['label']}'"

            if node_instance:
                # Wrap node's run method to avoid closure issues
                if self.stream:
                    # Stream mode: create an async generator function
                    # LangGraph collects all yielded values; the last yielded dictionary is merged into the state
                    def make_stream_func(inst):
                        async def node_func(state: WorkflowState):
                            async for item in inst.run_stream(state):
                                yield item

                        return node_func

                    self.graph.add_node(node_id, make_stream_func(node_instance))
                else:
                    # Non-stream mode: create an async function
                    def make_func(inst):
                        async def node_func(state: WorkflowState):
                            return await inst.run(state)

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

                def router_fn(state: WorkflowState) -> list[Send]:
                    branch_activate = []
                    new_state = state.copy()
                    new_state["activate"] = dict(state.get("activate", {}))  # deep copy of activate

                    for label, branch in unique_branch.items():
                        if evaluate_condition(
                                branch["condition"],
                                state.get("variables", {}),
                                state.get("runtime_vars", {}),
                                {
                                    "execution_id": state.get("execution_id"),
                                    "workspace_id": state.get("workspace_id"),
                                    "user_id": state.get("user_id")
                                }
                        ):
                            logger.debug(f"Conditional routing {src}: selected branch {label}")
                            new_state["activate"][branch["node"]["name"]] = True
                            continue
                        new_state["activate"][branch["node"]["name"]] = False
                    for label, branch in unique_branch.items():
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
        for end_node_id in self.end_node_ids:
            self.graph.add_edge(end_node_id, END)
            logger.debug(f"Added edge: {end_node_id} -> END")
        return

    def build(self) -> CompiledStateGraph:
        checkpointer = InMemorySaver()
        self.graph = self.graph.compile(checkpointer=checkpointer)
        return self.graph
