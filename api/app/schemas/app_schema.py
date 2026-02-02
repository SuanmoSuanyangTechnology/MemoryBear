import datetime
import uuid
from typing import Optional, Any, List, Dict, Union
from enum import Enum

from pydantic import BaseModel, Field, ConfigDict, field_serializer, field_validator


# ---------- Multimodal File Support ----------

class FileType(str, Enum):
    """文件类型枚举"""
    IMAGE = "image"
    DOCUMENT = "document"
    AUDIO = "audio"
    VIDEO = "video"


class TransferMethod(str, Enum):
    """文件传输方式枚举"""
    LOCAL_FILE = "local_file"  # 已上传到系统的文件
    REMOTE_URL = "remote_url"  # 外部URL


class FileInput(BaseModel):
    """文件输入 Schema"""
    type: FileType = Field(..., description="文件类型: image/document/audio/video")
    transfer_method: TransferMethod = Field(..., description="传输方式: local_file/remote_url")
    upload_file_id: Optional[uuid.UUID] = Field(None, description="已上传文件ID（local_file时必填）")
    url: Optional[str] = Field(None, description="远程URL（remote_url时必填）")
    
    @field_validator("upload_file_id")
    @classmethod
    def validate_local_file(cls, v, info):
        """验证 local_file 时必须提供 upload_file_id"""
        if info.data.get("transfer_method") == TransferMethod.LOCAL_FILE and not v:
            raise ValueError("transfer_method 为 local_file 时，upload_file_id 不能为空")
        return v
    
    @field_validator("url")
    @classmethod
    def validate_remote_url(cls, v, info):
        """验证 remote_url 时必须提供 url"""
        if info.data.get("transfer_method") == TransferMethod.REMOTE_URL and not v:
            raise ValueError("transfer_method 为 remote_url 时，url 不能为空")
        return v


# ---------- Input Schemas ----------

class KnowledgeBaseConfig(BaseModel):
    """单个知识库配置"""
    kb_id: str = Field(..., description="知识库ID")
    top_k: int = Field(default=3, ge=1, le=20, description="检索返回的文档数量")
    similarity_threshold: float = Field(default=0.7, ge=0.0, le=1.0, description="相似度阈值")
    strategy: str = Field(default="hybrid", description="检索策略: hybrid | bm25 | dense")
    weight: float = Field(default=1.0, ge=0.0, le=1.0, description="知识库权重（用于多知识库融合）")
    vector_similarity_weight: float = Field(default=0.5, ge=0.0, le=1.0, description="向量相似度权重")
    retrieve_type: str = Field(default="hybrid", description="检索方式participle｜ semantic｜hybrid")


class KnowledgeRetrievalConfig(BaseModel):
    """知识库检索配置（支持多个知识库，每个有独立配置）"""
    knowledge_bases: List[KnowledgeBaseConfig] = Field(
        default_factory=list,
        description="关联的知识库列表，每个知识库有独立配置"
    )

    # 多知识库融合策略
    merge_strategy: str = Field(
        default="weighted",
        description="多知识库结果融合策略: weighted | rrf | concat"
    )
    reranker_id: Optional[str] = Field(default=None, description="多知识库结果融合的模型ID")
    reranker_top_k: int = Field(default=10, ge=0, le=1024, description="多知识库结果融合的模型参数")
    use_graph: bool = Field(default=False, description="是否使用图搜索")


class ToolConfig(BaseModel):
    """工具配置"""
    enabled: bool = Field(default=False, description="是否启用该工具")
    tool_id: Optional[str] = Field(default=None, description="工具ID")
    operation: Optional[str] = Field(default=None, description="工具特定配置")


class ToolOldConfig(BaseModel):
    """工具配置"""
    enabled: bool = Field(default=False, description="是否启用该工具")
    config: Optional[Dict[str, Any]] = Field(default_factory=dict, description="工具特定配置")


class MemoryConfig(BaseModel):
    """记忆配置"""
    enabled: bool = Field(default=True, description="是否启用对话历史记忆")
    memory_content: Optional[str] = Field(default=None, description="选择记忆的内容类型")
    max_history: int = Field(default=10, ge=0, le=100, description="最大保留的历史对话轮数")


class ModelParameters(BaseModel):
    """模型参数配置"""
    temperature: float = Field(default=0.7, ge=0.0, le=2.0, description="温度参数，控制输出的随机性")
    max_tokens: int = Field(default=2000, ge=1, le=32000, description="最大生成token数")
    top_p: float = Field(default=1.0, ge=0.0, le=1.0, description="核采样参数")
    frequency_penalty: float = Field(default=0.0, ge=-2.0, le=2.0, description="频率惩罚")
    presence_penalty: float = Field(default=0.0, ge=-2.0, le=2.0, description="存在惩罚")
    n: int = Field(default=1, ge=1, le=10, description="生成的回复数量")
    stop: Optional[List[str]] = Field(default=None, description="停止序列")


class VariableDefinition(BaseModel):
    """变量定义"""
    name: str = Field(..., description="变量名称（标识符）")
    display_name: Optional[str] = Field(None, description="显示名称（用户看到的名称）")
    type: str = Field(
        default="string",
        description="变量类型: string(单行文本) | text(多行文本) | number(数字)"
    )
    required: bool = Field(default=False, description="是否必填")
    description: Optional[str] = Field(default=None, description="变量描述")
    max_length: Optional[int] = Field(default=None, description="最大长度（用于文本类型）")


class AgentConfigCreate(BaseModel):
    """Agent 行为配置"""
    # 提示词配置
    system_prompt: Optional[str] = Field(default=None, description="系统提示词，定义 Agent 的角色和行为准则")

    # 模型配置
    default_model_config_id: Optional[uuid.UUID] = Field(default=None, description="默认使用的模型配置ID")
    model_parameters: ModelParameters = Field(
        default_factory=ModelParameters,
        description="模型参数配置（temperature、max_tokens 等）"
    )

    # 知识库关联
    knowledge_retrieval: Optional[KnowledgeRetrievalConfig] = Field(
        default=None,
        description="知识库检索配置"
    )

    # 记忆配置
    memory: MemoryConfig = Field(
        default_factory=lambda: MemoryConfig(enabled=True),
        description="对话历史记忆配置"
    )

    # 变量配置
    variables: List[VariableDefinition] = Field(
        default_factory=list,
        description="Agent 可用的变量列表"
    )

    # 工具配置
    tools: List[ToolConfig] = Field(
        default_factory=list,
        description="Agent 可用的工具列表"
    )


class AppCreate(BaseModel):
    name: str
    description: Optional[str] = None
    icon: Optional[str] = None
    icon_type: Optional[str] = None
    type: str = Field(pattern=r"^(agent|workflow|multi_agent)$")
    visibility: Optional[str] = None
    status: Optional[str] = None
    tags: Optional[List[str]] = Field(default_factory=list)

    # only for type=agent
    agent_config: Optional[AgentConfigCreate] = None

    # only for type=multi_agent
    multi_agent_config: Optional[Dict[str, Any]] = None


class AppUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    icon: Optional[str] = None
    icon_type: Optional[str] = None
    visibility: Optional[str] = None
    status: Optional[str] = None
    tags: Optional[List[str]] = None


class AgentConfigUpdate(BaseModel):
    """更新 Agent 行为配置"""
    # 提示词配置
    system_prompt: Optional[str] = Field(default=None, description="系统提示词")

    # 模型配置
    default_model_config_id: Optional[uuid.UUID] = Field(default=None, description="默认模型配置ID")
    model_parameters: Optional[ModelParameters] = Field(default=None, description="模型参数配置")

    # 知识库关联
    knowledge_retrieval: Optional[KnowledgeRetrievalConfig] = Field(
        default=None,
        description="知识库检索配置"
    )

    # 记忆配置
    memory: Optional[MemoryConfig] = Field(default=None, description="对话历史记忆配置")

    # 变量配置
    variables: Optional[List[VariableDefinition]] = Field(default=None, description="变量列表")

    # 工具配置
    tools: Optional[List[ToolConfig]] = Field(default_factory=list, description="工具列表")


# ---------- Output Schemas ----------

class App(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    workspace_id: uuid.UUID
    created_by: uuid.UUID
    name: str
    description: Optional[str] = None
    icon: Optional[str] = None
    icon_type: Optional[str] = None
    type: str
    visibility: str
    status: str
    tags: List[str] = []
    current_release_id: Optional[uuid.UUID] = None
    is_active: bool
    is_shared: bool = False  # 是否是共享应用（从其他工作空间共享来的）
    created_at: datetime.datetime
    updated_at: datetime.datetime

    @field_serializer("created_at", when_used="json")
    def _serialize_created_at(self, dt: datetime.datetime):
        return int(dt.timestamp() * 1000) if dt else None

    @field_serializer("updated_at", when_used="json")
    def _serialize_updated_at(self, dt: datetime.datetime):
        return int(dt.timestamp() * 1000) if dt else None


class AgentConfig(BaseModel):
    """Agent 配置输出 Schema"""
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    app_id: uuid.UUID

    # 提示词
    system_prompt: Optional[str] = None

    # 模型配置
    default_model_config_id: Optional[uuid.UUID] = None
    model_parameters: ModelParameters = Field(default_factory=ModelParameters)

    # 知识库检索
    knowledge_retrieval: Optional[KnowledgeRetrievalConfig] = None

    # 记忆配置
    memory: MemoryConfig = Field(default_factory=lambda: MemoryConfig(enabled=True))

    # 变量配置
    variables: List[VariableDefinition] = []

    # 工具配置
    tools: Union[List[ToolConfig], Dict[str, ToolOldConfig]] = []

    is_active: bool
    created_at: datetime.datetime
    updated_at: datetime.datetime

    @field_validator("model_parameters", mode="before")
    @classmethod
    def validate_model_parameters(cls, v):
        """处理 None 值，返回默认的 ModelParameters"""
        if v is None:
            return ModelParameters()
        return v

    @field_validator("memory", mode="before")
    @classmethod
    def validate_memory(cls, v):
        """处理 None 值，返回默认的 MemoryConfig"""
        if v is None:
            return MemoryConfig(enabled=True)
        return v

    @field_validator("variables", mode="before")
    @classmethod
    def validate_variables(cls, v):
        """处理 None 值，返回空列表"""
        if v is None:
            return []
        return v

    @field_validator("tools", mode="before")
    @classmethod
    def validate_tools(cls, v):
        """处理 None 值，返回空字典"""
        if v is None:
            return {}
        return v

    @field_serializer("created_at", when_used="json")
    def _serialize_created_at(self, dt: datetime.datetime):
        return int(dt.timestamp() * 1000) if dt else None

    @field_serializer("updated_at", when_used="json")
    def _serialize_updated_at(self, dt: datetime.datetime):
        return int(dt.timestamp() * 1000) if dt else None


class PublishRequest(BaseModel):
    """发布应用请求"""
    version_name: str
    release_notes: Optional[str] = Field(None, description="版本说明")


class AppRelease(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    app_id: uuid.UUID
    version: int
    release_notes: Optional[str] = None
    version_name: str
    description: Optional[str] = None
    icon: Optional[str] = None
    icon_type: Optional[str] = None
    name: str
    type: str
    visibility: str
    config: Dict[str, Any] = {}
    default_model_config_id: Optional[uuid.UUID] = None
    published_by: uuid.UUID
    publisher_name: str
    published_at: datetime.datetime
    is_active: bool
    created_at: datetime.datetime
    updated_at: datetime.datetime

    @field_validator("config", mode="before")
    @classmethod
    def parse_config(cls, v):
        """处理 config 字段，如果是字符串则解析为字典"""
        if isinstance(v, str):
            import json
            try:
                return json.loads(v)
            except json.JSONDecodeError:
                return {}
        return v if v is not None else {}

    @field_serializer("created_at", when_used="json")
    def _serialize_created_at(self, dt: datetime.datetime):
        return int(dt.timestamp() * 1000) if dt else None

    @field_serializer("updated_at", when_used="json")
    def _serialize_updated_at(self, dt: datetime.datetime):
        return int(dt.timestamp() * 1000) if dt else None

    @field_serializer("published_at", when_used="json")
    def _serialize_published_at(self, dt: datetime.datetime):
        return int(dt.timestamp() * 1000) if dt else None


# ---------- App Share Schemas ----------

class AppShareCreate(BaseModel):
    """应用分享请求"""
    target_workspace_ids: List[uuid.UUID] = Field(..., description="目标工作空间ID列表")


class AppShare(BaseModel):
    """应用分享输出"""
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    source_app_id: uuid.UUID
    source_workspace_id: uuid.UUID
    target_workspace_id: uuid.UUID
    shared_by: uuid.UUID
    created_at: datetime.datetime
    updated_at: datetime.datetime

    @field_serializer("created_at", when_used="json")
    def _serialize_created_at(self, dt: datetime.datetime):
        return int(dt.timestamp() * 1000) if dt else None

    @field_serializer("updated_at", when_used="json")
    def _serialize_updated_at(self, dt: datetime.datetime):
        return int(dt.timestamp() * 1000) if dt else None


# ---------- Draft Run Schemas ----------

class AppChatRequest(BaseModel):
    message: str = Field(..., description="用户消息")
    conversation_id: Optional[str] = Field(default=None, description="会话ID（用于多轮对话）")
    user_id: Optional[str] = Field(default=None, description="用户ID（用于会话管理）")
    variables: Optional[Dict[str, Any]] = Field(default=None, description="自定义变量参数值")
    stream: bool = Field(default=False, description="是否流式返回")
    files: Optional[List[FileInput]] = Field(default=None, description="附件列表（支持多文件）")


class DraftRunRequest(BaseModel):
    """试运行请求"""
    message: str = Field(..., description="用户消息")
    conversation_id: Optional[str] = Field(default=None, description="会话ID（用于多轮对话）")
    user_id: Optional[str] = Field(default=None, description="用户ID（用于会话管理）")
    variables: Optional[Dict[str, Any]] = Field(default=None, description="自定义变量参数值")
    stream: bool = Field(default=False, description="是否流式返回")
    files: Optional[List[FileInput]] = Field(default=None, description="附件列表（支持多文件）")


class DraftRunResponse(BaseModel):
    """试运行响应（非流式）"""
    message: str = Field(..., description="AI 回复消息")
    conversation_id: Optional[str] = Field(default=None, description="会话ID（用于多轮对话）")
    usage: Optional[Dict[str, Any]] = Field(default=None, description="Token 使用情况")
    elapsed_time: Optional[float] = Field(default=None, description="耗时（秒）")


class DraftRunStreamChunk(BaseModel):
    """试运行流式响应块"""
    event: str = Field(..., description="事件类型: start | message | end | error")
    data: Dict[str, Any] = Field(..., description="事件数据")


# ---------- Draft Run Compare Schemas ----------

class ModelCompareItem(BaseModel):
    """单个对比模型配置"""
    model_config_id: uuid.UUID = Field(..., description="模型配置ID")
    model_parameters: Optional[Dict[str, Any]] = Field(
        None,
        description="覆盖模型参数，如 temperature, max_tokens 等"
    )
    label: Optional[str] = Field(
        None,
        description="自定义显示标签，用于区分同一模型的不同配置"
    )
    conversation_id: Optional[str] = Field(
        None,
        description="会话ID，用于为每个模型指定独立的会话历史"
    )


class DraftRunCompareRequest(BaseModel):
    """多模型对比试运行请求"""
    message: str = Field(..., description="用户消息")
    conversation_id: Optional[str] = Field(None, description="会话ID")
    user_id: Optional[str] = Field(None, description="用户ID")
    variables: Optional[Dict[str, Any]] = Field(None, description="变量参数")

    models: List[ModelCompareItem] = Field(
        ...,
        min_length=1,
        max_length=5,
        description="要对比的模型列表（1-5个）"
    )

    parallel: bool = Field(True, description="是否并行执行")
    stream: bool = Field(False, description="是否流式返回")
    timeout: Optional[int] = Field(60, ge=10, le=300, description="超时时间（秒）")


class ModelRunResult(BaseModel):
    """单个模型运行结果"""
    model_config_id: uuid.UUID
    model_name: str
    label: Optional[str] = None

    parameters_used: Dict[str, Any] = Field(..., description="实际使用的参数")

    message: Optional[str] = None
    usage: Optional[Dict[str, Any]] = None
    elapsed_time: float
    error: Optional[str] = None

    tokens_per_second: Optional[float] = None
    cost_estimate: Optional[float] = None
    conversation_id: Optional[str] = None


class DraftRunCompareResponse(BaseModel):
    """多模型对比响应"""
    results: List[ModelRunResult]

    total_elapsed_time: float
    successful_count: int
    failed_count: int

    fastest_model: Optional[str] = None
    cheapest_model: Optional[str] = None
