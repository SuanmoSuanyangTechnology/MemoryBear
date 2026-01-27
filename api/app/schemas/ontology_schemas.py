"""本体提取API的请求和响应模型

本模块定义了本体提取系统的所有API请求和响应的Pydantic模型。

Classes:
    ExtractionRequest: 本体提取请求模型
    ExtractionResponse: 本体提取响应模型
    ExportRequest: OWL文件导出请求模型
    ExportResponse: OWL文件导出响应模型
    ConfigResponse: 配置查询响应模型
    ConfigUpdateRequest: 配置更新请求模型
"""

from typing import List, Optional

from pydantic import BaseModel, Field

from app.core.memory.models.ontology_models import OntologyClass


class ExtractionRequest(BaseModel):
    """本体提取请求模型
    
    用于POST /api/ontology/extract端点的请求体。
    
    Attributes:
        scenario: 场景描述文本,不能为空
        domain: 可选的领域提示(如Healthcare, Education等)
        config_name: 配置名称,默认为"default"
    
    Examples:
        >>> request = ExtractionRequest(
        ...     scenario="医院管理患者记录...",
        ...     domain="Healthcare"
        ... )
    """
    scenario: str = Field(..., description="场景描述文本", min_length=1)
    domain: Optional[str] = Field(None, description="可选的领域提示")
    config_name: str = Field("default", description="配置名称")


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


class ConfigResponse(BaseModel):
    """配置查询响应模型
    
    用于GET /api/ontology/config/{config_name}端点的响应体。
    
    Attributes:
        config_name: 配置名称
        max_classes: 最大提取类数量
        min_classes: 最小提取类数量
        max_description_length: 描述最大字符数
        llm_temperature: LLM温度参数
        llm_max_tokens: LLM最大token数
        llm_timeout: LLM调用超时时间(秒)
        enable_owl_validation: 是否启用OWL验证
    
    Examples:
        >>> response = ConfigResponse(
        ...     config_name="default",
        ...     max_classes=15,
        ...     min_classes=5,
        ...     max_description_length=500,
        ...     llm_temperature=0.3,
        ...     llm_max_tokens=2000,
        ...     llm_timeout=30.0,
        ...     enable_owl_validation=True
        ... )
    """
    config_name: str = Field(..., description="配置名称")
    max_classes: int = Field(..., description="最大提取类数量")
    min_classes: int = Field(..., description="最小提取类数量")
    max_description_length: int = Field(..., description="描述最大字符数")
    llm_temperature: float = Field(..., description="LLM温度参数")
    llm_max_tokens: int = Field(..., description="LLM最大token数")
    llm_timeout: Optional[float] = Field(None, description="LLM调用超时时间(秒)")
    enable_owl_validation: bool = Field(..., description="是否启用OWL验证")


class ConfigUpdateRequest(BaseModel):
    """配置更新请求模型
    
    用于PUT /api/ontology/config/{config_name}端点的请求体。
    所有字段都是可选的,只更新提供的字段。
    
    Attributes:
        max_classes: 最大提取类数量
        min_classes: 最小提取类数量
        max_description_length: 描述最大字符数
        llm_temperature: LLM温度参数
        llm_max_tokens: LLM最大token数
        llm_timeout: LLM调用超时时间(秒)
        enable_owl_validation: 是否启用OWL验证
    
    Examples:
        >>> request = ConfigUpdateRequest(
        ...     max_classes=20,
        ...     llm_temperature=0.5,
        ...     llm_timeout=60.0
        ... )
    """
    max_classes: Optional[int] = Field(None, description="最大提取类数量", ge=1, le=50)
    min_classes: Optional[int] = Field(None, description="最小提取类数量", ge=1, le=50)
    max_description_length: Optional[int] = Field(None, description="描述最大字符数", ge=100, le=2000)
    llm_temperature: Optional[float] = Field(None, description="LLM温度参数", ge=0.0, le=2.0)
    llm_max_tokens: Optional[int] = Field(None, description="LLM最大token数", ge=100, le=10000)
    llm_timeout: Optional[float] = Field(None, description="LLM调用超时时间(秒)", ge=1.0, le=300.0)
    enable_owl_validation: Optional[bool] = Field(None, description="是否启用OWL验证")
