import asyncio
import ctypes
import os
import stat
import tempfile
from pathlib import Path

from app.config import get_config
from app.logger import get_logger

logger = get_logger()

RELEASE_LIB_PATH = "./lib/seccomp_redbear/target/release/libpython.so"
LIB_PATH = "/var/sandbox/sandbox-python"
LIB_NAME = "libpython.so"

lib = ctypes.CDLL(RELEASE_LIB_PATH)
lib.get_lib_version_static.restype = ctypes.c_char_p
lib.get_lib_feature_static.restype = ctypes.c_char_p
logger.info(f"Seccomp Env: python3, "
            f"Seccomp Feature: {lib.get_lib_feature_static().decode('utf-8')}, "
            f"Seccomp Version: {lib.get_lib_version_static().decode('utf-8')}")

try:
    with open(RELEASE_LIB_PATH, "rb") as f:
        _PYTHON_LIB = f.read()
except:
    logger.critical("failed to load python lib")
    raise


def check_lib_avaiable():
    return os.path.exists(os.path.join(LIB_PATH, LIB_NAME))


def release_lib_binary(force_remove: bool):
    logger.info("init runtime enviroment")

    lib_file = os.path.join(LIB_PATH, LIB_NAME)
    if os.path.exists(lib_file):
        if force_remove:
            try:
                os.remove(lib_file)
            except OSError:
                logger.critical(f"failed to remove {os.path.join(LIB_PATH, LIB_NAME)}")
                raise

            try:
                os.makedirs(LIB_PATH, mode=0o755, exist_ok=True)
            except OSError:
                logger.critical(f"failed to create {LIB_PATH}")
                raise

            try:
                with open(lib_file, "wb") as f:
                    f.write(_PYTHON_LIB)
                os.chmod(lib_file, 0o755)
            except OSError:
                logger.critical(f"failed to write {lib_file}")
                raise
    else:
        try:
            os.makedirs(LIB_PATH, mode=0o755, exist_ok=True)
        except OSError:
            logger.critical(f"failed to create {LIB_PATH}")
            raise

        try:
            with open(lib_file, "wb") as f:
                f.write(_PYTHON_LIB)
            os.chmod(lib_file, 0o755)
        except OSError:
            logger.critical(f"failed to write {lib_file}")
            raise

        logger.info("python runner environment initialized")


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
