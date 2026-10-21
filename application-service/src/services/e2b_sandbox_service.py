"""Fail-closed stand-in for the E2B sandbox execution backend.

E2B sandbox execution is not migrated into this service; trial-run executes
in-process only. Any attempt to obtain the sandbox service raises instead of
degrading to a different execution mode.
"""


def get_sandbox_service():
    raise RuntimeError("E2B sandbox execution is not available in this service")
