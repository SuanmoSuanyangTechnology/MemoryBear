"""App-domain schemas (monolith app_schema subset).

Only the contracts the definition-plane endpoints consume are ported; field
names, defaults, descriptions, and serializers must stay byte-compatible with
the host so responses remain wire-identical during coexistence.
"""

from __future__ import annotations

import datetime
import uuid
from enum import Enum, StrEnum
from typing import Any, Dict, List, Optional, Union

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_serializer,
    field_validator,
)

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
    LOCAL_FILE = "local_file"  # File already uploaded to the system
    REMOTE_URL = "remote_url"  # External URL


class FileInput(BaseModel):
    """Multimodal file input for app chat."""

    type: FileType = Field(..., description="文件类型: image/document/audio/video")
    transfer_method: TransferMethod = Field(..., description="传输方式: local_file/remote_url")
    upload_file_id: Optional[uuid.UUID] = Field(None, description="已上传文件 ID（local_file 时必填）")
    url: Optional[str] = Field(None, description="远程 HTTP(S) URL（remote_url 时必填）")
    file_type: Optional[str] = Field(None, description="具体文件格式（如 image/jpeg、audio/wav）")


# ---------- Input Schemas ----------

class KnowledgeBaseConfig(BaseModel):
    """Single knowledge-base config"""
    kb_id: str = Field(..., description="知识库ID")
    top_k: int = Field(default=3, ge=1, le=20, description="检索返回的文档数量")
    similarity_threshold: float = Field(default=0.7, ge=0.0, le=1.0, description="相似度阈值")
    # strategy: str = Field(default="hybrid", description="retrieval strategy: hybrid | bm25 | dense")
    # weight: float = Field(default=1.0, ge=0.0, le=1.0, description="knowledge-base weight (for multi-KB fusion)")
    vector_similarity_weight: float | None = Field(default=0.5, ge=0.0, le=1.0, description="向量相似度权重（语义/混合/图谱检索使用，分词检索不使用）")
    retrieve_type: str = Field(default="hybrid", description="检索方式participle｜ semantic｜hybrid")
    # Hybrid retrieval only: whether the evidence-graph channel is enabled;
    # 1=on, 0=off, None=falls back to the request-level default
    enable_graph_retrieval: Optional[int] = Field(default=None, ge=0, le=1, description="混合检索下是否启用证据图谱通道")
    rerank_mode: RerankMode | None = None
    rerank_weights: RerankWeights | None = None


class KnowledgeRetrievalConfig(BaseModel):
    """Knowledge-base retrieval config (multiple KBs, each independently configured)"""
    knowledge_bases: List[KnowledgeBaseConfig] = Field(
        default_factory=list,
        description="关联的知识库列表，每个知识库有独立配置"
    )

    # Multi-KB merge strategy
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
    # Document files: PDF/DOCX/XLSX/TXT/CSV/JSON, max 100MB
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
    # Maximum file count
    max_file_count: int = Field(default=5, ge=1)

    @field_validator("max_file_count")
    @classmethod
    def validate_max_file_count(cls, v: int) -> int:
        from ..config import current_settings
        max_file_count = current_settings().max_file_count
        if v > max_file_count:
            raise ValueError(f"max_file_count 不能超过 {max_file_count}")
        return v


class OpeningStatementConfig(BaseModel):
    """Conversation opening-statement config"""
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


class WebSearchConfig(BaseModel):
    """Web search config"""
    enabled: bool = Field(default=False)
    search_engine: Optional[str] = Field(default=None, description="搜索引擎")


class ContextEngineFeatureConfig(BaseModel):
    """Context-engine feature config."""
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
    """App features config"""
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

    # Optional ontology bound at creation (app-level, independent of releases); when omitted,
    # display falls back to the system default ontology.
    # Ignored in the community edition when the ontology module is absent.
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
    """Ontology currently in effect for the app (provided by the enterprise
    ontology module via plugin; None in the community edition)."""

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
    source_workspace_name: Optional[str] = None  # Source workspace name (shared apps only)
    source_workspace_icon: Optional[str] = None  # Source workspace icon
    source_app_version: Optional[str] = None     # App version
    source_app_is_active: Optional[bool] = None  # Whether the app is active
    share_id: Optional[uuid.UUID] = None         # Share record ID (used when cancelling the share)
    shared_by: Optional[uuid.UUID] = None        # Sharing user ID
    shared_by_name: Optional[str] = None         # Sharer name
    shared_at: Optional[datetime.datetime] = None  # Shared at
    ontology: Optional[AppOntologyInfo] = None     # Current ontology (None in the community edition)
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
        """Handle None: return the default ModelParameters"""
        if v is None:
            return ModelParameters()
        return v

    @field_validator("memory", mode="before")
    @classmethod
    def validate_memory(cls, v):
        """Handle None: return the default MemoryConfig"""
        if v is None:
            return MemoryConfig(enabled=True)
        return v

    @field_validator("variables", mode="before")
    @classmethod
    def validate_variables(cls, v):
        """Handle None: return an empty list"""
        if v is None:
            return []
        return v

    @field_validator("tools", mode="before")
    @classmethod
    def validate_tools(cls, v):
        """Handle None: return an empty dict"""
        if v is None:
            return {}
        return v

    @field_validator("features", mode="before")
    @classmethod
    def validate_features(cls, v):
        """Handle None: return the default AppFeatures"""
        if v is None:
            return AppFeatures()
        return v

    @field_serializer("created_at", when_used="json")
    def _serialize_created_at(self, dt: datetime.datetime):
        return to_timestamp_ms(dt)

    @field_serializer("updated_at", when_used="json")
    def _serialize_updated_at(self, dt: datetime.datetime):
        return to_timestamp_ms(dt)


class OpeningResponse(BaseModel):
    """App opening-statement response"""
    enabled: bool
    statement: Optional[str] = None
    suggested_questions: List[str] = Field(default_factory=list)


class PublishRequest(BaseModel):
    """Publish-app request"""
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
        """Handle the config field: parse a string value into a dict"""
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
    """Copy-app request"""
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
    """App share output

    Unlike the host, this schema carries no ``model_validate`` override: the
    service batch-loads the related app / workspace rows and constructs
    instances directly (host lazy-loads per row; same wire output).
    """
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
    # Source app's current ontology (None in the community edition)
    ontology: Optional[AppOntologyInfo] = None

    @field_serializer("created_at", when_used="json")
    def _serialize_created_at(self, dt: datetime.datetime):
        return to_timestamp_ms(dt)

    @field_serializer("updated_at", when_used="json")
    def _serialize_updated_at(self, dt: datetime.datetime):
        return to_timestamp_ms(dt)


# ---------- Message Interaction Schemas ----------

class MessageFeedbackRequest(BaseModel):
    """Message feedback request (like / dislike)."""

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
    """Message report request (with selected-feedback text)"""
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


class MessageVersion(BaseModel):
    """Message version"""
    message_id: uuid.UUID
    version: int
    is_current: bool
    content: str
    created_at: int


# ---------- Message Moderation Schemas ----------

class ReportReviewRequest(BaseModel):
    """Report review request"""
    severity: str = Field(..., description="严重程度: low/medium/high/critical")
    action_taken: str = Field(
        ...,
        description="处理措施: no_action/warning/content_removed/user_banned"
    )
    review_note: Optional[str] = Field(None, description="审核备注")


__all__ = [
    "AgentConfigCreate",
    "App",
    "AppCreate",
    "AppDisplayInfo",
    "AppFeatures",
    "AppOntologyInfo",
    "AppRelease",
    "AppShare",
    "AppShareCreate",
    "AppUpdate",
    "CitationConfig",
    "ContextEngineFeatureConfig",
    "CopyAppRequest",
    "EmotionReplyConfig",
    "FileUploadConfig",
    "KnowledgeBaseConfig",
    "KnowledgeRetrievalConfig",
    "MemoryConfig",
    "MessageFavoriteResponse",
    "MessageFeedbackRequest",
    "MessageFeedbackResponse",
    "MessageReportRequest",
    "MessageReportResponse",
    "MessageVersion",
    "ModelParameters",
    "OpeningStatementConfig",
    "PublishRequest",
    "ReportReviewRequest",
    "SkillConfig",
    "SuggestedQuestionsConfig",
    "TextToSpeechConfig",
    "ToolConfig",
    "ToolOldConfig",
    "UpdateSharePermissionRequest",
    "VariableDefinition",
    "WebSearchConfig",
]
