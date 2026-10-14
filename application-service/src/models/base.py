"""application-service local model base.

- ServiceBase: the 39 app-domain tables mapped by this service (13 groups:
  application / agent / multi-agent / workflow / conversation / tool / skill /
  MCP market / annotation / feedback / end-user / prompt-optimizer / file).
  Their DDL is owned exclusively by the monolith migration chain (shared
  database, coexistence dual-track) — this service ships no migration chain and
  never emits DDL. Schema changes land in the monolith first; these mappings
  are synchronized afterwards.
- The `fk_anchors.py` tables share this metadata for FK resolution only; they
  are never read or written by this service.
"""
from datetime import UTC, datetime

from sqlalchemy.orm import DeclarativeBase


class ServiceBase(DeclarativeBase):
    pass


# Tables this service writes at runtime — the only DML targets allowed by
# scripts/check_no_host_writes.py. All 39 are app-domain tables owned by this
# service (rows may be written by both sides during the coexistence period;
# the write-side follows the prefix routed to each backend).
SERVICE_WRITE_TABLES = frozenset(
    {
        # application
        "apps",
        "app_releases",
        "app_shares",
        "release_shares",
        # agent
        "agent_configs",
        "agent_executions",
        # multi-agent
        "multi_agent_configs",
        "agent_invocations",
        # workflow
        "workflow_configs",
        "workflow_executions",
        "workflow_node_executions",
        "workflow_node_caches",
        # conversation
        "conversations",
        "conversation_details",
        "messages",
        "conversation_context_state",
        "conversation_shares",
        # tool
        "tool_configs",
        "builtin_tool_configs",
        "custom_tool_configs",
        "mcp_tool_configs",
        "tool_executions",
        "workflow_tool_configs",
        # skill
        "skills",
        # MCP market
        "mcp_markets",
        "mcp_market_configs",
        # annotation
        "app_annotations",
        "app_annotation_settings",
        "app_annotation_hit_logs",
        # feedback
        "message_feedbacks",
        "message_reports",
        # end user
        "end_users",
        "end_user_info",
        "end_user_merge",
        # prompt optimizer
        "prompt_opt_session_list",
        "prompt_opt_session_history",
        "prompt_history",
        # file
        "generic_files",
        "file_metadata",
    }
)


def utcnow_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)
