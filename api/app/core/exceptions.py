"""
业务异常定义
"""
from typing import Any, Dict, Optional
from app.core.error_codes import BizCode


class BusinessException(Exception):
    """业务逻辑异常基类"""

    def __init__(
        self,
        message: str,
        code: BizCode | int | None = None,
        context: Optional[Dict[str, Any]] = None,
        cause: Optional[Exception] = None
    ):
        self.message = message
        self.code = code if code is not None else BizCode.BAD_REQUEST
        # Make a copy of context to avoid modifying the original dict
        self.context = dict(context) if context else {}
        self.cause = cause
        super().__init__(self.message)
    
    def __str__(self) -> str:
        ctx = f", context={self.context}" if self.context else ""
        code_name = self.code.name if isinstance(self.code, BizCode) else str(self.code)
        return f"{code_name}: {self.message}{ctx}"


class ValidationException(BusinessException):
    """数据验证异常"""
    
    def __init__(self, message: str, field: str = None, **kwargs):
        context = {"field": field} if field else {}
        if "context" in kwargs:
            context.update(kwargs.pop("context"))
        super().__init__(message, BizCode.VALIDATION_FAILED, context, **kwargs)


class AuthenticationException(BusinessException):
    """认证异常"""
    
    def __init__(self, message: str = "认证失败", **kwargs):
        super().__init__(message, BizCode.UNAUTHORIZED, **kwargs)


class AuthorizationException(BusinessException):
    """授权异常"""
    
    def __init__(self, message: str = "权限不足", **kwargs):
        super().__init__(message, BizCode.FORBIDDEN, **kwargs)


class ResourceNotFoundException(BusinessException):
    """资源未找到异常"""
    
    def __init__(self, resource_type: str, resource_id: str = None, **kwargs):
        message = f"{resource_type} 不存在"
        context = {"resource_type": resource_type}
        if resource_id:
            context["resource_id"] = resource_id
        if "context" in kwargs:
            context.update(kwargs.pop("context"))
        super().__init__(message, BizCode.FILE_NOT_FOUND, context, **kwargs)


class DuplicateResourceException(BusinessException):
    """资源重复异常"""
    
    def __init__(self, message: str = "资源已存在", **kwargs):
        super().__init__(message, BizCode.DUPLICATE_NAME, **kwargs)


class FileUploadException(BusinessException):
    """文件上传异常"""
    
    def __init__(self, message: str, **kwargs):
        super().__init__(message, BizCode.FILE_READ_ERROR, **kwargs)


class PermissionDeniedException(BusinessException):
    """权限拒绝异常"""
    
    def __init__(self, message: str = "权限不足", **kwargs):
        super().__init__(message, BizCode.FORBIDDEN, **kwargs)


class RateLimitException(BusinessException):
    """限流异常"""
    
    def __init__(self, message: str, code: BizCode = None, rate_headers: dict = None, **kwargs):
        # 如果没有指定错误码，默认使用通用限流错误码
        if code is None:
            code = BizCode.RATE_LIMITED
        
        # 将限流头信息添加到上下文中
        context = kwargs.get("context", {})
        if rate_headers:
            context["rate_limit_headers"] = rate_headers
        kwargs["context"] = context
        
        super().__init__(message, code, **kwargs)