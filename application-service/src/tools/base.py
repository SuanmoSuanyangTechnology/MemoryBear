"""Base tool interface definitions."""
import re
import time
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

from ..models.tool_model import ToolType, ToolStatus
from ..schemas.tool_schema import ToolParameter, ParameterType, ToolResult


class BaseTool(ABC):
    """Base abstract class for all tools."""

    def __init__(self, tool_id: str, config: Dict[str, Any]):
        """Initialize the tool

        Args:
            tool_id: Tool ID
            config: Tool configuration
        """
        self.tool_id = tool_id
        self.config = config
        self._status = ToolStatus.AVAILABLE
        self._runtime_context: Dict[str, Any] = {}

    @property
    @abstractmethod
    def name(self) -> str:
        """Tool name."""
        pass

    @property
    @abstractmethod
    def description(self) -> str:
        """Tool description."""
        pass

    @property
    @abstractmethod
    def tool_type(self) -> ToolType:
        """Tool type."""
        pass

    @property
    def version(self) -> str:
        """Tool version."""
        return self.config.get("version", "1.0.0")

    @property
    def status(self) -> ToolStatus:
        """Tool status."""
        return self._status

    @status.setter
    def status(self, value: ToolStatus):
        """Set the tool status."""
        self._status = value

    @property
    @abstractmethod
    def parameters(self) -> List[ToolParameter]:
        """Tool parameter definitions."""
        pass

    @property
    def tags(self) -> List[str]:
        """Tool tags."""
        return self.config.get("tags", [])

    def set_runtime_context(self, **context: Any):
        """Set the runtime context used to pass non-business parameters."""
        self._runtime_context.update({k: v for k, v in context.items() if v is not None})

    def get_runtime_context(self, key: Optional[str] = None, default: Any = None) -> Any:
        """Get the runtime context (all keys when ``key`` is None)."""
        if key is None:
            return dict(self._runtime_context)
        return self._runtime_context.get(key, default)

    def validate_parameters(self, parameters: Dict[str, Any]) -> Dict[str, str]:
        """Validate parameters.

        Args:
            parameters: Input parameters

        Returns:
            Error dict; empty means validation passed
        """
        errors = {}
        param_definitions = {p.name: p for p in self.parameters}

        # Required check. The workflow-node frontend submits blank strings ""
        # for unfilled fields instead of omitting the key, so an empty string
        # counts as "not filled" here too; otherwise the missing-required
        # report would be skipped and a misleading "invalid type" error raised
        # by the type check below instead.
        for param_def in self.parameters:
            if param_def.required and (
                param_def.name not in parameters or parameters.get(param_def.name) in (None, "")
            ):
                errors[param_def.name] = f"Required parameter '{param_def.name}' is missing"

        # Type and constraint checks
        for param_name, param_value in parameters.items():
            if param_name not in param_definitions:
                continue

            # Empty string means "not filled": required-missing is already
            # reported above, and optional parameters left blank should not be
            # type/constraint validated either (e.g. an optional integer as "").
            if param_value == "":
                continue

            param_def = param_definitions[param_name]

            # Type check
            if not self._validate_parameter_type(param_value, param_def):
                errors[param_name] = f"Parameter '{param_name}' has invalid type, expected {param_def.type}"

            # Constraint check
            constraint_error = self._validate_parameter_constraints(param_value, param_def)
            if constraint_error:
                errors[param_name] = constraint_error

        return errors

    def _validate_parameter_type(self, value: Any, param_def: ToolParameter) -> bool:
        """Validate a single parameter type."""
        if value is None:
            return not param_def.required

        # Numeric types: workflow template rendering / form input commonly pass
        # numbers as strings (e.g. "0"); accept anything convertible, the tool
        # itself does int()/float() as needed.
        if param_def.type in (ParameterType.INTEGER, ParameterType.NUMBER):
            if isinstance(value, bool):
                return False
            if isinstance(value, (int, float)):
                return True
            if isinstance(value, str):
                try:
                    float(value)
                    return True
                except ValueError:
                    return False
            return False

        type_mapping = {
            ParameterType.STRING: str,
            ParameterType.BOOLEAN: bool,
            ParameterType.ARRAY: list,
            ParameterType.OBJECT: dict,
            # file type: accepts a file variable (list/dict) or a URL string
            ParameterType.FILE: (str, list, dict),
        }

        expected_type = type_mapping.get(param_def.type)
        if expected_type:
            return isinstance(value, expected_type)

        return True

    def _validate_parameter_constraints(self, value: Any, param_def: ToolParameter) -> Optional[str]:
        """Validate a single parameter's constraints."""
        if value is None:
            return None

        # Enum check
        if param_def.enum and value not in param_def.enum:
            return f"Value must be one of {param_def.enum}"

        # Numeric range check
        if param_def.type in [ParameterType.INTEGER, ParameterType.NUMBER]:
            try:
                numeric_value = float(value)
            except (TypeError, ValueError):
                return f"Value must be a number, got {value!r}"
            if param_def.minimum is not None and numeric_value < param_def.minimum:
                return f"Value must be >= {param_def.minimum}"
            if param_def.maximum is not None and numeric_value > param_def.maximum:
                return f"Value must be <= {param_def.maximum}"

        # String pattern check
        if param_def.type == ParameterType.STRING and param_def.pattern:
            if not re.match(param_def.pattern, str(value)):
                return f"Value must match pattern: {param_def.pattern}"

        return None

    @abstractmethod
    async def execute(self, **kwargs) -> ToolResult:
        """Execute the tool.

        Args:
            **kwargs: Tool parameters

        Returns:
            Execution result
        """
        pass

    async def safe_execute(self, **kwargs) -> ToolResult:
        """Execute the tool with parameter validation and exception handling.

        Args:
            **kwargs: Tool parameters

        Returns:
            Execution result
        """
        start_time = time.time()

        try:
            # Parameter validation
            validation_errors = self.validate_parameters(kwargs)
            if validation_errors:
                execution_time = time.time() - start_time
                error_msg = "; ".join([f"{k}: {v}" for k, v in validation_errors.items()])
                return ToolResult.error_result(
                    error=f"Parameter validation failed: {error_msg}",
                    error_code="VALIDATION_ERROR",
                    execution_time=execution_time
                )

            # Execute the tool
            result = await self.execute(**kwargs)
            return result

        except Exception as e:
            execution_time = time.time() - start_time
            return ToolResult.error_result(
                error=str(e),
                error_code="EXECUTION_ERROR",
                execution_time=execution_time
            )

    def __repr__(self):
        return f"<{self.__class__.__name__}(id={self.tool_id}, name={self.name})>"
