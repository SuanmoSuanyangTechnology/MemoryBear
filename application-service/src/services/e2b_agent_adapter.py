"""Fail-closed stand-in for the E2B agent execution adapter.

E2B sandbox execution is not migrated into this service; trial-run executes
in-process only. Constructing the adapter raises instead of silently routing
agent runs somewhere else.
"""


class E2BAgentAdapter:
    """Sandbox adapter seam: construction is fail-closed."""

    def __init__(self, db=None):
        raise RuntimeError("E2B sandbox execution is not available in this service")
