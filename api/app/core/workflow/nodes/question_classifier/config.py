import uuid
from typing import Optional

from pydantic import Field, BaseModel

from app.core.workflow.nodes.base_config import BaseNodeConfig

class ClassifierConfig(BaseModel):
    """分类器节点配置"""

    class_name: str = Field(..., description="分类类别名称")


class QuestionClassifierNodeConfig(BaseNodeConfig):
    """问题分类器节点配置"""
    
    model_id: uuid.UUID = Field(..., description="LLM模型ID")
    input_variable: str = Field(default="{{sys.message}}", description="输入变量选择器(用户问题)")
    user_supplement_prompt: Optional[str] = Field(default=None, description="用户补充提示词，额外分类指令")
    categories: list[ClassifierConfig] = Field(..., description="分类类别列表")
    system_prompt: str = Field(
        default="你是一个问题分类器，请根据用户问题选择最合适的分类。只返回分类名称，不要其他内容。",
        description="系统提示词"
    )
    user_prompt: str = Field(
        default="问题：{question}\n\n可选分类：{categories}\n\n补充指令：{supplement_prompt}\n\n请选择最合适的分类。",
        description="用户提示词模板"
    )
    output_variable: str = Field(default="class_name", description="输出分类结果的变量名")
