"""
变量池 (Variable Pool)

工作流执行的数据中心，管理所有变量的存储和访问。

变量类型：
1. 系统变量 (sys.*) - 系统内置变量（execution_id, workspace_id, user_id, message 等）
2. 节点输出 (node_id.*) - 节点执行结果
3. 会话变量 (conv.*) - 会话级变量（跨多轮对话保持）
"""

import logging
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from app.core.workflow.nodes import WorkflowState

logger = logging.getLogger(__name__)


class VariableSelector:
    """变量选择器
    
    用于引用变量的路径表示。
    
    Examples:
        >>> selector = VariableSelector(["sys", "message"])
        >>> selector = VariableSelector(["node_A", "output"])
        >>> selector = VariableSelector.from_string("sys.message")
    """
    
    def __init__(self, path: list[str]):
        """初始化变量选择器
        
        Args:
            path: 变量路径，如 ["sys", "message"] 或 ["node_A", "output"]
        """
        if not path or len(path) < 1:
            raise ValueError("变量路径不能为空")
        
        self.path = path
        self.namespace = path[0]  # sys, var, 或 node_id
        self.key = path[1] if len(path) > 1 else None
    
    @classmethod
    def from_string(cls, selector_str: str) -> "VariableSelector":
        """从字符串创建选择器
        
        Args:
            selector_str: 选择器字符串，如 "sys.message" 或 "node_A.output"
        
        Returns:
            VariableSelector 实例
        
        Examples:
            >>> selector = VariableSelector.from_string("sys.message")
            >>> selector = VariableSelector.from_string("llm_qa.output")
        """
        path = selector_str.split(".")
        return cls(path)
    
    def __str__(self) -> str:
        return ".".join(self.path)
    
    def __repr__(self) -> str:
        return f"VariableSelector({self.path})"


class VariablePool:
    """变量池
    
    管理工作流执行过程中的所有变量。
    
    变量命名空间：
    - sys.*: 系统变量（message, execution_id, workspace_id, user_id, conversation_id）
    - conv.*: 会话变量（跨多轮对话保持的变量）
    - <node_id>.*: 节点输出
    
    Examples:
        >>> pool = VariablePool(state)
        >>> pool.get(["sys", "message"])
        "用户的问题"
        >>> pool.get(["llm_qa", "output"])
        "AI 的回答"
        >>> pool.set(["conv", "user_name"], "张三")
    """
    
    def __init__(self, state: "WorkflowState"):
        """初始化变量池
        
        Args:
            state: 工作流状态（LangGraph State）
        """
        self.state = state
    
    def get(self, selector: list[str] | str, default: Any = None) -> Any:
        """获取变量值
        
        Args:
            selector: 变量选择器，可以是列表或字符串
            default: 默认值（变量不存在时返回）
        
        Returns:
            变量值
        
        Examples:
            >>> pool.get(["sys", "message"])
            >>> pool.get("sys.message")
            >>> pool.get(["llm_qa", "output"])
            >>> pool.get("llm_qa.output")
        
        Raises:
            KeyError: 变量不存在且未提供默认值
        """
        # 转换为 VariableSelector
        if isinstance(selector, str):
            selector = VariableSelector.from_string(selector).path
        
        if not selector or len(selector) < 1:
            raise ValueError("变量选择器不能为空")
        
        namespace = selector[0]
        
        try:
            # 系统变量
            if namespace == "sys":
                key = selector[1] if len(selector) > 1 else None
                if not key:
                    return self.state.get("variables", {}).get("sys", {})
                return self.state.get("variables", {}).get("sys", {}).get(key, default)
            
            # 会话变量
            elif namespace == "conv":
                key = selector[1] if len(selector) > 1 else None
                if not key:
                    return self.state.get("variables", {}).get("conv", {})
                return self.state.get("variables", {}).get("conv", {}).get(key, default)
            
            # 节点输出（从 runtime_vars 读取）
            else:
                node_id = namespace
                runtime_vars = self.state.get("runtime_vars", {})
                
                if node_id not in runtime_vars:
                    if default is not None:
                        return default
                    raise KeyError(f"节点 '{node_id}' 的输出不存在")
                
                node_var = runtime_vars[node_id]
                
                # 如果只有节点 ID，返回整个变量
                if len(selector) == 1:
                    return node_var
                
                # 获取特定字段
                # 支持嵌套访问，如 node_id.field.subfield
                result = node_var
                for k in selector[1:]:
                    if isinstance(result, dict):
                        result = result.get(k)
                        if result is None:
                            if default is not None:
                                return default
                            raise KeyError(f"字段 '{'.'.join(selector)}' 不存在")
                    else:
                        if default is not None:
                            return default
                        raise KeyError(f"无法访问 '{'.'.join(selector)}'")
                
                return result
        
        except KeyError:
            if default is not None:
                return default
            raise
    
    def set(self, selector: list[str] | str, value: Any):
        """设置变量值
        
        Args:
            selector: 变量选择器
            value: 变量值
        
        Examples:
            >>> pool.set(["conv", "user_name"], "张三")
            >>> pool.set("conv.user_name", "张三")
        
        Note:
            - 只能设置会话变量 (conv.*)
            - 系统变量和节点输出是只读的
        """
        # 转换为 VariableSelector
        if isinstance(selector, str):
            selector = VariableSelector.from_string(selector).path
        
        if not selector or len(selector) < 2:
            raise ValueError("变量选择器必须包含命名空间和键名")
        
        namespace = selector[0]
        
        if namespace != "conv":
            raise ValueError("只能设置会话变量 (conv.*)")
        
        key = selector[1]
        
        # 确保 variables 结构存在
        if "variables" not in self.state:
            self.state["variables"] = {"sys": {}, "conv": {}}
        if "conv" not in self.state["variables"]:
            self.state["variables"]["conv"] = {}
        
        # 设置值
        self.state["variables"]["conv"][key] = value
        
        logger.debug(f"设置变量: {'.'.join(selector)} = {value}")
    
    def has(self, selector: list[str] | str) -> bool:
        """检查变量是否存在
        
        Args:
            selector: 变量选择器
        
        Returns:
            变量是否存在
        
        Examples:
            >>> pool.has(["sys", "message"])
            True
            >>> pool.has("llm_qa.output")
            False
        """
        try:
            self.get(selector)
            return True
        except KeyError:
            return False
    
    def get_all_system_vars(self) -> dict[str, Any]:
        """获取所有系统变量
        
        Returns:
            系统变量字典
        """
        return self.state.get("variables", {}).get("sys", {})
    
    def get_all_conversation_vars(self) -> dict[str, Any]:
        """获取所有会话变量
        
        Returns:
            会话变量字典
        """
        return self.state.get("variables", {}).get("conv", {})
    
    def get_all_node_outputs(self) -> dict[str, Any]:
        """获取所有节点输出（运行时变量）
        
        Returns:
            节点输出字典，键为节点 ID
        """
        return self.state.get("runtime_vars", {})
    
    def get_node_output(self, node_id: str) -> dict[str, Any] | None:
        """获取指定节点的输出（运行时变量）
        
        Args:
            node_id: 节点 ID
        
        Returns:
            节点输出或 None
        """
        return self.state.get("runtime_vars", {}).get(node_id)
    
    def to_dict(self) -> dict[str, Any]:
        """导出为字典
        
        Returns:
            包含所有变量的字典
        """
        return {
            "system": self.get_all_system_vars(),
            "conversation": self.get_all_conversation_vars(),
            "nodes": self.get_all_node_outputs()  # 从 runtime_vars 读取
        }
    
    def __repr__(self) -> str:
        sys_vars = self.get_all_system_vars()
        conv_vars = self.get_all_conversation_vars()
        runtime_vars = self.get_all_node_outputs()
        
        return (
            f"VariablePool(\n"
            f"  system_vars={len(sys_vars)},\n"
            f"  conversation_vars={len(conv_vars)},\n"
            f"  runtime_vars={len(runtime_vars)}\n"
            f")"
        )
