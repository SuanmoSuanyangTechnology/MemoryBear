"""
工作流配置验证器

验证工作流配置的有效性，确保配置符合规范。
"""

import logging
from typing import Any, Union

logger = logging.getLogger(__name__)


class WorkflowValidator:
    """工作流配置验证器"""
    
    @staticmethod
    def validate(workflow_config: Union[dict[str, Any], Any]) -> tuple[bool, list[str]]:
        """验证工作流配置
        
        Args:
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
        errors = []
        
        # 支持字典和 Pydantic 模型
        if isinstance(workflow_config, dict):
            nodes = workflow_config.get("nodes", [])
            edges = workflow_config.get("edges", [])
            variables = workflow_config.get("variables", [])
        else:
            # Pydantic 模型
            nodes = getattr(workflow_config, "nodes", [])
            edges = getattr(workflow_config, "edges", [])
            variables = getattr(workflow_config, "variables", [])
        
        # 1. 验证 start 节点（有且只有一个）
        start_nodes = [n for n in nodes if n.get("type") == "start"]
        if len(start_nodes) == 0:
            errors.append("工作流必须有一个 start 节点")
        elif len(start_nodes) > 1:
            errors.append(f"工作流只能有一个 start 节点，当前有 {len(start_nodes)} 个")
        
        # 2. 验证 end 节点（至少一个）
        end_nodes = [n for n in nodes if n.get("type") == "end"]
        if len(end_nodes) == 0:
            errors.append("工作流必须至少有一个 end 节点")
        
        # 3. 验证节点 ID 唯一性
        node_ids = [n.get("id") for n in nodes]
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
        
        # 6. 验证所有节点可达（从 start 节点出发）
        if start_nodes and not errors:  # 只有在前面验证通过时才检查可达性
            reachable = WorkflowValidator._get_reachable_nodes(
                start_nodes[0]["id"], 
                edges
            )
            unreachable = node_id_set - reachable
            if unreachable:
                errors.append(f"以下节点无法从 start 节点到达: {unreachable}")
        
        # 7. 检测循环依赖（非 loop 节点）
        if not errors:  # 只有在前面验证通过时才检查循环
            has_cycle, cycle_path = WorkflowValidator._has_cycle(nodes, edges)
            if has_cycle:
                errors.append(
                    f"工作流存在循环依赖（请使用 loop 节点实现循环）: {' -> '.join(cycle_path)}"
                )
        
        # 8. 验证变量名
        from app.core.workflow.expression_evaluator import ExpressionEvaluator
        var_errors = ExpressionEvaluator.validate_variable_names(variables)
        errors.extend(var_errors)
        
        return len(errors) == 0, errors
    
    @staticmethod
    def _get_reachable_nodes(start_id: str, edges: list[dict]) -> set[str]:
        """获取从 start 节点可达的所有节点
        
        Args:
            start_id: 起始节点 ID
            edges: 边列表
        
        Returns:
            可达节点 ID 集合
        """
        reachable = {start_id}
        queue = [start_id]
        
        while queue:
            current = queue.pop(0)
            for edge in edges:
                if edge.get("source") == current:
                    target = edge.get("target")
                    if target and target not in reachable:
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
        # 排除 loop 类型的节点
        loop_nodes = {n["id"] for n in nodes if n.get("type") == "loop"}
        
        # 构建邻接表（排除 loop 节点的边和错误边）
        graph: dict[str, list[str]] = {}
        for edge in edges:
            source = edge.get("source")
            target = edge.get("target")
            edge_type = edge.get("type")
            
            # 跳过错误边
            if edge_type == "error":
                continue
            
            # 如果涉及 loop 节点，跳过
            if source in loop_nodes or target in loop_nodes:
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
        is_valid, errors = WorkflowValidator.validate(workflow_config)
        
        if not is_valid:
            return False, errors
        
        # 额外的发布验证
        nodes = workflow_config.get("nodes", [])
        
        # 1. 验证所有节点都有名称
        for node in nodes:
            if node.get("type") not in ["start", "end"] and not node.get("name"):
                errors.append(
                    f"节点 {node.get('id')} 缺少名称（发布时必须提供）"
                )
        
        # 2. 验证所有非 start/end 节点都有配置
        for node in nodes:
            node_type = node.get("type")
            if node_type not in ["start", "end"]:
                config = node.get("config")
                if not config or not isinstance(config, dict):
                    errors.append(
                        f"节点 {node.get('id')} 缺少配置（发布时必须提供）"
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
    workflow_config: dict[str, Any],
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
