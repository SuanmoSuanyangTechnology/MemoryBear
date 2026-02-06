"""Nodejs execution service"""
import signal

from app.core.runners.nodejs.nodejs_runner import NodejsRunner
from app.logger import get_logger
from app.models import (
    success_response,
    error_response,
    RunCodeResponse,
    RunnerOptions
)


async def run_nodejs_code(code: str, preload: str, options: RunnerOptions):
    """Execute Node.js code in sandbox

    Args:
        options:
        code: Base64 encoded encrypted code
        preload: Preload code

    Returns:
        API response with execution result
    """
    logger = get_logger()

    try:
        runner = NodejsRunner()
        result = await runner.run(code, options, preload)
        if result.exit_code in [signal.SIGSYS + 0x80, -signal.SIGSYS]:
            return error_response(31, "sandbox security policy violation")

        if result.exit_code != 0:
            return error_response(result.exit_code, result.stderr)

        return success_response(RunCodeResponse(
            stdout=result.stdout,
            stderr=result.stderr
        ))

    except Exception as e:
        logger.error(f"JavaScript execution failed: {e}", exc_info=True)
        return error_response(500, str(e))
