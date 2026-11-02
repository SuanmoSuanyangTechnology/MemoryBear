"""Registry for dynamically injected system-prompt blocks.

A *prompt block* is one self-contained paragraph of the 【执行要求】 section. Each
block declares

* ``id``     - unique, dotted, lower-case (``<domain>.<topic>``, e.g. ``sandbox.deliver``);
* ``order``  - where it sits in the section (use the ``ORDER_*`` constants);
* ``when``   - a predicate over :class:`PromptContext`; the block is injected only when
               it returns True (default: always);
* a render function returning the text of the block (``None`` / empty = skip).

Blocks never talk to each other: ordering comes from ``order`` only, and a block that
fails to render is logged and skipped so one bad block cannot break an Agent run.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Callable, Iterable, List, Optional, Sequence

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Ordering slots. Leave gaps so a new domain can slot in without renumbering.
# ---------------------------------------------------------------------------
ORDER_HEADER = 0  # section title
ORDER_CORE = 100  # always-on baseline rules (L0, tool independent)
ORDER_TOOLS = 200  # generic rules that apply once any tool is assigned (L1)
ORDER_CAPABILITY = 300  # what a specific tool / toolset can or cannot do (L2)
ORDER_PLAYBOOK = 400  # how to combine tools to finish a kind of task (L2)
ORDER_DELIVERY = 500  # handing results to the user (L2)
ORDER_CHECK = 800  # closing self-check
ORDER_FOOTER = 900  # precedence rule, always last

_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")


@dataclass(frozen=True)
class PromptContext:
    """Facts a block may use to decide whether and what to inject.

    ``tool_names`` are the tools the Agent can call live in this run;
    ``executed_tool_names`` were already run upstream (ToolOrchestrator for weak models)
    and are never also listed in ``tool_names``.
    """

    tool_names: tuple = ()
    executed_tool_names: tuple = ()
    tool_call_limit: Optional[int] = None

    @classmethod
    def create(
        cls,
        tool_names: Iterable[str] = (),
        executed_tool_names: Iterable[str] = (),
        tool_call_limit: Optional[int] = None,
    ) -> "PromptContext":
        names = tuple(dict.fromkeys(n for n in tool_names if n))
        executed = tuple(dict.fromkeys(n for n in executed_tool_names if n and n not in names))
        return cls(names, executed, tool_call_limit)

    @property
    def has_tools(self) -> bool:
        return bool(self.tool_names)

    @property
    def has_executed(self) -> bool:
        return bool(self.executed_tool_names)

    def has(self, name: str) -> bool:
        """The tool can be called live."""
        return name in self.tool_names

    def has_any(self, *names: str) -> bool:
        return any(n in self.tool_names for n in names)

    def was_executed(self, name: str) -> bool:
        """The tool was already run upstream (results are in the prompt)."""
        return name in self.executed_tool_names


WhenFn = Callable[[PromptContext], bool]
RenderFn = Callable[[PromptContext], Optional[str]]


@dataclass(frozen=True)
class PromptBlock:
    id: str
    order: int
    render: RenderFn
    when: Optional[WhenFn] = None
    raw: bool = False  # True: emit as is; False: normalized into a "- " bullet

    def applies(self, ctx: PromptContext) -> bool:
        return True if self.when is None else bool(self.when(ctx))


class PromptBlockRegistry:
    """Holds blocks and renders the ones that apply to a context."""

    def __init__(self) -> None:
        self._blocks: List[PromptBlock] = []
        self._ids: set = set()

    def register(self, block: PromptBlock) -> PromptBlock:
        if not _ID_PATTERN.match(block.id):
            raise ValueError(
                f"invalid prompt block id {block.id!r}: use '<domain>.<topic>' in lower_snake_case"
            )
        if block.id in self._ids:
            raise ValueError(f"duplicate prompt block id {block.id!r}")
        self._ids.add(block.id)
        self._blocks.append(block)
        return block

    def block(
        self,
        id: str,
        *,
        order: int,
        when: Optional[WhenFn] = None,
        raw: bool = False,
    ) -> Callable[[RenderFn], RenderFn]:
        """Decorator form of :meth:`register`; the decorated function renders the block."""

        def decorator(fn: RenderFn) -> RenderFn:
            self.register(PromptBlock(id=id, order=order, render=fn, when=when, raw=raw))
            return fn

        return decorator

    @property
    def blocks(self) -> Sequence[PromptBlock]:
        """Blocks in render order (stable for equal ``order``)."""
        return tuple(sorted(self._blocks, key=lambda b: b.order))

    def select(self, ctx: PromptContext) -> List[str]:
        """Ids of the blocks that would be injected for ``ctx``."""
        return [b.id for b in self.blocks if self._safe_applies(b, ctx)]

    def render(self, ctx: PromptContext) -> str:
        lines: List[str] = []
        for block in self.blocks:
            if not self._safe_applies(block, ctx):
                continue
            try:
                text = block.render(ctx)
            except Exception:  # a broken block must not break the Agent
                logger.exception("prompt block %s failed to render; skipped", block.id)
                continue
            text = (text or "").strip()
            if not text:
                continue
            if not block.raw and not text.startswith("-"):
                text = f"- {text}"
            lines.append(text)
        return "\n".join(lines)

    @staticmethod
    def _safe_applies(block: PromptBlock, ctx: PromptContext) -> bool:
        try:
            return block.applies(ctx)
        except Exception:
            logger.exception("prompt block %s 'when' failed; skipped", block.id)
            return False


default_registry = PromptBlockRegistry()


def prompt_block(
    id: str,
    *,
    order: int,
    when: Optional[WhenFn] = None,
    raw: bool = False,
) -> Callable[[RenderFn], RenderFn]:
    """Register a block on the default registry (see module docstring)."""
    return default_registry.block(id, order=order, when=when, raw=raw)


def render_prompt_blocks(ctx: PromptContext, registry: Optional[PromptBlockRegistry] = None) -> str:
    return (registry or default_registry).render(ctx)
