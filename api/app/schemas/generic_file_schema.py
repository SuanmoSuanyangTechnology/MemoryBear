"""
Schemas for Generic File Upload System
"""
from pydantic import BaseModel, Field, ConfigDict
from typing import Optional, Dict, Any
import datetime
import uuid

from app.core.upload_enums import UploadContext


class GenericFileBase(BaseModel):
    """Base schema for generic file"""
    file_name: str = Field(..., description="文件名")
    context: UploadContext = Field(..., description="上传上下文")
    is_public: bool = Field(False, description="是否公开")
    file_metadata: Optional[Dict[str, Any]] = Field(default={}, description="文件元数据")


class GenericFileCreate(GenericFileBase):
    """Schema for creating a generic file"""
    tenant_id: uuid.UUID
    created_by: uuid.UUID
    file_ext: str
    file_size: int
    mime_type: Optional[str] = None
    storage_path: str


class GenericFileResponse(BaseModel):
    """Schema for generic file response"""
    id: uuid.UUID = Field(..., description="文件ID")
    file_name: str = Field(..., description="文件名")
    file_ext: str = Field(..., description="文件扩展名")
    file_size: int = Field(..., description="文件大小（字节）")
    mime_type: Optional[str] = Field(None, description="MIME类型")
    context: str = Field(..., description="上传上下文")
    access_url: Optional[str] = Field(None, description="访问URL")
    is_public: bool = Field(..., description="是否公开")
    file_metadata: Dict[str, Any] = Field(default={}, description="文件元数据")
    status: str = Field(..., description="文件状态")
    model_config = ConfigDict(from_attributes=True)

    created_at: datetime.datetime = Field(..., description="创建时间")
    updated_at: datetime.datetime = Field(..., description="更新时间")


class FileMetadataUpdate(BaseModel):
    """Schema for updating file metadata"""
    file_name: Optional[str] = Field(None, description="文件名")
    file_metadata: Optional[Dict[str, Any]] = Field(None, description="文件元数据")
    is_public: Optional[bool] = Field(None, description="是否公开")


class UploadResultSchema(BaseModel):
    """Schema for upload result"""
    success: bool = Field(..., description="是否成功")
    file_id: Optional[uuid.UUID] = Field(None, description="文件ID")
    file_name: str = Field(..., description="文件名")
    error: Optional[str] = Field(None, description="错误信息")
    file_info: Optional[GenericFileResponse] = Field(None, description="文件信息")


class BatchUploadResponse(BaseModel):
    """Schema for batch upload response"""
    total: int = Field(..., description="总文件数")
    success_count: int = Field(..., description="成功数量")
    failed_count: int = Field(..., description="失败数量")
    results: list[UploadResultSchema] = Field(..., description="上传结果列表")
