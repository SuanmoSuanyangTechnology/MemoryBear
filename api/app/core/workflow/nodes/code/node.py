import asyncio
import base64
import json
import logging
import re
import urllib.parse
from string import Template
from textwrap import dedent
from typing import Any

import httpx

from app.core.workflow.engine.state_manager import WorkflowState
from app.core.workflow.engine.variable_pool import VariablePool
from app.core.workflow.nodes import BaseNode
from app.core.workflow.nodes.code.config import CodeNodeConfig
from app.core.workflow.nodes.enums import HttpErrorHandle
from app.core.workflow.variable.base_variable import VariableType, DEFAULT_VALUE
from app.core.config import settings

logger = logging.getLogger(__name__)

PYTHON_SCRIPT_TEMPLATE = Template(dedent("""
$code

import json
from base64 import b64decode

# decode and prepare input dict
inputs_obj = json.loads(b64decode('$inputs_variable').decode('utf-8'))

# execute main function
output_obj = main(**inputs_obj)

# convert output to json and print
output_json = json.dumps(output_obj, indent=4)
result = "<<RESULT>>" + output_json + "<<RESULT>>"
print(result)
"""))

NODEJS_SCRIPT_TEMPLATE = Template(dedent("""
$code
// decode and prepare input object
var inputs_obj = JSON.parse(Buffer.from('$inputs_variable', 'base64').toString('utf-8'))

// execute main function
var output_obj = main(inputs_obj)

// convert output to json and print
var output_json = JSON.stringify(output_obj)
var result = `<<RESULT>>$${output_json}<<RESULT>>`
console.log(result)
"""))


class CodeNode(BaseNode):
    def __init__(self, node_config: dict[str, Any], workflow_config: dict[str, Any], down_stream_nodes: list[str]):
        super().__init__(node_config, workflow_config, down_stream_nodes)
        self.typed_config: CodeNodeConfig | None = None
        self._executed_code: str = ""
        self._language: str = ""

    def _extract_extra_fields(self, business_result: Any) -> dict:
        return {"process": {"language": self._language, "code": self._executed_code}}

    def _output_types(self) -> dict[str, VariableType]:
        output_dict = {}
        for output in self.typed_config.output_variables:
            output_dict[output.name] = output.type
        output_dict["branch_signal"] = VariableType.STRING
        return output_dict

    def extract_result(self, content: str):
        match = re.search(r'<<RESULT>>(.*?)<<RESULT>>', content, re.DOTALL)
        if match:
            extracted = match.group(1)
            exec_result = json.loads(extracted)
            result = {}
            for output in self.typed_config.output_variables:
                value = exec_result.get(output.name)
                if value is None:
                    result[output.name] = DEFAULT_VALUE(output.type)
                    continue
                match output.type:
                    case VariableType.STRING:
                        if not isinstance(value, str):
                            raise RuntimeError(f"Return value {output.name} should be a string")
                    case VariableType.BOOLEAN:
                        if not isinstance(value, bool):
                            raise RuntimeError(f"Return value {output.name} should be a boolean")
                    case VariableType.NUMBER:
                        if not isinstance(value, (int, float)):
                            raise RuntimeError(f"Return value {output.name} should be a number")
                    case VariableType.OBJECT:
                        if not isinstance(value, dict):
                            raise RuntimeError(f"Return value {output.name} should be a dictionary")
                    case VariableType.ARRAY_STRING:
                        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
                            raise RuntimeError(f"Return value {output.name} should be a list of strings")
                    case VariableType.ARRAY_NUMBER:
                        if not isinstance(value, list) or not all(isinstance(v, (int, float)) for v in value):
                            raise RuntimeError(f"Return value {output.name} should be a list of numbers")
                    case VariableType.ARRAY_OBJECT:
                        if not isinstance(value, list) or not all(isinstance(v, dict) for v in value):
                            raise RuntimeError(f"Return value {output.name} should be a list of dictionaries")
                    case VariableType.ARRAY_BOOLEAN:
                        if not isinstance(value, list) or not all(isinstance(v, bool) for v in value):
                            raise RuntimeError(f"Return value {output.name} should be a list of booleans")
                result[output.name] = value
            return result
        else:
            raise RuntimeError("The output of main must be a dictionary")

    async def execute(self, state: WorkflowState, variable_pool: VariablePool) -> Any:
        self.typed_config = CodeNodeConfig(**self.config)
        input_variable_dict = {}
        for input_variable in self.typed_config.input_variables:
            input_variable_dict[input_variable.name] = self.get_variable(input_variable.variable, variable_pool)

        code = base64.b64decode(
            self.typed_config.code
        ).decode("utf-8")
        code = urllib.parse.unquote(code, encoding='utf-8')
        self._executed_code = code
        self._language = self.typed_config.language

        input_variable_dict = base64.b64encode(
            json.dumps(input_variable_dict).encode("utf-8")
        ).decode("utf-8")
        if self.typed_config.language == "python3":
            final_script = PYTHON_SCRIPT_TEMPLATE.substitute(
                code=code,
                inputs_variable=input_variable_dict,
            )
        elif self.typed_config.language == 'javascript':
            final_script = NODEJS_SCRIPT_TEMPLATE.substitute(
                code=code,
                inputs_variable=input_variable_dict,
            )
        else:
            raise ValueError(f"Unsupported language: {self.typed_config.language}")

        max_attempts = self.typed_config.retry.max_attempts + 1 if self.typed_config.retry.enable else 1
        last_error = None

        for attempt in range(max_attempts):
            try:
                logger.info(f"节点 {self.node_id} 代码执行，尝试 {attempt + 1}/{max_attempts}")
                async with httpx.AsyncClient(timeout=60) as client:
                    response = await client.post(
                        f"{settings.SANDBOX_URL}/v1/sandbox/run",
                        headers={
                            "x-api-key": 'redbear-sandbox'
                        },
                        json={
                            "language": self.typed_config.language,
                            "code": base64.b64encode(final_script.encode("utf-8")).decode("utf-8"),
                            "options": {
                                "enable_network": True
                            }
                        }
                    )
                if response.status_code != 200:
                    raise RuntimeError(f"Sandbox HTTP error: status={response.status_code}, body={response.text[:200]}")

                resp = response.json()

                if 'code' not in resp:
                    raise RuntimeError(f"Sandbox returned unexpected response: {json.dumps(resp, ensure_ascii=False)[:200]}")

                match resp['code']:
                    case 31:
                        raise RuntimeError("Operation not permitted")
                    case 0:
                        result = self.extract_result(resp["data"]["stdout"])
                        result["branch_signal"] = "SUCCESS"
                        return result
                    case _:
                        raise RuntimeError(resp.get("message", f"Sandbox error code: {resp['code']}"))

            except Exception as e:
                last_error = e
                logger.error(f"节点 {self.node_id} 代码执行失败（尝试 {attempt + 1}/{max_attempts}）: {e}")

                if attempt < max_attempts - 1 and self.typed_config.retry.enable:
                    await asyncio.sleep(self.typed_config.retry.retry_interval / 1000)
                else:
                    break

        return self._handle_error(last_error)

    def _handle_error(self, error: Exception) -> dict:
        """根据 error_handle 配置决定异常处理行为"""
        if self.typed_config is None:
            raise error

        match self.typed_config.error_handle.method:
            case HttpErrorHandle.NONE:
                raise error
            case HttpErrorHandle.DEFAULT:
                logger.warning(f"节点 {self.node_id}: 代码执行失败，返回默认输出")
                default_output = self.typed_config.error_handle.output or ""
                result = {}
                for output_var in self.typed_config.output_variables:
                    result[output_var.name] = DEFAULT_VALUE(output_var.type)
                # Assign default output to the first matching variable
                if default_output and self.typed_config.output_variables:
                    target_var = next(
                        (v for v in self.typed_config.output_variables if v.name == "output"),
                        next((v for v in self.typed_config.output_variables), None)
                    )
                    if target_var:
                        if target_var.type == VariableType.NUMBER:
                            try:
                                result[target_var.name] = float(default_output)
                            except ValueError:
                                result[target_var.name] = DEFAULT_VALUE(VariableType.NUMBER)
                        elif target_var.type == VariableType.BOOLEAN:
                            result[target_var.name] = default_output.lower() in ("true", "1", "yes")
                        else:
                            result[target_var.name] = default_output
                result["branch_signal"] = "SUCCESS"
                return result
            case HttpErrorHandle.BRANCH:
                logger.warning(f"节点 {self.node_id}: 代码执行失败，切换到异常处理分支")
                result = {"branch_signal": "ERROR"}
                for output_var in self.typed_config.output_variables:
                    result[output_var.name] = DEFAULT_VALUE(output_var.type)
                return result

        raise error
