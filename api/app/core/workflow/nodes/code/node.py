import base64
import json
import logging
import re
from string import Template
from textwrap import dedent
from typing import Any

import httpx

from app.core.workflow.nodes import BaseNode, WorkflowState
from app.core.workflow.nodes.base_config import VariableType
from app.core.workflow.nodes.code.config import CodeNodeConfig

logger = logging.getLogger(__name__)

SCRIPT_TEMPLATE = Template(dedent("""
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


class CodeNode(BaseNode):
    def __init__(self, node_config: dict[str, Any], workflow_config: dict[str, Any]):
        super().__init__(node_config, workflow_config)
        self.typed_config: CodeNodeConfig | None = None

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

    async def execute(self, state: WorkflowState) -> Any:
        self.typed_config = CodeNodeConfig(**self.config)
        input_variable_dict = {}
        for input_variable in self.typed_config.input_variables:
            input_variable_dict[input_variable.name] = self.get_variable(input_variable.variable, state)
        code = base64.b64decode(
            self.typed_config.code
        ).decode("utf-8")

        input_variable_dict = base64.b64encode(
            json.dumps(input_variable_dict).encode("utf-8")
        ).decode("utf-8")

        final_script = SCRIPT_TEMPLATE.substitute(
            code=code,
            inputs_variable=input_variable_dict,
        )

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
