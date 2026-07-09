"""Data models for the orchestrator API"""
import time
import uuid
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class SandboxStatus(str, Enum):
    CREATING = "creating"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPING = "stopping"
    STOPPED = "stopped"
    ERROR = "error"


class CreateSandboxRequest(BaseModel):
    """Request to create a new sandbox"""
    template_id: str = Field(description="Template ID to use")
    timeout: int = Field(default=300, description="Sandbox timeout in seconds", ge=10, le=3600)
    env_vars: dict[str, str] = Field(default_factory=dict, description="Environment variables")
    metadata: dict[str, str] = Field(default_factory=dict, description="Custom metadata")
    cpu_count: int = Field(default=2, ge=1, le=8, description="Number of vCPUs")
    memory_mb: int = Field(default=512, ge=256, le=4096, description="Memory in MB")
    enable_network: bool = Field(default=True, description="Enable network access")


class SandboxInfo(BaseModel):
    """Sandbox information"""
    sandbox_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    template_id: str
    status: SandboxStatus = SandboxStatus.CREATING
    created_at: float = Field(default_factory=time.time)
    timeout: int = 300
    expires_at: float = 0
    cpu_count: int = 2
    memory_mb: int = 512
    env_vars: dict[str, str] = Field(default_factory=dict)
    metadata: dict[str, str] = Field(default_factory=dict)
    # Internal
    vm_ip: Optional[str] = None
    vm_pid: Optional[int] = None
    socket_path: Optional[str] = None


class CommandRequest(BaseModel):
    """Request to run a command in a sandbox"""
    cmd: str = Field(description="Command to execute")
    timeout: int = Field(default=60, ge=1, le=1800, description="Command timeout in seconds")
    env_vars: dict[str, str] = Field(default_factory=dict, description="Additional env vars for this command")
    cwd: str = Field(default="/app", description="Working directory")


class CommandResult(BaseModel):
    """Result of a command execution"""
    stdout: str = ""
    stderr: str = ""
    exit_code: int = -1
    duration_ms: int = 0


class FileWriteRequest(BaseModel):
    """Request to write a file in the sandbox"""
    path: str = Field(description="File path inside sandbox")
    content: str = Field(description="File content (base64 encoded for binary)")
    is_base64: bool = Field(default=False)


class FileReadResponse(BaseModel):
    """Response for reading a file"""
    content: str
    is_base64: bool = False


class TemplateInfo(BaseModel):
    """Template information"""
    template_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    dockerfile_content: Optional[str] = None
    created_at: float = Field(default_factory=time.time)
    status: str = "ready"  # building, ready, error
    rootfs_path: Optional[str] = None
    size_bytes: int = 0


class BuildTemplateRequest(BaseModel):
    """Request to build a template"""
    name: str = Field(description="Template name")
    dockerfile: str = Field(description="Dockerfile content")
    build_args: dict[str, str] = Field(default_factory=dict)
