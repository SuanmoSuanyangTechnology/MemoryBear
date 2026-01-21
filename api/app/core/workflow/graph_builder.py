import logging
import uuid
from collections import defaultdict
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import START, END
from langgraph.graph.state import CompiledStateGraph, StateGraph
from langgraph.types import Send

from app.core.workflow.expression_evaluator import evaluate_condition
from app.core.workflow.nodes import WorkflowState, NodeFactory
from app.core.workflow.nodes.enums import NodeType, BRANCH_NODES

logger = logging.getLogger(__name__)


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

    def _analyze_end_node_prefixes(self) -> tuple[dict[str, str], set[str]]:
        """
        Analyze the prefix configuration for End nodes.

        This function scans each End node's output template, identifies
        references to its direct upstream nodes, and extracts the prefix
        string appearing before the first reference.

        Returns:
            tuple:
                - dict[str, str]: Mapping from upstream node ID to its End node prefix
                - set[str]: Set of node IDs that are directly adjacent to End nodes and referenced
        """
        import re

        prefixes = {}
        adjacent_and_referenced = set()  # Record nodes directly adjacent to End and referenced

        # 找到所有 End 节点
        end_nodes = [node for node in self.nodes if node.get("type") == "end"]
        logger.info(f"[Prefix Analysis] Found {len(end_nodes)} End nodes")

        for end_node in end_nodes:
            end_node_id = end_node.get("id")
            output_template = end_node.get("config", {}).get("output")

            logger.info(f"[Prefix Analysis] End node {end_node_id} template: {output_template}")

            if not output_template:
                continue

            # Find all node references in the template
            # Matches {{node_id.xxx}} or {{ node_id.xxx }} format (allowing spaces)
            pattern = r'\{\{\s*([a-zA-Z0-9_-]+)\.[a-zA-Z0-9_]+\s*\}\}'
            matches = list(re.finditer(pattern, output_template))

            logger.info(f"[Prefix Analysis] 模板中找到 {len(matches)} 个节点引用")

            # Identify all direct upstream nodes connected to the End node
            direct_upstream_nodes = []
            for edge in self.edges:
                if edge.get("target") == end_node_id:
                    source_node_id = edge.get("source")
                    direct_upstream_nodes.append(source_node_id)

            logger.info(f"[Prefix Analysis] Direct upstream nodes of End node: {direct_upstream_nodes}")

            # 找到第一个直接上游节点的引用
            for match in matches:
                referenced_node_id = match.group(1)
                logger.info(f"[Prefix Analysis] Checking reference: {referenced_node_id}")

                if referenced_node_id in direct_upstream_nodes:
                    # 这是直接上游节点的引用，提取前缀
                    prefix = output_template[:match.start()]

                    logger.info(f"[Prefix Analysis] "
                                f"✅ Found reference to direct upstream node {referenced_node_id}, prefix: '{prefix}'")

                    # 标记这个节点为"相邻且被引用"
                    adjacent_and_referenced.add(referenced_node_id)

                    if prefix:
                        prefixes[referenced_node_id] = prefix
                        logger.info(f"[Prefix Analysis] "
                                    f"✅ Assign prefix for node {referenced_node_id}: '{prefix[:50]}...'")

                    # 只处理第一个直接上游节点的引用
                    break

        logger.info(f"[Prefix Analysis] Final prefixes: {prefixes}")
        logger.info(f"[Prefix Analysis] Nodes adjacent to End and referenced: {adjacent_and_referenced}")
        return prefixes, adjacent_and_referenced

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
        # Analyze End node prefixes if in stream mode
        end_prefixes, adjacent_and_referenced = self._analyze_end_node_prefixes() if self.stream else ({}, set())

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
                # Inject End node prefix configuration if in stream mode
                if self.stream and node_id in end_prefixes:
                    node_instance._end_node_prefix = end_prefixes[node_id]
                    logger.info(f"Injected End prefix for node {node_id}")

                # Mark nodes as adjacent and referenced to End node in stream mode
                if self.stream:
                    node_instance._is_adjacent_to_end = node_id in adjacent_and_referenced
                    if node_id in adjacent_and_referenced:
                        logger.info(f"Node {node_id} marked as adjacent and referenced to End node")

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
