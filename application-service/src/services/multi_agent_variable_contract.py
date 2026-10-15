"""Multi-agent cluster variable contract v1.

Problems addressed:
- The collaboration path used to hand only message / conversation_id to handoffs,
  dropping variables and user_id / memory / storage_type / user_rag_memory_id
  entirely -- effectively a "bare-LLM group chat";
- The supervisor path stuffed variables into initial_context, but there was no single
  definition source or required-field validation: each sub-agent validated against its
  own `variables` definition, so the entry point could not tell the user up front what
  was missing;
- `{{variable}}` placeholders in the user message were never rendered, which did not
  match the single-agent app experience that "configured variables should just work".

This module funnels variables into one contract chain (ClusterVariableBag):

    entry variable values  ──►  definition collection (cluster declarations > union of sub-agent definitions)
                          ──►  required validation / default fallback / undefined observation
                          ──►  rendered message + unified variable bag
                                 │
                                 ├─ supervisor: into _analyze_task initial_context → sub-agent variables
                                 └─ collaboration: passed through to handoff nodes for system_prompt / message rendering

Two rendering modes, do not mix them:
- `render_template`: full Jinja2 rendering, for system_prompt -- consistent with the
  single-agent app ( AgentRunService's `render_prompt_message(agent_config.system_prompt, ...)` ),
  supporting `{% if %}` and similar; on render failure or an invalid template it falls
  back to the raw text so the user never bears the error.
- `render_variables_in_text`: conservative substitution, replacing only `{{name}}`
  entries that actually exist in the value bag, for free text such as the **user
  message** that may contain braces -- a full Jinja render would silently swallow user
  input like `{{random junk}}` into an empty string, so it must be kept verbatim.
"""

from __future__ import annotations

import logging

import json
import re
from typing import Any, Dict, Iterable, List, Optional, Tuple


logger = logging.getLogger(__name__)

# Variable placeholder form: `{{ name }}`. Only simple identifiers are recognized;
# filters/expressions are left to full Jinja rendering.
_VARIABLE_TOKEN = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")
_JINJA_MARKERS = ("{{", "{%")

# Compatibility keys for "default value" in variable definitions: the frontend writes
# default_value; some legacy data uses default.
_DEFAULT_KEYS = ("default_value", "default", "default_val")

SOURCE_CLUSTER = "cluster"          # externally exposed variables declared by the cluster
SOURCE_SUB_AGENT_UNION = "sub_agent_union"  # derived from sub-agent definitions (fallback without explicit declarations)


def _as_dict(raw: Any) -> Dict[str, Any]:
    """Normalize a variable definition (possibly from a JSON column / pydantic object) into a dict."""
    if isinstance(raw, dict):
        return raw
    if hasattr(raw, "model_dump"):
        try:
            dumped = raw.model_dump()
            if isinstance(dumped, dict):
                return dumped
        except Exception:
            pass
    if hasattr(raw, "__dict__"):
        return dict(vars(raw))
    return {}


def _pick_default(raw: Dict[str, Any]) -> Tuple[Any, bool]:
    """Pick the default value; returns (value, present). A blank string counts as no default."""
    for key in _DEFAULT_KEYS:
        if key in raw:
            value = raw.get(key)
            if value is None:
                return None, False
            if isinstance(value, str) and value == "":
                return None, False
            return value, True
    return None, False


def _is_blank(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str) and value.strip() == "":
        return True
    return False


def normalize_definition(
    raw: Any,
    *,
    source: str,
    owner: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Normalize to {name, display_name, type, required, default, options, source, owner}."""
    data = _as_dict(raw)
    name = data.get("name")
    if not isinstance(name, str) or not name.strip():
        return None
    name = name.strip()
    default, has_default = _pick_default(data)
    return {
        "name": name,
        "display_name": data.get("display_name") or data.get("label") or name,
        "type": data.get("type") or "string",
        "required": bool(data.get("required", False)),
        "default": default if has_default else None,
        "has_default": has_default,
        "options": data.get("options"),
        "source": source,
        "owner": owner,
    }


def _merge_definition(target: Dict[str, Any], incoming: Dict[str, Any]) -> None:
    """Union-merge of same-named variables: required is OR'ed (required on either side wins); the first non-empty default wins."""
    target["required"] = bool(target.get("required")) or bool(incoming.get("required"))
    if not target.get("has_default") and incoming.get("has_default"):
        target["default"] = incoming.get("default")
        target["has_default"] = True
    owners = target.get("owners") or []
    if target.get("owner") and target["owner"] not in owners:
        owners.append(target["owner"])
    if incoming.get("owner") and incoming["owner"] not in owners:
        owners.append(incoming["owner"])
    target["owners"] = owners


def collect_variable_definitions(
    config: Any,
    sub_agents: Optional[Dict[str, Any]] = None,
) -> Tuple[List[Dict[str, Any]], str]:
    """Collect the cluster's externally exposed variable definitions.

    Variable definitions now live uniformly in ``config.supervisor_config['variables']``
    (the former top-level ``config.variables`` column has been removed).

    Priority:
    1) Explicit cluster declarations (`supervisor_config.variables`, reusing the app
       variable model);
    2) Otherwise degrade to the **union of sub-agent definitions** (merged by name), so
       existing clusters also get default values and required semantics without a
       prior data-migration round.

    Returns:
        (definition list, source marker)
    """
    supervisor_config = getattr(config, "supervisor_config", None)
    declared = (
        supervisor_config.get("variables")
        if isinstance(supervisor_config, dict)
        else None
    )
    if declared:
        definitions: List[Dict[str, Any]] = []
        for raw in declared:
            normalized = normalize_definition(raw, source=SOURCE_CLUSTER)
            if normalized:
                definitions.append(normalized)
        if definitions:
            return definitions, SOURCE_CLUSTER

    merged: Dict[str, Dict[str, Any]] = {}
    for _agent_id, payload in (sub_agents or {}).items():
        agent_config = (payload or {}).get("config")
        owner = (payload or {}).get("info", {}) or {}
        owner_name = owner.get("name") or getattr(agent_config, "name", None)
        for raw in (getattr(agent_config, "variables", None) or []):
            normalized = normalize_definition(raw, source=SOURCE_SUB_AGENT_UNION, owner=owner_name)
            if not normalized:
                continue
            existing = merged.get(normalized["name"])
            if existing is None:
                merged[normalized["name"]] = normalized
            else:
                _merge_definition(existing, normalized)

    return list(merged.values()), SOURCE_SUB_AGENT_UNION


class ClusterVariableBag:
    """Variable bag for one cluster execution: entry-validation result + unified values.

    - values: values rendered and passed down to sub-agents (undefined pass-through
      entries from the cluster are always kept, never dropped);
    - missing_required: variables declared required with neither a supplied value nor
      a default;
    - filled_defaults: variables left blank or unsupplied that were filled from their
      definition default;
    - undefined_values: variables supplied but defined neither by the cluster nor by
      any sub-agent -- still passed down, but recorded in logs / execution meta so a
      "configured variable that never took effect" remains attributable.
    """

    __slots__ = (
        "values",
        "definitions",
        "source",
        "missing_required",
        "filled_defaults",
        "undefined_values",
    )

    def __init__(
        self,
        values: Dict[str, Any],
        definitions: List[Dict[str, Any]],
        source: str,
        missing_required: Optional[List[str]] = None,
        filled_defaults: Optional[List[str]] = None,
        undefined_values: Optional[List[str]] = None,
    ) -> None:
        self.values = values
        self.definitions = definitions
        self.source = source
        self.missing_required = missing_required or []
        self.filled_defaults = filled_defaults or []
        self.undefined_values = undefined_values or []

    @property
    def defined_names(self) -> List[str]:
        return [d["name"] for d in self.definitions]

    def to_observation(self) -> Dict[str, Any]:
        """Observation view for logs / execution-record meta (excludes the variable values themselves, keeping sensitive values out of logs)."""
        return {
            "definition_source": self.source,
            "defined_count": len(self.definitions),
            "missing_required": list(self.missing_required),
            "filled_defaults": list(self.filled_defaults),
            "undefined_values": list(self.undefined_values),
        }

    def __repr__(self) -> str:  # pragma: no cover - for troubleshooting
        return f"<ClusterVariableBag source={self.source} values={sorted(self.values)} meta={self.to_observation()}>"


def build_cluster_variable_bag(
    config: Any,
    sub_agents: Optional[Dict[str, Any]],
    values: Optional[Dict[str, Any]],
    *,
    strict_required: Optional[bool] = None,
) -> ClusterVariableBag:
    """Build the variable bag for this round.

    Merge strategy (entry side):
    1. Values supplied by the user/caller take priority and are always kept -- the
       cluster is the caller; there is no reason for sub-agents to override it;
    2. Variables that are defined but unsupplied (or supplied blank) fall back to the
       default from their definition;
    3. Required variables with neither a value nor a default go into missing_required;
    4. Supplied-but-undefined variables are **not dropped**; they go into
       undefined_values for observation.

    Args:
        strict_required: Whether missing required variables raise immediately. Default
            behavior: only missing entries from "explicit cluster declarations" raise
            (that is the true external contract); the union derived from sub-agent
            definitions never raises -- a sub-agent declaring required inside an
            existing cluster does not mean the cluster entry point should block the
            whole conversation, which would abort clusters that used to run fine
            (that variable simply rendered empty).
    """
    definitions, source = collect_variable_definitions(config, sub_agents)
    incoming: Dict[str, Any] = dict(values or {})

    merged: Dict[str, Any] = dict(incoming)
    filled_defaults: List[str] = []
    missing_required: List[str] = []

    defined: Dict[str, Dict[str, Any]] = {d["name"]: d for d in definitions}
    for definition in definitions:
        name = definition["name"]
        provided = merged.get(name)
        if not _is_blank(provided):
            continue
        if definition.get("has_default"):
            merged[name] = definition["default"]
            filled_defaults.append(name)
        elif definition.get("required"):
            missing_required.append(name)

    undefined_values = [name for name in merged if name not in defined]

    if strict_required is None:
        strict_required = source == SOURCE_CLUSTER
    if strict_required and missing_required:
        from ..errors import BusinessException
        from ..errors import BizCode

        raise BusinessException(
            f"缺少必填变量: {', '.join(missing_required)}",
            BizCode.INVALID_PARAMETER,
        )

    bag = ClusterVariableBag(
        values=merged,
        definitions=definitions,
        source=source,
        missing_required=missing_required,
        filled_defaults=filled_defaults,
        undefined_values=undefined_values,
    )

    if missing_required or undefined_values:
        logger.warning(
            "集群变量契约：存在待确认项",
            extra={
                "definition_source": source,
                "missing_required": missing_required,
                "undefined_values": undefined_values,
                "filled_defaults": filled_defaults,
            },
        )
    return bag


def has_variable_syntax(text: Any) -> bool:
    """Whether the text contains Jinja variable/statement markers (used to skip branches that need no rendering and save a parse pass)."""
    return isinstance(text, str) and any(marker in text for marker in _JINJA_MARKERS)


def render_template(
    template: Optional[str],
    values: Optional[Dict[str, Any]],
    *,
    fallback: str = "",
) -> str:
    """Full Jinja2 rendering (system_prompt semantics, consistent with single-agent apps).

    Reuses `render_prompt_message`: undeclared template params are filled with empty
    strings, and missing variables render as empty. On render failure (syntax error /
    non-string input) it falls back to the raw text instead of surfacing template
    errors to the user.

    Args:
        template: template text
        values: variable values
        fallback: fallback text used when the render result is empty (default empty string)
    """
    if not template or not isinstance(template, str) or not has_variable_syntax(template):
        return template or fallback
    try:
        from ..schemas.prompt_schema import PromptMessageRole, render_prompt_message

        # render_prompt_message backfills missing variables into params; pass a copy
        # to avoid polluting the variable bag
        rendered = render_prompt_message(template, PromptMessageRole.USER, dict(values or {}))
        return rendered.get_text_content() or fallback
    except Exception as exc:
        logger.warning("变量渲染失败，已退回原文", extra={"error": str(exc)})
        return template


def render_variables_in_text(text: Any, values: Optional[Dict[str, Any]]) -> str:
    """Conservative rendering: replace only `{{name}}` entries present in the value bag,
    keeping everything else as-is.

    Used for free text such as user messages / handoff context -- braces appearing in
    these texts are usually intentional (writing code, quoting templates) and must not
    be consumed as variables.
    """
    if not isinstance(text, str) or not text or not values or not has_variable_syntax(text):
        return text if isinstance(text, str) else (text or "")
    try:
        def _replace(match: "re.Match[str]") -> str:
            name = match.group(1)
            if name not in values:
                return match.group(0)
            value = values[name]
            if isinstance(value, str):
                return value
            if isinstance(value, (int, float, bool)):
                return str(value)
            try:
                return json.dumps(value, ensure_ascii=False)
            except (TypeError, ValueError):
                return str(value)

        return _VARIABLE_TOKEN.sub(_replace, text)
    except Exception as exc:
        logger.warning("消息变量渲染失败，已退回原文", extra={"error": str(exc)})
        return text


def render_messages(messages: Iterable[Any], values: Optional[Dict[str, Any]]) -> List[Any]:
    """Conservatively render a message list (only message objects with string content are touched; everything else is returned as-is)."""
    if not values:
        return list(messages)
    rendered: List[Any] = []
    for message in messages:
        content = getattr(message, "content", None)
        if isinstance(content, str) and has_variable_syntax(content):
            try:
                from copy import copy

                cloned = copy(message)
                cloned.content = render_variables_in_text(content, values)
                rendered.append(cloned)
                continue
            except Exception:
                pass
        rendered.append(message)
    return rendered


def merge_sub_agent_variables(
    incoming: Optional[Dict[str, Any]],
    definitions: Optional[Iterable[Any]],
) -> Tuple[Dict[str, Any], List[str], List[str]]:
    """Sub-agent-level variable merge.

    Compared with the former behavior of `AgentRunService.prepare_variables` (validate
    required only, return inputs unchanged), this:
    1. Keeps cluster-supplied values with priority (the former behavior kept them too,
       but without default-value fallback);
    2. Fills defaults from the definition for variables defined by the sub-agent but
       unsupplied / supplied blank from the cluster;
    3. **Never raises on the sub-agent's required rules** -- in cluster scenarios the
       entry point adjudicates uniformly; one sub-agent's required semantics should not
       break the whole cluster round. Missing cases are handed back to the caller
       through the return value for observation.

    Returns:
        (merged values, missing required names, undefined names)
    """
    merged = dict(incoming or {})
    missing_required: List[str] = []
    defined: Dict[str, Dict[str, Any]] = {}

    for raw in (definitions or []):
        definition = normalize_definition(raw, source="sub_agent")
        if not definition:
            continue
        name = definition["name"]
        defined[name] = definition
        provided = merged.get(name)
        if not _is_blank(provided):
            continue
        if definition.get("has_default"):
            merged[name] = definition["default"]
        elif definition.get("required"):
            missing_required.append(name)

    undefined = [name for name in merged if name not in defined]
    return merged, missing_required, undefined
