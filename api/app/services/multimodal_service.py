"""
多模态文件处理服务

处理图片、文档等多模态文件，转换为 LLM 可用的格式

格式说明：
- 当前使用通义千问格式
- 通义千问格式: {"type": "image", "image": "url"}
"""
import uuid
from typing import List, Dict, Any, Optional
from sqlalchemy.orm import Session

from app.core.logging_config import get_business_logger
from app.core.exceptions import BusinessException
from app.core.error_codes import BizCode
from app.schemas.app_schema import FileInput, FileType, TransferMethod
from app.models.generic_file_model import GenericFile

logger = get_business_logger()


class MultimodalService:
    """多模态文件处理服务"""
    
    def __init__(self, db: Session):
        self.db = db
    
    async def process_files(
        self, 
        files: Optional[List[FileInput]]
    ) -> List[Dict[str, Any]]:
        """
        处理文件列表，返回 LLM 可用的格式
        
        Args:
            files: 文件输入列表
            
        Returns:
            List[Dict]: LLM 可用的内容格式列表
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
        
        logger.info(f"成功处理 {len(result)}/{len(files)} 个文件")
        return result
    
    async def _process_image(self, file: FileInput) -> Dict[str, Any]:
        """
        处理图片文件
        
        Args:
            file: 图片文件输入
            
        Returns:
            Dict: 通义千问格式
        """
        if file.transfer_method == TransferMethod.REMOTE_URL:
            # 远程 URL，使用通义千问格式
            return {
                "type": "image",
                "image": file.url
            }
        else:
            # 本地文件，获取访问 URL
            url = await self._get_file_url(file.upload_file_id)
            return {
                "type": "image",
                "image": url
            }
    
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
    
    async def _get_file_url(self, file_id: uuid.UUID) -> str:
        """
        获取文件的访问 URL
        
        Args:
            file_id: 文件ID
            
        Returns:
            str: 文件访问 URL
            
        Raises:
            BusinessException: 文件不存在
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
        
        # 如果有 access_url，直接返回
        if generic_file.access_url:
            return generic_file.access_url
        
        # 否则，根据 storage_path 生成 URL
        # TODO: 根据实际存储方式生成 URL（本地存储、OSS 等）
        # 这里暂时返回一个占位 URL
        return f"/api/files/{file_id}/download"
    
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
