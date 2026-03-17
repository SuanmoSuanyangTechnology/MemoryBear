"""
多模态文件处理服务

处理图片、文档等多模态文件，转换为 LLM 可用的格式

支持的 Provider:
- DashScope (通义千问): 支持 URL 格式
- Bedrock/Anthropic: 仅支持 base64 格式
- OpenAI: 支持 URL 和 base64 格式
"""
import base64
import io
import uuid
from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional

import PyPDF2
import httpx
import magic
from docx import Document
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.error_codes import BizCode
from app.core.exceptions import BusinessException
from app.core.logging_config import get_business_logger
from app.models import ModelApiKey
from app.models.file_metadata_model import FileMetadata
from app.schemas.app_schema import FileInput, FileType, TransferMethod
from app.schemas.model_schema import ModelInfo
from app.services.audio_transcription_service import AudioTranscriptionService
from app.tasks import write_perceptual_memory

logger = get_business_logger()

TEXT_MIME = ['text/plain', 'text/x-markdown']
PDF_MIME = ['application/pdf']
DOC_MIME = [
    'application/msword',
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
]


class MultimodalFormatStrategy(ABC):
    """多模态格式策略基类"""

    def __init__(self, file: FileInput):
        self.file = file

    @abstractmethod
    async def format_image(self, url: str, content: bytes | None = None) -> Dict[str, Any]:
        """格式化图片"""
        pass

    @abstractmethod
    async def format_document(self, file_name: str, text: str) -> Dict[str, Any]:
        """格式化文档"""
        pass

    @abstractmethod
    async def format_audio(self, file_type: str, url: str, content: bytes | None = None) -> Dict[str, Any]:
        """格式化音频"""
        pass

    @abstractmethod
    async def format_video(self, url: str) -> Dict[str, Any]:
        """格式化视频"""
        pass


class DashScopeFormatStrategy(MultimodalFormatStrategy):
    """通义千问策略"""

    async def format_image(self, url: str, content: bytes | None = None) -> Dict[str, Any]:
        """通义千问图片格式：{"type": "image", "image": "url"}"""
        return {
            "type": "image",
            "image": url
        }

    async def format_document(self, file_name: str, text: str) -> Dict[str, Any]:
        """通义千问文档格式"""
        return {
            "type": "text",
            "text": f"<document name=\"{file_name}\">\n{text}\n</document>"
        }

    async def format_audio(
            self,
            file_type: str,
            url: str,
            content: bytes | None = None,
            transcription: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        通义千问音频格式
        - 原生支持: qwen-audio 系列
        - 其他模型: 需要转录为文本
        """
        if transcription:
            return {
                "type": "text",
                "text": f"<audio url=\"{url}\">\ntext_transcription:{transcription}\n</audio>"
            }
        # 通义千问音频格式：{"type": "audio", "audio": "url"}
        return {
            "type": "audio",
            "audio": url
        }

    async def format_video(self, url: str) -> Dict[str, Any]:
        """通义千问视频格式（qwen-vl 系列原生支持）"""
        return {
            "type": "video",
            "video": url
        }


class BedrockFormatStrategy(MultimodalFormatStrategy):
    """Bedrock/Anthropic 策略"""

    async def format_image(self, url: str, content: bytes | None = None) -> Dict[str, Any]:
        """
        Bedrock/Anthropic 格式: base64 编码
        {"type": "image", "source": {"type": "base64", "media_type": "...", "data": "..."}}
        """

        logger.info(f"下载并编码图片: {url}")

        # 下载图片
        if content is None:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(url, follow_redirects=True)
                response.raise_for_status()
                content = response.content
                self.file.set_content(content)

            # 确定 media type
        content_type = magic.from_buffer(content, mime=True)
        media_type = content_type if content_type.startswith("image/") else "image/jpeg"
        base64_data = base64.b64encode(content).decode("utf-8")

        logger.info(f"图片编码完成: media_type={media_type}, size={len(base64_data)}")

        return {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": media_type,
                "data": base64_data
            }
        }

    async def format_document(self, file_name: str, text: str) -> Dict[str, Any]:
        """Bedrock/Anthropic 文档格式（需要 base64 编码）"""
        # Bedrock 文档需要 base64 编码
        text_bytes = text.encode('utf-8')
        base64_text = base64.b64encode(text_bytes).decode('utf-8')

        return {
            "type": "document",
            "source": {
                "type": "base64",
                "media_type": "text/plain",
                "data": base64_text
            }
        }

    async def format_audio(
            self, file_type: str,
            url: str,
            content: bytes | None = None,
            transcription: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Bedrock/Anthropic 音频格式
        不支持原生音频，必须转录为文本
        """
        if transcription:
            return {
                "type": "text",
                "text": f"[音频转录]\n{transcription}"
            }
        return {
            "type": "text",
            "text": "[音频文件：Bedrock 不支持原生音频，请启用音频转文本功能]"
        }

    async def format_video(self, url: str) -> Dict[str, Any]:
        """Bedrock/Anthropic 视频格式"""
        return {
            "type": "text",
            "text": f"<video url=\"{url}\">\n[视频文件，当前 provider 暂不支持]\n</video>"
        }


class OpenAIFormatStrategy(MultimodalFormatStrategy):
    """OpenAI 策略"""

    async def format_image(self, url: str, content: bytes | None = None) -> Dict[str, Any]:
        """OpenAI 格式: {"type": "image_url", "image_url": {"url": "..."}}"""
        return {
            "type": "image_url",
            "image_url": {
                "url": url
            }
        }

    async def format_document(self, file_name: str, text: str) -> Dict[str, Any]:
        """OpenAI 文档格式"""
        return {
            "type": "text",
            "text": f"<document name=\"{file_name}\">\n{text}\n</document>"
        }

    async def format_audio(
            self,
            file_type: str,
            url: str,
            content: bytes | None = None,
            transcription: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        OpenAI 音频格式
        - gpt-4o-audio 系列支持原生音频（需要 base64 编码）
        - 其他模型使用转录文本
        """
        if transcription:
            return {
                "type": "text",
                "text": f"<audio url=\"{url}\">\n{transcription}\n</audio>"
            }

        # OpenAI 音频需要 base64 编码
        try:
            audio_data = content
            if content is None:
                async with httpx.AsyncClient(timeout=30.0) as client:
                    response = await client.get(url, follow_redirects=True)
                    response.raise_for_status()
                    audio_data = response.content
                    self.file.set_content(audio_data)
            base64_audio = base64.b64encode(audio_data).decode('utf-8')

            # 1. 优先从 file_type (MIME) 取扩展名
            file_ext = file_type.split('/')[-1] if file_type and '/' in file_type else None
            # 2. 从响应头 content-type 取
            if not file_ext:
                content_type = magic.from_buffer(audio_data, mime=True)
                file_ext = content_type.split('/')[-1].split(';')[0].strip() if '/' in content_type else None
            # 3. 从 URL 路径取扩展名
            if not file_ext:
                file_ext = url.split('?')[0].rsplit('.', 1)[-1].lower() or None
            # 4. 默认 wav
            # supported_ext = {"wav", "mp3", "mp4", "ogg", "flac", "webm", "m4a", "wave", "x-m4a"}
            file_ext = "wav" if not file_ext else file_ext

            return {
                "type": "input_audio",
                "input_audio": {
                    "data": f"data:;base64,{base64_audio}",
                    "format": file_ext
                }
            }
        except Exception as e:
            logger.error(f"下载音频失败: {e}")
            return {
                "type": "text",
                "text": f"[音频处理失败: {str(e)}]"
            }

    async def format_video(self, url: str) -> Dict[str, Any]:
        """OpenAI 视频格式"""
        return {
            "type": "video_url",
            "video_url": {
                "url": url
            }
        }


# Provider 到策略的映射
PROVIDER_STRATEGIES = {
    "dashscope": DashScopeFormatStrategy,
    "bedrock": BedrockFormatStrategy,
    "anthropic": BedrockFormatStrategy,
    "openai": OpenAIFormatStrategy,
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
            db: Session,
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

    async def process_files(
            self,
            end_user_id: uuid.UUID | str,
            files: Optional[List[FileInput]],

    ) -> List[Dict[str, Any]]:
        """
        处理文件列表，返回 LLM 可用的格式
        
        Args:
            end_user_id: 用户ID
            files: 文件输入列表
            
        Returns:
            List[Dict]: LLM 可用的内容格式列表（根据 provider 返回不同格式）
        """
        if not files:
            return []
        if isinstance(end_user_id, uuid.UUID):
            end_user_id = str(end_user_id)

        # 获取对应的策略
        # dashscope 的 omni 模型使用 OpenAI 兼容格式
        if self.provider == "dashscope" and self.is_omni:
            strategy_class = OpenAIFormatStrategy
        else:
            strategy_class = PROVIDER_STRATEGIES.get(self.provider)
            if not strategy_class:
                logger.warning(f"未找到 provider '{self.provider}' 的策略，使用默认策略")
                strategy_class = DashScopeFormatStrategy

        result = []
        for idx, file in enumerate(files):
            strategy = strategy_class(file)
            if not file.url:
                file.url = await self.get_file_url(file)
            try:
                if file.type == FileType.IMAGE and "vision" in self.capability:
                    content = await self._process_image(file, strategy)
                    result.append(content)
                    self.write_perceptual_memory(end_user_id, file.type, file.url, content)
                elif file.type == FileType.DOCUMENT:
                    content = await self._process_document(file, strategy)
                    result.append(content)
                    self.write_perceptual_memory(end_user_id, file.type, file.url, content)
                elif file.type == FileType.AUDIO and "audio" in self.capability:
                    content = await self._process_audio(file, strategy)
                    result.append(content)
                    self.write_perceptual_memory(end_user_id, file.type, file.url, content)
                elif file.type == FileType.VIDEO and "video" in self.capability:
                    content = await self._process_video(file, strategy)
                    result.append(content)
                    self.write_perceptual_memory(end_user_id, file.type, file.url, content)
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
                # 继续处理其他文件，不中断整个流程
                result.append({
                    "type": "text",
                    "text": f"[文件处理失败: {str(e)}]"
                })

        logger.info(f"成功处理 {len(result)}/{len(files)} 个文件，provider={self.provider}")
        return result

    def write_perceptual_memory(
            self,
            end_user_id: str,
            file_type: str,
            file_url: str,
            file_message: dict
    ):
        """写入感知记忆"""
        if end_user_id and self.api_config:
            write_perceptual_memory.delay(end_user_id, self.api_config.model_dump(), file_type, file_url, file_message)

    async def _process_image(self, file: FileInput, strategy) -> Dict[str, Any]:
        """
        处理图片文件
        
        Args:
            file: 图片文件输入
            strategy: 格式化策略
            
        Returns:
            Dict: 根据 provider 返回不同格式的图片内容
        """
        try:
            # url = await self.get_file_url(file)
            return await strategy.format_image(file.url, content=file.get_content())
        except Exception as e:
            logger.error(f"处理图片失败: {e}", exc_info=True)
            return {
                "type": "text",
                "text": f"[图片处理失败: {str(e)}]"
            }

    async def _process_document(self, file: FileInput, strategy) -> Dict[str, Any]:
        """
        处理文档文件（PDF、Word 等）
        
        Args:
            file: 文档文件输入
            strategy: 格式化策略
            
        Returns:
            Dict: 根据 provider 返回不同格式的文档内容
        """
        if file.transfer_method == TransferMethod.REMOTE_URL:
            return {
                "type": "text",
                "text": f"<document url=\"{file.url}\">\n{await self._extract_document_text(file)}\n</document>"
            }
        else:
            # 本地文件，提取文本内容
            server_url = settings.FILE_LOCAL_SERVER_URL
            file.url = f"{server_url}/storage/permanent/{file.upload_file_id}"
            text = await self._extract_document_text(file)
            file_metadata = self.db.query(FileMetadata).filter(
                FileMetadata.id == file.upload_file_id
            ).first()

            file_name = file_metadata.file_name if file_metadata else "unknown"

            # 使用策略格式化文档
            return await strategy.format_document(file_name, text)

    async def _process_audio(self, file: FileInput, strategy) -> Dict[str, Any]:
        """
        处理音频文件
        
        Args:
            file: 音频文件输入
            strategy: 格式化策略
            
        Returns:
            Dict: 根据 provider 返回不同格式的音频内容
        """
        try:
            # url = await self.get_file_url(file)

            # 如果启用音频转文本且有 API Key
            transcription = None
            if self.enable_audio_transcription and self.audio_api_key:
                logger.info(f"开始音频转文本: {file.url}")
                if self.provider == "dashscope":
                    transcription = await AudioTranscriptionService.transcribe_dashscope(file.url, self.audio_api_key)
                elif self.provider == "openai":
                    transcription = await AudioTranscriptionService.transcribe_openai(file.url, self.audio_api_key)
                else:
                    logger.warning(f"Provider {self.provider} 不支持音频转文本")

            return await strategy.format_audio(file.file_type, file.url, file.get_content(), transcription)
        except Exception as e:
            logger.error(f"处理音频失败: {e}", exc_info=True)
            return {
                "type": "text",
                "text": f"[音频处理失败: {str(e)}]"
            }

    async def _process_video(self, file: FileInput, strategy) -> Dict[str, Any]:
        """
        处理视频文件
        
        Args:
            file: 视频文件输入
            strategy: 格式化策略
            
        Returns:
            Dict: 根据 provider 返回不同格式的视频内容
        """
        try:
            # url = await self.get_file_url(file)
            return await strategy.format_video(file.url)
        except Exception as e:
            logger.error(f"处理视频失败: {e}", exc_info=True)
            return {
                "type": "text",
                "text": f"[视频处理失败: {str(e)}]"
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
            file_metadata = self.db.query(FileMetadata).filter(
                FileMetadata.id == file_id,
                FileMetadata.status == "completed"
            ).first()

            if not file_metadata:
                raise BusinessException(
                    f"文件不存在或已删除: {file_id}",
                    BizCode.NOT_FOUND
                )

            # 返回永久URL
            server_url = settings.FILE_LOCAL_SERVER_URL
            return f"{server_url}/storage/permanent/{file_id}"

    async def _extract_document_text(self, file: FileInput) -> str:
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
                return file_content.decode("utf-8")
            elif file_mime_type in PDF_MIME:
                return await self._extract_pdf_text(file_content)
            elif file_mime_type in DOC_MIME:
                return await self._extract_word_text(file_content)
            else:
                return f"[Unsupported file type: {file_mime_type}]"
        except Exception as e:
            logger.error(f"Failed to load file. - {e}")
            return "[Failed to load file.]"

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
        """提取 Word 文档文本"""
        try:
            # 使用 BytesIO 读取 Word 文档
            word_file = io.BytesIO(file_content)
            doc = Document(word_file)
            text_parts = [paragraph.text for paragraph in doc.paragraphs]
            return '\n'.join(text_parts)
        except Exception as e:
            logger.error(f"提取 Word 文本失败: {e}")
            return f"[Word 提取失败: {str(e)}]"


def get_multimodal_service(db: Session) -> MultimodalService:
    """获取多模态服务实例（依赖注入）"""
    return MultimodalService(db)
