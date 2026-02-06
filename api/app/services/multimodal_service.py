"""
多模态文件处理服务

处理图片、文档等多模态文件，转换为 LLM 可用的格式

支持的 Provider:
- DashScope (通义千问): 支持 URL 格式
- Bedrock/Anthropic: 仅支持 base64 格式
- OpenAI: 支持 URL 和 base64 格式
"""
import uuid
from typing import List, Dict, Any, Optional, Protocol
from sqlalchemy.orm import Session

from app.core.logging_config import get_business_logger
from app.core.exceptions import BusinessException
from app.core.error_codes import BizCode
from app.schemas.app_schema import FileInput, FileType, TransferMethod
from app.models.generic_file_model import GenericFile

logger = get_business_logger()


class ImageFormatStrategy(Protocol):
    """图片格式策略接口"""

    async def format_image(self, url: str) -> Dict[str, Any]:
        """将图片 URL 转换为特定 provider 的格式"""
        ...


class DashScopeImageStrategy:
    """通义千问图片格式策略"""

    async def format_image(self, url: str) -> Dict[str, Any]:
        """通义千问格式: {"type": "image", "image": "url"}"""
        return {
            "type": "image",
            "image": url
        }


class BedrockImageStrategy:
    """Bedrock/Anthropic 图片格式策略"""

    async def format_image(self, url: str) -> Dict[str, Any]:
        """
        Bedrock/Anthropic 格式: base64 编码
        {"type": "image", "source": {"type": "base64", "media_type": "...", "data": "..."}}
        """
        import httpx
        import base64
        from mimetypes import guess_type

        logger.info(f"下载并编码图片: {url}")

        # 下载图片
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(url)
            response.raise_for_status()

            # 获取图片数据
            image_data = response.content

            # 确定 media type
            content_type = response.headers.get("content-type")
            if content_type and content_type.startswith("image/"):
                media_type = content_type
            else:
                guessed_type, _ = guess_type(url)
                media_type = guessed_type if guessed_type and guessed_type.startswith("image/") else "image/jpeg"

            # 转换为 base64
            base64_data = base64.b64encode(image_data).decode("utf-8")

            logger.info(f"图片编码完成: media_type={media_type}, size={len(base64_data)}")

            return {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": media_type,
                    "data": base64_data
                }
            }


class OpenAIImageStrategy:
    """OpenAI 图片格式策略"""

    async def format_image(self, url: str) -> Dict[str, Any]:
        """OpenAI 格式: {"type": "image_url", "image_url": {"url": "..."}}"""
        return {
            "type": "image_url",
            "image_url": {
                "url": url
            }
        }


# Provider 到策略的映射
PROVIDER_STRATEGIES = {
    "dashscope": DashScopeImageStrategy,
    "bedrock": BedrockImageStrategy,
    "anthropic": BedrockImageStrategy,
    "openai": OpenAIImageStrategy,
}


class MultimodalService:
    """多模态文件处理服务"""

    def __init__(self, db: Session, provider: str = "dashscope"):
        """
        初始化多模态服务
        
        Args:
            db: 数据库会话
            provider: 模型提供商（dashscope, bedrock, anthropic 等）
        """
        self.db = db
        self.provider = provider.lower()

    async def process_files(
            self,
            files: Optional[List[FileInput]]
    ) -> List[Dict[str, Any]]:
        """
        处理文件列表，返回 LLM 可用的格式
        
        Args:
            files: 文件输入列表
            
        Returns:
            List[Dict]: LLM 可用的内容格式列表（根据 provider 返回不同格式）
        """
        if not files:
            return []

        result = []
        for idx, file in enumerate(files):
            try:
                if file.type == FileType.IMAGE:
                    content = await self._process_image(file)
                    result.append(content)
                elif file.type == FileType.DOCUMENT:
                    content = await self._process_document(file)
                    result.append(content)
                elif file.type == FileType.AUDIO:
                    content = await self._process_audio(file)
                    result.append(content)
                elif file.type == FileType.VIDEO:
                    content = await self._process_video(file)
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
                    }
                )
                # 继续处理其他文件，不中断整个流程
                result.append({
                    "type": "text",
                    "text": f"[文件处理失败: {str(e)}]"
                })

        logger.info(f"成功处理 {len(result)}/{len(files)} 个文件，provider={self.provider}")
        return result

    async def _process_image(self, file: FileInput) -> Dict[str, Any]:
        """
        处理图片文件
        
        Args:
            file: 图片文件输入
            
        Returns:
            Dict: 根据 provider 返回不同格式
                - Anthropic/Bedrock: {"type": "image", "source": {"type": "base64", "media_type": "...", "data": "..."}}
                - 通义千问: {"type": "image", "image": "url"}
        """
        url = await self.get_file_url(file)

        logger.debug(f"处理图片: {url}, provider={self.provider}")

        # 根据 provider 返回不同格式
        if self.provider in ["bedrock", "anthropic"]:
            # Anthropic/Bedrock 只支持 base64 格式，需要下载并转换
            try:
                logger.info(f"开始下载并编码图片: {url}")
                base64_data, media_type = await self._download_and_encode_image(url)
                result = {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": media_type,
                        "data": base64_data[:100] + "..."  # 只记录前100个字符
                    }
                }
                logger.info(f"图片编码完成: media_type={media_type}, data_length={len(base64_data)}")
                # 返回完整数据
                result["source"]["data"] = base64_data
                return result
            except Exception as e:
                logger.error(f"下载并编码图片失败: {e}", exc_info=True)
                # 返回错误提示
                return {
                    "type": "text",
                    "text": f"[图片加载失败: {str(e)}]"
                }
        else:
            # 通义千问等其他格式支持 URL
            return {
                "type": "image",
                "image": url
            }

    async def _download_and_encode_image(self, url: str) -> tuple[str, str]:
        """
        下载图片并转换为 base64
        
        Args:
            url: 图片 URL
            
        Returns:
            tuple: (base64_data, media_type)
        """
        import httpx
        import base64
        from mimetypes import guess_type

        # 下载图片
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(url)
            response.raise_for_status()

            # 获取图片数据
            image_data = response.content

            # 确定 media type
            content_type = response.headers.get("content-type")
            if content_type and content_type.startswith("image/"):
                media_type = content_type
            else:
                # 从 URL 推断
                guessed_type, _ = guess_type(url)
                media_type = guessed_type if guessed_type and guessed_type.startswith("image/") else "image/jpeg"

            # 转换为 base64
            base64_data = base64.b64encode(image_data).decode("utf-8")

            logger.debug(f"图片编码完成: media_type={media_type}, size={len(base64_data)}")

            return base64_data, media_type

    async def _process_document(self, file: FileInput) -> Dict[str, Any]:
        """
        处理文档文件（PDF、Word 等）
        
        Args:
            file: 文档文件输入
            
        Returns:
            Dict: text 格式的内容（包含提取的文本）
        """
        if file.transfer_method == TransferMethod.REMOTE_URL:
            # 远程文档暂不支持提取
            return {
                "type": "text",
                "text": f"<document url=\"{file.url}\">\n[远程文档，暂不支持内容提取]\n</document>"
            }
        else:
            # 本地文件，提取文本内容
            text = await self._extract_document_text(file.upload_file_id)
            generic_file = self.db.query(GenericFile).filter(
                GenericFile.id == file.upload_file_id
            ).first()

            file_name = generic_file.file_name if generic_file else "unknown"

            return {
                "type": "text",
                "text": f"<document name=\"{file_name}\">\n{text}\n</document>"
            }

    async def _process_audio(self, file: FileInput) -> Dict[str, Any]:
        """
        处理音频文件
        
        Args:
            file: 音频文件输入
            
        Returns:
            Dict: 音频内容（暂时返回占位符）
        """
        # TODO: 实现音频转文字功能
        return {
            "type": "text",
            "text": "[音频文件，暂不支持处理]"
        }

    async def _process_video(self, file: FileInput) -> Dict[str, Any]:
        """
        处理视频文件
        
        Args:
            file: 视频文件输入
            
        Returns:
            Dict: 视频内容（暂时返回占位符）
        """
        # TODO: 实现视频处理功能
        return {
            "type": "text",
            "text": "[视频文件，暂不支持处理]"
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
            # 本地文件，通过 file_storage 系统获取永久访问 URL
            from app.models.file_metadata_model import FileMetadata
            from app.core.config import settings
            
            file_id = file.upload_file_id
            print("="*50)
            print("file_id",file_id)
            
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

    async def _extract_document_text(self, file_id: uuid.UUID) -> str:
        """
        提取文档文本内容
        
        Args:
            file_id: 文件ID
            
        Returns:
            str: 提取的文本内容
        """
        generic_file = self.db.query(GenericFile).filter(
            GenericFile.id == file_id,
            GenericFile.status == "active"
        ).first()

        if not generic_file:
            raise BusinessException(
                f"文件不存在或已删除: {file_id}",
                BizCode.NOT_FOUND
            )

        # TODO: 根据文件类型提取文本
        # - PDF: 使用 PyPDF2 或 pdfplumber
        # - Word: 使用 python-docx
        # - TXT/MD: 直接读取

        file_ext = generic_file.file_ext.lower()

        if file_ext in ['.txt', '.md', '.markdown']:
            return await self._read_text_file(generic_file.storage_path)
        elif file_ext == '.pdf':
            return await self._extract_pdf_text(generic_file.storage_path)
        elif file_ext in ['.doc', '.docx']:
            return await self._extract_word_text(generic_file.storage_path)
        else:
            return f"[不支持的文档格式: {file_ext}]"

    async def _read_text_file(self, storage_path: str) -> str:
        """读取纯文本文件"""
        try:
            with open(storage_path, 'r', encoding='utf-8') as f:
                return f.read()
        except Exception as e:
            logger.error(f"读取文本文件失败: {e}")
            return f"[文件读取失败: {str(e)}]"

    async def _extract_pdf_text(self, storage_path: str) -> str:
        """提取 PDF 文本"""
        try:
            # TODO: 实现 PDF 文本提取
            # import PyPDF2 或 pdfplumber
            return "[PDF 文本提取功能待实现]"
        except Exception as e:
            logger.error(f"提取 PDF 文本失败: {e}")
            return f"[PDF 提取失败: {str(e)}]"

    async def _extract_word_text(self, storage_path: str) -> str:
        """提取 Word 文档文本"""
        try:
            # TODO: 实现 Word 文本提取
            # import docx
            return "[Word 文本提取功能待实现]"
        except Exception as e:
            logger.error(f"提取 Word 文本失败: {e}")
            return f"[Word 提取失败: {str(e)}]"


def get_multimodal_service(db: Session) -> MultimodalService:
    """获取多模态服务实例（依赖注入）"""
    return MultimodalService(db)
