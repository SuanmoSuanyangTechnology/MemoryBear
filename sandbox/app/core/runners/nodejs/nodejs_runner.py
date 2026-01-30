"""Nodejs code runner"""
import asyncio
import os
import uuid
from typing import Optional

from app.core.executor import CodeExecutor, ExecutionResult
from app.core.runners.nodejs.env import check_lib_avaiable, release_lib_binary, LIB_PATH
from app.logger import get_logger
from app.models import RunnerOptions

# Nodejs sandbox prescript template
with open("app/core/runners/nodejs/prescript.js") as f:
    NODEJS_PRESCRIPT = f.read()

logger = get_logger()


class NodejsRunner(CodeExecutor):
    """Node.js code runner with security isolation"""

    def __init__(self):
        super().__init__()

    @staticmethod
    def init_environment(code: str, preload: str) -> str:
        if not check_lib_avaiable():
            release_lib_binary(False)
        code_file_name = uuid.uuid4().hex.replace("-", "_")

        script = NODEJS_PRESCRIPT.replace("{{preload}}", preload, 1)

        eval_code = f"eval(Buffer.from('{code}', 'base64').toString('utf-8'))"
        script = script.replace("{{code}}", eval_code, 1)

        code_path = f"{LIB_PATH}/node_temp/tmp/{code_file_name}.js"
        try:
            os.makedirs(os.path.dirname(code_path), mode=0o755, exist_ok=True)
            with open(code_path, "w", encoding="utf-8") as f:
                f.write(script)
            os.chmod(code_path, 0o755)

        except OSError as e:
            raise RuntimeError(f"Failed to write {code_path}") from e

        return code_path

    async def run(
            self,
            code: str,
            options: RunnerOptions,
            preload: str = "",
            timeout: Optional[int] = None
    ) -> ExecutionResult:
        """Run Python code in sandbox

        Args:
            options:
            code: Base64 encoded encrypted code
            preload: Preload code to execute before main code
            timeout: Execution timeout in seconds

        Returns:
            ExecutionResult with stdout, stderr, and exit code
        """
        config = self.config

        if timeout is None:
            timeout = config.worker_timeout

        # Check if preload is allowed
        if not preload or not config.enable_preload:
            preload = ""
        script_path = self.init_environment(code, preload)

        try:
            # Setup environment
            env = {
                "UV_USE_IO_URING": "0"
            }

            # Add proxy settings if configured
            if config.proxy.socks5:
                env["HTTPS_PROXY"] = config.proxy.socks5
                env["HTTP_PROXY"] = config.proxy.socks5
            elif config.proxy.https or config.proxy.http:
                if config.proxy.https:
                    env["HTTPS_PROXY"] = config.proxy.https
                if config.proxy.http:
                    env["HTTP_PROXY"] = config.proxy.http

            # Add allowed syscalls if configured
            if config.allowed_syscalls:
                env["ALLOWED_SYSCALLS"] = ",".join(map(str, config.allowed_syscalls))

            process = await asyncio.create_subprocess_exec(
                config.nodejs_path,
                script_path,
                LIB_PATH,
                str(config.sandbox_uid),
                str(config.sandbox_gid),
                options.model_dump_json(),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
                cwd=LIB_PATH
            )

            # Wait for completion with timeout
            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(),
                    timeout=timeout
                )

                return ExecutionResult(
                    stdout=stdout.decode('utf-8', errors='replace'),
                    stderr=stderr.decode('utf-8', errors='replace'),
                    exit_code=process.returncode
                )

            except asyncio.TimeoutError:
                # Kill process on timeout
                try:
                    process.kill()
                    await process.wait()
                except:
                    pass

                return ExecutionResult(
                    stdout="",
                    stderr="Execution timeout",
                    exit_code=-1,
                )

        finally:
            # Cleanup temporary file
            self.cleanup_temp_file(script_path)
