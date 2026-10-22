import datetime
import uuid
from typing import Optional, Any, List, Dict, Union
from enum import Enum, StrEnum
from urllib.parse import urlparse

from pydantic import BaseModel, Field, ConfigDict, field_serializer, field_validator, model_serializer, model_validator

from ..utils.datetime_utils import to_timestamp_ms
from .rerank_schema import RerankMode, RerankWeights
from .workflow_schema import WorkflowConfigCreate


# ---------- Multimodal File Support ----------

class FileType(StrEnum):
    """File type enum"""
    IMAGE = "image"
    DOCUMENT = "document"
    AUDIO = "audio"
    VIDEO = "video"

    @classmethod
    def trans(cls, value: str) -> 'FileType':
        if value.startswith("image"):
            return cls.IMAGE
        elif value.startswith("document"):
            return cls.DOCUMENT
        elif value.startswith("audio"):
            return cls.AUDIO
        elif value.startswith("video"):
            return cls.VIDEO
        else:
            raise RuntimeError("Unsupport file type")


class TransferMethod(str, Enum):
    """File transfer method enum"""
    LOCAL_FILE = "local_file"  # file already uploaded to the system
    REMOTE_URL = "remote_url"  # external URL


class FileInput(BaseModel):
    """Multimodal file input for app chat."""

    type: FileType = Field(..., description="文件类型: image/document/audio/video")
    transfer_method: TransferMethod = Field(..., description="传输方式: local_file/remote_url")
    upload_file_id: Optional[uuid.UUID] = Field(None, description="已上传文件 ID（local_file 时必填）")
    url: Optional[str] = Field(None, description="远程 HTTP(S) URL（remote_url 时必填）")
    file_type: Optional[str] = Field(None, description="具体文件格式（如 image/jpeg、audio/wav）")
    name: Optional[str] = Field(None, description="文件名")
    size: Optional[int] = Field(None, ge=0, description="文件大小（字节，不能为负数）")

    _content = None

    def __init__(self, **data):
        raw_type = data.get("type")
        if isinstance(raw_type, str) and not data.get("file_type"):
            try:
                normalized_type = FileType.trans(raw_type)
            except (AttributeError, RuntimeError, TypeError, ValueError):
                pass
            else:
                if raw_type != normalized_type.value:
                    data["file_type"] = raw_type

        # Preserve compatibility with old clients that send file_id and omit
        # transfer_method; new clients should pass transfer_method explicitly rather
        # than relying on server-side inference.
        if "transfer_method" not in data:
            if "upload_file_id" in data or "file_id" in data:
                data["transfer_method"] = TransferMethod.LOCAL_FILE
            elif "url" in data and not str(data.get("url", "")).startswith("blob:"):
                data["transfer_method"] = TransferMethod.REMOTE_URL
            else:
                data["transfer_method"] = TransferMethod.LOCAL_FILE

        if str(data.get("url", "")).startswith("blob:"):
            data.pop("url", None)

        if "file_id" in data and "upload_file_id" not in data:
            data["upload_file_id"] = data.pop("file_id")
        super().__init__(**data)

    def set_content(self, content: bytes):
        self._content = content

    def get_content(self) -> bytes | None:
        return self._content

    @field_validator("type", mode="before")
    @classmethod
    def validate_type(cls, value):
        """Accept both exact type values and the historical type-prefix format."""
        if isinstance(value, FileType):
            return value
        try:
            return FileType.trans(value)
        except (AttributeError, RuntimeError, TypeError, ValueError) as exc:
            raise ValueError("type 仅支持 image、document、audio 或 video 及其子类型") from exc

    @field_validator("url")
    @classmethod
    def normalize_url(cls, value: Optional[str]) -> Optional[str]:
        return value.strip() if value is not None else None

    @model_validator(mode="after")
    def validate_transfer_fields(self):
        if self.transfer_method == TransferMethod.LOCAL_FILE:
            if self.upload_file_id is None:
                raise ValueError("transfer_method 为 local_file 时，upload_file_id 不能为空")
            return self

        if not self.url:
            raise ValueError("transfer_method 为 remote_url 时，url 不能为空")

        parsed = urlparse(self.url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("transfer_method 为 remote_url 时，url 必须是合法的 HTTP(S) URL")
        return self


# ---------- Input Schemas ----------

class KnowledgeBaseConfig(BaseModel):
    """Single knowledge base config"""
    kb_id: str = Field(..., description="知识库ID")
    top_k: int = Field(default=3, ge=1, le=20, description="检索返回的文档数量")
    similarity_threshold: float = Field(default=0.7, ge=0.0, le=1.0, description="相似度阈值")
    # strategy: str = Field(default="hybrid", description="检索策略: hybrid | bm25 | dense")
    # weight: float = Field(default=1.0, ge=0.0, le=1.0, description="知识库权重（用于多知识库融合）")
    vector_similarity_weight: float | None = Field(default=0.5, ge=0.0, le=1.0, description="向量相似度权重（语义/混合/图谱检索使用，分词检索不使用）")
    retrieve_type: str = Field(default="hybrid", description="检索方式participle｜ semantic｜hybrid")
    # Whether to enable the evidence-graph channel for hybrid retrieval (only
    # effective for hybrid type); 1=enabled, 0=disabled, None=request-level fallback
    enable_graph_retrieval: Optional[int] = Field(default=None, ge=0, le=1, description="混合检索下是否启用证据图谱通道")
    rerank_mode: RerankMode | None = None
    rerank_weights: RerankWeights | None = None


class KnowledgeRetrievalConfig(BaseModel):
    """Knowledge base retrieval config (multiple KBs, each with its own config)"""
    knowledge_bases: List[KnowledgeBaseConfig] = Field(
        default_factory=list,
        description="关联的知识库列表，每个知识库有独立配置"
    )

    # Multi-KB fusion strategy
    merge_strategy: str = Field(
        default="weighted",
        description="多知识库结果融合策略: weighted | rrf | concat"
    )
    reranker_id: Optional[str] = Field(default=None, description="多知识库结果融合的模型ID")
    reranker_top_k: int = Field(default=10, ge=0, le=1024, description="多知识库结果融合的模型参数")
    rerank_mode: RerankMode | None = None
    rerank_weights: RerankWeights | None = None
    use_graph: bool = Field(default=False, description="是否使用图搜索")


class ToolConfig(BaseModel):
    """Tool config"""
    enabled: bool = Field(default=False, description="是否启用该工具")
    tool_id: Optional[str] = Field(default=None, description="工具ID")
    operation: Optional[str] = Field(default=None, description="工具特定配置")


class SkillConfig(BaseModel):
    """Skill config"""
    enabled: bool = Field(default=True, description="是否启用该技能")
    skill_ids: Optional[list[str]] = Field(default_factory=list, description="技能ID列表")
    all_skills: Optional[bool] = Field(default=False, description="是否允许访问所有技能")


# ---------- App Features ----------

class FileUploadConfig(BaseModel):
    """File upload config"""
    enabled: bool = Field(default=False)
    # Allowed transfer methods: local_file / remote_url; both allowed by default
    allowed_transfer_methods: List[str] = Field(
        default=["local_file", "remote_url"],
        description="允许的传输方式"
    )
    # Image files: PNG/JPG/JPEG/GIF/WEBP, max 20MB
    image_enabled: bool = Field(default=False)
    image_max_size_mb: int = Field(default=20)
    image_allowed_extensions: List[str] = Field(
        default=["png", "jpg", "jpeg"]
    )
    # Audio files: MP3/WAV/M4A/OGG/FLAC, max 50MB
    audio_enabled: bool = Field(default=False)
    audio_max_size_mb: int = Field(default=50)
    audio_allowed_extensions: List[str] = Field(
        default=["mp3", "wav", "m4a"]
    )
    # General files: PDF/DOCX/XLSX/TXT/CSV/JSON, max 100MB
    document_enabled: bool = Field(default=False)
    document_max_size_mb: int = Field(default=50)
    document_allowed_extensions: List[str] = Field(
        default=["pdf", "docx", "doc", "xlsx", "xls", "txt", "csv", "json", "md"]
    )
    document_image_recognition: bool = Field(
        default=False,
        description="是否识别文档中的图片（需配置视觉模型）"
    )
    # Video files: MP4/MOV/AVI/WebM, max 500MB
    video_enabled: bool = Field(default=False)
    video_max_size_mb: int = Field(default=50)
    video_allowed_extensions: List[str] = Field(
        default=["mp4"]
    )
    # Max number of files
    max_file_count: int = Field(default=5, ge=1)

    @field_validator("max_file_count")
    @classmethod
    def validate_max_file_count(cls, v: int) -> int:
        from ..config import current_settings
        if v > current_settings().max_file_count:
            raise ValueError(f"max_file_count 不能超过 {current_settings().max_file_count}")
        return v


class OpeningStatementConfig(BaseModel):
    """Conversation opening statement config"""
    enabled: bool = Field(default=False)
    statement: Optional[str] = Field(default=None, description="开场白内容")
    suggested_questions: List[str] = Field(default_factory=list, description="预设问题列表")


class SuggestedQuestionsConfig(BaseModel):
    """Follow-up question suggestions config"""
    enabled: bool = Field(default=False)


class TextToSpeechConfig(BaseModel):
    """Text-to-speech config"""
    enabled: bool = Field(default=False)
    voice: Optional[str] = Field(default=None, description="语音音色")
    language: Optional[str] = Field(default=None, description="语言")
    autoplay: bool = Field(default=False, description="是否自动播放")


class CitationConfig(BaseModel):
    """Citation and attribution config"""
    enabled: bool = Field(default=False)
    allow_download: bool = Field(default=False, description="是否允许下载引用文档")


class Citation(BaseModel):
    document_id: str
    doc_id: str
    file_name: str
    knowledge_id: str
    score: float
    download_url: Optional[str] = Field(default=None, description="引用文档下载链接（allow_download 开启时返回）")


class WebSearchConfig(BaseModel):
    """Web search config"""
    enabled: bool = Field(default=False)
    search_engine: Optional[str] = Field(default=None, description="搜索引擎")


class ContextEngineFeatureConfig(BaseModel):
    """Context engine feature config."""
    enabled: bool = Field(default=False, description="是否启用上下文引擎")
    cross_session_recent_enabled: bool = Field(
        default=False,
        description="是否注入同应用同用户其他会话的最近消息",
    )
    cross_session_recent_limit: int = Field(
        default=6,
        ge=1,
        le=50,
        description="跨会话注入的最近消息条数",
    )


class EmotionReplyConfig(BaseModel):
    """Emotion-aware reply config."""
    enabled: bool = Field(default=False, description="是否启用情绪感知回复")


class AppFeatures(BaseModel):
    """App feature flags config"""
    file_upload: FileUploadConfig = Field(default_factory=FileUploadConfig)
    opening_statement: OpeningStatementConfig = Field(default_factory=OpeningStatementConfig)
    suggested_questions_after_answer: SuggestedQuestionsConfig = Field(default_factory=SuggestedQuestionsConfig)
    text_to_speech: TextToSpeechConfig = Field(default_factory=TextToSpeechConfig)
    citation: CitationConfig = Field(default_factory=CitationConfig)
    web_search: WebSearchConfig = Field(default_factory=WebSearchConfig)
    context_engine: Optional[ContextEngineFeatureConfig] = Field(default=None, description="上下文引擎配置")
    emotion_reply: EmotionReplyConfig = Field( default_factory=EmotionReplyConfig, description="情绪感知回复配置")


class ToolOldConfig(BaseModel):
    """Tool config"""
    enabled: bool = Field(default=False, description="是否启用该工具")
    config: Optional[Dict[str, Any]] = Field(default_factory=dict, description="工具特定配置")


class MemoryConfig(BaseModel):
    """Memory config"""
    enabled: bool = Field(default=True, description="是否启用对话历史记忆")


class ModelParameters(BaseModel):
    """Model parameters config"""
    temperature: float = Field(default=0.7, ge=0.0, le=2.0, description="温度参数，控制输出的随机性")
    max_tokens: int = Field(default=2000, ge=1, le=32000, description="最大生成token数")
    top_p: float = Field(default=1.0, ge=0.0, le=1.0, description="核采样参数")
    frequency_penalty: float = Field(default=0.0, ge=-2.0, le=2.0, description="频率惩罚")
    presence_penalty: float = Field(default=0.0, ge=-2.0, le=2.0, description="存在惩罚")
    n: int = Field(default=1, ge=1, le=10, description="生成的回复数量")
    stop: Optional[List[str]] = Field(default=None, description="停止序列")
    deep_thinking: bool = Field(default=False, description="是否启用深度思考模式（需模型支持，如 DeepSeek-R1、QwQ 等）")
    thinking_budget_tokens: Optional[int] = Field(default=None, ge=1, le=131072, description="深度思考 token 预算（仅部分模型支持）")
    json_output: bool = Field(default=False, description="是否强制 JSON 格式输出（需模型支持 json_output 能力）")


class VariableDefinition(BaseModel):
    """Variable definition"""
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
    """Agent behavior config"""
    # Prompt config
    system_prompt: Optional[str] = Field(default=None, description="系统提示词，定义 Agent 的角色和行为准则")

    # Model config
    default_model_config_id: Optional[uuid.UUID] = Field(default=None, description="默认使用的模型配置ID")
    model_parameters: ModelParameters = Field(
        default_factory=ModelParameters,
        description="模型参数配置（temperature、max_tokens 等）"
    )

    # Knowledge base association
    knowledge_retrieval: Optional[KnowledgeRetrievalConfig] = Field(
        default=None,
        description="知识库检索配置"
    )

    # Memory config
    memory: MemoryConfig = Field(
        default_factory=lambda: MemoryConfig(enabled=False),
        description="对话历史记忆配置"
    )

    # Variable config
    variables: List[VariableDefinition] = Field(
        default_factory=list,
        description="Agent 可用的变量列表"
    )

    # Tool config
    tools: List[ToolConfig] = Field(
        default_factory=list,
        description="Agent 可用的工具列表"
    )

    # Skill config
    skills: Optional[SkillConfig] = Field(default_factory=dict, description="关联的技能列表")

    # Features
    features: Optional[AppFeatures] = Field(default=None, description="功能特性配置")


class AppCreate(BaseModel):
    name: str
    description: Optional[str] = None
    icon: Optional[str] = None
    icon_type: Optional[str] = None
    type: str = Field(pattern=r"^(agent|workflow|pure_workflow|multi_agent)$")
    visibility: Optional[str] = None
    status: Optional[str] = None
    tags: Optional[List[str]] = Field(default_factory=list)

    # only for type=agent
    agent_config: Optional[AgentConfigCreate] = None

    # only for type=multi_agent
    multi_agent_config: Optional[Dict[str, Any]] = None

    workflow_config: Optional[WorkflowConfigCreate] = None

    # Optional: ontology bound at creation (app-level binding, version-independent);
    # when omitted, falls back to the system default ontology for display.
    # Ignored by the community edition when the ontology module is absent.
    ontology_id: Optional[uuid.UUID] = Field(default=None, description="绑定的本体ID（可选）")


class AppUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    icon: Optional[str] = None
    icon_type: Optional[str] = None
    visibility: Optional[str] = None
    status: Optional[str] = None
    tags: Optional[List[str]] = None


class AgentConfigUpdate(BaseModel):
    """Update Agent behavior config"""
    # Prompt config
    system_prompt: Optional[str] = Field(default=None, description="系统提示词")

    # Model config
    default_model_config_id: Optional[uuid.UUID] = Field(default=None, description="默认模型配置ID")
    model_parameters: Optional[ModelParameters] = Field(default=None, description="模型参数配置")

    # Knowledge base association
    knowledge_retrieval: Optional[KnowledgeRetrievalConfig] = Field(
        default=None,
        description="知识库检索配置"
    )

    # Memory config
    memory: Optional[MemoryConfig] = Field(default=None, description="对话历史记忆配置")

    # Variable config
    variables: Optional[List[VariableDefinition]] = Field(default=None, description="变量列表")

    # Tool config
    tools: Optional[List[ToolConfig]] = Field(default_factory=list, description="工具列表")

    # Skill config
    skills: Optional[SkillConfig] = Field(default_factory=dict, description="关联的技能列表")

    # Features
    features: Optional[AppFeatures] = Field(default=None, description="功能特性配置")


# ---------- Output Schemas ----------

class AppOntologyInfo(BaseModel):
    """The app's currently effective ontology (provided by the enterprise ontology module via plugin; None in the community edition)."""

    bound: bool = Field(description="是否有有效的自定义绑定；false 表示走系统默认本体兜底")
    is_fallback_default: bool = Field(description="是否为系统默认本体兜底")
    ontology_id: Optional[uuid.UUID] = Field(default=None, description="当前生效本体ID")
    ontology_name: Optional[str] = Field(default=None, description="当前生效本体名称")
    scene_type_id: Optional[uuid.UUID] = Field(default=None, description="本体关联场景ID")
    scene_name: Optional[str] = Field(default=None, description="本体关联场景名称")
    custom_field_labels: List[str] = Field(default_factory=list, description="场景自定义字段展示名列表")
    emotion_count: int = Field(default=0, description="本体情绪策略（emotion_code）数量")


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
    is_shared: bool = False
    share_permission: Optional[str] = None
    source_workspace_name: Optional[str] = None  # source workspace name of the share (only set for shared apps)
    source_workspace_icon: Optional[str] = None  # source workspace icon of the share
    source_app_version: Optional[str] = None     # app version
    source_app_is_active: Optional[bool] = None  # whether the app is active
    share_id: Optional[uuid.UUID] = None         # share record ID (used when unsharing)
    shared_by: Optional[uuid.UUID] = None        # sharer user ID
    shared_by_name: Optional[str] = None         # sharer name
    shared_at: Optional[datetime.datetime] = None  # share time
    ontology: Optional[AppOntologyInfo] = None     # currently effective ontology (None in the community edition)
    created_at: datetime.datetime
    updated_at: datetime.datetime

    @field_serializer("created_at", when_used="json")
    def _serialize_created_at(self, dt: datetime.datetime):
        return to_timestamp_ms(dt)

    @field_serializer("updated_at", when_used="json")
    def _serialize_updated_at(self, dt: datetime.datetime):
        return to_timestamp_ms(dt)

    @field_serializer("shared_at", when_used="json")
    def _serialize_shared_at(self, dt: Optional[datetime.datetime]):
        return to_timestamp_ms(dt)


class AgentConfig(BaseModel):
    """Agent config output schema"""
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    app_id: uuid.UUID

    # Prompt
    system_prompt: Optional[str] = None

    # Model config
    default_model_config_id: Optional[uuid.UUID] = None
    model_parameters: ModelParameters = Field(default_factory=ModelParameters)

    # Knowledge base retrieval
    knowledge_retrieval: Optional[KnowledgeRetrievalConfig] = None

    # Memory config
    memory: MemoryConfig = Field(default_factory=lambda: MemoryConfig(enabled=True))

    # Variable config
    variables: List[VariableDefinition] = []

    # Tool config
    tools: Union[List[ToolConfig], Dict[str, ToolOldConfig]] = []

    skills: Optional[SkillConfig] = {}

    features: Optional[AppFeatures] = None

    is_active: bool
    created_at: datetime.datetime
    updated_at: datetime.datetime

    @field_validator("model_parameters", mode="before")
    @classmethod
    def validate_model_parameters(cls, v):
        """Handle a None value, returning the default ModelParameters"""
        if v is None:
            return ModelParameters()
        return v

    @field_validator("memory", mode="before")
    @classmethod
    def validate_memory(cls, v):
        """Handle a None value, returning the default MemoryConfig"""
        if v is None:
            return MemoryConfig(enabled=True)
        return v

    @field_validator("variables", mode="before")
    @classmethod
    def validate_variables(cls, v):
        """Handle a None value, returning an empty list"""
        if v is None:
            return []
        return v

    @field_validator("tools", mode="before")
    @classmethod
    def validate_tools(cls, v):
        """Handle a None value, returning an empty dict"""
        if v is None:
            return {}
        return v

    @field_validator("features", mode="before")
    @classmethod
    def validate_features(cls, v):
        """Handle a None value, returning the default AppFeatures"""
        if v is None:
            return AppFeatures()
        return v

    @field_serializer("created_at", when_used="json")
    def _serialize_created_at(self, dt: datetime.datetime):
        return to_timestamp_ms(dt)

    @field_serializer("updated_at", when_used="json")
    def _serialize_updated_at(self, dt: datetime.datetime):
        return to_timestamp_ms(dt)


class PublishRequest(BaseModel):
    """Publish app request"""
    version_name: str
    release_notes: str = Field(..., description="版本说明")
    name: str = Field(..., description="公开分享标题")
    icon: Optional[str] = Field(None, description="公开分享图标，未传则使用应用图标")


class AppDisplayInfo(BaseModel):
    """Publish display info"""
    name: str
    icon: Optional[str] = None


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
        """Handle the config field, parsing it into a dict when it is a string"""
        if isinstance(v, str):
            import json
            try:
                return json.loads(v)
            except json.JSONDecodeError:
                return {}
        return v if v is not None else {}

    @field_serializer("created_at", when_used="json")
    def _serialize_created_at(self, dt: datetime.datetime):
        return to_timestamp_ms(dt)

    @field_serializer("updated_at", when_used="json")
    def _serialize_updated_at(self, dt: datetime.datetime):
        return to_timestamp_ms(dt)

    @field_serializer("published_at", when_used="json")
    def _serialize_published_at(self, dt: datetime.datetime):
        return to_timestamp_ms(dt)


# ---------- App Copy Schema ----------

class CopyAppRequest(BaseModel):
    """Copy app request"""
    new_name: Optional[str] = Field(None, description="新应用名称，不填则使用原名称-副本")


# ---------- App Share Schemas ----------

class AppShareCreate(BaseModel):
    """App share request"""
    target_workspace_ids: List[uuid.UUID] = Field(..., description="目标工作空间ID列表")
    permission: str = Field(default="readonly", description="权限模式: readonly | editable")


class UpdateSharePermissionRequest(BaseModel):
    """Update share permission request"""
    permission: str = Field(..., description="新权限值: readonly | editable")


class AppShare(BaseModel):
    """App share output"""
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    source_app_id: uuid.UUID
    source_workspace_id: uuid.UUID
    target_workspace_id: uuid.UUID
    shared_by: uuid.UUID
    permission: str = "readonly"
    created_at: datetime.datetime
    updated_at: datetime.datetime

    # Related names (read from the relationship)
    source_app_name: Optional[str] = None
    source_app_type: Optional[str] = None
    source_app_version: Optional[str] = None
    source_app_is_active: Optional[bool] = None
    target_workspace_name: Optional[str] = None
    target_workspace_icon: Optional[str] = None
    # the source app's currently effective ontology (None in the community edition)
    ontology: Optional[AppOntologyInfo] = None

    @classmethod
    def model_validate(cls, obj, **kwargs):
        instance = super().model_validate(obj, **kwargs)
        if hasattr(obj, 'source_app') and obj.source_app:
            instance.source_app_name = obj.source_app.name
            instance.source_app_type = obj.source_app.type
            instance.source_app_is_active = obj.source_app.is_active
            release = obj.source_app.current_release
            instance.source_app_version = release.version_name if release else None
        if hasattr(obj, 'target_workspace') and obj.target_workspace:
            instance.target_workspace_name = obj.target_workspace.name
            instance.target_workspace_icon = obj.target_workspace.icon
        return instance

    @field_serializer("created_at", when_used="json")
    def _serialize_created_at(self, dt: datetime.datetime):
        return to_timestamp_ms(dt)

    @field_serializer("updated_at", when_used="json")
    def _serialize_updated_at(self, dt: datetime.datetime):
        return to_timestamp_ms(dt)


# ---------- Draft Run Schemas ----------

class AppChatRequest(BaseModel):
    message: Optional[str] = Field(default=None, description="用户消息，pure_workflow 可不传")
    conversation_id: Optional[str] = Field(default=None, description="会话 ID（用于多轮对话）")
    user_id: Optional[str] = Field(default=None, description="用户 ID（用于会话管理）")
    variables: Optional[Dict[str, Any]] = Field(default=None, description="自定义变量参数值")
    stream: bool = Field(default=False, description="是否流式返回")
    thinking: bool = Field(default=False, description="是否启用深度思考（需 Agent 配置支持）")
    files: List[FileInput] = Field(default_factory=list, description="附件列表（支持多文件）")
    version: Optional[uuid.UUID] = Field(default=None, description="指定发布版本 ID，不传则使用当前生效版本")

    @field_validator("message")
    @classmethod
    def _normalize_message(cls, value: Optional[str]) -> Optional[str]:
        """Strip surrounding whitespace uniformly; app-type rules decide whether empty messages are allowed."""
        return value.strip() if value is not None else None

    @field_validator("conversation_id")
    @classmethod
    def _validate_conversation_id(cls, value: Optional[str]) -> Optional[str]:
        """conversation_id must be a valid UUID.

        A non-UUID string would trigger a DataError at the DB layer, wrapped into a
        500. Validate up front here and return a 422 parameter error instead.
        """
        if value is None:
            return None

        normalized = value.strip()
        if not normalized:
            return None

        try:
            uuid.UUID(normalized)
        except (ValueError, AttributeError, TypeError) as exc:
            raise ValueError("conversation_id 必须是合法的 UUID") from exc
        return normalized


class HumanInterventionRequest(BaseModel):
    """App workflow human intervention request."""

    node_id: str = Field(..., description="人工介入节点 ID")
    action_id: str = Field(..., description="用户触发的操作 ID")
    form_data: Optional[Dict[str, Any]] = Field(default=None, description="用户填写的表单数据")

    @field_validator("node_id", "action_id")
    @classmethod
    def _normalize_required_identifier(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("标识不能为空或全为空白")
        return normalized


class DraftRunRequest(BaseModel):
    """Draft run request"""
    message: Optional[str] = Field(default=None, description="用户消息，pure_workflow 可不传")
    conversation_id: Optional[str] = Field(default=None, description="会话ID（用于多轮对话）")
    user_id: Optional[str] = Field(default=None, description="用户ID（用于会话管理）")
    variables: Optional[Dict[str, Any]] = Field(default=None, description="自定义变量参数值")
    stream: bool = Field(default=False, description="是否流式返回")
    files: Optional[List[FileInput]] = Field(default_factory=list, description="附件列表（支持多文件）")
    # Cluster (multi_agent) chat capabilities: default False = previous behavior;
    # Agent/workflow draft runs do not read these two fields
    thinking: bool = Field(default=False, description="是否启用深度思考（集群需 model_parameters.deep_thinking 同时开启）")
    web_search: bool = Field(default=False, description="是否启用联网搜索（集群需 features.web_search.enabled 同时开启，仅主管生效）")
    trigger_payload: Optional[Dict[str, Any]] = Field(default=None, description="触发器 payload，webhook 试运行时传入")
    from_message_id: Optional[str] = Field(default=None, description="从指定分支消息继续聊天（多分支场景）：以该消息对应 execution 的输出作为历史上下文起点")


class SuggestedQuestion(BaseModel):
    """Suggested question"""
    content: str


class CitationSource(BaseModel):
    """Citation source"""
    title: str
    content: str
    score: Optional[float] = None
    kb_id: Optional[str] = None


class DraftRunResponse(BaseModel):
    """Draft run response (non-streaming)"""
    message: str = Field(..., description="AI 回复消息")
    reasoning_content: Optional[str] = Field(default=None, description="深度思考内容")
    conversation_id: Optional[str] = Field(default=None, description="会话ID（用于多轮对话）")
    usage: Optional[Dict[str, Any]] = Field(default=None, description="Token 使用情况")
    elapsed_time: Optional[float] = Field(default=None, description="耗时（秒）")
    suggested_questions: List[str] = Field(default_factory=list, description="下一步建议问题")
    citations: List[Dict[str, Any]] = Field(default_factory=list, description="引用来源")
    audio_url: Optional[str] = Field(default=None, description="TTS 语音URL")
    audio_status: Optional[str] = Field(default=None, description="TTS 语音状态")

    @model_serializer(mode="wrap")
    def _serialize(self, handler):
        data = handler(self)
        if not data.get("reasoning_content"):
            data.pop("reasoning_content", None)
        return data


class OpeningResponse(BaseModel):
    """App opening statement response"""
    enabled: bool
    statement: Optional[str] = None
    suggested_questions: List[str] = Field(default_factory=list)


class DraftRunStreamChunk(BaseModel):
    """Draft run streaming response chunk"""
    event: str = Field(..., description="事件类型: start | message | end | error")
    data: Dict[str, Any] = Field(..., description="事件数据")


# ---------- Draft Run Compare Schemas ----------

class ModelCompareItem(BaseModel):
    """Single comparison model config"""
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


class NodeRunRequest(BaseModel):
    """Single node draft run request"""
    # Flat format, supporting:
    #   node variables:  {"node_id.var_name": value}
    #   system variables:  {"sys.message": "hello", "sys.files": [...]}
    inputs: Dict[str, Any] = Field(
        default_factory=dict,
        description="节点输入变量，格式: {'node_id.var_name': value} 或 {'sys.message': 'hello'}",
        examples=[{
            "sys.message": "帮我写一首诗",
            "sys.user_id": "user-123",
            "sys.files": [],
            "llm_node_abc.output": "上游输出内容",
        }]
    )
    stream: bool = Field(default=False, description="是否流式返回")


class NodeCacheUpdateRequest(BaseModel):
    """Update node cache content"""
    result_data: Optional[Dict[str, Any]] = Field(
        default=None,
        description="兼容旧模式：直接提交完整的缓存结果数据",
    )
    patches: List["NodeCacheVariablePatch"] = Field(
        default_factory=list,
        description="新模式：按变量 patch 缓存内容，只修改指定字段",
    )

    @model_validator(mode="after")
    def validate_payload(self):
        if self.result_data is None and not self.patches:
            raise ValueError("result_data 和 patches 至少需要传一个")
        if self.result_data is not None and self.patches:
            raise ValueError("result_data 和 patches 不能同时传")
        return self


class NodeCacheVariablePatch(BaseModel):
    """Patch node cache content per variable"""
    scope: str = Field(
        default="output",
        description="修改范围，只支持 output/input/process",
    )
    path: Optional[str] = Field(
        default=None,
        description="变量路径，支持 dot notation，例如 output 或 metadata.answer",
    )
    name: Optional[str] = Field(
        default=None,
        description="变量名，等价于 path 的简写",
    )
    value: Any = Field(
        ...,
        description="变量新值",
    )
    type: Optional[str] = Field(
        default=None,
        description="变量类型，仅作为前端透传和调试信息，后端以 value 为准",
    )

    @model_validator(mode="after")
    def validate_patch(self):
        if self.scope not in {"output", "input", "process"}:
            raise ValueError("scope 只支持 output、input、process")
        if bool(self.path) == bool(self.name):
            raise ValueError("path 和 name 必须二选一")
        return self


class NodeRerunRequest(BaseModel):
    """Re-run based on the latest single-node debug input"""
    invalidate_cache: bool = Field(default=False, description="重跑前是否先失效当前缓存")
    bypass_cache: bool = Field(default=False, description="重跑时是否跳过缓存直接真实执行")


class DraftRunCompareRequest(BaseModel):
    """Multi-model comparison draft run request"""
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
    files: Optional[List[FileInput]] = Field(default=None, description="附件列表（支持多文件）")
    parallel: bool = Field(True, description="是否并行执行")
    stream: bool = Field(False, description="是否流式返回")
    timeout: Optional[int] = Field(60, ge=10, le=300, description="超时时间（秒）")


class ModelRunResult(BaseModel):
    """Single model run result"""
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
    """Multi-model comparison response"""
    results: List[ModelRunResult]

    total_elapsed_time: float
    successful_count: int
    failed_count: int

    fastest_model: Optional[str] = None
    cheapest_model: Optional[str] = None


# ========== Message interaction feature schemas ==========

class MessageFeedbackRequest(BaseModel):
    """Message feedback request (like/dislike)."""

    feedback_type: str = Field(..., pattern="^(like|dislike)$", description="反馈类型: like/dislike")
    feedback_content: Optional[str] = Field(default=None, description="反馈内容（点踩时填写原因；默认 null）")


class MessageFeedbackResponse(BaseModel):
    """Message feedback response"""
    action: str = Field(..., description="操作: created/updated/cancelled")
    feedback_type: Optional[str] = Field(None, description="当前反馈类型")


class MessageFavoriteResponse(BaseModel):
    """Message favorite response"""
    action: str = Field(..., description="操作: created/cancelled")
    is_favorited: bool = Field(..., description="是否已收藏")


class MessageReportRequest(BaseModel):
    """Message report request (with selected-text feedback)"""
    report_type: str = Field(
        ...,
        description="举报类型: politics/porn/violence/fake/ad/quality/other"
    )
    report_reason: Optional[str] = Field(None, description="举报原因详细描述")
    text_start_offset: Optional[int] = Field(None, description="选中文本起始位置")
    text_end_offset: Optional[int] = Field(None, description="选中文本结束位置")
    selected_text: Optional[str] = Field(None, description="选中的违规文本片段")


class MessageReportResponse(BaseModel):
    """Message report response"""
    report_id: str = Field(..., description="举报ID")
    status: str = Field(..., description="状态: pending")


class ShareConversationRequest(BaseModel):
    """Share conversation request"""
    password: Optional[str] = Field(None, description="访问密码（可选）")
    expire_hours: Optional[int] = Field(None, ge=1, le=720, description="过期时长（小时）")
    allow_copy: bool = Field(True, description="是否允许复制内容")


class ShareConversationResponse(BaseModel):
    """Share conversation response"""
    share_id: str = Field(..., description="分享ID")
    share_uuid: str = Field(..., description="分享唯一标识")
    share_url: str = Field(..., description="分享链接")
    expire_at: Optional[int] = Field(None, description="过期时间（毫秒时间戳）")
    has_password: bool = Field(..., description="是否有密码")


class MessageVersion(BaseModel):
    """Message version"""
    message_id: uuid.UUID
    version: int
    is_current: bool
    content: str
    created_at: int


class RegenerateRequest(BaseModel):
    """Regenerate request"""
    variables: Optional[Dict[str, Any]] = Field(default=None, description="变量参数")
    web_search: bool = Field(default=False, description="是否启用网络搜索")
    memory: bool = Field(default=True, description="是否启用长期记忆")
    stream: bool = Field(default=False, description="是否流式返回")
    thinking: bool = Field(default=False, description="是否启用深度思考（需Agent配置支持）")


class RegenerateResponse(BaseModel):
    """Regenerate response"""
    message_id: uuid.UUID
    message: str
    version: int
    conversation_id: str


# ---------- Workflow regeneration ----------

class WorkflowRegenerateRequest(BaseModel):
    """Workflow regeneration request (re-execute the workflow for one assistant reply)"""
    stream: bool = Field(default=True, description="是否流式返回")
    variables: Optional[Dict[str, Any]] = Field(
        default=None, description="覆盖原输入快照中的开始节点变量；为空则完全沿用原输入"
    )


class WorkflowRegenerateResponse(BaseModel):
    """Workflow regeneration response (non-streaming)"""
    message_id: str = Field(..., description="新版本消息ID")
    message: str = Field(..., description="新生成的回复内容")
    version: int = Field(..., description="新版本号")
    conversation_id: str = Field(..., description="会话ID")
    execution_id: str = Field(..., description="本次重新生成产生的执行记录ID")
    citations: List[Dict[str, Any]] = Field(default_factory=list, description="引用来源")
    token_usage: Dict[str, Any] = Field(default_factory=dict, description="Token 使用情况")
    elapsed_time: Optional[float] = Field(default=None, description="耗时（秒）")


# ========== Message moderation feature schemas ==========

class ReportListRequest(BaseModel):
    """Report list query request"""
    status: Optional[str] = Field(None, description="状态过滤: pending/reviewed/resolved/rejected")
    severity: Optional[str] = Field(None, description="严重程度过滤: low/medium/high/critical")
    page: int = Field(default=1, ge=1, description="页码")
    pagesize: int = Field(default=20, ge=1, le=100, description="每页数量")


class ReportReviewRequest(BaseModel):
    """Report review request"""
    severity: str = Field(..., description="严重程度: low/medium/high/critical")
    action_taken: str = Field(
        ...,
        description="处理措施: no_action/warning/content_removed/user_banned"
    )
    review_note: Optional[str] = Field(None, description="审核备注")


class ReportStatisticsRequest(BaseModel):
    """Report statistics query request"""
    start_date: Optional[datetime.datetime] = Field(None, description="开始日期")
    end_date: Optional[datetime.datetime] = Field(None, description="结束日期")
