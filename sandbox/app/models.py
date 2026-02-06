"""Data models"""
from typing import Optional, Any

from pydantic import BaseModel, Field


class RunnerOptions(BaseModel):
    enable_network: bool = Field(default=False, description="Sandbox network flag")


class RunCodeRequest(BaseModel):
    """Request model for code execution"""
    language: str = Field(..., description="Programming language (python3 or nodejs)")
    code: str = Field(..., description="Base64 encoded encrypted code")
    preload: Optional[str] = Field(default="", description="Preload code")
    options: RunnerOptions = Field(default_factory=RunnerOptions, description="Enable network access")


class RunCodeResponse(BaseModel):
    """Response model for code execution"""
    stdout: str = Field(default="", description="Standard output")
    stderr: str = Field(default="", description="Standard error")


class DependencyRequest(BaseModel):
    """Request model for dependency operations"""
    language: str = Field(..., description="Programming language")


class UpdateDependencyRequest(BaseModel):
    """Request model for updating dependencies"""
    language: str = Field(..., description="Programming language")
    packages: list[str] = Field(default_factory=list, description="Packages to install")


class Dependency(BaseModel):
    """Dependency information"""
    name: str
    version: str


class ListDependenciesResponse(BaseModel):
    """Response model for listing dependencies"""
    dependencies: list[Dependency] = Field(default_factory=list)


class RefreshDependenciesResponse(BaseModel):
    """Response model for refreshing dependencies"""
    dependencies: list[Dependency] = Field(default_factory=list)


class UpdateDependenciesResponse(BaseModel):
    """Response model for updating dependencies"""
    success: bool = True
    installed: list[str] = Field(default_factory=list)


class HealthResponse(BaseModel):
    """Health check response"""
    status: str = "healthy"
    version: str = "2.0.0"


class ApiResponse(BaseModel):
    """Standard API response wrapper"""
    code: int = Field(default=0, description="Response code (0 for success, negative for error)")
    message: str = Field(default="success", description="Response message")
    data: Optional[Any] = Field(default=None, description="Response data")


def success_response(data: Any) -> ApiResponse:
    """Create success response"""
    return ApiResponse(code=0, message="success", data=data)


def error_response(code: int, message: str) -> ApiResponse:
    """Create error response"""
    return ApiResponse(code=code, message=message, data=None)
