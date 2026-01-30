"""
Redbear Sandbox - Main Entry Point
"""
import asyncio
import os
import sys
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI

from app.config import get_config
from app.controllers import manager_router
from app.core.runners import init_sandbox_user
from app.dependencies import setup_dependencies, update_dependencies_periodically
from app.logger import setup_logger, get_logger

setup_logger()
config = get_config()
logger = get_logger()


def check_root_privileges():
    """Check if running with root privileges"""
    if os.geteuid() != 0:
        logger.info("Error: Sandbox must be run as root for security features (chroot, setuid)")
        sys.exit(1)


check_root_privileges()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager"""
    logger = get_logger()
    config = get_config()
    # Startup
    logger.info("Starting RedBear Sandbox...")
    logger.info(f"Starting server on port {config.app.port}")
    logger.info(f"Debug mode: {config.app.debug}")
    logger.info(f"Max workers: {config.max_workers}")
    logger.info(f"Max requests: {config.max_requests}")
    logger.info(f"Network enabled: {config.enable_network}")
    init_sandbox_user()
    await setup_dependencies()

    if config.python_deps_update_interval:
        asyncio.create_task(update_dependencies_periodically())

    yield

    # Shutdown
    logger.info("Shutting down Redbear Sandbox...")

app = FastAPI(
    title="Sandbox",
    description="Secure code execution sandbox",
    version="0.1.0",
    lifespan=lifespan,
    debug=config.app.debug
)

app.include_router(manager_router)
