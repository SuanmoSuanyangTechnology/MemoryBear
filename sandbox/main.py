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
from app.dependencies import setup_dependencies, update_dependencies_periodically
from app.logger import setup_logger, get_logger

logger = get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager"""
    logger = get_logger()

    # Startup
    logger.info("Starting RedBear Sandbox...")

    # Setup dependencies in background
    asyncio.create_task(setup_dependencies())

    # Start periodic dependency updates
    config = get_config()
    if config.python_deps_update_interval:
        asyncio.create_task(update_dependencies_periodically())

    yield

    # Shutdown
    logger.info("Shutting down Redbear Sandbox...")


def create_app() -> FastAPI:
    """Create FastAPI application"""
    config = get_config()

    app = FastAPI(
        title="Sandbox",
        description="Secure code execution sandbox",
        version="2.0.0",
        lifespan=lifespan,
        debug=config.app.debug
    )

    app.include_router(manager_router)

    return app


def check_root_privileges():
    """Check if running with root privileges"""
    if os.geteuid() != 0:
        logger.info("Error: Sandbox must be run as root for security features (chroot, setuid)")
        sys.exit(1)


def main():
    """Main entry point"""
    # Check root privileges
    check_root_privileges()

    # Setup logging
    setup_logger()

    config = get_config()
    logger = get_logger()

    logger.info(f"Starting server on port {config.app.port}")
    logger.info(f"Debug mode: {config.app.debug}")
    logger.info(f"Max workers: {config.max_workers}")
    logger.info(f"Max requests: {config.max_requests}")
    logger.info(f"Network enabled: {config.enable_network}")

    # Create app
    app = create_app()

    # Run server
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=config.app.port,
        log_level="debug" if config.app.debug else "info",
        access_log=config.app.debug
    )


if __name__ == "__main__":
    main()
