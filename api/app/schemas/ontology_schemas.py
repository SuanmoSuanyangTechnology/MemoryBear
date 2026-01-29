"""本体提取API的请求和响应模型

本模块定义了本体提取系统的所有API请求和响应的Pydantic模型。

Classes:
    ExtractionRequest: 本体提取请求模型
    ExtractionResponse: 本体提取响应模型
    ExportRequest: OWL文件导出请求模型
    ExportResponse: OWL文件导出响应模型
    OntologyResultResponse: 本体提取结果响应模型(带毫秒时间戳)
    SceneCreateRequest: 场景创建请求模型
    SceneUpdateRequest: 场景更新请求模型
    SceneResponse: 场景响应模型
    SceneListResponse: 场景列表响应模型
    ClassCreateRequest: 类型创建请求模型
    ClassUpdateRequest: 类型更新请求模型
    ClassResponse: 类型响应模型
    ClassListResponse: 类型列表响应模型
"""

from typing import List, Optional
import datetime
from uuid import UUID

from pydantic import BaseModel, Field, field_serializer, ConfigDict

from app.core.memory.models.ontology_models import OntologyClass


class ExtractionRequest(BaseModel):
    """本体提取请求模型
    
    用于POST /api/ontology/extract端点的请求体。
    
    Attributes:
        scenario: 场景描述文本,不能为空
        domain: 可选的领域提示(如Healthcare, Education等)
        llm_id: LLM模型ID,必须提供
        scene_id: 场景ID,必须提供,用于将提取的类保存到指定场景
    
    Examples:
        >>> request = ExtractionRequest(
        ...     scenario="医院管理患者记录...",
        ...     domain="Healthcare",
        ...     llm_id="550e8400-e29b-41d4-a716-446655440000",
        ...     scene_id="660e8400-e29b-41d4-a716-446655440000"
        ... )
    """
    scenario: str = Field(..., description="场景描述文本", min_length=1)
    domain: Optional[str] = Field(None, description="可选的领域提示")
    llm_id: str = Field(..., description="LLM模型ID")
    scene_id: UUID = Field(..., description="场景ID,用于将提取的类保存到指定场景")


class ExtractionResponse(BaseModel):
    """本体提取响应模型
    
    用于POST /api/ontology/extract端点的响应体。
    
    Attributes:
        classes: 提取的本体类列表
        domain: 识别的领域
        extracted_count: 提取的类数量
    
    Examples:
        >>> response = ExtractionResponse(
        ...     classes=[...],
        ...     domain="Healthcare",
        ...     extracted_count=7
        ... )
    """
    classes: List[OntologyClass] = Field(default_factory=list, description="提取的本体类列表")
    domain: str = Field(..., description="识别的领域")
    extracted_count: int = Field(..., description="提取的类数量")


class ExportRequest(BaseModel):
    """OWL文件导出请求模型
    
    用于POST /api/ontology/export端点的请求体。
    
    Attributes:
        classes: 要导出的本体类列表
        format: 导出格式,可选值: rdfxml, turtle, ntriples, json
        include_metadata: 是否包含完整的OWL元数据(命名空间等),默认True
    
    Examples:
        >>> request = ExportRequest(
        ...     classes=[...],
        ...     format="rdfxml",
        ...     include_metadata=True
        ... )
    """
    classes: List[OntologyClass] = Field(..., description="要导出的本体类列表", min_length=1)
    format: str = Field("rdfxml", description="导出格式: rdfxml, turtle, ntriples, json")
    include_metadata: bool = Field(True, description="是否包含完整的OWL元数据")


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
        classes_json: 提取的本体类数据(JSON格式)
        extracted_count: 提取的类数量
        user_id: 用户ID
        created_at: 创建时间(毫秒时间戳)
    
    Examples:
        >>> response = OntologyResultResponse(
        ...     id=uuid.uuid4(),
        ...     scenario="医院管理患者记录...",
        ...     domain="Healthcare",
        ...     classes_json={"classes": [...]},
        ...     extracted_count=7,
        ...     user_id=123,
        ...     created_at=datetime.now()
        ... )
    """
    id: UUID = Field(..., description="结果ID")
    scenario: str = Field(..., description="场景描述文本")
    domain: Optional[str] = Field(None, description="领域")
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



# ==================== 本体场景相关 Schema ====================

class SceneCreateRequest(BaseModel):
    """场景创建请求模型
    
    用于创建新的本体场景。
    
    Attributes:
        scene_name: 场景名称，必填，1-200字符
        scene_description: 场景描述，可选
    
    Examples:
        >>> request = SceneCreateRequest(
        ...     scene_name="医疗场景",
        ...     scene_description="用于医疗领域的本体建模"
        ... )
    """
    scene_name: str = Field(..., min_length=1, max_length=200, description="场景名称")
    scene_description: Optional[str] = Field(None, description="场景描述")


class SceneUpdateRequest(BaseModel):
    """场景更新请求模型
    
    用于更新已有本体场景信息。
    
    Attributes:
        scene_name: 场景名称，可选，1-200字符
        scene_description: 场景描述，可选
    
    Examples:
        >>> request = SceneUpdateRequest(
        ...     scene_name="更新后的场景名称",
        ...     scene_description="更新后的描述"
        ... )
    """
    scene_name: Optional[str] = Field(None, min_length=1, max_length=200, description="场景名称")
    scene_description: Optional[str] = Field(None, description="场景描述")


class SceneResponse(BaseModel):
    """场景响应模型
    
    用于返回本体场景信息。
    
    Attributes:
        scene_id: 场景ID
        scene_name: 场景名称
        scene_description: 场景描述
        type_num: 类型数量
        workspace_id: 所属工作空间ID
        created_at: 创建时间（毫秒时间戳）
        updated_at: 更新时间（毫秒时间戳）
        classes_count: 类型数量
    
    Examples:
        >>> response = SceneResponse(
        ...     scene_id=uuid.uuid4(),
        ...     scene_name="医疗场景",
        ...     scene_description="用于医疗领域的本体建模",
        ...     type_num=0,
        ...     workspace_id=uuid.uuid4(),
        ...     created_at=datetime.now(),
        ...     updated_at=datetime.now(),
        ...     classes_count=5
        ... )
    """
    scene_id: UUID = Field(..., description="场景ID")
    scene_name: str = Field(..., description="场景名称")
    scene_description: Optional[str] = Field(None, description="场景描述")
    type_num: int = Field(..., description="类型数量")
    entity_type: Optional[List[str]] = Field(None, description="实体类型列表（最多3个class_name）")
    workspace_id: UUID = Field(..., description="所属工作空间ID")
    created_at: datetime.datetime = Field(..., description="创建时间（毫秒时间戳）")
    updated_at: datetime.datetime = Field(..., description="更新时间（毫秒时间戳）")
    classes_count: int = Field(0, description="类型数量")
    
    @field_serializer("created_at", when_used="json")
    def _serialize_created_at(self, dt: datetime.datetime):
        """将创建时间序列化为毫秒时间戳"""
        return int(dt.timestamp() * 1000) if dt else None
    
    @field_serializer("updated_at", when_used="json")
    def _serialize_updated_at(self, dt: datetime.datetime):
        """将更新时间序列化为毫秒时间戳"""
        return int(dt.timestamp() * 1000) if dt else None
    
    model_config = ConfigDict(from_attributes=True)


class PaginationInfo(BaseModel):
    """分页信息模型
    
    Attributes:
        page: 当前页码
        pagesize: 每页数量
        total: 总数量
        hasnext: 是否有下一页
    """
    page: int = Field(..., description="当前页码")
    pagesize: int = Field(..., description="每页数量")
    total: int = Field(..., description="总数量")
    hasnext: bool = Field(..., description="是否有下一页")


class SceneListResponse(BaseModel):
    """场景列表响应模型（支持分页）
    
    用于返回本体场景列表。
    
    Attributes:
        items: 场景列表
        page: 分页信息（可选，分页时返回）
    
    Examples:
        >>> # 不分页
        >>> response = SceneListResponse(
        ...     items=[scene1, scene2]
        ... )
        >>> # 分页
        >>> response = SceneListResponse(
        ...     items=[scene1, scene2, ...],
        ...     page=PaginationInfo(page=1, pagesize=100, total=150, hasnext=True)
        ... )
    """
    items: List[SceneResponse] = Field(..., description="场景列表")
    page: Optional[PaginationInfo] = Field(None, description="分页信息")


# ==================== 本体类型相关 Schema ====================

class ClassItem(BaseModel):
    """单个类型信息模型
    
    Attributes:
        class_name: 类型名称，必填，1-200字符
        class_description: 类型描述，可选
    
    Examples:
        >>> item = ClassItem(
        ...     class_name="患者",
        ...     class_description="医院患者信息"
        ... )
    """
    class_name: str = Field(..., min_length=1, max_length=200, description="类型名称")
    class_description: Optional[str] = Field(None, description="类型描述")


class ClassCreateRequest(BaseModel):
    """类型创建请求模型（统一使用列表形式）
    
    通过列表中元素数量决定创建模式：
    - 列表包含 1 个元素：单个创建
    - 列表包含多个元素：批量创建
    
    Attributes:
        scene_id: 所属场景ID，必填
        classes: 类型列表，必填，至少包含 1 个元素
    
    Examples:
        # 单个创建（列表中 1 个元素）
        >>> request = ClassCreateRequest(
        ...     scene_id=uuid.uuid4(),
        ...     classes=[
        ...         ClassItem(class_name="患者", class_description="医院患者信息")
        ...     ]
        ... )
        
        # 批量创建（列表中多个元素）
        >>> request = ClassCreateRequest(
        ...     scene_id=uuid.uuid4(),
        ...     classes=[
        ...         ClassItem(class_name="患者", class_description="医院患者信息"),
        ...         ClassItem(class_name="医生", class_description="医院医生信息"),
        ...         ClassItem(class_name="药品", class_description="医院药品信息")
        ...     ]
        ... )
    """
    scene_id: UUID = Field(..., description="所属场景ID")
    classes: List[ClassItem] = Field(..., min_length=1, description="类型列表，至少包含 1 个元素")


class ClassUpdateRequest(BaseModel):
    """类型更新请求模型
    
    用于更新已有本体类型信息。
    
    Attributes:
        class_name: 类型名称，可选，1-200字符
        class_description: 类型描述，可选
    
    Examples:
        >>> request = ClassUpdateRequest(
        ...     class_name="更新后的类型名称",
        ...     class_description="更新后的描述"
        ... )
    """
    class_name: Optional[str] = Field(None, min_length=1, max_length=200, description="类型名称")
    class_description: Optional[str] = Field(None, description="类型描述")


class ClassResponse(BaseModel):
    """类型响应模型
    
    用于返回本体类型信息。
    
    Attributes:
        class_id: 类型ID
        class_name: 类型名称
        class_description: 类型描述
        scene_id: 所属场景ID
        created_at: 创建时间（毫秒时间戳）
        updated_at: 更新时间（毫秒时间戳）
    
    Examples:
        >>> response = ClassResponse(
        ...     class_id=uuid.uuid4(),
        ...     class_name="患者",
        ...     class_description="医院患者信息",
        ...     scene_id=uuid.uuid4(),
        ...     created_at=datetime.now(),
        ...     updated_at=datetime.now()
        ... )
    """
    class_id: UUID = Field(..., description="类型ID")
    class_name: str = Field(..., description="类型名称")
    class_description: Optional[str] = Field(None, description="类型描述")
    scene_id: UUID = Field(..., description="所属场景ID")
    created_at: datetime.datetime = Field(..., description="创建时间（毫秒时间戳）")
    updated_at: datetime.datetime = Field(..., description="更新时间（毫秒时间戳）")
    
    @field_serializer("created_at", when_used="json")
    def _serialize_created_at(self, dt: datetime.datetime):
        """将创建时间序列化为毫秒时间戳"""
        return int(dt.timestamp() * 1000) if dt else None
    
    @field_serializer("updated_at", when_used="json")
    def _serialize_updated_at(self, dt: datetime.datetime):
        """将更新时间序列化为毫秒时间戳"""
        return int(dt.timestamp() * 1000) if dt else None
    
    model_config = ConfigDict(from_attributes=True)


class ClassBatchCreateResponse(BaseModel):
    """批量创建类型响应模型
    
    用于返回批量创建的结果统计和详情。
    
    Attributes:
        total: 总共尝试创建的数量
        success_count: 成功创建的数量
        failed_count: 失败的数量
        items: 成功创建的类型列表
        errors: 失败的错误信息列表（可选）
    
    Examples:
        >>> response = ClassBatchCreateResponse(
        ...     total=3,
        ...     success_count=2,
        ...     failed_count=1,
        ...     items=[class1, class2],
        ...     errors=["创建类型 '药品' 失败: 类型名称已存在"]
        ... )
    """
    total: int = Field(..., description="总共尝试创建的数量")
    success_count: int = Field(..., description="成功创建的数量")
    failed_count: int = Field(0, description="失败的数量")
    items: List[ClassResponse] = Field(..., description="成功创建的类型列表")
    errors: Optional[List[str]] = Field(None, description="失败的错误信息列表")


class ClassListResponse(BaseModel):
    """类型列表响应模型
    
    用于返回本体类型列表。
    
    Attributes:
        total: 总数量
        scene_id: 所属场景ID
        scene_name: 场景名称
        scene_description: 场景描述
        items: 类型列表
    
    Examples:
        >>> response = ClassListResponse(
        ...     total=3,
        ...     scene_id=uuid.uuid4(),
        ...     scene_name="医疗场景",
        ...     scene_description="用于医疗领域的本体建模",
        ...     items=[class1, class2, class3]
        ... )
    """
    total: int = Field(..., description="总数量")
    scene_id: UUID = Field(..., description="所属场景ID")
    scene_name: str = Field(..., description="场景名称")
    scene_description: Optional[str] = Field(None, description="场景描述")
    items: List[ClassResponse] = Field(..., description="类型列表")
