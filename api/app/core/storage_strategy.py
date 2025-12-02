"""
Storage strategy interface and concrete implementations for file upload system.
"""
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Dict, Any
import uuid

from app.core.upload_enums import UploadContext
from app.core.upload_policies import UploadPolicy, get_upload_policy
from app.core.config import settings


class StorageStrategy(ABC):
    """Abstract base class for storage strategies."""
    
    @abstractmethod
    def get_storage_path(
        self,
        tenant_id: uuid.UUID,
        file_id: uuid.UUID,
        file_extension: str,
        metadata: Dict[str, Any]
    ) -> Path:
        """
        Generate the storage path for a file.
        
        Args:
            tenant_id: The tenant ID
            file_id: The unique file ID
            file_extension: The file extension (e.g., ".jpg")
            metadata: Additional metadata that may influence path generation
            
        Returns:
            Path object representing the file storage location
        """
        pass
    
    @abstractmethod
    def get_upload_policy(self) -> UploadPolicy:
        """
        Get the upload policy for this storage strategy.
        
        Returns:
            UploadPolicy object with constraints and rules
        """
        pass


class AvatarStorageStrategy(StorageStrategy):
    """Storage strategy for user avatar files."""
    
    def get_storage_path(
        self,
        tenant_id: uuid.UUID,
        file_id: uuid.UUID,
        file_extension: str,
        metadata: Dict[str, Any]
    ) -> Path:
        """
        Generate storage path for avatar files.
        Path format: {GENERIC_FILE_PATH}/avatars/{tenant_id}/{file_id}{extension}
        """
        base_path = Path(settings.GENERIC_FILE_PATH)
        return base_path / "avatars" / str(tenant_id) / f"{file_id}{file_extension}"
    
    def get_upload_policy(self) -> UploadPolicy:
        """Get upload policy for avatar context."""
        return get_upload_policy(UploadContext.AVATAR)


class AppIconStorageStrategy(StorageStrategy):
    """Storage strategy for application icon files."""
    
    def get_storage_path(
        self,
        tenant_id: uuid.UUID,
        file_id: uuid.UUID,
        file_extension: str,
        metadata: Dict[str, Any]
    ) -> Path:
        """
        Generate storage path for app icon files.
        Path format: {GENERIC_FILE_PATH}/app_icons/{tenant_id}/{file_id}{extension}
        """
        base_path = Path(settings.GENERIC_FILE_PATH)
        return base_path / "app_icons" / str(tenant_id) / f"{file_id}{file_extension}"
    
    def get_upload_policy(self) -> UploadPolicy:
        """Get upload policy for app_icon context."""
        return get_upload_policy(UploadContext.APP_ICON)


class KnowledgeBaseStorageStrategy(StorageStrategy):
    """Storage strategy for knowledge base files."""
    
    def get_storage_path(
        self,
        tenant_id: uuid.UUID,
        file_id: uuid.UUID,
        file_extension: str,
        metadata: Dict[str, Any]
    ) -> Path:
        """
        Generate storage path for knowledge base files.
        Path format: {GENERIC_FILE_PATH}/knowledge_base/{tenant_id}/{kb_id}/{file_id}{extension}
        
        If kb_id is provided in metadata, it will be included in the path for compatibility
        with existing knowledge base file structure.
        """
        base_path = Path(settings.GENERIC_FILE_PATH)
        kb_id = metadata.get("kb_id")
        
        if kb_id:
            # Include kb_id in path for compatibility with existing structure
            return base_path / "knowledge_base" / str(tenant_id) / str(kb_id) / f"{file_id}{file_extension}"
        else:
            # Default path without kb_id
            return base_path / "knowledge_base" / str(tenant_id) / f"{file_id}{file_extension}"
    
    def get_upload_policy(self) -> UploadPolicy:
        """Get upload policy for knowledge_base context."""
        return get_upload_policy(UploadContext.KNOWLEDGE_BASE)


class TempStorageStrategy(StorageStrategy):
    """Storage strategy for temporary files."""
    
    def get_storage_path(
        self,
        tenant_id: uuid.UUID,
        file_id: uuid.UUID,
        file_extension: str,
        metadata: Dict[str, Any]
    ) -> Path:
        """
        Generate storage path for temporary files.
        Path format: {GENERIC_FILE_PATH}/temp/{tenant_id}/{file_id}{extension}
        """
        base_path = Path(settings.GENERIC_FILE_PATH)
        return base_path / "temp" / str(tenant_id) / f"{file_id}{file_extension}"
    
    def get_upload_policy(self) -> UploadPolicy:
        """Get upload policy for temp context."""
        return get_upload_policy(UploadContext.TEMP)


class AttachmentStorageStrategy(StorageStrategy):
    """Storage strategy for attachment files."""
    
    def get_storage_path(
        self,
        tenant_id: uuid.UUID,
        file_id: uuid.UUID,
        file_extension: str,
        metadata: Dict[str, Any]
    ) -> Path:
        """
        Generate storage path for attachment files.
        Path format: {GENERIC_FILE_PATH}/attachments/{tenant_id}/{file_id}{extension}
        """
        base_path = Path(settings.GENERIC_FILE_PATH)
        return base_path / "attachments" / str(tenant_id) / f"{file_id}{file_extension}"
    
    def get_upload_policy(self) -> UploadPolicy:
        """Get upload policy for attachment context."""
        return get_upload_policy(UploadContext.ATTACHMENT)


class StrategyFactory:
    """Factory class for creating storage strategies based on upload context."""
    
    _strategies = {
        UploadContext.AVATAR: AvatarStorageStrategy,
        UploadContext.APP_ICON: AppIconStorageStrategy,
        UploadContext.KNOWLEDGE_BASE: KnowledgeBaseStorageStrategy,
        UploadContext.TEMP: TempStorageStrategy,
        UploadContext.ATTACHMENT: AttachmentStorageStrategy,
    }
    
    @classmethod
    def get_strategy(cls, context: UploadContext) -> StorageStrategy:
        """
        Get the appropriate storage strategy for the given context.
        
        Args:
            context: The upload context
            
        Returns:
            An instance of the appropriate StorageStrategy
            
        Raises:
            ValueError: If no strategy is defined for the given context
        """
        strategy_class = cls._strategies.get(context)
        if strategy_class is None:
            raise ValueError(f"No storage strategy defined for context: {context}")
        return strategy_class()
