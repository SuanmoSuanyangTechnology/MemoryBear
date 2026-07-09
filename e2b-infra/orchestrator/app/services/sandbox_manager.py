"""
Sandbox Manager - Core orchestration logic

Manages the lifecycle of Firecracker microVM sandboxes:
- Create: Build rootfs overlay → Spawn Firecracker → Wait for boot → Register
- Execute: Communicate via vsock/ssh to run commands and transfer files
- Kill: Terminate Firecracker process → Cleanup resources

For the initial phase, we use Docker containers as "sandboxes" for faster
iteration. Migration to Firecracker microVMs can happen incrementally.
"""
import asyncio
import base64
import json
import logging
import os
import time
import uuid
from typing import AsyncGenerator, Optional

from app.config import Settings
from app.models import (
    BuildTemplateRequest,
    CommandRequest,
    CommandResult,
    CreateSandboxRequest,
    FileWriteRequest,
    SandboxInfo,
    SandboxStatus,
    TemplateInfo,
)
from app.services.redis_store import RedisStore

logger = logging.getLogger(__name__)


class SandboxManager:
    """Manages sandbox lifecycle

    Phase 1: Docker-based isolation (快速验证)
    Phase 2: Firecracker microVM (生产部署)

    切换方式：通过 SANDBOX_BACKEND 环境变量选择后端
    """

    def __init__(self, redis_store: RedisStore, settings: Settings):
        self.redis_store = redis_store
        self.settings = settings
        self._cleanup_task: Optional[asyncio.Task] = None
        # Backend selection: "docker" (phase 1) or "firecracker" (phase 2)
        self._backend = os.getenv("SANDBOX_BACKEND", "docker")

    async def initialize(self):
        """Initialize sandbox manager, start background cleanup task"""
        self._cleanup_task = asyncio.create_task(self._cleanup_loop())
        logger.info(f"SandboxManager initialized (backend={self._backend})")

    async def shutdown(self):
        """Shutdown: kill all running sandboxes, cancel cleanup"""
        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass

        # Kill all running sandboxes
        sandboxes = await self.redis_store.list_sandboxes()
        for sandbox in sandboxes:
            if sandbox.status in (SandboxStatus.CREATING, SandboxStatus.RUNNING):
                await self._destroy_sandbox_backend(sandbox)
                await self.redis_store.update_sandbox_status(
                    sandbox.sandbox_id, SandboxStatus.STOPPED
                )

        logger.info("SandboxManager shutdown complete")

    async def get_stats(self) -> dict:
        """Get sandbox statistics"""
        sandboxes = await self.redis_store.list_sandboxes()
        running = sum(1 for s in sandboxes if s.status == SandboxStatus.RUNNING)
        creating = sum(1 for s in sandboxes if s.status == SandboxStatus.CREATING)
        return {
            "total": len(sandboxes),
            "running": running,
            "creating": creating,
            "max": self.settings.MAX_SANDBOXES,
        }

    # ──────────────────────────────────────────────────────────
    # Sandbox Lifecycle
    # ──────────────────────────────────────────────────────────

    async def create_sandbox(self, request: CreateSandboxRequest) -> SandboxInfo:
        """Create a new sandbox"""
        # Check capacity
        active_count = await self.redis_store.get_active_sandbox_count()
        if active_count >= self.settings.MAX_SANDBOXES:
            raise RuntimeError(
                f"Maximum sandbox limit reached ({self.settings.MAX_SANDBOXES})"
            )

        # Verify template exists
        template = await self.redis_store.get_template(request.template_id)
        if not template:
            # Try by name
            template = await self.redis_store.get_template_by_name(request.template_id)
        if not template:
            raise RuntimeError(f"Template not found: {request.template_id}")

        # Create sandbox info
        sandbox = SandboxInfo(
            sandbox_id=str(uuid.uuid4()),
            template_id=template.template_id,
            status=SandboxStatus.CREATING,
            created_at=time.time(),
            timeout=request.timeout or self.settings.DEFAULT_SANDBOX_TIMEOUT,
            cpu_count=request.cpu_count,
            memory_mb=request.memory_mb,
            env_vars=request.env_vars,
            metadata=request.metadata,
        )
        sandbox.expires_at = sandbox.created_at + sandbox.timeout

        await self.redis_store.save_sandbox(sandbox)

        # Spawn sandbox in background
        asyncio.create_task(self._spawn_sandbox(sandbox, template))

        return sandbox

    async def get_sandbox(self, sandbox_id: str) -> Optional[SandboxInfo]:
        return await self.redis_store.get_sandbox(sandbox_id)

    async def list_sandboxes(self, status: Optional[str] = None) -> list[SandboxInfo]:
        return await self.redis_store.list_sandboxes(status=status)

    async def kill_sandbox(self, sandbox_id: str) -> bool:
        sandbox = await self.redis_store.get_sandbox(sandbox_id)
        if not sandbox:
            return False

        await self._destroy_sandbox_backend(sandbox)
        await self.redis_store.update_sandbox_status(
            sandbox_id, SandboxStatus.STOPPED
        )
        return True

    async def keepalive(self, sandbox_id: str, timeout: int) -> bool:
        sandbox = await self.redis_store.get_sandbox(sandbox_id)
        if not sandbox or sandbox.status != SandboxStatus.RUNNING:
            return False

        sandbox.timeout = timeout
        sandbox.expires_at = time.time() + timeout
        await self.redis_store.save_sandbox(sandbox)
        return True

    # ──────────────────────────────────────────────────────────
    # Command Execution
    # ──────────────────────────────────────────────────────────

    async def run_command(self, sandbox_id: str, request: CommandRequest) -> CommandResult:
        """Run a command and wait for completion"""
        sandbox = await self.redis_store.get_sandbox(sandbox_id)
        if not sandbox or sandbox.status != SandboxStatus.RUNNING:
            raise RuntimeError("Sandbox is not running")

        start_time = time.time()

        if self._backend == "docker":
            result = await self._docker_exec(sandbox, request)
        else:
            result = await self._firecracker_exec(sandbox, request)

        result.duration_ms = int((time.time() - start_time) * 1000)
        return result

    async def run_command_stream(
        self, sandbox_id: str, request: CommandRequest
    ) -> AsyncGenerator[str, None]:
        """Run a command with streaming output"""
        sandbox = await self.redis_store.get_sandbox(sandbox_id)
        if not sandbox or sandbox.status != SandboxStatus.RUNNING:
            raise RuntimeError("Sandbox is not running")

        if self._backend == "docker":
            async for chunk in self._docker_exec_stream(sandbox, request):
                yield chunk
        else:
            async for chunk in self._firecracker_exec_stream(sandbox, request):
                yield chunk

    # ──────────────────────────────────────────────────────────
    # File Operations
    # ──────────────────────────────────────────────────────────

    async def write_file(
        self, sandbox_id: str, path: str, content: str, is_base64: bool = False
    ):
        """Write a file into the sandbox filesystem"""
        sandbox = await self.redis_store.get_sandbox(sandbox_id)
        if not sandbox or sandbox.status != SandboxStatus.RUNNING:
            raise RuntimeError("Sandbox is not running")

        if is_base64:
            file_content = base64.b64decode(content)
        else:
            file_content = content.encode("utf-8")

        if self._backend == "docker":
            await self._docker_write_file(sandbox, path, file_content)
        else:
            await self._firecracker_write_file(sandbox, path, file_content)

    async def read_file(self, sandbox_id: str, path: str) -> str:
        """Read a file from the sandbox filesystem"""
        sandbox = await self.redis_store.get_sandbox(sandbox_id)
        if not sandbox or sandbox.status != SandboxStatus.RUNNING:
            raise RuntimeError("Sandbox is not running")

        if self._backend == "docker":
            return await self._docker_read_file(sandbox, path)
        else:
            return await self._firecracker_read_file(sandbox, path)

    # ──────────────────────────────────────────────────────────
    # Template Building
    # ──────────────────────────────────────────────────────────

    async def build_template(self, request: BuildTemplateRequest) -> TemplateInfo:
        """Build a template from Dockerfile content"""
        template = TemplateInfo(
            template_id=str(uuid.uuid4()),
            name=request.name,
            dockerfile_content=request.dockerfile,
            status="building",
        )
        await self.redis_store.save_template(template)

        # Build in background
        asyncio.create_task(self._build_template_async(template, request))
        return template

    # ──────────────────────────────────────────────────────────
    # Docker Backend (Phase 1)
    # ──────────────────────────────────────────────────────────

    async def _spawn_sandbox(self, sandbox: SandboxInfo, template: TemplateInfo):
        """Spawn sandbox using Docker backend"""
        try:
            if self._backend == "docker":
                await self._docker_spawn(sandbox, template)
            else:
                await self._firecracker_spawn(sandbox, template)

            sandbox.status = SandboxStatus.RUNNING
            await self.redis_store.save_sandbox(sandbox)
            logger.info(f"Sandbox {sandbox.sandbox_id} is running")
        except Exception as e:
            logger.error(f"Failed to spawn sandbox {sandbox.sandbox_id}: {e}", exc_info=True)
            await self.redis_store.update_sandbox_status(
                sandbox.sandbox_id, SandboxStatus.ERROR
            )

    async def _docker_spawn(self, sandbox: SandboxInfo, template: TemplateInfo):
        """Spawn a Docker container as sandbox"""
        container_name = f"e2b-sandbox-{sandbox.sandbox_id[:12]}"
        image_name = f"e2b-template-{template.name}:latest"

        # Prepare environment variables
        env_args = []
        for k, v in sandbox.env_vars.items():
            env_args.extend(["-e", f"{k}={v}"])

        # Run container
        # Mount host code for workflow execution (ensures app.* is always available)
        host_app_path = os.getenv("E2B_HOST_APP_PATH", "")
        host_stubs_path = os.getenv("E2B_HOST_STUBS_PATH", "")
        volume_args = []
        if host_app_path:
            volume_args.extend(["-v", f"{host_app_path}:/app/app:ro"])
        if host_stubs_path:
            volume_args.extend(["-v", f"{host_stubs_path}:/app/stubs:ro"])

        cmd = [
            "docker", "run", "-d",
            "--name", container_name,
            "--memory", f"{sandbox.memory_mb}m",
            "--cpus", str(sandbox.cpu_count),
            "--network", "e2b-sandbox-net",
            # Ensure PYTHONPATH is set
            "-e", "PYTHONPATH=/app",
            # Security
            "--security-opt", "no-new-privileges",
            "--cap-drop", "ALL",
            "--cap-add", "NET_RAW",  # For network access
            *volume_args,
            *env_args,
            image_name,
            "sleep", "infinity",  # Keep container alive
        ]

        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()

        if process.returncode != 0:
            error_msg = stderr.decode().strip()
            raise RuntimeError(f"Docker run failed: {error_msg}")

        container_id = stdout.decode().strip()[:12]
        sandbox.metadata["container_id"] = container_id
        sandbox.metadata["container_name"] = container_name

        # Get container IP
        ip_cmd = [
            "docker", "inspect", "-f",
            "{{range.NetworkSettings.Networks}}{{.IPAddress}}{{end}}",
            container_name,
        ]
        proc = await asyncio.create_subprocess_exec(
            *ip_cmd, stdout=asyncio.subprocess.PIPE
        )
        ip_stdout, _ = await proc.communicate()
        sandbox.vm_ip = ip_stdout.decode().strip()

    async def _docker_exec(self, sandbox: SandboxInfo, request: CommandRequest) -> CommandResult:
        """Execute command in Docker container"""
        container_name = sandbox.metadata.get("container_name")
        if not container_name:
            raise RuntimeError("Container name not found")

        env_args = []
        for k, v in request.env_vars.items():
            env_args.extend(["-e", f"{k}={v}"])

        cmd = [
            "docker", "exec",
            "-w", request.cwd,
            *env_args,
            container_name,
            "bash", "-c", request.cmd,
        ]

        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                process.communicate(), timeout=request.timeout
            )
            return CommandResult(
                stdout=stdout.decode("utf-8", errors="replace"),
                stderr=stderr.decode("utf-8", errors="replace"),
                exit_code=process.returncode or 0,
            )
        except asyncio.TimeoutError:
            process.kill()
            raise TimeoutError(f"Command timed out after {request.timeout}s")

    async def _docker_exec_stream(
        self, sandbox: SandboxInfo, request: CommandRequest
    ) -> AsyncGenerator[str, None]:
        """Execute command in Docker container with streaming output"""
        container_name = sandbox.metadata.get("container_name")
        if not container_name:
            raise RuntimeError("Container name not found")

        env_args = []
        for k, v in request.env_vars.items():
            env_args.extend(["-e", f"{k}={v}"])

        cmd = [
            "docker", "exec",
            "-w", request.cwd,
            *env_args,
            container_name,
            "bash", "-c", request.cmd,
        ]

        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        try:
            while True:
                line = await asyncio.wait_for(
                    process.stdout.readline(), timeout=request.timeout
                )
                if not line:
                    break
                yield json.dumps({
                    "type": "stdout",
                    "data": line.decode("utf-8", errors="replace").rstrip("\n"),
                })

            # Read remaining stderr
            stderr = await process.stderr.read()
            if stderr:
                yield json.dumps({
                    "type": "stderr",
                    "data": stderr.decode("utf-8", errors="replace"),
                })

            await process.wait()
            yield json.dumps({
                "type": "exit",
                "exit_code": process.returncode,
            })

        except asyncio.TimeoutError:
            process.kill()
            yield json.dumps({
                "type": "error",
                "data": f"Command timed out after {request.timeout}s",
            })

    async def _docker_write_file(self, sandbox: SandboxInfo, path: str, content: bytes):
        """Write file to Docker container"""
        container_name = sandbox.metadata.get("container_name")
        if not container_name:
            raise RuntimeError("Container name not found")

        # Create directory if needed
        dir_path = os.path.dirname(path)
        if dir_path:
            mkdir_cmd = ["docker", "exec", container_name, "mkdir", "-p", dir_path]
            proc = await asyncio.create_subprocess_exec(*mkdir_cmd)
            await proc.wait()

        # Write via stdin pipe + docker exec tee
        cmd = ["docker", "exec", "-i", container_name, "tee", path]
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.DEVNULL,
        )
        await process.communicate(input=content)
        if process.returncode != 0:
            raise RuntimeError(f"Failed to write file: {path}")

    async def _docker_read_file(self, sandbox: SandboxInfo, path: str) -> str:
        """Read file from Docker container"""
        container_name = sandbox.metadata.get("container_name")
        if not container_name:
            raise RuntimeError("Container name not found")

        cmd = ["docker", "exec", container_name, "cat", path]
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()
        if process.returncode != 0:
            raise FileNotFoundError(f"File not found: {path}")
        return stdout.decode("utf-8", errors="replace")

    async def _destroy_sandbox_backend(self, sandbox: SandboxInfo):
        """Destroy sandbox backend resources"""
        if self._backend == "docker":
            container_name = sandbox.metadata.get("container_name")
            if container_name:
                cmd = ["docker", "rm", "-f", container_name]
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                await proc.wait()
        else:
            # Firecracker: kill the VM process
            if sandbox.vm_pid:
                try:
                    os.kill(sandbox.vm_pid, 9)
                except ProcessLookupError:
                    pass

    # ──────────────────────────────────────────────────────────
    # Firecracker Backend (Phase 2 - placeholder)
    # ──────────────────────────────────────────────────────────

    async def _firecracker_spawn(self, sandbox: SandboxInfo, template: TemplateInfo):
        """Spawn Firecracker microVM - Phase 2 implementation"""
        raise NotImplementedError("Firecracker backend not yet implemented. Use SANDBOX_BACKEND=docker")

    async def _firecracker_exec(self, sandbox: SandboxInfo, request: CommandRequest) -> CommandResult:
        raise NotImplementedError("Firecracker backend not yet implemented")

    async def _firecracker_exec_stream(self, sandbox: SandboxInfo, request: CommandRequest):
        raise NotImplementedError("Firecracker backend not yet implemented")

    async def _firecracker_write_file(self, sandbox: SandboxInfo, path: str, content: bytes):
        raise NotImplementedError("Firecracker backend not yet implemented")

    async def _firecracker_read_file(self, sandbox: SandboxInfo, path: str) -> str:
        raise NotImplementedError("Firecracker backend not yet implemented")

    # ──────────────────────────────────────────────────────────
    # Template Building
    # ──────────────────────────────────────────────────────────

    async def _build_template_async(self, template: TemplateInfo, request: BuildTemplateRequest):
        """Build template in background"""
        try:
            image_name = f"e2b-template-{request.name}:latest"

            # Write Dockerfile to temp location
            tmp_dir = f"/tmp/e2b-build-{template.template_id}"
            os.makedirs(tmp_dir, exist_ok=True)

            dockerfile_path = os.path.join(tmp_dir, "Dockerfile")
            with open(dockerfile_path, "w") as f:
                f.write(request.dockerfile)

            # Build Docker image
            build_args = []
            for k, v in request.build_args.items():
                build_args.extend(["--build-arg", f"{k}={v}"])

            cmd = [
                "docker", "build",
                "-t", image_name,
                "-f", dockerfile_path,
                *build_args,
                tmp_dir,
            ]

            logger.info(f"Building template: {request.name}")
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await process.communicate()

            if process.returncode != 0:
                error_msg = stderr.decode().strip()
                logger.error(f"Template build failed: {error_msg}")
                template.status = "error"
                await self.redis_store.save_template(template)
                return

            template.status = "ready"
            template.rootfs_path = image_name
            await self.redis_store.save_template(template)
            logger.info(f"Template {request.name} built successfully")

        except Exception as e:
            logger.error(f"Template build error: {e}", exc_info=True)
            template.status = "error"
            await self.redis_store.save_template(template)

    # ──────────────────────────────────────────────────────────
    # Background Tasks
    # ──────────────────────────────────────────────────────────

    async def _cleanup_loop(self):
        """Background loop to clean up expired sandboxes"""
        while True:
            try:
                await asyncio.sleep(10)  # Check every 10 seconds
                now = time.time()
                sandboxes = await self.redis_store.list_sandboxes()

                for sandbox in sandboxes:
                    if sandbox.status == SandboxStatus.RUNNING and sandbox.expires_at < now:
                        logger.info(
                            f"Sandbox {sandbox.sandbox_id} expired, killing"
                        )
                        await self._destroy_sandbox_backend(sandbox)
                        await self.redis_store.update_sandbox_status(
                            sandbox.sandbox_id, SandboxStatus.STOPPED
                        )
                    elif sandbox.status == SandboxStatus.STOPPED:
                        # Clean up stopped sandboxes after 60s
                        if sandbox.expires_at + 60 < now:
                            await self.redis_store.delete_sandbox(sandbox.sandbox_id)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Cleanup loop error: {e}", exc_info=True)
                await asyncio.sleep(5)
