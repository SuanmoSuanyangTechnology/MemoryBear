"""Code runners package"""
import pwd
import subprocess

from app.config import get_config
from app.logger import get_logger

logger = get_logger()


def init_sandbox_user():
    config = get_config()
    sandbox_user = config.sandbox_user
    sandbox_uid = config.sandbox_uid
    try:
        pwd.getpwnam(sandbox_user)
        logger.info(f"User '{sandbox_user}' already exists")
    except KeyError:
        try:
            subprocess.run(
                ["useradd", "-u", str(sandbox_uid), sandbox_user],
                check=True,
                capture_output=True,
                text=True
            )
            logger.info(f"Created user '{sandbox_user}' with UID {sandbox_uid}")
        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to create user: {e.stderr}")
            raise RuntimeError(f"Failed to create user '{sandbox_user}': {e.stderr}") from e

    try:
        user_info = pwd.getpwnam(sandbox_user)
        config.set_sandbox_gid(user_info.pw_gid)
        logger.info(f"Sandbox user GID: {config.sandbox_gid}")
    except KeyError as e:
        logger.error(f"Failed to get GID for user '{sandbox_user}'")
        raise RuntimeError(f"Failed to get GID for user '{sandbox_user}'") from e



