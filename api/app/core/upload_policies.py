from dataclasses import dataclass
from typing import List, Optional
from app.core.upload_enums import UploadContext


@dataclass
class UploadPolicy:
    """上传策略，定义文件大小限制、允许的文件类型等规则"""
    max_file_size: int  # 最大文件大小（字节）
    allowed_extensions: List[str]  # 允许的文件扩展名列表
    allowed_mime_types: List[str]  # 允许的 MIME 类型列表
    require_authentication: bool = True  # 是否需要认证
    enable_virus_scan: bool = False  # 是否启用病毒扫描
    enable_compression: bool = False  # 是否启用压缩
    auto_delete_after_days: Optional[int] = None  # 自动删除天数（None 表示不自动删除）


# 各上下文的上传策略配置
UPLOAD_POLICIES = {
    UploadContext.AVATAR: UploadPolicy(
        max_file_size=5 * 1024 * 1024,  # 5MB
        allowed_extensions=[".jpg", ".jpeg", ".png", ".gif", ".webp"],
        allowed_mime_types=["image/jpeg", "image/png", "image/gif", "image/webp"],
        require_authentication=True,
        enable_compression=True,
    ),
    UploadContext.APP_ICON: UploadPolicy(
        max_file_size=2 * 1024 * 1024,  # 2MB
        allowed_extensions=[".jpg", ".jpeg", ".png", ".svg"],
        allowed_mime_types=["image/jpeg", "image/png", "image/svg+xml"],
        require_authentication=True,
        enable_compression=True,
    ),
    UploadContext.KNOWLEDGE_BASE: UploadPolicy(
        max_file_size=50 * 1024 * 1024,  # 50MB
        allowed_extensions=[".pdf", ".doc", ".docx", ".txt", ".md", ".xlsx", ".csv"],
        allowed_mime_types=[
            "application/pdf",
            "application/msword",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "text/plain",
            "text/markdown",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "text/csv",
        ],
        require_authentication=True,
        enable_virus_scan=True,
    ),
    UploadContext.TEMP: UploadPolicy(
        max_file_size=10 * 1024 * 1024,  # 10MB
        allowed_extensions=[],  # 允许所有类型
        allowed_mime_types=[],  # 允许所有类型
        require_authentication=True,
        auto_delete_after_days=7,
    ),
    UploadContext.ATTACHMENT: UploadPolicy(
        max_file_size=20 * 1024 * 1024,  # 20MB
        allowed_extensions=[],  # 允许所有类型
        allowed_mime_types=[],  # 允许所有类型
        require_authentication=True,
    ),
}


def get_upload_policy(context: UploadContext) -> UploadPolicy:
    """
    根据上传上下文获取对应的上传策略
    
    Args:
        context: 上传上下文
        
    Returns:
        对应的上传策略
        
    Raises:
        ValueError: 如果上下文不存在对应的策略
    """
    if context not in UPLOAD_POLICIES:
        raise ValueError(f"未定义上传上下文 '{context}' 的策略")
    return UPLOAD_POLICIES[context]
