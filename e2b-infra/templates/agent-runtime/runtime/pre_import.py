"""
Pre-import warmup for sandbox containers.

Runs in warm-pool containers after creation to compile .pyc bytecode
and warm the OS page cache, so that actual agent/workflow executions
don't pay the import cost on the critical path.

Usage:
    python /app/runtime/pre_import.py
"""
import logging
import sys
import time

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] pre_import: %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("pre_import")

_ELAPSED: dict[str, float] = {}


def _import_block(label: str, modules: list[str]):
    t0 = time.perf_counter()
    for mod in modules:
        try:
            __import__(mod)
        except Exception as exc:
            logger.warning("skip %s: %s", mod, exc)
    _ELAPSED[label] = round(time.perf_counter() - t0, 3)


def main():
    t_total = time.perf_counter()

    # ── lightweight runtime modules ──
    _import_block("runtime_core", [
        "runtime.config",
        "runtime.protocol",
        "runtime.callback_client",
    ])

    # ── tool system (langchain_core + pydantic) ──
    _import_block("tool_system", [
        "runtime.tools.base",
        "runtime.tools.loader",
    ])

    # ── agent (heavy: LangChain + full app.core.agent) ──
    _import_block("agent", [
        "runtime.core.agent.langchain_agent",
    ])

    # ── workflow (heavy: patches + full app.core.workflow) ──
    _import_block("workflow_patches", [
        "stubs.sandbox_patches",
    ])
    import stubs.sandbox_patches as patches  # noqa: E402
    patches.apply()
    _import_block("workflow_engine", [
        "app.core.workflow.engine.runtime_schema",
        "app.core.workflow.executor",
    ])

    total = round(time.perf_counter() - t_total, 3)
    logger.info("done total=%.3fs %s", total, _ELAPSED)


if __name__ == "__main__":
    main()
