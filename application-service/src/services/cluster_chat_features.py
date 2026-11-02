"""Shared pure functions for cluster supervisor_loop chat features.

Clusters have multiple entry points (share / API key / trial run; streaming and
non-streaming), so the chat-feature decision logic is centralized here and every
entry point calls this module instead of duplicating the rules.

Backward-compatibility contract: existing clusters have no
`supervisor_config.features` and their requests carry no new parameters. With
such input every function here must return "off / empty" so callers keep the old
behavior.
"""
from typing import Any, Dict, Optional, Tuple

def _to_dict(value: Any) -> Dict[str, Any]:
    """Pydantic model / dict / anything else -> dict (anything else becomes an empty dict)."""
    if hasattr(value, "model_dump"):
        try:
            value = value.model_dump()
        except Exception:  # noqa: BLE001 - dirty values must not break execution
            return {}
    return value if isinstance(value, dict) else {}


def get_features(supervisor_config: Any) -> Dict[str, Any]:
    """Extract features from supervisor_config (missing / dirty value -> empty dict = everything off)."""
    return _to_dict(_to_dict(supervisor_config).get("features"))


def get_features_from_config(config: Any) -> Dict[str, Any]:
    """Extract features from a cluster config object (ORM / published-snapshot proxy)."""
    return get_features(getattr(config, "supervisor_config", None))


def is_supervisor_web_search_on(features: Dict[str, Any], request_web_search: bool) -> bool:
    """Supervisor web search: effective only when features.web_search.enabled and
    the request's web_search are both true.

    Sub-agent web search does not go through here (still passed through from
    request parameters, not governed by cluster features).
    """
    web_cfg = _to_dict((features or {}).get("web_search"))
    return bool(web_cfg.get("enabled")) and bool(request_web_search)


def is_deep_thinking_on(model_parameters: Any, request_thinking: bool) -> bool:
    """Deep thinking: both cluster model_parameters.deep_thinking and request thinking must be true (same rule as the Agent entry)."""
    mp = _to_dict(model_parameters)
    if not mp and model_parameters is not None and not isinstance(model_parameters, dict):
        mp = {"deep_thinking": getattr(model_parameters, "deep_thinking", False)}
    return bool(mp.get("deep_thinking")) and bool(request_thinking)


def resolve_opening(
    features: Dict[str, Any],
    variables: Optional[Dict[str, Any]],
    is_new_conversation: bool,
) -> Tuple[Optional[str], list]:
    """Opening statement for a new conversation (with simple {{var}} substitution).

    Returns (None, []) when the feature is off or the conversation is not new.
    Shares the same source as the Agent app: reuses AgentRunService._get_opening_statement.
    """
    if not is_new_conversation or not features:
        return None, []
    opening_cfg = _to_dict(features.get("opening_statement"))
    if not (opening_cfg.get("enabled") and opening_cfg.get("statement")):
        return None, []
    from .draft_run_service import AgentRunService

    # _get_opening_statement reads opening["suggested_questions"] directly, so a
    # missing key raises KeyError; fill it in here first
    safe_features = dict(features)
    safe_features["opening_statement"] = {
        **opening_cfg,
        "suggested_questions": opening_cfg.get("suggested_questions") or [],
    }
    statement, questions = AgentRunService._get_opening_statement(safe_features, True, variables)
    return statement, list(questions or [])
