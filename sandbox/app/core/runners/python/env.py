import asyncio
import tempfile
import stat
from pathlib import Path

from app.config import get_config
from app.core.runners.python.settings import LIB_PATH
from app.logger import get_logger

logger = get_logger()


async def prepare_python_dependencies_env():
    config = get_config()

    with tempfile.TemporaryDirectory(dir="/") as root_path:
        root = Path(root_path)

        env_sh = root / "env.sh"
        with open("script/env.sh") as f:
            env_sh.write_text(f.read())
        env_sh.chmod(env_sh.stat().st_mode | stat.S_IXUSR)

        for lib_path in config.python_lib_paths:
            lib_path = Path(lib_path)

            if not lib_path.exists():
                logger.warning("python lib path %s is not available", lib_path)
                continue

            cmd = [
                "bash",
                str(env_sh),
                str(lib_path),
                str(LIB_PATH),
            ]

            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )

            stdout, stderr = await process.communicate()
            retcode = process.returncode

            if retcode != 0:
                logger.error(
                    f"create env error for file {lib_path}: retcode={retcode}, stderr={stderr.decode()}"
                )
