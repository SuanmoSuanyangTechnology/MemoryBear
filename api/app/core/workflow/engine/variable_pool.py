# -*- coding: UTF-8 -*-
# Author: Eternity
# @Email: 1533512157@qq.com
# @Time : 2025/12/15 19:50
import logging
import re
from asyncio import Lock
from collections import defaultdict
from copy import deepcopy
from typing import Any, Generic

from pydantic import BaseModel

from app.core.workflow.engine.runtime_schema import ExecutionContext
from app.core.workflow.variable.base_variable import VariableType, DEFAULT_VALUE
from app.core.workflow.variable.variable_objects import T, create_variable_instance, ArrayVariable, FileVariable

logger = logging.getLogger(__name__)

VARIABLE_PATTERN = re.compile(r"\{\{\s*(.*?)\s*}}")


class LazyVariableDict:
    def __init__(self, source, literal):
        self._source: dict[str, VariableStruct[Any]] = source
        self._literal: bool = literal
        self._cache = {}

    def keys(self):
        return self._source.keys()

    def _resolve(self, key):
        if key in self._cache:
            return self._cache[key]
        var_struct = self._source.get(key)
        if var_struct is None:
            raise KeyError(key)
        value = var_struct.instance.to_literal() if self._literal else var_struct.instance.get_value()
        self._cache[key] = value
        return value

    def get(self, key, default=None):
        try:
            return self._resolve(key)
        except KeyError:
            return default

    def __getitem__(self, key):
        return self._resolve(key)

    def __getattr__(self, key):
        if key.startswith('_'):
            raise AttributeError(f"'{self.__class__.__name__}' object has no attribute '{key}'")
        return self._resolve(key)

    def __contains__(self, key):
        return key in self._source

    def __iter__(self):
        return iter(self._source)

    def __len__(self):
        return len(self._source)


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
            NumberVariable, ArrayVariable[StringVariable]).
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
        self.variables: dict[str, dict[str, VariableStruct[Any]]] = {"sys": {}, "conv": {}}

    @staticmethod
    def transform_selector(selector):
        variable_literal = VARIABLE_PATTERN.sub(r"\1", selector).strip()
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

    def get_instance(
            self,
            selector: str,
            default: Any = None,
            strict: bool = True
    ):
        """Retrieve a variable instance from the variable pool.

        Args:
            selector:
                Variable selector as a string variable literal (e.g. "{{ sys.message }}").
            default:
                The value to return if the variable does not exist.
            strict:
                If True, raises KeyError when the variable does not exist.

        Returns:
            The variable instance object if it exists; otherwise returns `default`.

        Raises:
            KeyError: If strict is True and the variable does not exist.
        """
        variable_struct = self._get_variable_struct(selector)
        if variable_struct is None:
            if strict:
                raise KeyError(f"{selector} not exist")
            return default

        return variable_struct.instance

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
            var_type: VariableType | None,
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

    def lazy_namespace(self, namespace: str, literal: bool = False) -> LazyVariableDict:
        return LazyVariableDict(self.variables.get(namespace, {}), literal)

    def lazy_all_node_outputs(self, literal: bool = False) -> dict[str, LazyVariableDict]:
        return {
            ns: LazyVariableDict(vars_dict, literal)
            for ns, vars_dict in self.variables.items()
            if ns not in ("sys", "conv")
        }

    def get_all_system_vars(self, literal=False) -> dict[str, Any]:
        """获取所有系统变量
        
        Returns:
            系统变量字典
        """
        sys_namespace = self.variables.get("sys", {})
        if literal:
            return {k: v.instance.to_literal() for k, v in sys_namespace.items()}
        return {k: v.instance.get_value() for k, v in sys_namespace.items()}

    def get_all_conversation_vars(self, literal=False) -> dict[str, Any]:
        """获取所有会话变量
        
        Returns:
            会话变量字典
        """
        conv_namespace = self.variables.get("conv", {})
        if literal:
            return {k: v.instance.to_literal() for k, v in conv_namespace.items()}
        return {k: v.instance.get_value() for k, v in conv_namespace.items()}

    def get_all_node_outputs(self, literal=False) -> dict[str, Any]:
        """获取所有节点输出（运行时变量）
        
        Returns:
            节点输出字典，键为节点 ID
        """
        if literal:
            runtime_vars = {
                namespace: {
                    k: v.instance.to_literal()
                    for k, v in vars_dict.items()
                }
                for namespace, vars_dict in self.variables.items()
                if namespace not in ("sys", "conv")
            }
        else:
            runtime_vars = {
                namespace: {
                    k: v.instance.get_value()
                    for k, v in vars_dict.items()
                }
                for namespace, vars_dict in self.variables.items()
                if namespace not in ("sys", "conv")
            }
        return runtime_vars

    def get_node_output(self, node_id: str, default: Any = None, strict: bool = True) -> dict[str, Any] | None:
        """获取指定节点的输出（运行时变量）
        
        Args:
            node_id: 节点 ID
            default: 默认值
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
            return default

    def copy(self, pool: 'VariablePool'):
        self.variables = deepcopy(pool.variables)

    def is_file_variable(self, selector):
        variable_struct = self.get_instance(selector, default=None, strict=False)
        if variable_struct is None:
            return False
        if isinstance(variable_struct, FileVariable):
            return True
        elif isinstance(variable_struct, ArrayVariable) and variable_struct.child_type == FileVariable:
            return True
        return False

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


class VariablePoolInitializer:
    def __init__(self, workflow_config: dict):
        self.workflow_config = workflow_config

    async def initialize(
            self,
            variable_pool: VariablePool,
            input_data: dict,
            execution_context: ExecutionContext
    ) -> None:
        await self._init_conversation_vars(variable_pool, input_data)
        await self._init_system_vars(variable_pool, input_data, execution_context)

    async def _init_conversation_vars(
            self,
            variable_pool: VariablePool,
            input_data: dict
    ):
        init_conv_vars: list[dict] = self.workflow_config.get("variables") or []
        runtime_conv_vars: dict[str, Any] = input_data.get("conv", {})

        for var_def in init_conv_vars:
            var_name = var_def.get("name")
            var_default = runtime_conv_vars.get(var_name, var_def.get("default"))
            var_type = var_def.get("type")
            if var_name:
                if var_default:
                    var_value = var_default
                else:
                    var_value = DEFAULT_VALUE(var_type)
                # Convert FileInput-format dicts to full FileObject dicts
                if var_type == VariableType.FILE:
                    if not var_value:
                        continue
                    var_value = await self._resolve_file_default(var_value)
                    if not var_value:
                        continue
                elif var_type == VariableType.ARRAY_FILE:
                    if not var_value:
                        var_value = []
                    else:
                        resolved = []
                        for item in var_value:
                            f = await self._resolve_file_default(item)
                            if f:
                                resolved.append(f)
                        var_value = resolved
                await variable_pool.new(
                    namespace="conv",
                    key=var_name,
                    value=var_value,
                    var_type=var_type,
                    mut=True
                )

    @staticmethod
    async def _resolve_file_default(file_def: dict) -> dict | None:
        """Accept only already-resolved FileObject dicts (is_file=True).
        FileInput-format dicts are converted at save time by WorkflowService._resolve_variables_file_defaults.
        """
        if not isinstance(file_def, dict):
            return None
        if file_def.get("is_file"):
            return file_def
        return None

    @staticmethod
    async def _init_system_vars(
            variable_pool: VariablePool,
            input_data: dict,
            context: ExecutionContext
    ):
        user_message = input_data.get("message") or ""
        user_files = input_data.get("files") or []
        conversations = input_data.get("conv_messages", [])
        conversation_index = len(conversations) // 2

        input_variables = input_data.get("variables") or {}
        sys_vars = {
            "message": (user_message, VariableType.STRING),
            "conversation_index": (conversation_index, VariableType.NUMBER),
            "conversation_id": (input_data.get("conversation_id"), VariableType.STRING),
            "execution_id": (context.execution_id, VariableType.STRING),
            "workspace_id": (context.workspace_id, VariableType.STRING),
            "user_id": (context.user_id, VariableType.STRING),
            "input_variables": (input_variables, VariableType.OBJECT),
            "files": (user_files, VariableType.ARRAY_FILE)
        }
        for key, var_def in sys_vars.items():
            value = var_def[0]
            var_type = var_def[1]
            await variable_pool.new(
                namespace='sys',
                key=key,
                value=value,
                var_type=var_type,
                mut=False
            )
