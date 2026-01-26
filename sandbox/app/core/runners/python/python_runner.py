"""Python code runner"""
import asyncio
import base64
import os
import uuid
from typing import Optional

from app.config import SANDBOX_USER_ID, SANDBOX_GROUP_ID, get_config
from app.core.encryption import generate_key, encrypt_code
from app.core.executor import CodeExecutor, ExecutionResult
from app.core.runners.python.settings import check_lib_avaiable, release_lib_binary, LIB_PATH
from app.models import RunnerOptions

# Python sandbox prescript template
with open("app/core/runners/python/prescript.py") as f:
    PYTHON_PRESCRIPT = f.read()


class PythonRunner(CodeExecutor):
    """Python code runner with security isolation"""

    def __init__(self):
        super().__init__()

    @staticmethod
    def init_enviroment(code: bytes, preload, options: RunnerOptions) -> tuple[str, str]:
        if not check_lib_avaiable():
            release_lib_binary(False)
        config = get_config()
        code_file_name = uuid.uuid4().hex.replace("-", "_")

        script = PYTHON_PRESCRIPT.replace("{{uid}}", str(SANDBOX_USER_ID), 1)
        script = script.replace("{{gid}}", str(SANDBOX_GROUP_ID), 1)
        script = script.replace(
            "{{enable_network}}",
            str(int(options.enable_network and config.enable_network)
                ),
            1
        )
        script = script.replace("{{preload}}", f"{preload}\n", 1)

        key = generate_key(64)

        encoded_code = encrypt_code(code, key)
        encoded_key = base64.b64encode(key).decode("utf-8")

        script = script.replace("{{code}}", encoded_code, 1)

        code_path = f"{LIB_PATH}/tmp/{code_file_name}.py"
        try:
            os.makedirs(os.path.dirname(code_path), mode=0o755, exist_ok=True)
            with open(code_path, "w", encoding="utf-8") as f:
                f.write(script)
            os.chmod(code_path, 0o755)

        except OSError as e:
            raise RuntimeError(f"Failed to write {code_path}") from e

        return code_path, encoded_key

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
        if not config.enable_preload:
            preload = ""
        code = base64.b64decode(code)
        script_path, encoded_key = self.init_enviroment(code, preload, options=options)

        try:
            # Setup environment
            env = {}

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

            # Execute with Python interpreter

            process = await asyncio.create_subprocess_exec(
                config.python_path,
                script_path,
                LIB_PATH,
                encoded_key,
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
                    error="Execution timeout"
                )

        finally:
            # Cleanup temporary file
            self.cleanup_temp_file(script_path)
