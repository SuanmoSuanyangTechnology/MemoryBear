import logging
from typing import Any

from langgraph.graph import StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.core.workflow.nodes import WorkflowState
from app.core.workflow.nodes.base_node import BaseNode
from app.core.workflow.nodes.cycle_graph.config import LoopNodeConfig, IterationNodeConfig
from app.core.workflow.nodes.cycle_graph.iteration import IterationRuntime
from app.core.workflow.nodes.cycle_graph.loop import LoopRuntime
from app.core.workflow.nodes.enums import NodeType

logger = logging.getLogger(__name__)


class CycleGraphNode(BaseNode):
    """
    Node representing a cyclic (loop or iteration) subgraph within the workflow.

    A CycleGraphNode is a structural node that:
    - Extracts a group of nodes marked as belonging to the same cycle
    - Builds an isolated internal StateGraph (subgraph)
    - Delegates runtime execution to LoopRuntime or IterationRuntime
      depending on the node type

    This node itself does NOT execute business logic directly.
    It acts as a container and execution controller for a subgraph.
    """

    def __init__(self, node_config: dict[str, Any], workflow_config: dict[str, Any]):
        super().__init__(node_config, workflow_config)
        self.typed_config: LoopNodeConfig | IterationNodeConfig | None = None

        self.cycle_nodes = list()  # Nodes belonging to this cycle
        self.cycle_edges = list()  # Edges connecting nodes within the cycle
        self.start_node_id = None  # ID of the start node within the cycle
        self.end_node_ids = []  # IDs of end nodes within the cycle

        self.graph: StateGraph | CompiledStateGraph | None = None
        self.build_graph()
        self.iteration_flag = True

    def pure_cycle_graph(self) -> tuple[list, list]:
        """
        Extract cycle-scoped nodes and internal edges from the workflow configuration.

        This method:
        - Identifies all nodes marked with `cycle == self.node_id`
        - Collects edges that fully connect cycle nodes
        - Removes extracted nodes and edges from the global workflow configuration

        Safety check:
        - Raises an error if a cycle node is connected to an external node

        Returns:
            tuple[list, list]:
                - cycle_nodes: Nodes belonging to this cycle
                - cycle_edges: Edges connecting nodes within the cycle

        Raises:
            ValueError: If a cycle node is improperly connected to an external node.
        """
        nodes = self.workflow_config.get("nodes", [])
        edges = self.workflow_config.get("edges", [])

        # Select all nodes that belong to the current cycle
        cycle_nodes = [node for node in nodes if node.get("cycle") == self.node_id]
        cycle_node_ids = {node.get("id") for node in cycle_nodes}

        cycle_edges = []
        remain_edges = []

        for edge in edges:
            source_in = edge.get("source") in cycle_node_ids
            target_in = edge.get("target") in cycle_node_ids

            # Raise error if cycle nodes are connected with external nodes
            if source_in ^ target_in:
                raise ValueError(
                    f"Cycle node is connected to external node, "
                    f"source: {edge.get('source')}, target: {edge.get('target')}"
                )

            if source_in and target_in:
                cycle_edges.append(edge)
            else:
                remain_edges.append(edge)

        # Update workflow_config by removing cycle nodes and internal edges
        self.workflow_config["nodes"] = [
            node for node in nodes if node.get("cycle") != self.node_id
        ]
        self.workflow_config["edges"] = remain_edges

        return cycle_nodes, cycle_edges

    def build_graph(self):
        """
        Build and compile the internal subgraph for this cycle node.

        Steps:
        1. Extract cycle nodes and internal edges from the workflow
        2. Construct a StateGraph using GraphBuilder in subgraph mode
        3. Compile the graph for runtime execution
        """
        from app.core.workflow.graph_builder import GraphBuilder
        self.cycle_nodes, self.cycle_edges = self.pure_cycle_graph()
        self.graph = GraphBuilder(
            {
                "nodes": self.cycle_nodes,
                "edges": self.cycle_edges,
            },
            subgraph=True
        ).build()

    async def execute(self, state: WorkflowState) -> Any:
        """
        Execute the cycle node at runtime.

        Based on the node type:
        - LOOP: Executes LoopRuntime, repeatedly invoking the subgraph
        - ITERATION: Executes IterationRuntime, iterating over a collection

        Args:
            state: The current workflow state when entering the cycle node.

        Returns:
            Any: The runtime result produced by the loop or iteration executor.

        Raises:
            RuntimeError: If the node type is unsupported.
        """
        if self.node_type == NodeType.LOOP:
            return await LoopRuntime(
                graph=self.graph,
                node_id=self.node_id,
                config=self.config,
                state=state,
            ).run()
        if self.node_type == NodeType.ITERATION:
            return await IterationRuntime(
                graph=self.graph,
                node_id=self.node_id,
                config=self.config,
                state=state,
            ).run()
        raise RuntimeError("Unknown cycle node type")
