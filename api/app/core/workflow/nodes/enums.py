from enum import StrEnum


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
