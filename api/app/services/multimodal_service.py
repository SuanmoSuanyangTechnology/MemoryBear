"""
多模态文件处理服务

处理图片、文档等多模态文件，转换为 LLM 可用的格式

支持的 Provider:
- DashScope (通义千问): 支持 URL 格式
- Bedrock/Anthropic: 仅支持 base64 格式
- OpenAI: 支持 URL 和 base64 格式
"""
import base64
import csv
import io
import json
import re
import olefile
import struct
import zipfile
from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional

import PyPDF2
import chardet
import httpx
import magic
import openpyxl
import uuid
from docx import Document
from ipaddress import ip_address
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session
from urllib.parse import urlparse

from app.core.config import settings
from app.core.error_codes import BizCode
from app.core.exceptions import BusinessException
from app.core.logging_config import get_business_logger
from app.core.utils.text_sanitize import sanitize_text
from app.models.file_metadata_model import FileMetadata
from app.models.models_model import ModelCapability
from app.schemas.app_schema import FileInput, FileType, FileUploadConfig, TransferMethod
from app.schemas.model_schema import ModelInfo
from app.services.audio_transcription_service import AudioTranscriptionService
from app.services.file_content_service import extract_permanent_file_id
from app.services.file_storage_service import FileStorageService

logger = get_business_logger()

TEXT_MIME = ['text/plain', 'text/x-markdown']
PDF_MIME = ['application/pdf']
DOC_MIME = [
    'application/msword',
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
]
XLSX_MIME = [
    'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    'application/vnd.ms-excel',
]
CSV_MIME = ['text/csv', 'application/csv']
JSON_MIME = ['application/json']

OPENAI_AUDIO_FORMAT_ALIASES = {
    "mpeg": "mp3",
    "x-mpeg": "mp3",
    "x-wav": "wav",
    "wave": "wav",
    "x-m4a": "m4a",
    "oga": "ogg",
}


def _detect_media_type(content: bytes, declared_type: str | None, fallback: str) -> str:
    """优先根据内容识别 MIME 类型，并在识别失败时使用受控回退值。"""
    try:
        detected_type = magic.from_buffer(content, mime=True)
    except Exception:
        detected_type = None

    if detected_type and detected_type != "application/octet-stream":
        return detected_type.split(";", 1)[0].strip()
    if declared_type:
        return declared_type.split(";", 1)[0].strip()
    return fallback


def _build_data_url(content: bytes, declared_type: str | None, fallback: str) -> str:
    """构造模型 API 支持的 RFC 2397 Data URL。"""
    media_type = _detect_media_type(content, declared_type, fallback)
    encoded_content = base64.b64encode(content).decode("utf-8")
    return f"data:{media_type};base64,{encoded_content}"


def _build_audio_data_url(content: bytes) -> str:
    """构造 DashScope/OpenAI 音频输入兼容的 Data URL。"""
    encoded_content = base64.b64encode(content).decode("utf-8")
    return f"data:;base64,{encoded_content}"


MAX_HISTORY_FILE_TEXT_CHARS = 100_000

# 模型侧不可达的附件需编码成 Base64/Data URL 内联出站。单文件上限必须与应用
# 文件上传配置（FileUploadConfig 各类型 *_max_size_mb）保持一致，否则用户能
# 上传成功的附件会在模型侧被静默丢弃；未显式传入配置的调用方（记忆/工作流）
# 直接沿用 FileUploadConfig 的默认值，保证只有一个数据源。
_DEFAULT_UPLOAD_CONFIG = FileUploadConfig()
DEFAULT_INLINE_MAX_SIZE: Dict[str, int] = {
    FileType.IMAGE: _DEFAULT_UPLOAD_CONFIG.image_max_size_mb * 1024 * 1024,
    FileType.AUDIO: _DEFAULT_UPLOAD_CONFIG.audio_max_size_mb * 1024 * 1024,
}
# 单请求内联总量按本次涉及类型的单文件上限之和派生，避免多个大附件同时编码
# 造成内存峰值，不单独暴露配置项。
_FILE_TYPE_LABELS = {
    FileType.IMAGE: "图片",
    FileType.AUDIO: "音频",
    FileType.VIDEO: "视频",
    FileType.DOCUMENT: "文档",
}


def _resolve_inline_limits(config: Optional[Dict[str, Any]]) -> Dict[str, int]:
    """从应用 file_upload 配置解析各类型内联单文件上限（原始字节）。"""
    limits = dict(DEFAULT_INLINE_MAX_SIZE)
    if isinstance(config, dict):
        mapping = (
            (FileType.IMAGE, "image_max_size_mb"),
            (FileType.AUDIO, "audio_max_size_mb"),
        )
        for file_type, config_key in mapping:
            value = config.get(config_key)
            if value:
                limits[file_type] = int(float(value) * 1024 * 1024)
    return limits


def serialize_file_reference(
        file: FileInput,
        *,
        name: str | None = None,
        size: int | None = None,
) -> Dict[str, Any]:
    """将附件序列化为可安全持久化、可重新水合的引用。"""
    is_local = file.transfer_method == TransferMethod.LOCAL_FILE
    return {
        "type": file.type,
        "transfer_method": file.transfer_method.value,
        "upload_file_id": str(file.upload_file_id) if is_local and file.upload_file_id else None,
        # 私有 URL 不能写入消息元数据；本地文件必须由 ID 重新做权限校验后读取。
        "url": None if is_local else file.url,
        "name": name if name is not None else file.name,
        "size": size if size is not None else file.size,
        "file_type": file.file_type,
    }


def _built_permanent_file_id(url: Any, *, any_host: bool = False) -> str | None:
    """识别本服务自铸的永久下载 URL，并取出其中的文件 ID。

    判定实现已抽到公共文件内容服务（file_content_service.extract_permanent_file_id），
    与知识库图片检索等消费点共用同一套口径，避免私有化部署下"URL 是否属于本服务"
    的判断在各处慢慢分叉。
    """
    return extract_permanent_file_id(url, trust_any_host=any_host)


def _model_cannot_reach_url(url: Any) -> bool:
    """判断模型服务端能否访问该 URL。

    仅按主机名/字面量 IP 判定，不做 DNS 解析：缺省主机名、localhost、回环、
    私有网段、链路本地/保留地址，以及本平台自身域名（FILE_LOCAL_SERVER_URL
    指向 localhost 或内网）都视为模型不可达，此时附件必须改以内联字节出站。
    公网域名或公网 IP 视为可达，保留原生 URL 直传能力。
    """
    if not isinstance(url, str) or not url.strip():
        return True

    host = urlparse(url.strip()).hostname
    if not host:
        return True

    host = host.strip("[]").lower()
    if host == "localhost" or host.endswith(".localhost"):
        return True

    own_host = urlparse(settings.FILE_LOCAL_SERVER_URL or "").hostname
    if own_host and host == own_host.strip("[]").lower():
        return True

    try:
        parsed_ip = ip_address(host)
    except ValueError:
        # 域名不做解析：解析结果随部署环境变化，公网域名按可达处理。
        return False

    return bool(
        parsed_ip.is_loopback
        or parsed_ip.is_private
        or parsed_ip.is_link_local
        or parsed_ip.is_reserved
        or parsed_ip.is_unspecified
    )


def _requires_inline_bearer(file: FileInput) -> bool:
    """判断该附件是否必须由本服务读取字节后随请求出站。

    命中任一条件即内联：
    1. 显式 local_file 附件；
    2. URL 是本服务自铸的永久下载地址（无论 transfer_method 标注成什么）；
    3. URL 模型侧不可达（内网/回环），且持文件 ID 可绕过 HTTP 自取字节。
    """
    if file.transfer_method == TransferMethod.LOCAL_FILE:
        return True
    if not file.url:
        return False
    if _built_permanent_file_id(file.url):
        return True
    return bool(file.upload_file_id) and _model_cannot_reach_url(file.url)


def deserialize_file_reference(reference: Dict[str, Any]) -> FileInput:
    """从当前或旧版消息元数据恢复 FileInput，且不将私有 URL 作为远程文件使用。"""
    url = reference.get("url")
    upload_file_id = reference.get("upload_file_id")
    transfer_method_value = reference.get("transfer_method")

    try:
        transfer_method = TransferMethod(transfer_method_value) if transfer_method_value else None
    except ValueError:
        transfer_method = None

    if transfer_method is None:
        if upload_file_id:
            transfer_method = TransferMethod.LOCAL_FILE
        else:
            legacy_file_id = _built_permanent_file_id(url, any_host=True)
            if legacy_file_id:
                upload_file_id = legacy_file_id
                transfer_method = TransferMethod.LOCAL_FILE
            else:
                transfer_method = TransferMethod.REMOTE_URL

    payload: Dict[str, Any] = {
        "type": reference.get("type", FileType.DOCUMENT),
        "transfer_method": transfer_method,
        "file_type": reference.get("file_type"),
        "name": reference.get("name"),
        "size": reference.get("size"),
    }
    if transfer_method == TransferMethod.LOCAL_FILE:
        payload["upload_file_id"] = upload_file_id
    else:
        payload["url"] = url
    return FileInput(**payload)


def sanitize_processed_files_for_history(processed_files: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """移除内联二进制内容，并保留有界文本或附件存在提示。"""
    def is_data_url(value: Any) -> bool:
        return isinstance(value, str) and value.startswith("data:")

    def binary_history_projection(part: Dict[str, Any]) -> Dict[str, Any]:
        source = part.get("source")
        if isinstance(source, dict) and source.get("type") == "base64" and source.get("media_type") == "text/plain":
            try:
                text = base64.b64decode(source.get("data", "")).decode("utf-8", errors="replace")
                return {"type": "text", "text": text[:MAX_HISTORY_FILE_TEXT_CHARS]}
            except (TypeError, ValueError):
                pass

        part_type = str(part.get("type") or "附件")
        return {
            "type": "text",
            "text": f"[{part_type} 附件已在原始请求中处理；二进制内容未保存到会话历史]",
        }

    sanitized_files: List[Dict[str, Any]] = []
    inline_binary_count = 0
    for part in processed_files:
        if not isinstance(part, dict):
            continue

        source = part.get("source")
        input_audio = part.get("input_audio")
        image_url = part.get("image_url")
        audio_url = part.get("audio_url")
        video_url = part.get("video_url")
        contains_inline_binary = (
            isinstance(source, dict) and source.get("type") == "base64"
        ) or (
            isinstance(input_audio, dict) and is_data_url(input_audio.get("data"))
        ) or (
            isinstance(image_url, dict) and is_data_url(image_url.get("url"))
        ) or (
            isinstance(audio_url, dict) and is_data_url(audio_url.get("url"))
        ) or (
            isinstance(video_url, dict) and is_data_url(video_url.get("url"))
        ) or is_data_url(part.get("image")) or is_data_url(part.get("audio")) or is_data_url(part.get("video"))

        if contains_inline_binary:
            inline_binary_count += 1
            sanitized_files.append(binary_history_projection(part))
        else:
            sanitized_files.append(part)

    if inline_binary_count:
        logger.info("已从会话历史移除 %s 个内联附件内容", inline_binary_count)
    return sanitized_files


class MultimodalFormatStrategy(ABC):
    """多模态格式策略基类"""

    def __init__(self, file: FileInput):
        self.file = file

    @abstractmethod
    async def format_image(self, url: str | None, content: bytes | None = None) -> tuple[bool, Dict[str, Any]]:
        """格式化图片"""
        pass

    @abstractmethod
    async def format_document(self, file_name: str, text: str) -> tuple[bool, Dict[str, Any]]:
        """格式化文档"""
        pass

    @abstractmethod
    async def format_audio(
            self,
            file_type: str,
            url: str | None,
            content: bytes | None = None,
    ) -> tuple[bool, Dict[str, Any]]:
        """格式化音频"""
        pass

    @abstractmethod
    async def format_video(self, url: str | None, content: bytes | None = None) -> tuple[bool, Dict[str, Any]]:
        """格式化视频"""
        pass


class DashScopeFormatStrategy(MultimodalFormatStrategy):
    """通义千问策略"""

    async def format_image(self, url: str | None, content: bytes | None = None) -> tuple[bool, Dict[str, Any]]:
        """通义千问图片格式：本地内容使用 Data URL，远程内容保留 URL。"""
        image_source = _build_data_url(content, self.file.file_type, "image/jpeg") if content is not None else url
        if not image_source:
            return False, {"type": "text", "text": "[图片文件缺少可用内容]"}
        return True, {
            "type": "image",
            "image": image_source,
        }

    async def format_document(self, file_name: str, text: str) -> tuple[bool, Dict[str, Any]]:
        """通义千问文档格式"""
        return True, {
            "type": "text",
            "text": f"<document name=\"{file_name}\">\n文档内容：\n{text}\n</document>",
        }

    async def format_audio(
            self,
            file_type: str,
            url: str | None,
            content: bytes | None = None,
            transcription: Optional[str] = None,
    ) -> tuple[bool, Dict[str, Any]]:
        """
        通义千问音频格式。

        公网模型不能访问私有 OSS 时，本地音频以 Data URL 主动随请求传出；
        DashScope HTTP API 支持该格式。
        """
        if transcription:
            return True, {
                "type": "text",
                "text": f"[音频转录]\n{transcription}",
            }

        audio_source = _build_audio_data_url(content) if content is not None else url
        if not audio_source:
            return False, {"type": "text", "text": "[音频文件缺少可用内容]"}
        return True, {
            "type": "audio",
            "audio": audio_source,
        }

    async def format_video(self, url: str | None, content: bytes | None = None) -> tuple[bool, Dict[str, Any]]:
        """通义千问视频格式；本地视频需后续接入 provider Files API。"""
        if content is not None:
            return False, {
                "type": "text",
                "text": "[视频文件无法通过当前模型接口安全传输，请配置 provider Files API 后重试]",
            }
        if not url:
            return False, {"type": "text", "text": "[视频文件缺少可用 URL]"}
        return True, {
            "type": "video",
            "video": url,
        }


class BedrockFormatStrategy(MultimodalFormatStrategy):
    """Bedrock/Anthropic 策略"""

    async def format_image(self, url: str | None, content: bytes | None = None) -> tuple[bool, Dict[str, Any]]:
        """
        Bedrock/Anthropic 格式: base64 编码
        {"type": "image", "source": {"type": "base64", "media_type": "...", "data": "..."}}
        """
        if content is None:
            if not url:
                return False, {"type": "text", "text": "[图片文件缺少可用内容]"}
            logger.info(f"下载并编码图片: {url}")
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(url, follow_redirects=True)
                response.raise_for_status()
                content = response.content
                self.file.set_content(content)

        media_type = _detect_media_type(content, self.file.file_type, "image/jpeg")
        if not media_type.startswith("image/"):
            media_type = "image/jpeg"
        base64_data = base64.b64encode(content).decode("utf-8")

        logger.info(f"图片编码完成: media_type={media_type}, size={len(base64_data)}")

        return True, {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": media_type,
                "data": base64_data,
            },
        }

    async def format_document(self, file_name: str, text: str) -> tuple[bool, Dict[str, Any]]:
        """Bedrock/Anthropic 文档格式（需要 base64 编码）"""
        text = f"文档内容：\n{text}\n"
        text_bytes = text.encode("utf-8")
        base64_text = base64.b64encode(text_bytes).decode("utf-8")

        return True, {
            "type": "document",
            "source": {
                "type": "base64",
                "media_type": "text/plain",
                "data": base64_text,
            },
        }

    async def format_audio(
            self,
            file_type: str,
            url: str | None,
            content: bytes | None = None,
            transcription: Optional[str] = None,
    ) -> tuple[bool, Dict[str, Any]]:
        """Bedrock/Anthropic 不支持原生音频，必须转录为文本。"""
        if transcription:
            return True, {
                "type": "text",
                "text": f"[音频转录]\n{transcription}",
            }
        return False, {
            "type": "text",
            "text": "[音频文件：Bedrock 不支持原生音频，请启用音频转文本功能]",
        }

    async def format_video(self, url: str | None, content: bytes | None = None) -> tuple[bool, Dict[str, Any]]:
        """Bedrock/Anthropic 视频格式"""
        return False, {
            "type": "text",
            "text": "[视频文件：当前 provider 暂不支持]",
        }


class OpenAIFormatStrategy(MultimodalFormatStrategy):
    """OpenAI 兼容策略"""

    async def format_image(self, url: str | None, content: bytes | None = None) -> tuple[bool, Dict[str, Any]]:
        """本地图片使用 Data URL，避免模型侧回拉内网 URL。"""
        image_source = _build_data_url(content, self.file.file_type, "image/jpeg") if content is not None else url
        if not image_source:
            return False, {"type": "text", "text": "[图片文件缺少可用内容]"}
        return True, {
            "type": "image_url",
            "image_url": {
                "url": image_source,
            },
        }

    async def format_document(self, file_name: str, text: str) -> tuple[bool, Dict[str, Any]]:
        """OpenAI 文档格式"""
        return True, {
            "type": "text",
            "text": f"<document name=\"{file_name}\">\n文档内容：\n{text}\n</document>",
        }

    async def format_audio(
            self,
            file_type: str,
            url: str | None,
            content: bytes | None = None,
            transcription: Optional[str] = None,
    ) -> tuple[bool, Dict[str, Any]]:
        """OpenAI 音频格式。"""
        if transcription:
            return True, {
                "type": "text",
                "text": f"[音频转录]\n{transcription}",
            }

        try:
            audio_data = content
            if audio_data is None:
                if not url:
                    raise ValueError("音频文件缺少可用内容")
                async with httpx.AsyncClient(timeout=30.0) as client:
                    response = await client.get(url, follow_redirects=True)
                    response.raise_for_status()
                    audio_data = response.content
                    self.file.set_content(audio_data)

            file_ext = file_type.split("/")[-1] if file_type and "/" in file_type else None
            if not file_ext:
                content_type = _detect_media_type(audio_data, self.file.file_type, "audio/wav")
                file_ext = content_type.split("/")[-1].split(";", 1)[0].strip() if "/" in content_type else None
            if not file_ext and url:
                file_ext = url.split("?", 1)[0].rsplit(".", 1)[-1].lower() or None
            file_ext = "wav" if not file_ext else file_ext.lower()
            file_ext = OPENAI_AUDIO_FORMAT_ALIASES.get(file_ext, file_ext)

            return True, {
                "type": "input_audio",
                "input_audio": {
                    "data": _build_audio_data_url(audio_data),
                    "format": file_ext,
                },
            }
        except Exception as e:
            logger.error(f"下载音频失败: {e}")
            return False, {
                "type": "text",
                "text": f"[音频处理失败: {str(e)}]",
            }

    async def format_video(self, url: str | None, content: bytes | None = None) -> tuple[bool, Dict[str, Any]]:
        """OpenAI 视频格式；本地视频需后续接入 provider Files API。"""
        if content is not None:
            return False, {
                "type": "text",
                "text": "[视频文件无法通过当前模型接口安全传输，请配置 provider Files API 后重试]",
            }
        if not url:
            return False, {"type": "text", "text": "[视频文件缺少可用 URL]"}
        return True, {
            "type": "video_url",
            "video_url": {
                "url": url,
            },
        }


# Provider 到策略的映射
PROVIDER_STRATEGIES = {
    # dashscope 全量模型已统一 OpenAI 兼容协议（ChatTongyi 原生协议退役，见
    # core/models/base.py:get_provider_llm_class）。原生的
    # {"type": "image", "image": url} 会被兼容端点以 400 invalid_value 拒绝，
    # 必须产出 OpenAI 格式（type=text/image_url/video_url）。
    "dashscope": OpenAIFormatStrategy,
    "bedrock": BedrockFormatStrategy,
    "anthropic": BedrockFormatStrategy,
    "openai": OpenAIFormatStrategy,
    "volcano": OpenAIFormatStrategy,
    "speedbear": OpenAIFormatStrategy,
    "gpustack": OpenAIFormatStrategy,
}


class MultimodalService:
    """
    Service for handling multimodal file processing.

    Attributes:
        db (Session): Database session.
        model_api_key (str): API key for the model provider.
        provider (str): Name of the model provider.
        is_omni (bool): Indicates whether the model supports full multimodal capability.
        capability (list): Capability configuration of the model.
        audio_api_key (str | None): API key used for audio transcription.
        enable_audio_transcription (bool): Whether audio transcription is enabled.
    """

    def __init__(
            self,
            db: Session | AsyncSession,
            api_config: ModelInfo | None = None,
            audio_api_key: Optional[str] = None,
            enable_audio_transcription: bool = False,
    ):
        """
        Initialize the multimodal service.

        Args:
            db (Session): Database session.
            api_config (ModelApiKey | None): Model API configuration.
            audio_api_key (str | None): API key for audio transcription.
            enable_audio_transcription (bool): Enable audio transcription.
        """
        self.db = db
        self.api_config = api_config
        if self.api_config is not None:
            self.model_api_key = api_config.api_key
            self.provider = api_config.provider.lower()
            self.is_omni = api_config.is_omni
            self.capability = api_config.capability
        self.audio_api_key = audio_api_key
        self.enable_audio_transcription = enable_audio_transcription

    def _uses_async_session(self) -> bool:
        return isinstance(self.db, AsyncSession)

    async def _get_file_metadata(
            self,
            file_id: uuid.UUID | None,
            *,
            completed_only: bool = False,
    ) -> FileMetadata | None:
        if file_id is None:
            return None

        if self._uses_async_session():
            stmt = select(FileMetadata).where(FileMetadata.id == file_id)
            if completed_only:
                stmt = stmt.where(FileMetadata.status == "completed")
            return (await self.db.execute(stmt)).scalar_one_or_none()

        query = self.db.query(FileMetadata).filter(FileMetadata.id == file_id)
        if completed_only:
            query = query.filter(FileMetadata.status == "completed")
        return query.first()

    async def _get_local_file_metadata(
            self,
            file_id: uuid.UUID | None,
            workspace_id: uuid.UUID | str | None,
    ) -> FileMetadata:
        """按当前工作空间或租户范围查询可供模型处理的本地附件。"""
        if file_id is None:
            raise BusinessException("本地文件 ID 不能为空", BizCode.FILE_NOT_FOUND)
        if self.db is None:
            raise BusinessException("缺少读取本地文件所需的数据库上下文", BizCode.FILE_READ_ERROR)

        tenant_id = getattr(self.api_config, "tenant_id", None) if self.api_config else None
        if workspace_id is None and tenant_id is None:
            raise BusinessException(
                "缺少工作空间或租户上下文，无法读取本地文件",
                BizCode.FILE_NOT_FOUND,
            )

        if self._uses_async_session():
            stmt = select(FileMetadata).where(
                FileMetadata.id == file_id,
                FileMetadata.status == "completed",
            )
            if workspace_id is not None:
                stmt = stmt.where(FileMetadata.workspace_id == workspace_id)
            if tenant_id is not None:
                stmt = stmt.where(FileMetadata.tenant_id == tenant_id)
            metadata = (await self.db.execute(stmt)).scalar_one_or_none()
        else:
            query = self.db.query(FileMetadata).filter(
                FileMetadata.id == file_id,
                FileMetadata.status == "completed",
            )
            if workspace_id is not None:
                query = query.filter(FileMetadata.workspace_id == workspace_id)
            if tenant_id is not None:
                query = query.filter(FileMetadata.tenant_id == tenant_id)
            metadata = query.first()

        if metadata is None:
            # 对调用方统一返回不可用，避免泄露其他工作空间的文件存在性。
            raise BusinessException("文件不存在、未完成或无权访问", BizCode.FILE_NOT_FOUND)
        return metadata

    async def _hydrate_local_file(
            self,
            file: FileInput,
            workspace_id: uuid.UUID | str | None,
    ) -> int:
        """读取模型侧不可达的附件字节，供出站适配器内联使用。

        - 持 upload_file_id：走 workspace/tenant 受限查询 + 存储后端直读，
          除字节外还回填文件名、大小与 MIME，供感知记忆等下游使用；
        - 否则仅识别本服务自铸的永久 URL，由本服务自己 HTTP 取回，
          不把该 URL 交给模型，也不接受任意远程 URL（避免 SSRF）。
        """
        existing_content = file.get_content()
        if existing_content is not None:
            if len(existing_content) > settings.MAX_FILE_SIZE:
                raise BusinessException("文件大小不在允许范围内", BizCode.FILE_READ_ERROR)
            return len(existing_content)

        if file.upload_file_id:
            metadata = await self._get_local_file_metadata(file.upload_file_id, workspace_id)
            file_size = int(metadata.file_size or 0)
            if file_size <= 0 or file_size > settings.MAX_FILE_SIZE:
                raise BusinessException("文件大小不在允许范围内", BizCode.FILE_READ_ERROR)

            content = await FileStorageService().download_file(metadata.file_key)
            if len(content) != file_size:
                logger.error(
                    "本地文件大小与元数据不一致",
                    extra={"file_id": str(metadata.id), "expected_size": file_size, "actual_size": len(content)},
                )
                raise BusinessException("文件读取失败", BizCode.FILE_READ_ERROR)

            file.set_content(content)
            file.name = metadata.file_name
            file.size = file_size
            file.file_type = _detect_media_type(
                content,
                metadata.content_type or file.file_type,
                "application/octet-stream",
            )
            return file_size

        if not _built_permanent_file_id(file.url):
            # 非本服务铸出的不可达 URL：不自取，交由策略层降级，避免 SSRF。
            raise BusinessException("附件 URL 模型侧不可达且无法定位文件", BizCode.FILE_READ_ERROR)

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(file.url, follow_redirects=True)
                response.raise_for_status()
                content = response.content
        except Exception as exc:
            logger.error(f"读取本服务附件 URL 失败: {exc}", exc_info=True)
            raise BusinessException("文件读取失败", BizCode.FILE_READ_ERROR)

        if len(content) > settings.MAX_FILE_SIZE:
            raise BusinessException("文件大小不在允许范围内", BizCode.FILE_READ_ERROR)

        file.set_content(content)
        return len(content)

    async def process_files(
            self,
            files: Optional[List[FileInput]],
            workspace_id: uuid.UUID = None,
            document_image_recognition: bool = False,
            include_processing_errors: bool = True,
            file_upload_config: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """
        处理文件列表，返回 LLM 可用的格式

        Args:
            files: 文件输入列表
            include_processing_errors: 是否把失败文件的占位文本加入结果；为 False 时跳过失败文件并继续处理后续文件
            file_upload_config: 应用文件上传配置（FileUploadConfig.model_dump），
                内联大小上限按其中各类型 *_max_size_mb 生效

        Returns:
            List[Dict]: LLM 可用的内容格式列表（根据 provider 返回不同格式）
        """
        if not files:
            return []

        # 内联单文件上限取应用配置，保证"能上传就能进模型请求"；总量按本次涉及类型
        # 中最大单文件限额的 2 倍派生（与历史口径一致），兜住多附件同时编码的内存峰值。
        inline_limits = _resolve_inline_limits(file_upload_config)
        present_limits = [
            inline_limits[f.type] for f in files if f.type in inline_limits
        ]
        inline_total_limit = max(present_limits, default=0) * 2

        # 获取对应的策略：已统一走 OpenAI 兼容协议的 provider（dashscope/volcano/
        # openai/minimax/...）都必须产出 OpenAI 多模态格式。is_omni 不再参与判定——
        # ChatTongyi 原生协议退役后，原生 {"type": "image", ...} 只会被兼容端点以
        # 400 invalid_value 拒绝（见 core/models/base.py:get_provider_llm_class）。
        strategy_class = PROVIDER_STRATEGIES.get(self.provider)
        if not strategy_class:
            logger.warning(
                f"未找到 provider '{self.provider}' 的策略，使用 OpenAI 兼容格式"
            )
            strategy_class = OpenAIFormatStrategy

        result = []
        inline_payload_size = 0
        for idx, file in enumerate(files):
            # 模型侧不可达的附件只能由本服务读取字节后随请求出站，绝不能把
            # FILE_LOCAL_SERVER_URL 或私有 OSS URL 交给模型回拉；公网可达的
            # URL 保持原样直传，不额外搬运字节。
            if _requires_inline_bearer(file):
                # 当前 URL 型视频 provider 没有通用安全内联协议；先完成归属校验，
                # 再在下载大对象前明确拒绝，等待 provider Files API 适配。
                if file.type == FileType.VIDEO:
                    if file.upload_file_id:
                        await self._get_local_file_metadata(file.upload_file_id, workspace_id)
                    if "video" in self.capability and include_processing_errors:
                        result.append({
                            "type": "text",
                            "text": "[视频文件无法通过当前模型接口安全传输，请配置 provider Files API 后重试]",
                        })
                    elif "video" not in self.capability:
                        logger.warning(f"不支持的文件类型: {file.type}")
                    continue

                try:
                    inline_size = await self._hydrate_local_file(file, workspace_id)
                except BusinessException as exc:
                    # 单个附件无法内联只降级该附件，不影响同一请求里的其他文件。
                    logger.warning(f"附件内联读取失败，已降级: {exc}")
                    if include_processing_errors:
                        result.append({"type": "text", "text": f"[文件处理失败: {str(exc)}]"})
                    continue
                if file.type in {FileType.IMAGE, FileType.AUDIO}:
                    file_type_limit = inline_limits.get(file.type)
                    if file_type_limit is None:
                        # 未配置内联上限的类型不做大小兜限（如后续新增类型）。
                        inline_payload_size += inline_size
                    elif inline_size > file_type_limit:
                        logger.warning(
                            "附件超过应用配置的内联大小限制",
                            extra={"file_id": str(file.upload_file_id), "size": inline_size},
                        )
                        if include_processing_errors:
                            limit_mb = file_type_limit // (1024 * 1024)
                            result.append({
                                "type": "text",
                                "text": f"[{_FILE_TYPE_LABELS.get(file.type, '文件')}超过单文件大小限制 {limit_mb}MB，未发送给模型，请压缩或拆分后重试]",
                            })
                        continue
                    elif inline_payload_size + inline_size > inline_total_limit:
                        logger.warning(
                            "本次请求内联附件总大小超过限制",
                            extra={"file_id": str(file.upload_file_id), "size": inline_size},
                        )
                        if include_processing_errors:
                            result.append({
                                "type": "text",
                                "text": "[本次请求附件总大小超限，该文件未发送给模型，请减少附件后重试]",
                            })
                        continue
                    else:
                        inline_payload_size += inline_size
            elif not file.url:
                file.url = await self.get_file_url(file)

            strategy = strategy_class(file)
            try:
                if file.type == FileType.IMAGE and ModelCapability.VISION in self.capability:
                    is_support, content = await self._process_image(file, strategy)
                    if is_support or include_processing_errors:
                        result.append(content)
                elif file.type == FileType.DOCUMENT:
                    is_support, content = await self._process_document(file, strategy)
                    if not is_support and not include_processing_errors:
                        # 只跳过当前失败文档，后续文件仍会继续处理；若最终无成功结果，上层回退已有 summary。
                        continue
                    result.append(content)
                    # 仅当开关开启且模型支持视觉时，才提取文档内嵌图片
                    if document_image_recognition and ModelCapability.VISION in self.capability:
                        img_infos = await self.extract_document_images(file)
                        img_result = []
                        for img_info in img_infos:
                            page = img_info["page"]
                            index = img_info["index"]
                            ext = img_info.get("ext", "png")
                            image_bytes = img_info["bytes"]
                            if len(image_bytes) > inline_limits.get(FileType.IMAGE, 0) or (
                                    inline_payload_size + len(image_bytes) > inline_total_limit
                            ):
                                logger.warning(
                                    "文档内嵌图片超过内联大小限制，已跳过",
                                    extra={"page": page, "index": index, "size": len(image_bytes)},
                                )
                                continue
                            inline_payload_size += len(image_bytes)
                            try:
                                placeholder = f"第{page}页 第{index + 1}张" if page > 0 else f"第{index + 1}张"
                                # 文本仅保留位置标记；展示 URL 属于前端/存储层，不得进入公网模型上下文。
                                if result and result[-1].get("type") in ("text", "document"):
                                    key = "text" if "text" in result[-1] else list(result[-1].keys())[-1]
                                    result[-1][key] = result[-1].get(key, "") + f"\n[图片 {placeholder}]"
                                # 内嵌图片直接以字节形式提供给视觉模型，不创建或暴露内网永久 URL。
                                img_file = FileInput(
                                    type=FileType.IMAGE,
                                    transfer_method=TransferMethod.LOCAL_FILE,
                                    upload_file_id=file.upload_file_id or uuid.uuid4(),
                                    file_type=f"image/{ext}",
                                )
                                img_file.set_content(image_bytes)
                                img_support, img_content = await self._process_image(img_file, strategy_class(img_file))
                                if img_support or include_processing_errors:
                                    img_result.append(img_content)
                            except Exception as img_err:
                                logger.warning(f"文档图片处理失败: {img_err}")
                        result.extend(img_result)
                elif file.type == FileType.AUDIO and "audio" in self.capability:
                    is_support, content = await self._process_audio(file, strategy)
                    if is_support or include_processing_errors:
                        result.append(content)
                elif file.type == FileType.VIDEO and "video" in self.capability:
                    is_support, content = await self._process_video(file, strategy)
                    if is_support or include_processing_errors:
                        result.append(content)
                else:
                    logger.warning(f"不支持的文件类型: {file.type}")
            except Exception as e:
                logger.error(
                    f"处理文件失败",
                    extra={
                        "file_index": idx,
                        "file_type": file.type,
                        "error": str(e)
                    },
                    exc_info=True
                )
                # 默认保留历史错误占位；关闭后只跳过当前失败文件，后续文件仍会继续处理。
                if include_processing_errors:
                    result.append({
                        "type": "text",
                        "text": f"[文件处理失败: {str(e)}]"
                    })

        logger.info(f"成功处理 {len(result)}/{len(files)} 个文件，provider={self.provider}")
        return result

    async def _process_image(self, file: FileInput, strategy) -> tuple[bool, Dict[str, Any]]:
        """处理图片文件。"""
        try:
            return await strategy.format_image(file.url, content=file.get_content())
        except Exception as e:
            logger.error(f"处理图片失败: {e}", exc_info=True)
            return False, {
                "type": "text",
                "text": f"[图片处理失败: {str(e)}]",
            }

    async def _process_document(self, file: FileInput, strategy) -> tuple[bool, Dict[str, Any]]:
        """处理文档文件（PDF、Word 等），仅向模型传递提取出的内容。"""
        if not _requires_inline_bearer(file):
            return True, {
                "type": "text",
                "text": f"<document url=\"{file.url}\">\n{await self.extract_document_text(file)}\n</document>",
            }

        text = await self.extract_document_text(file)
        return await strategy.format_document(file.name or "unknown", text)

    async def _process_audio(self, file: FileInput, strategy) -> tuple[bool, Dict[str, Any]]:
        """处理音频文件。"""
        try:
            transcription = None
            if self.enable_audio_transcription and self.audio_api_key:
                if not _requires_inline_bearer(file) and file.url:
                    logger.info(f"开始音频转文本: {file.url}")
                    if self.provider == "dashscope":
                        transcription = await AudioTranscriptionService.transcribe_dashscope(file.url, self.audio_api_key)
                    elif self.provider == "openai":
                        transcription = await AudioTranscriptionService.transcribe_openai(file.url, self.audio_api_key)
                    else:
                        logger.warning(f"Provider {self.provider} 不支持音频转文本")
                elif file.get_content() is not None:
                    # 本地附件不能把内网 URL 交给公网 ASR；支持原生音频的策略会使用 Data URL。
                    logger.info("本地音频使用 provider 原生内容输入，跳过基于 URL 的转写接口")

            return await strategy.format_audio(
                file.file_type or "",
                file.url,
                file.get_content(),
                transcription,
            )
        except Exception as e:
            logger.error(f"处理音频失败: {e}", exc_info=True)
            return False, {
                "type": "text",
                "text": f"[音频处理失败: {str(e)}]",
            }

    async def _process_video(self, file: FileInput, strategy) -> tuple[bool, Dict[str, Any]]:
        """处理视频文件。"""
        try:
            return await strategy.format_video(file.url, content=file.get_content())
        except Exception as e:
            logger.error(f"处理视频失败: {e}", exc_info=True)
            return False, {
                "type": "text",
                "text": f"[视频处理失败: {str(e)}]",
            }

    async def get_file_url(self, file: FileInput) -> str:
        """
        获取文件的访问 URL
        
        Args:
            file: File Input Struct
            
        Returns:
            str: 文件访问 URL（永久URL）
            
        Raises:
            BusinessException: 文件不存在
        """
        if file.transfer_method == TransferMethod.REMOTE_URL:
            return file.url
        else:
            file_id = file.upload_file_id

            # 查询 FileMetadata
            file_metadata = await self._get_file_metadata(file_id, completed_only=True)

            if not file_metadata:
                raise BusinessException(
                    f"文件不存在或已删除: {file_id}",
                    BizCode.NOT_FOUND
                )

            # 返回永久URL
            server_url = settings.FILE_LOCAL_SERVER_URL
            return f"{server_url}/storage/permanent/{file_id}"

    async def extract_document_text(self, file: FileInput) -> str:
        """
        提取文档文本内容
        
        Args:
            file: 文件输入
            
        Returns:
            str: 提取的文本内容
        """
        try:
            file_content = file.get_content()
            if not file_content:
                async with httpx.AsyncClient(timeout=30.0) as client:
                    response = await client.get(file.url, follow_redirects=True)
                    response.raise_for_status()
                    file_content = response.content
                    file.set_content(file_content)
            file_mime_type = magic.from_buffer(file_content, mime=True)
            if file_mime_type in TEXT_MIME:
                return self._decode_text_safe(file_content)
            elif file_mime_type in PDF_MIME:
                return await self._extract_pdf_text(file_content)
            elif self._is_word_file(file_content, file_mime_type):
                return await self._extract_word_text(file_content)
            elif self._is_excel_file(file_content, file_mime_type):
                return await self._extract_xlsx_text(file_content)
            elif file_mime_type in CSV_MIME:
                return await self._extract_csv_text(file_content)
            elif file_mime_type in JSON_MIME:
                return await self._extract_json_text(file_content)
            else:
                return f"[Unsupported file type: {file_mime_type}]"
        except Exception as e:
            logger.error(f"Failed to load file. - {e}")
            return "[Failed to load file.]"

    async def extract_document_images(self, file: FileInput) -> list[dict]:
        """
        提取文档中的内嵌图片（支持 PDF 和 DOCX），附带位置信息。

        Returns:
            list[dict]: 每项包含:
                - bytes: 图片二进制
                - page: 所在页码（PDF 从 1 开始，DOCX 为 0）
                - index: 该页/文档内的图片序号（从 0 开始）
                - ext: 图片扩展名（如 png、jpeg）
        """
        try:
            file_content = file.get_content()
            if not file_content:
                async with httpx.AsyncClient(timeout=30.0) as client:
                    response = await client.get(file.url, follow_redirects=True)
                    response.raise_for_status()
                    file_content = response.content
                    file.set_content(file_content)

            file_mime_type = magic.from_buffer(file_content, mime=True)
            if file_mime_type in PDF_MIME:
                return self._extract_pdf_images(file_content)
            elif self._is_word_file(file_content, file_mime_type):
                return self._extract_docx_images(file_content)
            return []
        except Exception as e:
            logger.error(f"提取文档图片失败: {e}")
            return []

    @staticmethod
    def _extract_pdf_images(file_content: bytes) -> list[dict]:
        """从 PDF 提取内嵌图片，附带页码和序号"""
        images = []
        import fitz  # PyMuPDF
        try:
            doc = fitz.open(stream=file_content, filetype="pdf")
            for page_num, page in enumerate(doc, start=1):
                for idx, img in enumerate(page.get_images(full=True)):
                    xref = img[0]
                    base_image = doc.extract_image(xref)
                    images.append({
                        "bytes": base_image["image"],
                        "ext": base_image.get("ext", "png"),
                        "page": page_num,
                        "index": idx,
                    })
            doc.close()
        except ImportError:
            logger.warning("PyMuPDF 未安装，无法提取 PDF 图片，请执行: uv add pymupdf")
        except Exception as e:
            logger.error(f"提取 PDF 图片失败: {e}")
        return images

    @staticmethod
    def _extract_docx_images(file_content: bytes) -> list[dict]:
        """从 DOCX 提取内嵌图片，附带序号（DOCX 无页码概念，page 固定为 0）"""
        images = []
        try:
            if file_content[:2] != b'PK':
                return []
            with zipfile.ZipFile(io.BytesIO(file_content)) as zf:
                media_files = sorted(
                    name for name in zf.namelist()
                    if name.startswith("word/media/") and not name.endswith("/")
                )
                for idx, name in enumerate(media_files):
                    ext = name.rsplit(".", 1)[-1].lower() if "." in name else "png"
                    images.append({
                        "bytes": zf.read(name),
                        "ext": ext,
                        "page": 0,
                        "index": idx,
                    })
        except Exception as e:
            logger.error(f"提取 DOCX 图片失败: {e}")
        return images

    @staticmethod
    async def _extract_pdf_text(file_content: bytes) -> str:
        """提取 PDF 文本"""
        try:
            # 使用 BytesIO 读取 PDF
            text_parts = []
            pdf_file = io.BytesIO(file_content)
            pdf_reader = PyPDF2.PdfReader(pdf_file)
            for page in pdf_reader.pages:
                text_parts.append(page.extract_text())
            return '\n'.join(text_parts)
        except Exception as e:
            logger.error(f"提取 PDF 文本失败: {e}")
            return f"[PDF 提取失败: {str(e)}]"

    @staticmethod
    async def _extract_word_text(file_content: bytes) -> str:
        """提取 Word 文档文本（支持 .docx 和旧版 .doc）"""
        # 先尝试 docx（ZIP 格式）
        if file_content[:2] == b'PK':
            try:
                word_file = io.BytesIO(file_content)
                doc = Document(word_file)
                text_lines = []
                for p in doc.paragraphs:
                    text = p.text.strip()
                    if text:
                        text_lines.append(text)

                for table in doc.tables:
                    for row in table.rows:
                        for cell in row.cells:
                            text = cell.text.strip()
                            if text:
                                text_lines.append(text)

                full_text = "\n".join(text_lines)
                return full_text.strip() or "[docx 文件无文本内容]"
            except Exception as e:
                logger.error(f"提取 docx 文本失败: {str(e)}", exc_info=True)
                return f"[docx 提取失败: {str(e)}]"

        # 旧版 .doc（OLE2/CFB 格式），按 Word Binary Format 规范解析 piece table
        try:
            ole = olefile.OleFileIO(io.BytesIO(file_content))
            word_stream = ole.openstream('WordDocument').read()

            # FIB offset 0xA bit9 决定使用 0Table 还是 1Table
            fib_flags = struct.unpack_from('<H', word_stream, 0xA)[0]
            table_name = '1Table' if (fib_flags & 0x0200) else '0Table'
            table_stream = ole.openstream(table_name).read()

            # 从 FIB 读取 fcClx/lcbClx 定位 piece table
            fc_clx, lcb_clx = struct.unpack_from("<II", word_stream, 0x1A2)
            clx = table_stream[fc_clx: fc_clx + lcb_clx]

            # 解析 CLX，找到 PlcPcd（piece table）
            i, plc_pcd = 0, None
            while i < len(clx):
                clxt = clx[i]
                if clxt == 0x01:
                    i += 3 + struct.unpack_from('<H', clx, i + 1)[0]
                elif clxt == 0x02:
                    cb = struct.unpack_from('<I', clx, i + 1)[0]
                    plc_pcd = clx[i + 5: i + 5 + cb]
                    break
                else:
                    break

            if plc_pcd is None:
                raise ValueError("PlcPcd not found")

            # PlcPcd: (n+1) 个 CP（4字节）+ n 个 PCD（8字节）
            n_pieces = (len(plc_pcd) - 4) // 12
            cp_array = [struct.unpack_from('<I', plc_pcd, k * 4)[0] for k in range(n_pieces + 1)]

            parts = []
            for k in range(n_pieces):
                fc_value = struct.unpack_from('<I', plc_pcd, (n_pieces + 1) * 4 + k * 8 + 2)[0]
                is_ansi = bool(fc_value & 0x40000000)
                fc = fc_value & 0x3FFFFFFF
                char_count = cp_array[k + 1] - cp_array[k]

                if is_ansi:
                    parts.append(word_stream[fc: fc + char_count].decode('cp1252', errors='replace'))
                else:
                    parts.append(word_stream[fc: fc + char_count * 2].decode('utf-16-le', errors='replace'))

            ole.close()
            result = re.sub(r'[\x00-\x1f\x7f]', '', ''.join(parts))
            return result.strip()

        except Exception as e:
            logger.error(f"提取 doc 文本失败: {e}")
            return f"[doc 提取失败: {str(e)}]"

    @staticmethod
    async def _extract_xlsx_text(file_content: bytes) -> str:
        """提取 Excel 文本（支持 .xlsx 和旧版 .xls）"""
        # xlsx（ZIP 格式）
        if file_content[:2] == b'PK':
            try:
                wb = openpyxl.load_workbook(io.BytesIO(file_content), read_only=True, data_only=True)
                parts = []
                for sheet in wb.worksheets:
                    parts.append(f"[Sheet: {sheet.title}]")
                    for row in sheet.iter_rows(values_only=True):
                        parts.append('\t'.join('' if v is None else str(v) for v in row))
                return '\n'.join(parts)
            except Exception as e:
                # openpyxl 对不规范 styles.xml（如空 <fill/>）零容忍，会抛
                # TypeError: expected <class 'openpyxl.styles.fills.Fill'>；
                # calamine（Rust 实现，不解析 styles.xml）可正常读取此类文件
                logger.warning(f"openpyxl 提取 xlsx 文本失败: {e}，尝试 calamine 降级读取")
                try:
                    from python_calamine import CalamineWorkbook
                    cwb = CalamineWorkbook.from_filelike(io.BytesIO(file_content))
                    parts = []
                    for sheet_name in cwb.sheet_names:
                        parts.append(f"[Sheet: {sheet_name}]")
                        for row in cwb.get_sheet_by_name(sheet_name).to_python():
                            parts.append('\t'.join('' if v is None else str(v) for v in row))
                    return '\n'.join(parts)
                except Exception as e_fallback:
                    logger.error(f"提取 xlsx 文本失败: openpyxl({e}), calamine({e_fallback})")
                    return f"[xlsx 提取失败: {str(e_fallback)}]"

        # xls（OLE2/BIFF 格式）
        try:
            import xlrd
            wb = xlrd.open_workbook(file_contents=file_content)
            parts = []
            for sheet in wb.sheets():
                parts.append(f"[Sheet: {sheet.name}]")
                for row_idx in range(sheet.nrows):
                    parts.append('\t'.join(str(sheet.cell_value(row_idx, col)) for col in range(sheet.ncols)))
            return '\n'.join(parts)
        except Exception as e:
            logger.error(f"提取 xls 文本失败: {e}")
            return f"[xls 提取失败: {str(e)}]"

    async def _extract_csv_text(self, file_content: bytes) -> str:
        """提取 CSV 文本"""
        try:
            text = self._decode_text_safe(file_content)
            reader = csv.reader(io.StringIO(text))
            return '\n'.join('\t'.join(row) for row in reader)
        except Exception as e:
            logger.error(f"提取 CSV 文本失败: {e}")
            return f"[CSV 提取失败: {str(e)}]"

    async def _extract_json_text(self, file_content: bytes) -> str:
        """提取 JSON 文本"""
        try:
            text = self._decode_text_safe(file_content)
            data = json.loads(text)
            return json.dumps(data, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"提取 JSON 文本失败: {e}")
            return f"[JSON 提取失败: {str(e)}]"

    def _is_word_file(self, file_content: bytes, mime_type: str) -> bool:
        """判断是不是 Word 文件（doc / docx），不依赖后缀"""
        # 旧版 .doc
        if mime_type == 'application/msword':
            return True

        # 新版 .docx（ZIP 内部包含 word/document.xml）
        header = file_content[:4]
        if header == b'PK\x03\x04':
            try:
                with zipfile.ZipFile(io.BytesIO(file_content)) as zf:
                    return "word/document.xml" in zf.namelist()
            except:
                pass

        return False

    def _is_excel_file(self, file_content: bytes, mime_type: str) -> bool:
        """判断是不是 Excel 文件（xls / xlsx），不依赖后缀"""
        # 旧版 .xls
        if mime_type == 'application/vnd.ms-excel':
            return True

        # 新版 .xlsx（ZIP 内部包含 xl/workbook.xml）
        header = file_content[:4]
        if header == b'PK\x03\x04':
            try:
                with zipfile.ZipFile(io.BytesIO(file_content)) as zf:
                    return "xl/workbook.xml" in zf.namelist()
            except:
                pass

        return False

    @staticmethod
    def _decode_text_safe(file_content: bytes) -> str:
        """
        【万能文本解码】
        自动检测编码，支持 utf-8 / gbk / gb2312 / utf-8-sig / ascii 等
        永远不报错，永远不乱码
        """
        if not file_content:
            return ""

        # 1. 自动检测文件编码
        detect = chardet.detect(file_content)
        encoding = detect.get("encoding") or "utf-8"
        encoding = encoding.lower()

        # 2. 兼容常见中文编码
        # 注意：不能用 latin-1 兜底——它逐字节 1:1 映射且永不失败，会把
        # ZIP/PDF 等二进制“成功”解码成含 NUL(\\x00) 的字符串，写入
        # PostgreSQL 时触发 CharacterNotInRepertoireError。
        compatible_encodings = ["utf-8", "gbk", "gb18030", "gb2312", "ascii"]

        # 3. 按优先级尝试解码
        for enc in [encoding] + compatible_encodings:
            if not enc:
                continue
            try:
                return sanitize_text(file_content.decode(enc.strip()))
            except (UnicodeDecodeError, LookupError):
                continue

        # 终极兜底：非法字节替换为 U+FFFD，同时剥除 NUL
        return sanitize_text(file_content.decode("utf-8", errors="replace"))


def get_multimodal_service(db: Session) -> MultimodalService:
    """获取多模态服务实例（依赖注入）"""
    return MultimodalService(db)
