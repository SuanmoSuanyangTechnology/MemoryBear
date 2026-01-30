"""Dependency management"""
import asyncio
from pathlib import Path
from typing import List, Dict

from app.config import get_config
from app.core.runners.nodejs.env import prepare_nodejs_dependencies_env
from app.core.runners.python.env import prepare_python_dependencies_env
from app.logger import get_logger


async def setup_dependencies():
    """Setup initial dependencies"""
    logger = get_logger()

    try:
        logger.info("Installing Python dependencies...")
        await install_python_dependencies()
        logger.info("Python dependencies installed")

        logger.info("Preparing Python dependencies environment...")
        await prepare_python_dependencies_env()
        logger.info("Python Environment Ready ....")
        logger.info("Preparing Nodejs dependencies environment...")
        await prepare_nodejs_dependencies_env()
        logger.info("Nodejs Environment Ready ...")

    except Exception as e:
        logger.error(f"Failed to setup dependencies: {e}")


async def update_dependencies():
    # TODO
    return


async def install_python_dependencies():
    """Install Python dependencies from requirements file"""
    logger = get_logger()
    config = get_config()

    # Check if requirements file exists
    req_file = Path("dependencies/python/python-requirements.txt")
    if not req_file.exists():
        logger.warning("Python requirements file not found, skipping installation")
        return

    # Read requirements
    requirements = req_file.read_text().strip()
    if not requirements:
        logger.info("No Python requirements to install")
        return

    # Install using pip
    cmd = [
        config.python_path,
        "-m",
        "pip",
        "install",
        "--upgrade"
    ]

    # Add packages from requirements
    for line in requirements.split("\n"):
        line = line.strip()
        if line and not line.startswith("#"):
            cmd.append(line)

    try:
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )

        stdout, stderr = await process.communicate()

        if process.returncode != 0:
            logger.error(f"Failed to install Python dependencies: {stderr.decode()}")
        else:
            logger.info("Python dependencies installed successfully")

    except Exception as e:
        logger.error(f"Error installing Python dependencies: {e}")


async def list_dependencies(language: str) -> List[Dict[str, str]]:
    """List installed dependencies

    Args:
        language: Language (python or Node.js)

    Returns:
        List of dependencies with name and version
    """
    if language == "python":
        return await list_python_packages()
    else:
        return []


async def list_python_packages() -> List[Dict[str, str]]:
    """List installed Python packages"""
    config = get_config()

    try:
        process = await asyncio.create_subprocess_exec(
            config.python_path,
            "-m",
            "pip",
            "list",
            "--format=freeze",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )

        stdout, stderr = await process.communicate()

        if process.returncode != 0:
            return []

        # Parse output
        packages = []
        for line in stdout.decode().split("\n"):
            line = line.strip()
            if line and "==" in line:
                name, version = line.split("==", 1)
                packages.append({"name": name, "version": version})

        return packages

    except Exception as e:
        get_logger().error(f"Failed to list Python packages: {e}")
        return []


async def update_dependencies_periodically():
    """Periodically update dependencies"""
    logger = get_logger()
    config = get_config()

    # Parse interval
    interval_str = config.python_deps_update_interval

    # Convert to seconds
    if interval_str.endswith("m"):
        interval = int(interval_str[:-1]) * 60
    elif interval_str.endswith("h"):
        interval = int(interval_str[:-1]) * 3600
    elif interval_str.endswith("s"):
        interval = int(interval_str[:-1])
    else:
        interval = 1800  # Default 30 minutes

    logger.info(f"Starting periodic dependency updates every {interval} seconds")

    while True:
        await asyncio.sleep(interval)

        try:
            logger.info("Updating Python dependencies...")
            # TODO: await update_dependencies("python")
            logger.info("Python dependencies updated successfully")
        except Exception as e:
            logger.error(f"Failed to update Python dependencies: {e}")
