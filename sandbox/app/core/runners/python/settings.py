import os

from app.logger import get_logger

logger = get_logger()

RELEASE_LIB_PATH = "./lib/seccomp_python/target/release/libpython.so"
LIB_PATH = "/var/sandbox/sandbox-python"
LIB_NAME = "libpython.so"

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
