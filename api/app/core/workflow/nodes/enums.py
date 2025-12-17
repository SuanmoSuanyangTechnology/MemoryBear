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
