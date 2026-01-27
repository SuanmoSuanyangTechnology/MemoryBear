"""本体提取API的请求和响应模型

本模块定义了本体提取系统的所有API请求和响应的Pydantic模型。

Classes:
    ExtractionRequest: 本体提取请求模型
    ExtractionResponse: 本体提取响应模型
    ExportRequest: OWL文件导出请求模型
    ExportResponse: OWL文件导出响应模型
    OntologyResultResponse: 本体提取结果响应模型(带毫秒时间戳)
"""

from typing import List, Optional
import datetime
from uuid import UUID

from pydantic import BaseModel, Field, field_serializer

from app.core.memory.models.ontology_models import OntologyClass


class ExtractionRequest(BaseModel):
    """本体提取请求模型
    
    用于POST /api/ontology/extract端点的请求体。
    
    Attributes:
        scenario: 场景描述文本,不能为空
        domain: 可选的领域提示(如Healthcare, Education等)
    
    Examples:
        >>> request = ExtractionRequest(
        ...     scenario="医院管理患者记录...",
        ...     domain="Healthcare"
        ... )
    """
    scenario: str = Field(..., description="场景描述文本", min_length=1)
    domain: Optional[str] = Field(None, description="可选的领域提示")


class ExtractionResponse(BaseModel):
    """本体提取响应模型
    
    用于POST /api/ontology/extract端点的响应体。
    
    Attributes:
        classes: 提取的本体类列表
        domain: 识别的领域
        namespace: 本体命名空间URI
        extracted_count: 提取的类数量
    
    Examples:
        >>> response = ExtractionResponse(
        ...     classes=[...],
        ...     domain="Healthcare",
        ...     namespace="http://example.org/ontology#",
        ...     extracted_count=7
        ... )
    """
    classes: List[OntologyClass] = Field(default_factory=list, description="提取的本体类列表")
    domain: str = Field(..., description="识别的领域")
    namespace: Optional[str] = Field(None, description="本体命名空间URI")
    extracted_count: int = Field(..., description="提取的类数量")


class ExportRequest(BaseModel):
    """OWL文件导出请求模型
    
    用于POST /api/ontology/export端点的请求体。
    
    Attributes:
        classes: 要导出的本体类列表
        format: 导出格式,可选值: rdfxml, turtle, ntriples
        namespace: 可选的命名空间URI
    
    Examples:
        >>> request = ExportRequest(
        ...     classes=[...],
        ...     format="rdfxml"
        ... )
    """
    classes: List[OntologyClass] = Field(..., description="要导出的本体类列表", min_length=1)
    format: str = Field("rdfxml", description="导出格式: rdfxml, turtle, ntriples")
    namespace: Optional[str] = Field(None, description="可选的命名空间URI")


class ExportResponse(BaseModel):
    """OWL文件导出响应模型
    
    用于POST /api/ontology/export端点的响应体。
    
    Attributes:
        owl_content: OWL文件内容
        format: 导出格式
        classes_count: 导出的类数量
    
    Examples:
        >>> response = ExportResponse(
        ...     owl_content="<?xml version='1.0'?>...",
        ...     format="rdfxml",
        ...     classes_count=7
        ... )
    """
    owl_content: str = Field(..., description="OWL文件内容")
    format: str = Field(..., description="导出格式")
    classes_count: int = Field(..., description="导出的类数量")


class OntologyResultResponse(BaseModel):
    """本体提取结果响应模型
    
    用于返回数据库中存储的提取结果,时间戳为毫秒级。
    
    Attributes:
        id: 结果ID (UUID)
        scenario: 场景描述文本
        domain: 领域
        namespace: 本体命名空间URI
        classes_json: 提取的本体类数据(JSON格式)
        extracted_count: 提取的类数量
        user_id: 用户ID
        created_at: 创建时间(毫秒时间戳)
    
    Examples:
        >>> response = OntologyResultResponse(
        ...     id=uuid.uuid4(),
        ...     scenario="医院管理患者记录...",
        ...     domain="Healthcare",
        ...     namespace="http://example.org/ontology#",
        ...     classes_json={"classes": [...]},
        ...     extracted_count=7,
        ...     user_id=123,
        ...     created_at=datetime.now()
        ... )
    """
    id: UUID = Field(..., description="结果ID")
    scenario: str = Field(..., description="场景描述文本")
    domain: Optional[str] = Field(None, description="领域")
    namespace: Optional[str] = Field(None, description="本体命名空间URI")
    classes_json: dict = Field(..., description="提取的本体类数据(JSON格式)")
    extracted_count: int = Field(..., description="提取的类数量")
    user_id: Optional[int] = Field(None, description="用户ID")
    created_at: datetime.datetime = Field(..., description="创建时间")
    
    @field_serializer("created_at", when_used="json")
    def _serialize_created_at(self, dt: datetime.datetime):
        """将创建时间序列化为毫秒时间戳"""
        return int(dt.timestamp() * 1000) if dt else None
    
    class Config:
        from_attributes = True
