from enum import Enum


class UploadContext(str, Enum):
    """上传上下文枚举，定义文件上传的目的和分类"""
    AVATAR = "avatar"
    APP_ICON = "app_icon"
    KNOWLEDGE_BASE = "knowledge_base"
    TEMP = "temp"
    ATTACHMENT = "attachment"
