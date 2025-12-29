import logging
from typing import Any

from langgraph.graph import StateGraph, START, END
from langgraph.graph.state import CompiledStateGraph

from app.core.workflow.expression_evaluator import evaluate_condition
from app.core.workflow.nodes import WorkflowState
from app.core.workflow.nodes.base_node import BaseNode
from app.core.workflow.nodes.cycle_graph.config import LoopNodeConfig, IterationNodeConfig
from app.core.workflow.nodes.cycle_graph.iteration import IterationRuntime
from app.core.workflow.nodes.cycle_graph.loop import LoopRuntime
from app.core.workflow.nodes.enums import NodeType

logger = logging.getLogger(__name__)


class CycleGraphNode(BaseNode):
    """
    Node representing a cycle (loop) subgraph within the workflow.

    This node manages internal loop/iteration nodes, builds a subgraph
    for execution, handles conditional routing, and executes loop
    or iteration logic based on node type.
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
        Extract cycle nodes and internal edges from the workflow configuration,
        removing them from the global workflow.

        Raises:
            ValueError: If cycle nodes are connected to external nodes improperly.

        Returns:
            Tuple containing:
            - cycle_nodes: List of removed nodes
            - cycle_edges: List of removed edges
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
                raise ValueError(f"循环节点与外部节点存在连接,soruce: {edge.get("source")}, target:{edge.get("target")}")

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

    def create_node(self):
        """
        Instantiate node objects for each node in the cycle subgraph and add them to the graph.

        Special handling is applied for conditional nodes to generate
        edge conditions based on node outputs.
        """
        from app.core.workflow.nodes import NodeFactory
        for node in self.cycle_nodes:
            node_type = node.get("type")
            node_id = node.get("id")

            if node_type == NodeType.CYCLE_START:
                self.start_node_id = node_id
                continue
            elif node_type == NodeType.END:
                self.end_node_ids.append(node_id)

            node_instance = NodeFactory.create_node(node, self.workflow_config)

            if node_type in [NodeType.IF_ELSE, NodeType.HTTP_REQUEST]:
                expressions = node_instance.build_conditional_edge_expressions()

                # Number of branches, usually matches the number of conditional expressions
                branch_number = len(expressions)

                # Find all edges whose source is the current node
                related_edge = [edge for edge in self.cycle_edges if edge.get("source") == node_id]

                # Iterate over each branch
                for idx in range(branch_number):
                    # Generate a condition expression for each edge
                    # Used later to determine which branch to take based on the node's output
                    # Assumes node output `node.<node_id>.output` matches the edge's label
                    # For example, if node.123.output == 'CASE1', take the branch labeled 'CASE1'
                    related_edge[idx]['condition'] = f"node.{node_id}.output == '{related_edge[idx]['label']}'"

            def make_func(inst):
                async def node_func(state: WorkflowState):
                    return await inst.run(state)

                return node_func

            self.graph.add_node(node_id, make_func(node_instance))

    def create_edge(self):
        """
        Connect nodes within the cycle subgraph by adding edges to the internal graph.

        Conditional edges are routed based on evaluated expressions.
        Start and end nodes are connected to global START and END nodes.
        """
        for edge in self.cycle_edges:
            source = edge.get("source")
            target = edge.get("target")
            edge_type = edge.get("type")
            condition = edge.get("condition")

            # 跳过从 start 节点出发的边（因为已经从 START 连接到 start）
            if source == self.start_node_id:
                # 但要连接 start 到下一个节点
                self.graph.add_edge(START, target)
                logger.debug(f"添加边: {source} -> {target}")
                continue

            if condition:
                # 条件边
                def router(state: WorkflowState, cond=condition, tgt=target):
                    """条件路由函数"""
                    if evaluate_condition(
                            cond,
                            state.get("variables", {}),
                            state.get("node_outputs", {}),
                            {
                                "execution_id": state.get("execution_id"),
                                "workspace_id": state.get("workspace_id"),
                                "user_id": state.get("user_id")
                            }
                    ):
                        return tgt
                    return END  # 条件不满足，结束

                self.graph.add_conditional_edges(source, router)
                logger.debug(f"添加条件边: {source} -> {target} (condition={condition})")
            else:
                # 普通边
                self.graph.add_edge(source, target)
                logger.debug(f"添加边: {source} -> {target}")

        # 从 end 节点连接到 END
        for end_node_id in self.end_node_ids:
            self.graph.add_edge(end_node_id, END)
            logger.debug(f"添加边: {end_node_id} -> END")

    def build_graph(self):
        """
        Build the internal subgraph for the cycle node.

        Steps:
        1. Extract cycle nodes and edges.
        2. Create node instances and add them to the graph.
        3. Connect edges and conditional routes.
        4. Compile the graph for execution.
        """
        self.graph = StateGraph(WorkflowState)
        self.cycle_nodes, self.cycle_edges = self.pure_cycle_graph()
        self.create_node()
        self.create_edge()
        self.graph = self.graph.compile()

    async def execute(self, state: WorkflowState) -> Any:
        """
        Execute the cycle node at runtime.

        Depending on the node type, runs either a loop (LoopRuntime)
        or an iteration (IterationRuntime) over the internal subgraph.

        Args:
            state: Current workflow state.

        Returns:
            Runtime result of the cycle, typically the final loop/iteration variables.

        Raises:
            RuntimeError: If node type is unrecognized.
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
        raise RuntimeError("未知循环节点类型")
