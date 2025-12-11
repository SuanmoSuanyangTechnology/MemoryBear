"""
File validator for generic file upload system.
Validates file size, type, content, and upload policies.
"""
import mimetypes
from typing import Optional, List
from dataclasses import dataclass
from fastapi import UploadFile

from app.core.upload_policies import UploadPolicy
from app.core.exceptions import BusinessException
from app.core.error_codes import BizCode


# Magic numbers for common file types (first few bytes of file)
MAGIC_NUMBERS = {
    # Images
    b'\xFF\xD8\xFF': ['.jpg', '.jpeg'],
    b'\x89PNG\r\n\x1a\n': ['.png'],
    b'GIF87a': ['.gif'],
    b'GIF89a': ['.gif'],
    b'RIFF': ['.webp'],  # Note: WEBP has additional checks needed
    b'<svg': ['.svg'],
    b'<?xml': ['.svg'],
    
    # Documents
    b'%PDF': ['.pdf'],
    b'PK\x03\x04': ['.docx', '.xlsx', '.zip'],  # ZIP-based formats
    b'\xD0\xCF\x11\xE0\xA1\xB1\x1A\xE1': ['.doc', '.xls'],  # MS Office old format
    
    # Text files (no specific magic number, will be validated differently)
}


@dataclass
class ValidationResult:
    """Result of a validation operation."""
    is_valid: bool
    error_message: Optional[str] = None
    error_code: Optional[int] = None


class FileUploadError(BusinessException):
    """Base exception for file upload errors."""
    pass


class FileSizeExceededError(FileUploadError):
    """Exception raised when file size exceeds the limit."""
    def __init__(self, max_size: int, actual_size: int):
        super().__init__(
            f"文件大小 {actual_size} 字节超过限制 {max_size} 字节",
            code=BizCode.BAD_REQUEST
        )
        self.max_size = max_size
        self.actual_size = actual_size


class FileTypeNotAllowedError(FileUploadError):
    """Exception raised when file type is not allowed."""
    def __init__(self, file_type: str, allowed_types: List[str]):
        allowed_str = ', '.join(allowed_types) if allowed_types else '任意类型'
        super().__init__(
            f"文件类型 '{file_type}' 不在允许列表中: {allowed_str}",
            code=BizCode.BAD_REQUEST
        )
        self.file_type = file_type
        self.allowed_types = allowed_types


class EmptyFileError(FileUploadError):
    """Exception raised when file is empty."""
    def __init__(self):
        super().__init__(
            "文件内容为空，无法上传",
            code=BizCode.BAD_REQUEST
        )


class FileContentMismatchError(FileUploadError):
    """Exception raised when file content doesn't match its extension."""
    def __init__(self, extension: str):
        super().__init__(
            f"文件内容与扩展名 '{extension}' 不匹配，可能存在文件类型伪装",
            code=BizCode.BAD_REQUEST
        )
        self.extension = extension


class FileValidator:
    """
    Validator for file uploads.
    Validates file size, type, content, and upload policies.
    """
    
    def __init__(self):
        """Initialize the file validator."""
        # Initialize mimetypes
        mimetypes.init()
    
    def validate_file_size(self, file_size: int, max_size: int) -> ValidationResult:
        """
        Validate that file size does not exceed the maximum allowed size.
        
        Args:
            file_size: Size of the file in bytes
            max_size: Maximum allowed size in bytes
            
        Returns:
            ValidationResult indicating if validation passed
        """
        if file_size == 0:
            return ValidationResult(
                is_valid=False,
                error_message="文件大小为 0 字节，无法上传空文件",
                error_code=BizCode.BAD_REQUEST
            )
        
        if file_size > max_size:
            return ValidationResult(
                is_valid=False,
                error_message=f"文件大小 {file_size} 字节超过限制 {max_size} 字节",
                error_code=BizCode.BAD_REQUEST
            )
        
        return ValidationResult(is_valid=True)
    
    def validate_file_type(
        self,
        file_extension: str,
        allowed_extensions: List[str],
        mime_type: Optional[str] = None,
        allowed_mime_types: Optional[List[str]] = None
    ) -> ValidationResult:
        """
        Validate file type based on extension and MIME type.
        
        Args:
            file_extension: File extension (e.g., '.jpg')
            allowed_extensions: List of allowed extensions (empty list means all allowed)
            mime_type: MIME type of the file (optional)
            allowed_mime_types: List of allowed MIME types (empty list means all allowed)
            
        Returns:
            ValidationResult indicating if validation passed
        """
        # Normalize extension to lowercase and ensure it starts with a dot
        if not file_extension.startswith('.'):
            file_extension = f'.{file_extension}'
        file_extension = file_extension.lower()
        
        # If allowed_extensions is empty, all extensions are allowed
        if allowed_extensions:
            # Normalize allowed extensions
            normalized_allowed = [ext.lower() if ext.startswith('.') else f'.{ext.lower()}' 
                                 for ext in allowed_extensions]
            
            if file_extension not in normalized_allowed:
                return ValidationResult(
                    is_valid=False,
                    error_message=f"文件扩展名 '{file_extension}' 不在允许列表中: {', '.join(normalized_allowed)}",
                    error_code=BizCode.BAD_REQUEST
                )
        
        # Validate MIME type if provided
        if mime_type and allowed_mime_types:
            mime_type_lower = mime_type.lower()
            allowed_mime_lower = [mt.lower() for mt in allowed_mime_types]
            
            if mime_type_lower not in allowed_mime_lower:
                return ValidationResult(
                    is_valid=False,
                    error_message=f"文件 MIME 类型 '{mime_type}' 不在允许列表中: {', '.join(allowed_mime_types)}",
                    error_code=BizCode.BAD_REQUEST
                )
        
        return ValidationResult(is_valid=True)
    
    def validate_file_content(
        self,
        file_content: bytes,
        file_extension: str
    ) -> ValidationResult:
        """
        Validate file content by checking magic numbers (file signatures).
        This helps prevent file type spoofing.
        
        Args:
            file_content: First bytes of the file content (at least 16 bytes recommended)
            file_extension: Expected file extension
            
        Returns:
            ValidationResult indicating if validation passed
        """
        if not file_content:
            return ValidationResult(
                is_valid=False,
                error_message="文件内容为空",
                error_code=BizCode.BAD_REQUEST
            )
        
        # Normalize extension
        if not file_extension.startswith('.'):
            file_extension = f'.{file_extension}'
        file_extension = file_extension.lower()
        
        # For text-based files, we skip magic number validation
        text_extensions = ['.txt', '.md', '.csv', '.json', '.xml', '.html', '.css', '.js']
        if file_extension in text_extensions:
            return ValidationResult(is_valid=True)
        
        # Check magic numbers for binary files
        content_matched = False
        for magic_bytes, extensions in MAGIC_NUMBERS.items():
            if file_content.startswith(magic_bytes):
                # Special handling for WEBP (needs additional check)
                if magic_bytes == b'RIFF' and len(file_content) >= 12:
                    if file_content[8:12] == b'WEBP':
                        extensions = ['.webp']
                    else:
                        continue
                
                # Check if the detected type matches the extension
                if file_extension in extensions:
                    content_matched = True
                    break
                else:
                    # Content doesn't match extension
                    return ValidationResult(
                        is_valid=False,
                        error_message=f"文件内容与扩展名 '{file_extension}' 不匹配，检测到的类型为: {', '.join(extensions)}",
                        error_code=BizCode.BAD_REQUEST
                    )
        
        # If we checked for magic numbers but didn't find a match, it might be an issue
        # However, for some file types (like .docx, .xlsx which are ZIP files), 
        # we allow them through if they match the ZIP signature
        if not content_matched and file_extension in ['.docx', '.xlsx', '.zip']:
            if file_content.startswith(b'PK\x03\x04'):
                content_matched = True
        
        # For file types we don't have magic numbers for, we'll allow them through
        # This is a pragmatic approach - we validate what we can
        return ValidationResult(is_valid=True)
    
    def validate_upload_policy(
        self,
        file: UploadFile,
        policy: UploadPolicy
    ) -> ValidationResult:
        """
        Validate a file against an upload policy.
        This is a comprehensive validation that checks size, type, and content.
        
        Args:
            file: The uploaded file
            policy: The upload policy to validate against
            
        Returns:
            ValidationResult indicating if validation passed
        """
        # Get file extension from filename
        filename = file.filename or ""
        file_extension = ""
        if "." in filename:
            file_extension = "." + filename.rsplit(".", 1)[1].lower()
        
        # Get file size
        file.file.seek(0, 2)  # Seek to end
        file_size = file.file.tell()
        file.file.seek(0)  # Reset to beginning
        
        # Validate file size
        size_result = self.validate_file_size(file_size, policy.max_file_size)
        if not size_result.is_valid:
            return size_result
        
        # Get MIME type
        mime_type = file.content_type
        
        # Validate file type (extension and MIME type)
        type_result = self.validate_file_type(
            file_extension,
            policy.allowed_extensions,
            mime_type,
            policy.allowed_mime_types
        )
        if not type_result.is_valid:
            return type_result
        
        # Read first bytes for content validation (read up to 16 bytes for magic number check)
        file_content = file.file.read(16)
        file.file.seek(0)  # Reset to beginning
        
        # Validate file content (magic numbers)
        content_result = self.validate_file_content(file_content, file_extension)
        if not content_result.is_valid:
            return content_result
        
        return ValidationResult(is_valid=True)
    
    def validate_and_raise(
        self,
        file: UploadFile,
        policy: UploadPolicy
    ) -> None:
        """
        Validate a file against a policy and raise an exception if validation fails.
        This is a convenience method for use in services.
        
        Args:
            file: The uploaded file
            policy: The upload policy to validate against
            
        Raises:
            FileSizeExceededError: If file size exceeds limit
            FileTypeNotAllowedError: If file type is not allowed
            EmptyFileError: If file is empty
            FileContentMismatchError: If file content doesn't match extension
        """
        result = self.validate_upload_policy(file, policy)
        
        if not result.is_valid:
            # Determine which specific error to raise based on the error message
            if "大小" in result.error_message and "超过" in result.error_message:
                # Extract sizes from the validation result
                filename = file.filename or ""
                file_extension = ""
                if "." in filename:
                    file_extension = "." + filename.rsplit(".", 1)[1].lower()
                
                file.file.seek(0, 2)
                file_size = file.file.tell()
                file.file.seek(0)
                
                raise FileSizeExceededError(policy.max_file_size, file_size)
            
            elif "为 0 字节" in result.error_message or "内容为空" in result.error_message:
                raise EmptyFileError()
            
            elif "不匹配" in result.error_message:
                filename = file.filename or ""
                file_extension = ""
                if "." in filename:
                    file_extension = "." + filename.rsplit(".", 1)[1].lower()
                raise FileContentMismatchError(file_extension)
            
            elif "扩展名" in result.error_message or "MIME 类型" in result.error_message:
                filename = file.filename or ""
                file_extension = ""
                if "." in filename:
                    file_extension = "." + filename.rsplit(".", 1)[1].lower()
                raise FileTypeNotAllowedError(file_extension, policy.allowed_extensions)
            
            else:
                # Generic error
                raise FileUploadError(result.error_message, code=result.error_code)
