"""
变量池 (Variable Pool)

工作流执行的数据中心，管理所有变量的存储和访问。

变量类型：
1. 系统变量 (sys.*) - 系统内置变量（execution_id, workspace_id, user_id, message 等）
2. 节点输出 (node_id.*) - 节点执行结果
3. 会话变量 (conv.*) - 会话级变量（跨多轮对话保持）
"""

import logging
import re
from asyncio import Lock
from collections import defaultdict
from copy import deepcopy
from typing import Any, Generic

from pydantic import BaseModel

from app.core.workflow.variable.base_variable import VariableType
from app.core.workflow.variable.variable_objects import T, create_variable_instance

logger = logging.getLogger(__name__)


class VariableSelector:
    """变量选择器
    
    用于引用变量的路径表示。
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
        """
        path = selector_str.split(".")
        return cls(path)

    def __str__(self) -> str:
        return ".".join(self.path)

    def __repr__(self) -> str:
        return f"VariableSelector({self.path})"


class VariableStruct(BaseModel, Generic[T]):
    """A typed variable struct.

    Represents a runtime variable with an associated logical type and
    a concrete value object.

    This class bridges the static type system (via generics) and the
    runtime type system (via ``VariableType``).

    Attributes:
        type:
            Logical variable type descriptor used for runtime validation,
            serialization, and workflow type checking.
        instance:
            The concrete variable object. The actual Python type is
            represented by the generic parameter ``T`` (e.g. StringVariable,
            NumberVariable, ArrayObject[StringVariable]).
        mut:
            Whether the variable is mutable.
    """
    type: VariableType
    instance: T
    mut: bool

    model_config = {
        "arbitrary_types_allowed": True
    }


class VariablePool:
    """Variable pool.

    Manages all variables during workflow execution, including storage,
    namespacing, and concurrency control.

    Variable namespace conventions:
        - ``sys.*``:
            System variables (e.g. message, execution_id, workspace_id,
            user_id, conversation_id).
        - ``conv.*``:
            Conversation-level variables that persist across multiple turns.
        - ``<node_id>.*``:
            Variables produced by workflow nodes.
    """

    def __init__(self):
        """Initialize the variable pool.

        Attributes:
            self.locks:
                A per-key lock table used for fine-grained concurrency control.

            self.variables:
                Storage for all variables managed by the pool.
        """
        self.locks = defaultdict(Lock)
        self.variables: dict[str, dict[str, VariableStruct[Any]]] = {}

    @staticmethod
    def transform_selector(selector):
        pattern = r"\{\{\s*(.*?)\s*\}\}"
        variable_literal = re.sub(pattern, r"\1", selector).strip()
        selector = VariableSelector.from_string(variable_literal).path
        if len(selector) != 2:
            raise ValueError(f"Selector not valid - {selector}")
        return selector

    def _get_variable_struct(
            self,
            selector: str
    ) -> VariableStruct[T] | None:
        """Retrieve a variable struct from the variable pool.

        Args:
            selector:
                Variable selector, either:
                - A string variable literal (e.g. "{{ sys.message }}")

        Returns:
            The variable's struct if it exists; otherwise returns None.
        """
        selector = self.transform_selector(selector)

        namespace = selector[0]
        variable_name = selector[1]

        namespace_variables = self.variables.get(namespace)
        if namespace_variables is None:
            return None

        var_instance = namespace_variables.get(variable_name)
        if var_instance is None:
            return None
        return var_instance

    def get_value(
            self,
            selector: str,
            default: Any = None,
            strict: bool = True,
    ) -> Any:
        """Retrieve a variable value from the variable pool.

        Args:
            selector:
                Variable selector, either:
                - A list of path components (e.g. ["sys", "message"])
                - A string variable literal (e.g. "{{ sys.message }}")
            default:
                The value to return if the variable does not exist.
            strict:
                If True, raises KeyError when the variable does not exist.

        Returns:
            The variable's value if it exists; otherwise returns `default`.

        Raises:
            KeyError: If strict is True and the variable does not exist.
        """
        variable_struct = self._get_variable_struct(selector)
        if variable_struct is None:
            if strict:
                raise KeyError(f"{selector} not exist")
            return default

        return variable_struct.instance.get_value()

    def get_literal(
            self,
            selector: str,
            default: Any = None,
            strict: bool = True,
    ) -> Any:
        """Retrieve a variable value from the variable pool.

        Args:
            selector:
                Variable selector, either:
                - A list of path components (e.g. ["sys", "message"])
                - A string variable literal (e.g. "{{ sys.message }}")
            default:
                The value to return if the variable does not exist.
            strict:
                If True, raises KeyError when the variable does not exist.

        Returns:
            The variable's value if it exists; otherwise returns `default`.

        Raises:
            KeyError: If strict is True and the variable does not exist.
        """
        variable_struct = self._get_variable_struct(selector)
        if variable_struct is None:
            if strict:
                raise KeyError(f"{selector} not exist")
            return default

        return variable_struct.instance.to_literal()

    async def set(
            self,
            selector: str,
            value: Any
    ):
        """设置变量值
        
        Args:
            selector: 变量选择器
            value: 变量值

        Note:
            - 只能设置会话变量 (conv.*)
            - 系统变量和节点输出是只读的
        """
        variable_struct = self._get_variable_struct(selector)
        if variable_struct is None:
            raise KeyError(f"Variable {selector} is not defined")
        if not variable_struct.mut:
            raise KeyError(f"{selector} cannot be modified")
        async with self.locks[selector]:
            variable_struct.instance.set(value)

    async def new(
            self,
            namespace: str,
            key: str,
            value: Any,
            var_type: VariableType,
            mut: bool
    ):
        if self.has(f"{namespace}.{key}"):
            try:
                await self.set(f"{namespace}.{key}", value)
            except KeyError:
                pass
        instance = create_variable_instance(var_type, value)
        variable_struct = VariableStruct(type=var_type, instance=instance, mut=mut)
        namespace_variable = self.variables.get(namespace)
        if namespace_variable is None:
            self.variables[namespace] = {
                key: variable_struct
            }
        else:
            self.variables[namespace][key] = variable_struct

    def has(self, selector: str) -> bool:
        """检查变量是否存在
        
        Args:
            selector: 变量选择器
        
        Returns:
            变量是否存在
        """
        return self._get_variable_struct(selector) is not None

    def get_all_system_vars(self) -> dict[str, Any]:
        """获取所有系统变量
        
        Returns:
            系统变量字典
        """
        sys_namespace = self.variables.get("sys", {})
        return {k: v.instance.get_value() for k, v in sys_namespace.items()}

    def get_all_conversation_vars(self) -> dict[str, Any]:
        """获取所有会话变量
        
        Returns:
            会话变量字典
        """
        conv_namespace = self.variables.get("conv", {})
        return {k: v.instance.get_value() for k, v in conv_namespace.items()}

    def get_all_node_outputs(self) -> dict[str, Any]:
        """获取所有节点输出（运行时变量）
        
        Returns:
            节点输出字典，键为节点 ID
        """
        runtime_vars = {
            namespace: {
                k: v.instance.get_value()
                for k, v in vars_dict.items()
            }
            for namespace, vars_dict in self.variables.items()
            if namespace not in ("sys", "conv")
        }
        return runtime_vars

    def get_node_output(self, node_id: str, defalut: Any = None, strict: bool = True) -> dict[str, Any] | None:
        """获取指定节点的输出（运行时变量）
        
        Args:
            node_id: 节点 ID
            defalut: 默认值
            strict: 是否严格模式
        
        Returns:
            节点输出或 None
        """
        node_namespace = self.variables.get(node_id)
        if node_namespace:
            return {k: v.instance.get_value() for k, v in node_namespace.items()}
        if strict:
            raise KeyError(f"node {node_id} output not exist")
        else:
            return defalut

    def copy(self, pool: 'VariablePool'):
        self.variables = deepcopy(pool.variables)

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
