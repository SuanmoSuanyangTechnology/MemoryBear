import asyncio
import ctypes
import os
import shutil
import stat
import tempfile
from pathlib import Path

from app.logger import get_logger
from app.config import get_config

logger = get_logger()

RELEASE_LIB_PATH = "./lib/seccomp_redbear/target/release/libnodejs.so"
LIB_PATH = "/var/sandbox/sandbox-nodejs"
LIB_NAME = "libnodejs.so"

lib = ctypes.CDLL(RELEASE_LIB_PATH)
lib.get_lib_version_static.restype = ctypes.c_char_p
lib.get_lib_feature_static.restype = ctypes.c_char_p
logger.info(f"Seccomp Env: nodejs, "
            f"Seccomp Feature: {lib.get_lib_feature_static().decode('utf-8')}, "
            f"Seccomp Version: {lib.get_lib_version_static().decode('utf-8')}")

try:
    with open(RELEASE_LIB_PATH, "rb") as f:
        _NODEJS_LIB = f.read()
except:
    logger.critical("failed to load nodejs lib")
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
                    f.write(_NODEJS_LIB)
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
                f.write(_NODEJS_LIB)
            os.chmod(lib_file, 0o755)
        except OSError:
            logger.critical(f"failed to write {lib_file}")
            raise

        logger.info("nodejs runner environment initialized")


async def prepare_nodejs_dependencies_env():
    config = get_config()

    with tempfile.TemporaryDirectory(dir="/") as root_path:
        root = Path(root_path)

        env_sh = root / "env.sh"
        with open("script/env.sh") as f:
            env_sh.write_text(f.read())
        env_sh.chmod(env_sh.stat().st_mode | stat.S_IXUSR)

        shutil.copytree("dependencies/nodejs", os.path.join(LIB_PATH, "node_temp"), dirs_exist_ok=True)
        for root, dirs, files in os.walk(os.path.join(LIB_PATH, "node_temp")):
            for d in dirs:
                os.chmod(os.path.join(root, d), 0o755)
            for f in files:
                os.chmod(os.path.join(root, f), 0o444)

        for lib_path in config.nodejs_lib_paths:
            lib_path = Path(lib_path)

            if not lib_path.exists():
                logger.warning("nodejs lib path %s is not available", lib_path)
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
