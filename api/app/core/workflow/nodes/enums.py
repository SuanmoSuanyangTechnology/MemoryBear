from enum import StrEnum
from typing import Union

from app.core.workflow.nodes.base_node import BaseNode
from app.core.workflow.nodes.if_else import IfElseNode
from app.core.workflow.nodes.llm import LLMNode
from app.core.workflow.nodes.agent import AgentNode
from app.core.workflow.nodes.transform import TransformNode
from app.core.workflow.nodes.start import StartNode
from app.core.workflow.nodes.end import EndNode


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


WorkflowNode = Union[
    BaseNode,
    StartNode,
    EndNode,
    LLMNode,
    IfElseNode,
    AgentNode,
    TransformNode,
]
