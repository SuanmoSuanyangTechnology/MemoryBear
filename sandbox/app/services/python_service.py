"""Python execution service"""
import signal

from app.core.runners.python.python_runner import PythonRunner
from app.dependencies import (
    list_dependencies as list_deps,
    update_dependencies as update_deps
)
from app.logger import get_logger
from app.models import (
    success_response,
    error_response,
    RunCodeResponse,
    ListDependenciesResponse,
    UpdateDependenciesResponse,
    Dependency,
    RunnerOptions
)


async def run_python_code(code: str, preload: str, options: RunnerOptions):
    """Execute Python code in sandbox
    
    Args:
        options:
        code: Base64 encoded encrypted code
        preload: Preload code

    Returns:
        API response with execution result
    """
    logger = get_logger()
    
    try:
        runner = PythonRunner()
        result = await runner.run(code, options, preload)
        if result.exit_code == -signal.SIGSYS:
            return error_response(31, "sandbox security policy violation")

        if result.stderr and result.exit_code != 0:
            return error_response(500, result.stderr)
        
        return success_response(RunCodeResponse(
            stdout=result.stdout,
            stderr=result.stderr
        ))
        
    except Exception as e:
        logger.error(f"Python execution failed: {e}", exc_info=True)
        return error_response(-500, str(e))


async def list_python_dependencies():
    """List installed Python dependencies
    
    Returns:
        API response with dependency list
    """
    try:
        deps = await list_deps("python")
        dependencies = [
            Dependency(name=dep["name"], version=dep["version"])
            for dep in deps
        ]
        return success_response(ListDependenciesResponse(dependencies=dependencies))
    except Exception as e:
        return error_response(500, str(e))


async def update_python_dependencies():
    """Update Python dependencies
    
    Returns:
        API response with update result
    """
    try:
        await update_deps()
        return success_response(UpdateDependenciesResponse(success=True))
    except Exception as e:
        return error_response(500, str(e))
