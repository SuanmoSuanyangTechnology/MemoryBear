import logging
import uuid
from typing import Any

from langgraph.graph.state import CompiledStateGraph, StateGraph
from langgraph.graph import START, END

from app.core.workflow.expression_evaluator import evaluate_condition
from app.core.workflow.nodes import WorkflowState, NodeFactory
from app.core.workflow.nodes.enums import NodeType

logger = logging.getLogger(__name__)


# TODO: 子图拆解支持
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

        self.graph: StateGraph | CompiledStateGraph | None = None

    @property
    def nodes(self) -> list[dict[str, Any]]:
        return self.workflow_config.get("nodes", [])

    @property
    def edges(self) -> list[dict[str, Any]]:
        return self.workflow_config.get("edges", [])

    def _analyze_end_node_prefixes(self) -> tuple[dict[str, str], set[str]]:
        """分析 End 节点的前缀配置

        检查每个 End 节点的模板，找到直接上游节点的引用，
        提取该引用之前的前缀部分。

        Returns:
            元组：({上游节点ID: End节点前缀}, {与End相邻且被引用的节点ID集合})
        """
        import re

        prefixes = {}
        adjacent_and_referenced = set()  # 记录与 End 节点相邻且被引用的节点

        # 找到所有 End 节点
        end_nodes = [node for node in self.nodes if node.get("type") == "end"]
        logger.info(f"[前缀分析] 找到 {len(end_nodes)} 个 End 节点")

        for end_node in end_nodes:
            end_node_id = end_node.get("id")
            output_template = end_node.get("config", {}).get("output")

            logger.info(f"[前缀分析] End 节点 {end_node_id} 模板: {output_template}")

            if not output_template:
                continue

            # 查找模板中引用了哪些节点
            # 匹配 {{node_id.xxx}} 或 {{ node_id.xxx }} 格式（支持空格）
            pattern = r'\{\{\s*([a-zA-Z0-9_-]+)\.[a-zA-Z0-9_]+\s*\}\}'
            matches = list(re.finditer(pattern, output_template))

            logger.info(f"[前缀分析] 模板中找到 {len(matches)} 个节点引用")

            # 找到所有直接连接到 End 节点的上游节点
            direct_upstream_nodes = []
            for edge in self.edges:
                if edge.get("target") == end_node_id:
                    source_node_id = edge.get("source")
                    direct_upstream_nodes.append(source_node_id)

            logger.info(f"[前缀分析] End 节点的直接上游节点: {direct_upstream_nodes}")

            # 找到第一个直接上游节点的引用
            for match in matches:
                referenced_node_id = match.group(1)
                logger.info(f"[前缀分析] 检查引用: {referenced_node_id}")

                if referenced_node_id in direct_upstream_nodes:
                    # 这是直接上游节点的引用，提取前缀
                    prefix = output_template[:match.start()]

                    logger.info(f"[前缀分析] ✅ 找到直接上游节点 {referenced_node_id} 的引用，前缀: '{prefix}'")

                    # 标记这个节点为"相邻且被引用"
                    adjacent_and_referenced.add(referenced_node_id)

                    if prefix:
                        prefixes[referenced_node_id] = prefix
                        logger.info(f"✅ [前缀分析] 为节点 {referenced_node_id} 配置前缀: '{prefix[:50]}...'")

                    # 只处理第一个直接上游节点的引用
                    break

        logger.info(f"[前缀分析] 最终配置: {prefixes}")
        logger.info(f"[前缀分析] 与 End 相邻且被引用的节点: {adjacent_and_referenced}")
        return prefixes, adjacent_and_referenced

    def add_nodes(self):
        end_prefixes, adjacent_and_referenced = self._analyze_end_node_prefixes() if self.stream else ({}, set())

        for node in self.nodes:
            node_type = node.get("type")
            node_id = node.get("id")
            cycle_node = node.get("cycle")
            if cycle_node:
                # 处于循环子图中的节点由 CycleGraphNode 进行构建处理
                if not self.subgraph:
                    continue

            # 记录 start 和 end 节点 ID
            if node_type in [NodeType.START, NodeType.CYCLE_START]:
                self.start_node_id = node_id
            elif node_type == NodeType.END:
                self.end_node_ids.append(node_id)

            # 创建节点实例（现在 start 和 end 也会被创建）
            # NOTE:Loop node creation automatically removes the nodes and edges of the subgraph from the current graph
            node_instance = NodeFactory.create_node(node, self.workflow_config)

            if node_type in [NodeType.IF_ELSE, NodeType.HTTP_REQUEST, NodeType.QUESTION_CLASSIFIER]:

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
                # 如果是流式模式，且节点有 End 前缀配置，注入配置
                if self.stream and node_id in end_prefixes:
                    # 将 End 前缀配置注入到节点实例
                    node_instance._end_node_prefix = end_prefixes[node_id]
                    logger.info(f"为节点 {node_id} 注入 End 前缀配置")

                # 如果是流式模式，标记节点是否与 End 相邻且被引用
                if self.stream:
                    node_instance._is_adjacent_to_end = node_id in adjacent_and_referenced
                    if node_id in adjacent_and_referenced:
                        logger.info(f"节点 {node_id} 标记为与 End 相邻且被引用")

                # 包装节点的 run 方法
                # 使用函数工厂避免闭包问题
                if self.stream:
                    # 流式模式：创建 async generator 函数
                    # LangGraph 会收集所有 yield 的值，最后一个 yield 的字典会被合并到 state
                    def make_stream_func(inst):
                        async def node_func(state: WorkflowState):
                            # logger.debug(f"流式执行节点: {inst.node_id}, 支持流式: {inst.supports_streaming()}")
                            async for item in inst.run_stream(state):
                                yield item

                        return node_func

                    self.graph.add_node(node_id, make_stream_func(node_instance))
                else:
                    # 非流式模式：创建 async function
                    def make_func(inst):
                        async def node_func(state: WorkflowState):
                            return await inst.run(state)

                        return node_func

                    self.graph.add_node(node_id, make_func(node_instance))

                logger.debug(f"添加节点: {node_id} (type={node_type}, stream={self.stream})")

    def add_edges(self):
        if self.start_node_id:
            self.graph.add_edge(START, self.start_node_id)
            logger.debug(f"添加边: START -> {self.start_node_id}")

        for edge in self.edges:
            source = edge.get("source")
            target = edge.get("target")
            edge_type = edge.get("type")
            condition = edge.get("condition")

            # 跳过从 start 节点出发的边（因为已经从 START 连接到 start）
            if source == self.start_node_id:
                # 但要连接 start 到下一个节点
                self.graph.add_edge(source, target)
                logger.debug(f"添加边: {source} -> {target}")
                continue

            # # 处理到 end 节点的边
            # if target in end_node_ids:
            #     # 连接到 end 节点
            #     workflow.add_edge(source, target)
            #     logger.debug(f"添加边: {source} -> {target}")
            #     continue

            # 跳过错误边（在节点内部处理）
            if edge_type == "error":
                continue

            if condition:
                # 条件边
                def make_router(cond, tgt):
                    """Dynamically generate a conditional router function to ensure each branch has a unique name."""

                    def router_fn(state: WorkflowState):
                        if evaluate_condition(
                                cond,
                                state.get("variables", {}),
                                state.get("runtime_vars", {}),
                                {
                                    "execution_id": state.get("execution_id"),
                                    "workspace_id": state.get("workspace_id"),
                                    "user_id": state.get("user_id")
                                }
                        ):
                            return tgt
                        return END

                    # 动态修改函数名，避免重复
                    router_fn.__name__ = f"router_{uuid.uuid4().hex[:8]}_{tgt}"
                    return router_fn

                router_fn = make_router(condition, target)
                self.graph.add_conditional_edges(source, router_fn)
                logger.debug(f"添加条件边: {source} -> {target} (condition={condition})")
            else:
                # 普通边
                self.graph.add_edge(source, target)
                logger.debug(f"添加边: {source} -> {target}")

        # 从 end 节点连接到 END
        for end_node_id in self.end_node_ids:
            self.graph.add_edge(end_node_id, END)
            logger.debug(f"添加边: {end_node_id} -> END")
        return

    def build(self) -> CompiledStateGraph:
        self.graph = StateGraph(WorkflowState)
        self.add_nodes()
        self.add_edges()  # 添加边必须在添加节点之后
        return self.graph.compile()
