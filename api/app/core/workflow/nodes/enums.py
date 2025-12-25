from enum import StrEnum

from app.core.workflow.nodes.operators import (
    StringOperator,
    NumberOperator,
    AssignmentOperatorType,
    BooleanOperator,
    ArrayOperator,
    ObjectOperator
)


class NodeType(StrEnum):
    START = "start"
    END = "end"
    ANSWER = "answer"
    LLM = "llm"
    KNOWLEDGE_RETRIEVAL = "knowledge-retrieval"
    IF_ELSE = "if-else"
    CODE = "code"
    TRANSFORM = "transform"
    QUESTION_CLASSIFIER = "question-classifier"
    HTTP_REQUEST = "http-request"
    TOOL = "tool"
    AGENT = "agent"
    ASSIGNER = "assigner"
    JINJARENDER = "jinja-render"


class ComparisonOperator(StrEnum):
    EMPTY = "empty"
    NOT_EMPTY = "not_empty"
    CONTAINS = "contains"
    NOT_CONTAINS = "not_contains"
    START_WITH = "startwith"
    END_WITH = "endwith"
    EQ = "eq"
    NE = "ne"
    LT = "lt"
    LE = "le"
    GT = "gt"
    GE = "ge"


class LogicOperator(StrEnum):
    AND = "and"
    OR = "or"


class AssignmentOperator(StrEnum):
    ASSIGN = "assign"
    CLEAR = "clear"

    ADD = "add"  # +=
    SUBTRACT = "subtract"  # -=
    MULTIPLY = "multiply"  # *=
    DIVIDE = "divide"  # /=

    APPEND = "append"
    REMOVE_LAST = "remove_last"
    REMOVE_FIRST = "remove_first"

    @classmethod
    def get_operator(cls, obj) -> AssignmentOperatorType:
        if isinstance(obj, str):
            return StringOperator
        elif isinstance(obj, bool):
            return BooleanOperator
        elif isinstance(obj, (int, float)):
            return NumberOperator
        elif isinstance(obj, list):
            return ArrayOperator
        elif isinstance(obj, dict):
            return ObjectOperator

        raise TypeError(f"Unsupported variable type ({type(obj)})")


class HttpRequestMethod(StrEnum):
    GET = "GET"
    POST = "POST"
    HEAD = "HEAD"
    PUT = "PUT"
    PATCH = "PATCH"
    DELETE = "DELETE"


class HttpAuthType(StrEnum):
    NONE = "none"
    BASIC = "basic"
    BEARER = "bearer"
    CUSTOM = "custom"


class HttpContentType(StrEnum):
    NONE = "none"
    FROM_DATA = "form-data"
    WWW_FORM = "x-www-form-urlencoded"
    JSON = "json"
    RAW = "raw"
    BINARY = "binary"


class HttpErrorHandle(StrEnum):
    NONE = "none"
    DEFAULT = "default"
    BRANCH = "branch"
