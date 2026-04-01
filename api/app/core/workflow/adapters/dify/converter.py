# -*- coding: UTF-8 -*-
# Author: Eternity
# @Email: 1533512157@qq.com
# @Time : 2026/2/25 18:21
import base64
import re
from typing import Any
from urllib.parse import quote

from app.core.workflow.adapters.base_converter import BaseConverter
from app.core.workflow.adapters.errors import (
    UnsupportedVariableType,
    UnknownModelWarning,
    ExceptionDefinition,
    ExceptionType
)
from app.core.workflow.nodes.assigner.config import AssignmentItem
from app.core.workflow.nodes.base_config import VariableDefinition, BaseNodeConfig
from app.core.workflow.nodes.code.config import InputVariable, OutputVariable
from app.core.workflow.nodes.configs import (
    StartNodeConfig,
    LLMNodeConfig,
    AssignerNodeConfig,
    CodeNodeConfig,
    LoopNodeConfig,
    IterationNodeConfig,
    EndNodeConfig,
    HttpRequestNodeConfig,
    IfElseNodeConfig,
    JinjaRenderNodeConfig,
    KnowledgeRetrievalNodeConfig,
    NoteNodeConfig,
    ParameterExtractorNodeConfig,
    QuestionClassifierNodeConfig,
    VariableAggregatorNodeConfig
)
from app.core.workflow.nodes.cycle_graph.config import (
    ConditionDetail as LoopConditionDetail,
    ConditionsConfig,
    CycleVariable
)
from app.core.workflow.nodes.enums import (
    ValueInputType,
    ComparisonOperator,
    AssignmentOperator,
    HttpAuthType,
    HttpContentType,
    HttpErrorHandle,
    NodeType
)
from app.core.workflow.nodes.http_request.config import (
    HttpAuthConfig,
    HttpContentTypeConfig,
    HttpFormData,
    HttpTimeOutConfig,
    HttpRetryConfig,
    HttpErrorDefaultTemplate,
    HttpErrorHandleConfig
)
from app.core.workflow.nodes.if_else.config import ConditionDetail, ConditionBranchConfig
from app.core.workflow.nodes.jinja_render.config import VariablesMappingConfig
from app.core.workflow.nodes.llm.config import MemoryWindowSetting, MessageConfig
from app.core.workflow.nodes.parameter_extractor.config import ParamsConfig
from app.core.workflow.nodes.question_classifier.config import ClassifierConfig
from app.core.workflow.variable.base_variable import VariableType, DEFAULT_VALUE


class DifyConverter(BaseConverter):
    errors: list
    warnings: list
    branch_node_cache: dict
    error_branch_node_cache: list
    node_output_map: dict

    def __init__(self):
        self.CONFIG_CONVERT_MAP = {
            NodeType.START: self.convert_start_node_config,
            NodeType.LLM: self.convert_llm_node_config,
            NodeType.END: self.convert_end_node_config,
            NodeType.IF_ELSE: self.convert_if_else_node_config,
            NodeType.LOOP: self.convert_loop_node_config,
            NodeType.ITERATION: self.convert_iteration_node_config,
            NodeType.ASSIGNER: self.convert_assigner_node_config,
            NodeType.CODE: self.convert_code_node_config,
            NodeType.HTTP_REQUEST: self.convert_http_node_config,
            NodeType.JINJARENDER: self.convert_jinja_render_node_config,
            NodeType.KNOWLEDGE_RETRIEVAL: self.convert_knowledge_node_config,
            NodeType.PARAMETER_EXTRACTOR: self.convert_parameter_extractor_node_config,
            NodeType.QUESTION_CLASSIFIER: self.convert_question_classifier_node_config,
            NodeType.VAR_AGGREGATOR: self.convert_variable_aggregator_node_config,
            NodeType.TOOL: self.convert_tool_node_config,
            NodeType.NOTES: self.convert_notes_config,
            NodeType.CYCLE_START: lambda x: {},
            NodeType.BREAK: lambda x: {},
        }

    def get_node_convert(self, node_type):
        func = self.CONFIG_CONVERT_MAP.get(node_type, lambda x: {})
        return func

    def config_validate(
            self,
            node_id: str,
            node_name: str,
            config: type[BaseNodeConfig],
            value: dict
    ):
        try:
            return config.model_validate(value)
        except Exception as e:
            self.errors.append(ExceptionDefinition(
                type=ExceptionType.CONFIG,
                node_id=node_id,
                node_name=node_name,
                detail=str(e)
            ))
            return None

    @staticmethod
    def is_variable(expression) -> bool:
        return bool(re.match(r"\{\{#(.*?)#}}", expression))

    def process_var_selector(self, var_selector):
        if not var_selector:
            return ""
        selector = var_selector.split('.')
        if len(selector) not in [2, 3] and var_selector != "context":
            raise Exception(f"invalid variable selector: {var_selector}")
        if len(selector) == 3:
            selector = selector[1:]
        if selector[0] == "conversation":
            selector[0] = "conv"
        var_selector = ".".join(selector)
        mapping = {
                      "sys.query": "sys.message"
                  } | self.node_output_map

        var_selector = mapping.get(var_selector, var_selector)
        return var_selector

    def _process_list_variable_literal(self, variable_selector: list) -> str | None:
        if not self.process_var_selector(".".join(variable_selector)):
            return None
        return "{{" + self.process_var_selector(".".join(variable_selector)) + "}}"

    def trans_variable_format(self, content):
        pattern = re.compile(r"\{\{#(.*?)#}}")

        def replacer(match: re.Match) -> str:
            raw_name = match.group(1)
            new_name = self.process_var_selector(raw_name)
            return f"{{{{{new_name}}}}}"

        return pattern.sub(replacer, content)

    @staticmethod
    def _convert_file(var):
        return None

    @staticmethod
    def _convert_array_file(var):
        return []

    @staticmethod
    def variable_type_map(source_type) -> VariableType | None:
        type_map = {
            "file": VariableType.FILE,
            "paragraph": VariableType.STRING,
            "text-input": VariableType.STRING,
            "number": VariableType.NUMBER,
            "checkbox": VariableType.BOOLEAN,
            "file-list": VariableType.ARRAY_FILE,
            "select": VariableType.STRING,
            "integer": VariableType.NUMBER,
            "float": VariableType.NUMBER,
        }
        var_type = type_map.get(source_type, source_type)
        return var_type

    def convert_variable_type(self, target_type: VariableType, origin_value: Any):
        if not origin_value:
            return DEFAULT_VALUE(target_type)
        try:
            match target_type:
                case VariableType.STRING:
                    return self._convert_string(origin_value)
                case VariableType.NUMBER:
                    return self._convert_number(origin_value)
                case VariableType.BOOLEAN:
                    return self._convert_boolean(origin_value)
                case VariableType.FILE:
                    return self._convert_file(origin_value)
                case VariableType.ARRAY_FILE:
                    return self._convert_array_file(origin_value)
                case _:
                    return origin_value
        except:
            raise Exception(f"convert variable failed: {target_type}")

    @staticmethod
    def convert_compare_operator(operator):
        operator_map = {
            "is": ComparisonOperator.EQ,
            "is not": ComparisonOperator.NE,
            "=": ComparisonOperator.EQ,
            "≠": ComparisonOperator.NE,
            ">": ComparisonOperator.GT,
            "<": ComparisonOperator.LT,
            "≥": ComparisonOperator.GE,
            "≤": ComparisonOperator.LE,
            "not empty": ComparisonOperator.NOT_EMPTY,
            "start with": ComparisonOperator.START_WITH,
            "end with": ComparisonOperator.END_WITH,
            "not contains": ComparisonOperator.NOT_CONTAINS,
            "exists": ComparisonOperator.NOT_EMPTY,
            "not exists": ComparisonOperator.EMPTY
        }
        return operator_map.get(operator, operator)

    @staticmethod
    def convert_assignment_operator(operator):
        operator_map = {
            "+=": AssignmentOperator.ADD,
            "-=": AssignmentOperator.SUBTRACT,
            "*=": AssignmentOperator.MULTIPLY,
            "/=": AssignmentOperator.DIVIDE,
            "over-write": AssignmentOperator.COVER,
            "remove-last": AssignmentOperator.REMOVE_LAST,
            "remove-first": AssignmentOperator.REMOVE_FIRST,
            "set": AssignmentOperator.ASSIGN,
        }
        return operator_map.get(operator, operator)

    @staticmethod
    def convert_http_auth_type(auth_type):
        auth_type_map = {
            "no-auth": HttpAuthType.NONE,
            "bearer": HttpAuthType.BEARER,
            "basic": HttpAuthType.BASIC,
            "custom": HttpAuthType.CUSTOM,
        }
        return auth_type_map.get(auth_type, auth_type)

    @staticmethod
    def convert_http_content_type(content_type):
        content_type_map = {
            "none": HttpContentType.NONE,
            "form-data": HttpContentType.FROM_DATA,
            "x-www-form-urlencoded": HttpContentType.WWW_FORM,
            "json": HttpContentType.JSON,
            "raw-text": HttpContentType.RAW,
            "binary": HttpContentType.BINARY,
        }
        return content_type_map.get(content_type, content_type)

    @staticmethod
    def convert_http_error_handle_type(handle_type):
        handle_type_map = {
            "none": HttpErrorHandle.NONE,
            "fail-branch": HttpErrorHandle.BRANCH,
            "default-value": HttpErrorHandle.DEFAULT,
        }
        return handle_type_map.get(handle_type, handle_type)

    def convert_start_node_config(self, node: dict) -> dict:
        node_data = node["data"]
        start_vars = []
        for var in node_data["variables"]:
            var_type = self.variable_type_map(var["type"])
            if not var_type:
                self.errors.append(
                    UnsupportedVariableType(
                        scope=node["id"],
                        name=var["variable"],
                        var_type=var["type"],
                        node_id=node["id"],
                        node_name=node_data["title"]
                    )
                )
                continue

            if var_type in ["file", "array[file]"]:
                self.errors.append(
                    ExceptionDefinition(
                        type=ExceptionType.VARIABLE,
                        node_id=node["id"],
                        node_name=node_data["title"],
                        name=var["variable"],
                        detail=f"Unsupported Variable type for start node: {var_type}"
                    )
                )
                continue

            var_def = VariableDefinition(
                name=var["variable"],
                type=var_type,
                required=var["required"],
                default=self.convert_variable_type(
                    var_type, var.get("default")
                ),
                description=var["label"],
                max_length=var.get("max_length", 50),
            )
            start_vars.append(var_def)
        result = StartNodeConfig.model_construct(
            variables=start_vars
        ).model_dump()
        self.config_validate(node["id"], node["data"]["title"], StartNodeConfig, result)
        return result

    def convert_question_classifier_node_config(self, node: dict) -> dict:
        node_data = node["data"]
        self.warnings.append(
            UnknownModelWarning(
                node_id=node["id"],
                node_name=node_data["title"],
                model_name=node_data["model"].get("name")
            )
        )
        categories = []
        for category in node_data["classes"]:
            self.branch_node_cache[node["id"]].append(category["id"])
            categories.append(
                ClassifierConfig.model_construct(
                    class_name=category["name"],
                )
            )

        result = QuestionClassifierNodeConfig.model_construct(
            input_variable=self._process_list_variable_literal(node_data.get("query_variable_selector")),
            user_supplement_prompt=self.trans_variable_format(node_data.get("instructions", "")),
            categories=categories,
        ).model_dump()
        self.config_validate(node["id"], node["data"]["title"], QuestionClassifierNodeConfig, result)
        return result

    def convert_llm_node_config(self, node: dict) -> dict:
        node_data = node["data"]
        self.warnings.append(
            UnknownModelWarning(
                node_id=node["id"],
                node_name=node_data["title"],
                model_name=node_data["model"].get("name")
            )
        )
        context = self._process_list_variable_literal(node_data["context"]["variable_selector"])
        memory = MemoryWindowSetting(
            enable=bool(node_data.get("memory")),
            enable_window=bool(node_data.get("memory", {}).get("window", {}).get("enabled", False)),
            window_size=int(node_data.get("memory", {}).get("window", {}).get("size", 20))
        )
        messages = []
        for message in node_data["prompt_template"]:
            messages.append(
                MessageConfig(
                    role=message["role"],
                    content=self.trans_variable_format(message["text"])
                )
            )
        if memory.enable:
            messages.append(
                MessageConfig(
                    role="user",
                    content=self.trans_variable_format(
                        node_data["memory"].get("query_prompt_template") or "{{#sys.query#}}"
                    )
                )
            )
        vision = node_data["vision"]["enabled"]
        vision_input = self._process_list_variable_literal(
            node_data["vision"]["configs"]["variable_selector"]
        ) if vision else None
        result = LLMNodeConfig.model_construct(
            model_id=None,
            context=context,
            memory=memory,
            vision=vision,
            vision_input=vision_input,
            messages=messages
        ).model_dump()
        self.config_validate(node["id"], node["data"]["title"], LLMNodeConfig, result)
        return result

    def convert_end_node_config(self, node: dict) -> dict:
        node_data = node["data"]
        result = EndNodeConfig.model_construct(
            output=self.trans_variable_format(node_data.get("answer", "")),
        ).model_dump()
        self.config_validate(node["id"], node["data"]["title"], EndNodeConfig, result)
        return result

    def convert_if_else_node_config(self, node: dict) -> dict:
        node_data = node["data"]
        cases = []
        for case in node_data["cases"]:
            case_id = case.get("id") or case.get("case_id")
            logical_operator = case["logical_operator"]
            conditions = []
            for condition in case["conditions"]:
                right_value = condition["value"]
                condition_detail = ConditionDetail(
                    operator=self.convert_compare_operator(condition["comparison_operator"]),
                    left="{{" + self.process_var_selector(".".join(condition["variable_selector"])) + "}}",
                    right=self.trans_variable_format(
                        right_value
                    ) if isinstance(right_value, str) and self.is_variable(right_value) else self.convert_variable_type(
                        self.variable_type_map(condition["varType"]),
                        condition["value"]
                    ),
                    input_type=ValueInputType.VARIABLE
                    if isinstance(right_value, str) and self.is_variable(right_value) else ValueInputType.CONSTANT,
                )
                conditions.append(condition_detail)
            cases.append(
                ConditionBranchConfig(
                    logical_operator=logical_operator,
                    expressions=conditions
                )
            )
            self.branch_node_cache[node["id"]].append(case_id)
        result = IfElseNodeConfig.model_construct(
            cases=cases
        ).model_dump()
        self.config_validate(node["id"], node["data"]["title"], IfElseNodeConfig, result)
        return result

    def convert_loop_node_config(self, node: dict) -> dict:
        node_data = node["data"]
        logical_operator = node_data["logical_operator"]
        conditions = []
        for condition in node_data["break_conditions"]:
            right_value = condition["value"]
            conditions.append(
                LoopConditionDetail.model_construct(
                    operator=self.convert_compare_operator(condition["comparison_operator"]),
                    left=self._process_list_variable_literal(condition["variable_selector"]),
                    right=self.trans_variable_format(
                        right_value
                    ) if isinstance(right_value, str) and self.is_variable(right_value) else self.convert_variable_type(
                        self.variable_type_map(condition["varType"]),
                        condition["value"]
                    ),
                    input_type=ValueInputType.VARIABLE
                    if isinstance(right_value, str) and self.is_variable(right_value) else ValueInputType.CONSTANT,
                )
            )
        condition_config = ConditionsConfig.model_construct(
            logical_operator=logical_operator,
            expressions=conditions
        )
        loop_variables = []
        for variable in node_data["loop_variables"]:
            right_input_type = variable["value_type"]
            right_value_type = self.variable_type_map(variable["var_type"])
            if right_input_type == ValueInputType.VARIABLE:
                right_value = self._process_list_variable_literal(variable.get("value", ""))
            else:
                right_value = self.convert_variable_type(right_value_type, variable.get("value", ""))
            loop_variables.append(
                CycleVariable(
                    name=variable["label"],
                    type=right_value_type,
                    value=right_value,
                    input_type=right_input_type
                )
            )
        result = LoopNodeConfig.model_construct(
            condition=condition_config,
            cycle_vars=loop_variables,
            max_loop=node_data.get("loop_count", 10)
        ).model_dump()
        self.config_validate(node["id"], node["data"]["title"], LoopNodeConfig, result)
        return result

    def convert_iteration_node_config(self, node: dict) -> dict:
        node_data = node["data"]
        result = IterationNodeConfig.model_construct(
            input=self._process_list_variable_literal(node_data["iterator_selector"]),
            parallel=node_data["is_parallel"],
            parallel_count=node_data["parallel_nums"],
            output=self._process_list_variable_literal(node_data["output_selector"]),
            output_type=self.variable_type_map(node_data.get("output_type")),
            flatten=node_data["flatten_output"],
        ).model_dump()

        self.config_validate(node["id"], node["data"]["title"], IterationNodeConfig, result)
        return result

    def convert_assigner_node_config(self, node: dict) -> dict:
        node_data = node["data"]
        assignments = []
        for assignment in node_data["items"]:
            if assignment.get("operation") is None or assignment.get("value") is None:
                continue
            assignments.append(
                AssignmentItem(
                    variable_selector=self._process_list_variable_literal(assignment["variable_selector"]),
                    value=self._process_list_variable_literal(
                        assignment["value"]
                    ) if assignment["input_type"] == ValueInputType.VARIABLE else assignment["value"],
                    operation=self.convert_assignment_operator(assignment["operation"])
                )
            )
        result = AssignerNodeConfig.model_construct(
            assignments=assignments
        ).model_dump()
        self.config_validate(node["id"], node["data"]["title"], AssignerNodeConfig, result)
        return result

    def convert_code_node_config(self, node: dict) -> dict:
        node_data = node["data"]
        input_variables = []
        for input_variable in node_data["variables"]:
            input_variables.append(
                InputVariable.model_construct(
                    name=input_variable["variable"],
                    variable=self._process_list_variable_literal(input_variable["value_selector"]),
                )
            )

        output_variables = []
        for output_variable in node_data["outputs"]:
            output_variables.append(
                OutputVariable.model_construct(
                    name=output_variable,
                    type=node_data["outputs"][output_variable]["type"],
                )
            )

        code = base64.b64encode(quote(node_data["code"]).encode("utf-8")).decode("utf-8")

        result = CodeNodeConfig.model_construct(
            input_variables=input_variables,
            language=node_data["code_language"],
            output_variables=output_variables,
            code=code
        ).model_dump()
        self.config_validate(node["id"], node["data"]["title"], CodeNodeConfig, result)
        return result

    def convert_http_node_config(self, node: dict) -> dict:
        node_data = node["data"]
        if node_data["authorization"]["type"] != 'no-auth':
            auth_type = self.convert_http_auth_type(node_data["authorization"]["config"]["type"])
            auth_config = HttpAuthConfig.model_construct(
                auth_type=auth_type,
                header=node_data["authorization"]["config"].get("header"),
                api_key=node_data["authorization"]["config"].get("api_key"),
            )
        else:
            auth_config = HttpAuthConfig()

        content_type = self.convert_http_content_type(node_data["body"]["type"])
        if content_type == HttpContentType.FROM_DATA:
            body_content = []
            for content in node_data["body"]["data"]:
                body_content.append(
                    HttpFormData(
                        key=self.trans_variable_format(content["key"]),
                        type=content["type"],
                        value=self.trans_variable_format(content["value"]),
                    )
                )
        elif content_type == HttpContentType.WWW_FORM:
            body_content = {}
            for content in node_data["body"]["data"]:
                body_content[
                    self.trans_variable_format(content["key"])
                ] = self.trans_variable_format(content["value"])
        else:
            if node_data["body"]["data"]:
                body_content = (node_data["body"]["data"][0].get("value") or
                                self._process_list_variable_literal(node_data["body"]["data"][0].get("file")))
            else:
                body_content = ""

        headers = {}
        for header in node_data.get("headers", "").split("\n"):
            if not header:
                continue

            key_value = header.split(":")
            if len(key_value) == 2:
                headers[
                    self.trans_variable_format(key_value[0])
                ] = self.trans_variable_format(key_value[1])
            else:
                self.warnings.append(ExceptionDefinition(
                    type=ExceptionType.CONFIG,
                    node_id=node["id"],
                    node_name=node_data["title"],
                    detail=f"Invalid header/param - {header}",
                ))

        params = {}
        for param in node_data.get("params", "").split("\n"):
            if not param:
                continue

            key_value = param.split(":")
            if len(key_value) == 2:
                params[
                    self.trans_variable_format(key_value[0])
                ] = self.trans_variable_format(key_value[1])
            else:
                self.warnings.append(ExceptionDefinition(
                    type=ExceptionType.CONFIG,
                    node_id=node["id"],
                    node_name=node_data["title"],
                    detail=f"Invalid header/param - {param}",
                ))

        error_handle_type = self.convert_http_error_handle_type(
            node_data.get("error_strategy", "none")
        )
        default_value = None
        if error_handle_type == HttpErrorHandle.DEFAULT:
            default_body = ""
            default_header = {}
            default_status_code = 0
            for var in node_data.get("default_value") or []:
                if var["key"] == "body":
                    default_body = var["value"]
                elif var["key"] == "header":
                    default_header = var["value"]
                elif var["key"] == "status_code":
                    default_status_code = var["value"]
            default_value = HttpErrorDefaultTemplate(
                body=default_body,
                headers=default_header,
                status_code=default_status_code,
            )

        self.error_branch_node_cache.append(node['id'])
        result = HttpRequestNodeConfig.model_construct(
            method=node_data["method"].upper(),
            url=node_data["url"],
            auth=auth_config,
            body=HttpContentTypeConfig.model_construct(
                content_type=self.convert_http_content_type(node_data["body"]["type"]),
                data=body_content,
            ),
            headers=headers,
            params=params,
            verify_ssl=node_data.get("ssl_verify", False),
            timeouts=HttpTimeOutConfig.model_construct(
                connect_timeout=node_data["timeout"]["max_connect_timeout"] or 5,
                read_timeout=node_data["timeout"]["max_read_timeout"] or 5,
                write_timeout=node_data["timeout"]["max_write_timeout"] or 5,
            ),
            retry=HttpRetryConfig.model_construct(
                enable=node_data["retry_config"]["retry_enabled"],
                max_attempts=node_data["retry_config"]["max_retries"],
                retry_interval=node_data["retry_config"]["retry_interval"],
            ),
            error_handle=HttpErrorHandleConfig.model_construct(
                method=error_handle_type,
                default=default_value,
            )
        ).model_dump()

        self.config_validate(node["id"], node["data"]["title"], HttpRequestNodeConfig, result)
        return result

    def convert_jinja_render_node_config(self, node: dict) -> dict:
        node_data = node["data"]
        mapping = []
        for variable in node_data["variables"]:
            mapping.append(VariablesMappingConfig.model_construct(
                name=variable["variable"],
                value=self._process_list_variable_literal(variable["value_selector"])
            ))
        result = JinjaRenderNodeConfig.model_construct(
            template=node_data["template"],
            mapping=mapping,
        ).model_dump()
        self.config_validate(node["id"], node["data"]["title"], JinjaRenderNodeConfig, result)
        return result

    def convert_knowledge_node_config(self, node: dict) -> dict:
        node_data = node["data"]
        self.warnings.append(ExceptionDefinition(
            node_id=node["id"],
            node_name=node_data["title"],
            type=ExceptionType.CONFIG,
            detail=f"Please reconfigure the Knowledge Retrieval node.",
        ))
        result = KnowledgeRetrievalNodeConfig.model_construct(
            query=self._process_list_variable_literal(node_data["query_variable_selector"]),
        ).model_dump()

        self.config_validate(node["id"], node["data"]["title"], KnowledgeRetrievalNodeConfig, result)
        return result

    def convert_parameter_extractor_node_config(self, node: dict) -> dict:
        node_data = node["data"]
        self.warnings.append(
            UnknownModelWarning(
                node_id=node["id"],
                node_name=node_data["title"],
                model_name=node_data["model"].get("name")
            )
        )
        params = []
        for param in node_data.get("parameters", []):
            params.append(
                ParamsConfig.model_construct(
                    name=param["name"],
                    desc=param["description"],
                    required=param["required"],
                    type=param["type"],
                )
            )
        result = ParameterExtractorNodeConfig.model_construct(
            text=self._process_list_variable_literal(node_data["query"]),
            params=params,
            prompt=node_data.get("instruction")
        ).model_dump()

        self.config_validate(node["id"], node["data"]["title"], ParameterExtractorNodeConfig, result)
        return result

    def convert_variable_aggregator_node_config(self, node: dict) -> dict:
        node_data = node["data"]
        advanced_settings = node_data.get("advanced_settings", {})
        group_variables = {}
        group_type = {}
        if not advanced_settings or not advanced_settings["group_enabled"]:
            group_variables = [
                self._process_list_variable_literal(variable)
                for variable in node_data["variables"]
            ]
            group_type["output"] = node_data["output_type"]
        else:
            for group in advanced_settings["groups"]:
                group_variables[group["group_name"]] = [
                    self._process_list_variable_literal(variable)
                    for variable in group["variables"]
                ]
                group_type[group["group_name"]] = group["output_type"]

        result = VariableAggregatorNodeConfig.model_construct(
            group=advanced_settings.get("group_enabled", False),
            group_variables=group_variables,
            group_type=group_type,
        ).model_dump()

        self.config_validate(node["id"], node["data"]["title"], VariableAggregatorNodeConfig, result)

        return result

    def convert_tool_node_config(self, node: dict) -> dict:
        node_data = node["data"]
        self.warnings.append(ExceptionDefinition(
            node_id=node["id"],
            node_name=node_data["title"],
            type=ExceptionType.CONFIG,
            detail=f"Please reconfigure the tool node.",
        ))
        return {}

    @staticmethod
    def convert_notes_config(node: dict):
        node_data = node["data"]
        result = NoteNodeConfig.model_construct(
            author=node_data.get("author", ""),
            text=node_data.get("text", ""),
            width=node_data.get("width", 80),
            height=node_data.get("height", 80),
            theme=node_data.get("theme", "blue"),
            show_author=node_data.get("showAuthor", True)
        ).model_dump()
        return result
