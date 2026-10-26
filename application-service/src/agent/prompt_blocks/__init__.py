"""Dynamic system-prompt injection for Agents.

How to add prompt text for a new feature (tool, toolset, skill, ...):

1. Create ``prompt_blocks/<domain>.py`` and register one block per rule::

       from .registry import ORDER_CAPABILITY, PromptContext, prompt_block

       @prompt_block("mytool.usage", order=ORDER_CAPABILITY,
                     when=lambda ctx: ctx.has("my_tool"))
       def _usage(ctx: PromptContext) -> str:
           return "..."          # a "- " bullet is added automatically

2. Import that module in the "built-in blocks" list below so it registers at import time.
3. Add tests that assert the block shows up (and stays out) for the tool combinations
   that matter; ``tests/integrations/test_prompt_blocks.py`` has the pattern.

Rules:

* One block, one rule. Block ids are unique ``<domain>.<topic>`` in lower_snake_case.
* Use ``ORDER_*`` slots for position; blocks must not depend on each other.
* How a single tool is used belongs in that tool's ``description`` (it travels with the
  tool). Only cross-tool procedure goes here, and only when its tools are present.
* Per-turn data (attachment manifests, upstream tool traces) belongs in the user
  message / its own prompt section, not in a block.
* Mention a tool by name only when ``when`` guarantees that tool is assigned.
"""

from __future__ import annotations

from typing import Iterable, Optional

from .registry import (
    ORDER_CAPABILITY,
    ORDER_CHECK,
    ORDER_CORE,
    ORDER_DELIVERY,
    ORDER_FOOTER,
    ORDER_HEADER,
    ORDER_PLAYBOOK,
    ORDER_TOOLS,
    PromptBlock,
    PromptBlockRegistry,
    PromptContext,
    default_registry,
    prompt_block,
    render_prompt_blocks,
)

# Built-in blocks: importing the module registers its blocks on ``default_registry``.
from . import base as _base  # noqa: F401,E402
from . import sandbox as _sandbox  # noqa: F401,E402


def build_execution_requirements(
    tool_names: Iterable[str] = (),
    executed_tool_names: Iterable[str] = (),
    tool_call_limit: Optional[int] = None,
) -> str:
    """Render the 【执行要求】 section for the tools actually assigned to an Agent."""
    return render_prompt_blocks(PromptContext.create(tool_names, executed_tool_names, tool_call_limit))


__all__ = [
    "ORDER_CAPABILITY",
    "ORDER_CHECK",
    "ORDER_CORE",
    "ORDER_DELIVERY",
    "ORDER_FOOTER",
    "ORDER_HEADER",
    "ORDER_PLAYBOOK",
    "ORDER_TOOLS",
    "PromptBlock",
    "PromptBlockRegistry",
    "PromptContext",
    "build_execution_requirements",
    "default_registry",
    "prompt_block",
    "render_prompt_blocks",
]
