"""
工作流配置验证器

验证工作流配置的有效性，确保配置符合规范。
"""

import copy
import logging
import re
from collections import defaultdict, deque
from typing import Any, Union, TYPE_CHECKING

from app.core.workflow.triggers import TRIGGER_NODES_PREPARED_FLAG, validate_trigger_nodes
from app.core.workflow.nodes.enums import NodeType

if TYPE_CHECKING:
    from app.schemas.workflow_schema import WorkflowConfig

logger = logging.getLogger(__name__)


class WorkflowValidator:
    """工作流配置验证器"""

    ENV_NAME_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
    ENV_ALLOWED_TYPES = {"string", "number", "secret"}

    @classmethod
    def pure_cycle_graph(cls, workflow_config: Union[dict[str, Any], Any], node_id) -> tuple[list, list]:
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
        nodes = workflow_config.get("nodes", [])
        edges = workflow_config.get("edges", [])

        # Select all nodes that belong to the current cycle
        cycle_nodes = [node for node in nodes if node.get("cycle") == node_id]
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
        workflow_config["nodes"] = [
            node for node in nodes if node.get("cycle") != node_id
        ]
        workflow_config["edges"] = remain_edges

        return cycle_nodes, cycle_edges

    @classmethod
    def get_subgraph(cls, workflow_config: Union[dict[str, Any], "WorkflowConfig"]) -> list:
        if not isinstance(workflow_config, dict):
            workflow_config = {
                "nodes": workflow_config.nodes,
                "edges": workflow_config.edges,
                "variables": workflow_config.variables,
            }
        cycle_nodes = [
            node.get("id")
            for node in workflow_config.get("nodes", [])
            if node.get("type") in [NodeType.LOOP, NodeType.ITERATION]
        ]
        graphs = []
        for cycle_node in cycle_nodes:
            nodes, edges = cls.pure_cycle_graph(workflow_config, cycle_node)
            graphs.append({
                "nodes": nodes,
                "edges": edges,
            })
        graphs.append(workflow_config)
        return graphs

    @classmethod
    def validate(cls, workflow_config: Union[dict[str, Any], Any], publish=False) -> tuple[bool, list[str]]:
        """验证工作流配置
        
        Args:
            publish: 发布验证标识
            workflow_config: 工作流配置字典或 WorkflowConfig Pydantic 模型
        
        Returns:
            (is_valid, errors): 是否有效和错误列表
        
        Examples:
            >>> config = {
            ...     "nodes": [
            ...         {"id": "start", "type": "start"},
            ...         {"id": "end", "type": "end"}
            ...     ],
            ...     "edges": [
            ...         {"source": "start", "target": "end"}
            ...     ]
            ... }
            >>> is_valid, errors = WorkflowValidator.validate(config)
            >>> is_valid
            True
        """
        workflow_config = copy.deepcopy(workflow_config)
        errors = []
        workflow_type = workflow_config.get("workflow_type", "workflow") if isinstance(workflow_config, dict) \
            else getattr(workflow_config, "workflow_type", "workflow")
        environment_variables = workflow_config.get("environment_variables", []) if isinstance(workflow_config, dict) \
            else getattr(workflow_config, "environment_variables", [])
        trigger_nodes_prepared = (
            isinstance(workflow_config, dict)
            and workflow_config.get(TRIGGER_NODES_PREPARED_FLAG) is True
        )

        if workflow_type == "pure_workflow" and (workflow_config.get("variables", []) if isinstance(workflow_config, dict)
                                                 else getattr(workflow_config, "variables", [])):
            errors.append("pure_workflow 不支持会话变量 variables，请改用开始节点输入变量或 environment_variables")

        errors.extend(cls._validate_environment_variables(environment_variables, publish=publish))

        graphs = cls.get_subgraph(workflow_config)
        for index, graph in enumerate(graphs):
            nodes = graph.get("nodes", [])
            edges = graph.get("edges", [])
            variables = graph.get("variables", [])
            is_main_graph = index == len(graphs) - 1

            # 1. 验证入口节点（主图支持 start/trigger，循环子图仅支持 cycle-start）
            if is_main_graph:
                entry_nodes = [
                    n for n in nodes
                    if n.get("type") in [NodeType.START, NodeType.TRIGGER]
                ]
                if len(entry_nodes) == 0:
                    errors.append("工作流必须有一个入口节点（start 或 trigger）")
                elif len(entry_nodes) > 1:
                    errors.append(f"工作流只能有一个入口节点，当前有 {len(entry_nodes)} 个")
                if not trigger_nodes_prepared:
                    try:
                        validate_trigger_nodes(nodes)
                    except ValueError as exc:
                        errors.append(str(exc))
            else:
                entry_nodes = [n for n in nodes if n.get("type") == NodeType.CYCLE_START]
                if len(entry_nodes) == 0:
                    errors.append("循环子图必须有一个 cycle-start 节点")
                elif len(entry_nodes) > 1:
                    errors.append(f"循环子图只能有一个 cycle-start 节点，当前有 {len(entry_nodes)} 个")

            if is_main_graph:
                # 2. 验证 主图end 节点（至少一个，output 节点也可作为终止节点）
                end_nodes = [n for n in nodes if n.get("type") in [NodeType.END, NodeType.OUTPUT]]
                if len(end_nodes) == 0:
                    errors.append("工作流必须至少有一个 end 节点 或 output 节点")

            # 3. 验证节点 ID 唯一性
            node_ids = [n.get("id") for n in nodes if n.get("type") != NodeType.NOTES]
            if len(node_ids) != len(set(node_ids)):
                duplicates = [nid for nid in node_ids if node_ids.count(nid) > 1]
                errors.append(f"节点 ID 必须唯一，重复的 ID: {set(duplicates)}")

            # 4. 验证节点必须有 id 和 type
            for i, node in enumerate(nodes):
                if not node.get("id"):
                    errors.append(f"节点 #{i} 缺少 id 字段")
                if not node.get("type"):
                    errors.append(f"节点 #{i} (id={node.get('id', 'unknown')}) 缺少 type 字段")

            # 5. 验证边的有效性
            node_id_set = set(node_ids)
            for i, edge in enumerate(edges):
                source = edge.get("source")
                target = edge.get("target")

                if not source:
                    errors.append(f"边 #{i} 缺少 source 字段")
                elif source not in node_id_set:
                    errors.append(f"边 #{i} 的 source 节点不存在: {source}")

                if not target:
                    errors.append(f"边 #{i} 缺少 target 字段")
                elif target not in node_id_set:
                    errors.append(f"边 #{i} 的 target 节点不存在: {target}")

            if publish:
                # 仅在发布时验证所有节点可达
                # 6. 验证所有节点可达（从 start 节点出发）
                if entry_nodes and not errors:  # 只有在前面验证通过时才检查可达性
                    reachable = WorkflowValidator.get_reachable_nodes(
                        entry_nodes[0]["id"],
                        edges
                    )
                    unreachable = node_id_set - reachable
                    if unreachable:
                        errors.append(f"以下节点无法从入口节点到达: {unreachable}")

            # 7. 检测循环依赖（非 loop 节点）
            if not errors:  # 只有在前面验证通过时才检查循环
                has_cycle, cycle_path = WorkflowValidator._has_cycle(nodes, edges)
                if has_cycle:
                    errors.append(
                        f"工作流存在循环依赖（请使用 loop/iteration 节点实现循环）: {' -> '.join(cycle_path)}"
                    )

            # 8. 验证变量名
            from app.core.workflow.utils.expression_evaluator import ExpressionEvaluator
            var_errors = ExpressionEvaluator.validate_variable_names(variables)
            errors.extend(var_errors)

            # 9. 验证开始节点变量的默认值长度
            for node in nodes:
                if node.get("type") not in [NodeType.START, NodeType.CYCLE_START]:
                    continue
                for var_def in node.get("config", {}).get("variables", []):
                    if var_def.get("type") == "string" and isinstance(var_def.get("default"), str):
                        max_length = var_def.get("max_length")
                        if max_length is not None and len(var_def["default"]) > max_length:
                            errors.append(
                                f"开始节点变量 '{var_def.get('name')}' 的默认值长度 ({len(var_def['default'])}) 超过最大长度限制 ({max_length})"
                            )

        return len(errors) == 0, errors

    @classmethod
    def _validate_environment_variables(
        cls,
        environment_variables: list[dict[str, Any]] | None,
        *,
        publish: bool = False,
    ) -> list[str]:
        errors: list[str] = []
        names: set[str] = set()

        for item in environment_variables or []:
            name = item.get("name", "")
            value_type = item.get("value_type")
            value = item.get("value")
            required = bool(item.get("required"))

            if not name:
                errors.append("environment_variables 中存在缺少 name 的变量")
                continue
            if name in names:
                errors.append(f"environment variable '{name}' 重复定义")
            names.add(name)

            if not cls.ENV_NAME_PATTERN.match(name):
                errors.append(f"environment variable '{name}' 名称不合法，建议使用大写字母、数字和下划线")

            if value_type not in cls.ENV_ALLOWED_TYPES:
                errors.append(
                    f"environment variable '{name}' 的 value_type '{value_type}' 不支持，仅支持 string、number、secret"
                )
                continue

            if value_type in {"string", "secret"} and value is not None and not isinstance(value, str):
                errors.append(f"environment variable '{name}' 的值必须是字符串")
            if value_type == "number" and value is not None and not isinstance(value, (int, float)):
                errors.append(f"environment variable '{name}' 的值必须是数字")

            if publish and required:
                if value in (None, ""):
                    errors.append(f"必填环境变量 '{name}' 尚未配置")
                elif value_type == "secret" and value == "__SECRET__":
                    errors.append(f"必填 secret 环境变量 '{name}' 仍为占位值，请先补全")

        return errors

    @staticmethod
    def get_reachable_nodes(start_id: str, edges: list[dict]) -> set[str]:
        """获取从 start 节点可达的所有节点
        
        Args:
            start_id: 起始节点 ID
            edges: 边列表
        
        Returns:
            可达节点 ID 集合
        """
        adj = defaultdict(list)
        for edge in edges:
            adj[edge["source"]].append(edge["target"])

        reachable = {start_id}
        queue = deque([start_id])
        while queue:
            current = queue.popleft()
            for target in adj[current]:
                if target not in reachable:
                    reachable.add(target)
                    queue.append(target)
        return reachable

    @staticmethod
    def _has_cycle(nodes: list[dict], edges: list[dict]) -> tuple[bool, list[str]]:
        """检测是否存在循环依赖（DFS）
        
        Args:
            nodes: 节点列表
            edges: 边列表
        
        Returns:
            (has_cycle, cycle_path): 是否有循环和循环路径
        """
        graph: dict[str, list[str]] = {}
        for edge in edges:
            source = edge.get("source")
            target = edge.get("target")
            edge_type = edge.get("type")

            # 跳过错误边
            if edge_type == "error":
                continue

            if source and target:
                if source not in graph:
                    graph[source] = []
                graph[source].append(target)

        # DFS 检测环
        visited = set()
        rec_stack = set()
        path = []
        cycle_path = []

        def dfs(node: str) -> bool:
            """DFS 检测环，返回是否找到环"""
            visited.add(node)
            rec_stack.add(node)
            path.append(node)

            for neighbor in graph.get(node, []):
                if neighbor not in visited:
                    if dfs(neighbor):
                        return True
                elif neighbor in rec_stack:
                    # 找到环，记录环路径
                    cycle_start = path.index(neighbor)
                    cycle_path.extend([*path[cycle_start:], neighbor])
                    return True

            rec_stack.remove(node)
            path.pop()
            return False

        # 检查所有节点
        for node_id in graph:
            if node_id not in visited:
                if dfs(node_id):
                    return True, cycle_path

        return False, []

    @staticmethod
    def validate_for_publish(workflow_config: dict[str, Any]) -> tuple[bool, list[str]]:
        """验证工作流配置是否可以发布（更严格的验证）
        
        Args:
            workflow_config: 工作流配置
        
        Returns:
            (is_valid, errors): 是否有效和错误列表
        """
        # 先执行基础验证
        is_valid, errors = WorkflowValidator.validate(workflow_config, publish=True)

        if not is_valid:
            return False, errors

        # 额外的发布验证
        nodes = workflow_config.get("nodes", [])

        # 1. 验证所有节点都有名称
        for node in nodes:
            if node.get("type") not in [NodeType.START, NodeType.TRIGGER, NodeType.CYCLE_START, NodeType.END] \
                    and not node.get("name"):
                errors.append(
                    f"节点 {node.get('name')} 缺少名称（发布时必须提供）"
                )

        # 2. 验证所有非 start/end 节点都有配置
        for node in nodes:
            node_type = node.get("type")
            if node_type not in [NodeType.START, NodeType.TRIGGER, NodeType.CYCLE_START, NodeType.END, NodeType.BREAK]:
                config = node.get("config")
                if not config or not isinstance(config, dict):
                    errors.append(
                        f"节点 {node.get('name')} 缺少配置（发布时必须提供）"
                    )

        # 3. 验证必填变量
        variables = workflow_config.get("variables", [])
        required_vars = [v for v in variables if v.get("required")]
        if required_vars:
            # 这里只是提示，实际执行时会检查
            logger.info(
                f"工作流包含 {len(required_vars)} 个必填变量: "
                f"{[v.get('name') for v in required_vars]}"
            )

        return len(errors) == 0, errors


def validate_workflow_config(
        workflow_config: Union[dict[str, Any], 'WorkflowConfig'],
        for_publish: bool = False
) -> tuple[bool, list[str]]:
    """验证工作流配置（便捷函数）
    
    Args:
        workflow_config: 工作流配置
        for_publish: 是否为发布验证（更严格）
    
    Returns:
        (is_valid, errors): 是否有效和错误列表
    """
    if for_publish:
        return WorkflowValidator.validate_for_publish(workflow_config)
    else:
        return WorkflowValidator.validate(workflow_config)
