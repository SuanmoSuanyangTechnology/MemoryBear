import base64
import json
import logging
import re
from string import Template
from textwrap import dedent
from typing import Any

import httpx

from app.core.workflow.nodes import BaseNode, WorkflowState
from app.core.workflow.variable.base_variable import VariableType
from app.core.workflow.nodes.code.config import CodeNodeConfig
from app.core.workflow.variable_pool import VariablePool

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
    def __init__(self, node_config: dict[str, Any], workflow_config: dict[str, Any]):
        super().__init__(node_config, workflow_config)
        self.typed_config: CodeNodeConfig | None = None

    def _output_types(self) -> dict[str, VariableType]:
        output_dict = {}
        for output in self.typed_config.output_variables:
            output_dict[output.name] = output.type
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
                    raise RuntimeError(f"Return value {output.name} does not exist")
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

        async with httpx.AsyncClient() as client:
            response = await client.post(
                "http://sandbox:8194/v1/sandbox/run",
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
        resp = response.json()

        match resp['code']:
            case 31:
                raise RuntimeError("Operation not permitted")
            case 0:
                return self.extract_result(resp["data"]["stdout"])
            case _:
                raise Exception(resp["message"])
