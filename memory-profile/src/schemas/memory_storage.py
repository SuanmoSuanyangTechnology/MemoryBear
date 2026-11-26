"""Memory-storage request/response models preserving legacy field validation."""

import uuid
from enum import Enum, StrEnum
from typing import Any, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from redbear_model.contracts import ModelType


class FileType(StrEnum):
    """文件类型枚举"""

    IMAGE = "image"
    DOCUMENT = "document"
    AUDIO = "audio"
    VIDEO = "video"

    @classmethod
    def trans(cls, value: str) -> "FileType":
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
    """文件传输方式枚举"""

    LOCAL_FILE = "local_file"  # 已上传到系统的文件
    REMOTE_URL = "remote_url"


class FileInput(BaseModel):
    """应用聊天的多模态文件输入。"""

    type: FileType = Field(..., description="文件类型: image/document/audio/video")
    transfer_method: TransferMethod = Field(..., description="传输方式: local_file/remote_url")
    upload_file_id: uuid.UUID | None = Field(None, description="已上传文件 ID（local_file 时必填）")
    url: str | None = Field(None, description="远程 HTTP(S) URL（remote_url 时必填）")
    file_type: str | None = Field(None, description="具体文件格式（如 image/jpeg、audio/wav）")
    name: str | None = Field(None, description="文件名")
    size: int | None = Field(None, ge=0, description="文件大小（字节，不能为负数）")

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

        # 保持 file_id 与省略 transfer_method 的旧客户端兼容性；新客户端应显式传递
        # transfer_method，避免依赖服务端推断。
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
        """兼容精确类别值及历史的类别前缀格式。"""
        if isinstance(value, FileType):
            return value
        try:
            return FileType.trans(value)
        except (AttributeError, RuntimeError, TypeError, ValueError) as exc:
            raise ValueError("type 仅支持 image、document、audio 或 video 及其子类型") from exc

    @field_validator("url")
    @classmethod
    def normalize_url(cls, value: str | None) -> str | None:
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


class WriteMessageItem(BaseModel):
    """写入记忆的单条消息"""

    role: str = Field(..., description="消息角色: user 或 assistant")
    content: str = Field(..., description="消息内容")
    dialog_at: str | None = Field(
        None,
        description="该条消息发生的绝对时间（ISO 8601 格式），不传则使用服务端当前时间",
    )
    files: list[FileInput] | None = Field(
        default=None, description="附带的文件列表（图片/文档/音频/视频）"
    )


class PilotRunInput(BaseModel):  # 试运行触发请求模型（QA 消息格式）
    """试运行请求 — QA 格式输入。

    与 /api/memory-storage/write 使用同一 messages 结构，避免两套输入解析逻辑。
    """

    config_id: uuid.UUID | int | str = Field(
        ..., description="萃取引擎配置 ID（唯一，支持 UUID、整数或字符串）"
    )
    messages: list[WriteMessageItem] = Field(
        ...,
        min_length=1,
        description="QA 格式消息列表，role 为 user/assistant，files 可选",
    )
    model_config = ConfigDict(populate_by_name=True, extra="forbid")


class TrialRunChatHistoryMessage(BaseModel):
    """萃取试运行对话的显式历史消息。"""

    role: Literal["user", "assistant"] = Field(..., description="消息角色")
    content: str = Field(..., min_length=1, description="文本内容")
    files: list[FileInput] = Field(
        default_factory=list,
        description="历史附件仅为请求格式兼容保留，试运行对话不会解析",
    )

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def validate_history_files(self):
        if self.role == "assistant" and self.files:
            raise ValueError("assistant 历史消息不能包含附件")
        return self


class TrialRunChatInput(BaseModel):
    """萃取试运行 AI 对话请求。"""

    config_id: uuid.UUID | int | str = Field(..., description="萃取引擎配置 ID")
    message: str = Field(..., min_length=1, description="当前用户消息")
    files: list[FileInput] = Field(default_factory=list, description="当前消息附件")
    history: list[TrialRunChatHistoryMessage] = Field(
        default_factory=list, description="显式对话上下文"
    )

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    @field_validator("message")
    @classmethod
    def validate_message(cls, value: str) -> str:
        message = value.strip()
        if not message:
            raise ValueError("消息内容不能为空")
        return message


class DeleteNodeRequest(BaseModel):
    """删除单个图节点请求"""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    element_id: str = Field(..., description="Neo4j elementId")
    end_user_id: str = Field(..., description="端用户 ID")


class FileUploadConfig(BaseModel):
    """文件上传配置"""

    enabled: bool = Field(default=False)
    # 允许的传输方式：local_file / remote_url，默认两种都允许
    allowed_transfer_methods: list[str] = Field(
        default=["local_file", "remote_url"], description="允许的传输方式"
    )
    # 图片文件：PNG/JPG/JPEG/GIF/WEBP，最大 20MB
    image_enabled: bool = Field(default=False)
    image_max_size_mb: int = Field(default=20)
    image_allowed_extensions: list[str] = Field(default=["png", "jpg", "jpeg"])
    # 语音文件：MP3/WAV/M4A/OGG/FLAC，最大 50MB
    audio_enabled: bool = Field(default=False)
    audio_max_size_mb: int = Field(default=50)
    audio_allowed_extensions: list[str] = Field(default=["mp3", "wav", "m4a"])
    # 通用文件：PDF/DOCX/XLSX/TXT/CSV/JSON，最大 100MB
    document_enabled: bool = Field(default=False)
    document_max_size_mb: int = Field(default=50)
    document_allowed_extensions: list[str] = Field(
        default=["pdf", "docx", "doc", "xlsx", "xls", "txt", "csv", "json", "md"]
    )
    document_image_recognition: bool = Field(
        default=False, description="是否识别文档中的图片（需配置视觉模型）"
    )
    # 视频文件：MP4/MOV/AVI/WebM，最大 500MB
    video_enabled: bool = Field(default=False)
    video_max_size_mb: int = Field(default=50)
    video_allowed_extensions: list[str] = Field(default=["mp4"])
    # 最大文件数量
    max_file_count: int = Field(default=5, ge=1)


class ModelInfo(BaseModel):
    """模型信息Schema（运行期壳；能力载体为契约 v2 三列）"""

    model_name: str = Field(..., description="模型名称")
    provider: str = Field(..., description="模型提供商")
    api_key: str | None = Field(
        None, description="API密钥；运行期非解密视图为 None（凭据不出模型服务）"
    )
    api_base: str | None = Field(None, description="API基础URL；空=使用提供商默认地址")
    model_type: ModelType = Field(..., description="模型类型")
    input_modalities: list[str] = Field(default_factory=list, description="输入模态（契约 v2）")
    output_modalities: list[str] = Field(default_factory=list, description="输出模态（契约 v2）")
    features: list[str] = Field(default_factory=list, description="能力特征（契约 v2）")
    tenant_id: str | None = Field(None, description="用量归属：租户ID")
    model_config_id: str | None = Field(None, description="用量归属：模型配置ID")
    channel_id: str | None = Field(None, description="用量归属：渠道ID")
    # 渠道换线计划随行透传（from_api_key 读取）；exclude 防序列化泄漏，拷贝丢失语义=退化单候选
    failover_plan: Any = Field(default=None, exclude=True, repr=False)


class HotMemoryTag(BaseModel):
    """工作空间热门记忆标签条目（``end_users.memory_tags`` 聚合结果）。"""

    name: str
    frequency: int
