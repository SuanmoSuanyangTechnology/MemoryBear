"""TODO"""
from pydantic import BaseModel


class NodeData(BaseModel):
    source: str
    target: str
