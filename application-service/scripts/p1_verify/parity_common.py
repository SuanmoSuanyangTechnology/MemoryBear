"""Parity battery: the shared definition-plane scenario for both stacks.

One canonical scenario is executed against the monolith and against the
application-service internal API through a small runner facade. Every wire
response and a handful of direct database snapshots are recorded with
volatile values normalized (run-scoped ids become role tokens, timestamps
become placeholders), so ``parity_compare.py`` can deep-diff the two
recordings step by step.

Normally invoked by the two venv-specific scripts in this directory:
``parity_monolith.py`` (root venv) and ``parity_app_service.py``
(application-service venv). This module is stdlib-only on purpose.

Runner contract (implemented by each side):

  request(method, path, json_body=None, params=None) -> (status, body)
      ``path`` is the canonical suffix (for example ``/apps/{app_id}`` with
      the placeholders already substituted); each runner prepends its own
      prefix (``/api`` vs ``/internal/v1``) and attaches its own auth.
      ``body`` is the decoded JSON when possible, otherwise the raw text.

  fetch_row(sql, **params) -> tuple | None
      Runs the query on the runner's own connection so uncommitted
      scenario state is visible; uuid/datetime values are converted to
      JSON-able primitives before returning.
"""

from __future__ import annotations

import re

# Fixed identity chain shared by both sides. Both runner scripts seed these
# rows (idempotent, committed) so the two runs see exactly the same base
# state: same tenant, same workspace, same actor, same model config.
SEED = {
    "actor_id": "aaaaaaa1-0000-4000-8000-000000000001",
    "tenant_id": "aaaaaaa1-0000-4000-8000-000000000002",
    "workspace_id": "aaaaaaa1-0000-4000-8000-000000000003",
    "member_id": "aaaaaaa1-0000-4000-8000-000000000004",
    "model_config_id": "aaaaaaa1-0000-4000-8000-000000000005",
}

SEED_STATEMENTS = [
    (
        "INSERT INTO tenants (id, name) VALUES (:tenant_id, 'parity tenant')"
        " ON CONFLICT (id) DO NOTHING",
        {"tenant_id": SEED["tenant_id"]},
    ),
    (
        "INSERT INTO model_configs (id, name, type, is_active, is_public,"
        " tenant_id, capability, is_omni) VALUES (:id, 'parity-model', 'llm',"
        " true, false, :tenant_id, ARRAY['chat']::varchar[], false)"
        " ON CONFLICT (id) DO NOTHING",
        {"id": SEED["model_config_id"], "tenant_id": SEED["tenant_id"]},
    ),
    (
        "INSERT INTO workspaces (id, name, tenant_id, storage_type)"
        " VALUES (:id, 'parity space', :tenant_id, 'local')"
        " ON CONFLICT (id) DO NOTHING",
        {"id": SEED["workspace_id"], "tenant_id": SEED["tenant_id"]},
    ),
    (
        "INSERT INTO users (id, username, email, hashed_password, is_active,"
        " is_superuser, tenant_id, current_workspace_id)"
        " VALUES (:id, 'parity-user', 'parity-user@example.com', 'x', true,"
        " false, :tenant_id, :workspace_id) ON CONFLICT (id) DO NOTHING",
        {
            "id": SEED["actor_id"],
            "tenant_id": SEED["tenant_id"],
            "workspace_id": SEED["workspace_id"],
        },
    ),
    (
        "INSERT INTO workspace_members (id, user_id, workspace_id, role, is_active)"
        " VALUES (:id, :user_id, :workspace_id, 'owner', true)"
        " ON CONFLICT (id) DO NOTHING",
        {
            "id": SEED["member_id"],
            "user_id": SEED["actor_id"],
            "workspace_id": SEED["workspace_id"],
        },
    ),
]

# A fixed one-year window fully in the past: both runs aggregate over the
# same empty span, so the statistics payloads are deterministic.
STATS_PARAMS = {"start_date": 1735689600000, "end_date": 1767225600000}

WORKFLOW_PAYLOAD = {
    "nodes": [
        {
            "id": "trigger",
            "type": "trigger",
            "name": "parity schedule",
            "config": {
                "trigger_type": "schedule",
                "enabled": True,
                "interval_seconds": 3600,
                "timezone": "UTC",
            },
        },
        {"id": "end", "type": "end", "name": "end"},
    ],
    "edges": [{"source": "trigger", "target": "end"}],
    "environment_variables": [
        {
            "name": "PARITY_ENV",
            "value_type": "string",
            "required": False,
            "value": "on",
            "description": "parity battery",
        }
    ],
    "workflow_type": "pure_workflow",
}

ISO_TS = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:?\d{2})?$")
EPOCH_MS_THRESHOLD = 10**12


def _reverse_ids(ids: dict) -> dict:
    return {str(value).lower(): role for role, value in ids.items() if value}


def normalize(value, ids: dict):
    """Recursively replace run-scoped ids and timestamps with placeholders."""
    return _normalize(value, _reverse_ids(ids))


def _normalize(value, reverse: dict):
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        role = reverse.get(value.lower())
        if role:
            return f"<{role}>"
        if ISO_TS.match(value):
            return "<ts>"
        if value.isdigit() and len(value) >= 12:
            return "<ts>"
        return value
    if isinstance(value, (int, float)) and value >= EPOCH_MS_THRESHOLD:
        return "<ts>"
    if isinstance(value, dict):
        return {k: _normalize(v, reverse) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_normalize(v, reverse) for v in value]
    if value is None:
        return None
    return str(value)


def normalize_text(text: str, ids: dict) -> str:
    """Scrub run ids and timestamps inside an opaque text payload (YAML)."""
    if not isinstance(text, str):
        return text
    for role, value in ids.items():
        if value:
            text = text.replace(str(value), f"<{role}>")
    text = ISO_TS.sub("<ts>", text)
    text = re.sub(r"\b\d{13}\b", "<ts>", text)
    return text


def _dig(body, *keys):
    value = body
    for key in keys:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def run_scenario(runner) -> list[dict]:
    """Execute the canonical scenario; return the recorded [step] list."""
    results: list[dict] = []
    ids: dict[str, str | None] = {}

    def record(name: str, status, data):
        results.append({"step": name, "status": status, "data": data})

    def req(
        name: str,
        method: str,
        path: str,
        json_body=None,
        params=None,
        capture=None,
        text_fields=(),
    ):
        """Capture ids first, then record the normalized response, so ids
        established by this very step are already role tokens in the log."""
        concrete = path.format(**{k: (v or "none") for k, v in ids.items()})
        status, body = runner.request(method, concrete, json_body, params)
        if capture and isinstance(body, dict):
            if callable(capture):
                ids.update(capture(body))
            else:
                for keys, role in capture:
                    ids[role] = _dig(body, *keys)
        for field_keys in text_fields:
            if isinstance(body, dict):
                container = _dig(body, *field_keys[:-1]) if len(field_keys) > 1 else body
                if isinstance(container, dict) and isinstance(
                    container.get(field_keys[-1]), str
                ):
                    container[field_keys[-1]] = normalize_text(
                        container[field_keys[-1]], ids
                    )
        record(name, status, normalize(body, ids))
        return body

    def db(name: str, sql: str, **params):
        row = runner.fetch_row(sql, **params)
        record(name, None, normalize(list(row) if row is not None else None, ids))

    def capture_builtin_tools(body):
        """Builtin tool rows are lazily seeded per tenant with fresh random
        ids on whichever side gets asked first; treat ids as run-scoped."""
        captured = {}
        for item in body.get("data") or []:
            if isinstance(item, dict) and item.get("tool_type") == "builtin":
                captured[f"builtin_{item.get('name')}"] = item.get("id")
        return captured

    # -- agent app: CRUD + agent config ------------------------------------
    req(
        "01_create_agent_app", "POST", "/apps",
        {"name": "parity-agent", "type": "agent"},
        capture=[(("data", "id"), "agent_app_id")],
    )
    req("02_get_agent_app", "GET", "/apps/{agent_app_id}")
    req("03_list_apps", "GET", "/apps")
    req("04_get_default_multi_agent", "GET", "/apps/{agent_app_id}/multi-agent")

    req(
        "05_update_agent_app", "PUT", "/apps/{agent_app_id}",
        {"name": "parity-agent-renamed", "description": "parity battery"},
    )
    req("06_get_agent_app_after_update", "GET", "/apps/{agent_app_id}")

    req(
        "07_put_agent_config", "PUT", "/apps/{agent_app_id}/config",
        {
            "system_prompt": "parity prompt",
            "default_model_config_id": SEED["model_config_id"],
        },
        capture=[(("data", "id"), "agent_config_id")],
    )
    req("08_get_agent_config", "GET", "/apps/{agent_app_id}/config")
    req("09_get_agent_opening", "GET", "/apps/{agent_app_id}/opening")

    # -- workflow app: draft + publish + export ----------------------------
    req(
        "10_create_flow_app", "POST", "/apps",
        {"name": "parity-flow", "type": "pure_workflow"},
        capture=[(("data", "id"), "flow_app_id")],
    )
    req(
        "11_put_workflow", "PUT", "/apps/{flow_app_id}/workflow",
        WORKFLOW_PAYLOAD,
        capture=[(("data", "id"), "workflow_config_id")],
    )
    req("12_get_workflow", "GET", "/apps/{flow_app_id}/workflow")
    db(
        "13_db_workflow_nodes",
        "SELECT workflow_type, jsonb_array_length(nodes) FROM workflow_configs"
        " WHERE app_id = :app_id",
        app_id=ids["flow_app_id"],
    )

    req(
        "14_publish_v1", "POST", "/apps/{flow_app_id}/publish",
        {"version_name": "v1", "release_notes": "parity v1", "name": "parity-flow"},
        capture=[(("data", "id"), "release_v1_id")],
    )
    req("15_get_release_after_v1", "GET", "/apps/{flow_app_id}/release")
    req("16_list_releases_after_v1", "GET", "/apps/{flow_app_id}/releases")

    req(
        "17_publish_v2", "POST", "/apps/{flow_app_id}/publish",
        {"version_name": "v2", "release_notes": "parity v2", "name": "parity-flow"},
        capture=[(("data", "id"), "release_v2_id")],
    )
    req("18_get_release_after_v2", "GET", "/apps/{flow_app_id}/release")
    db(
        "19_db_releases",
        "SELECT version, name, is_active FROM app_releases"
        " WHERE app_id = :app_id ORDER BY version",
        app_id=ids["flow_app_id"],
    )
    req(
        "20_export_workflow", "GET", "/apps/{flow_app_id}/workflow/export",
        text_fields=[("data", "content")],
    )

    # -- workflow published as tool ----------------------------------------
    req(
        "21_publish_tool", "POST", "/apps/{flow_app_id}/publish_tool",
        {"name": "parity-tool", "description": "parity battery"},
        capture=[(("data", "id"), "tool_id")],
    )
    db(
        "22_db_tool_row",
        "SELECT name, tool_type, is_active FROM tool_configs WHERE id = :id",
        id=ids["tool_id"],
    )
    db(
        "23_db_workflow_tool_binding",
        "SELECT app_id, release_id FROM workflow_tool_configs WHERE id = :id",
        id=ids["tool_id"],
    )
    req("24_preview_publish_tool", "GET", "/apps/{flow_app_id}/publish_tool/preview")
    req("25_get_tool_detail", "GET", "/tools/{tool_id}")
    req("26_list_tools", "GET", "/tools", capture=capture_builtin_tools)

    # -- statistics --------------------------------------------------------
    req(
        "27_app_statistics", "GET", "/apps/{flow_app_id}/statistics",
        params=STATS_PARAMS,
    )
    req(
        "28_workspace_api_statistics", "GET", "/apps/workspace/api-statistics",
        params=STATS_PARAMS,
    )

    # -- multi-agent cluster ----------------------------------------------
    req(
        "29_create_master_app", "POST", "/apps",
        {"name": "parity-master", "type": "agent"},
        capture=[(("data", "id"), "master_app_id")],
    )
    req(
        "30_put_master_config", "PUT", "/apps/{master_app_id}/config",
        {
            "system_prompt": "master",
            "default_model_config_id": SEED["model_config_id"],
        },
        capture=[(("data", "id"), "master_config_id")],
    )
    req(
        "31_publish_master", "POST", "/apps/{master_app_id}/publish",
        {"version_name": "v1", "release_notes": "parity master", "name": "parity-master"},
        capture=[(("data", "id"), "master_release_id")],
    )

    req(
        "32_create_sub_app", "POST", "/apps",
        {"name": "parity-sub", "type": "agent"},
        capture=[(("data", "id"), "sub_app_id")],
    )
    req(
        "33_put_sub_config", "PUT", "/apps/{sub_app_id}/config",
        {"system_prompt": "sub", "default_model_config_id": SEED["model_config_id"]},
        capture=[(("data", "id"), "sub_config_id")],
    )
    req(
        "34_publish_sub", "POST", "/apps/{sub_app_id}/publish",
        {"version_name": "v1", "release_notes": "parity sub", "name": "parity-sub"},
        capture=[(("data", "id"), "sub_release_id")],
    )

    req(
        "35_create_cluster_app", "POST", "/apps",
        {"name": "parity-cluster", "type": "multi_agent"},
        capture=[(("data", "id"), "cluster_app_id")],
    )
    req(
        "36_put_multi_agent", "PUT", "/apps/{cluster_app_id}/multi-agent",
        {
            "master_agent_id": ids["master_release_id"],
            "master_agent_name": "Master",
            "sub_agents": [
                {
                    "agent_id": ids["sub_app_id"],
                    "name": "小助手",
                    "role": "assistant",
                    "capabilities": ["chat"],
                    "release_policy": "current",
                }
            ],
            "execution_config": {"max_iterations": 5},
            "aggregation_strategy": "merge",
        },
        capture=[(("data", "id"), "multi_agent_config_id")],
    )
    req("37_get_multi_agent", "GET", "/apps/{cluster_app_id}/multi-agent")
    db(
        "38_db_multi_agent_row",
        "SELECT master_agent_id, execution_config, aggregation_strategy"
        " FROM multi_agent_configs WHERE app_id = :app_id AND is_active = true",
        app_id=ids["cluster_app_id"],
    )

    # -- skills ------------------------------------------------------------
    req(
        "39_skill_create", "POST", "/skills",
        {"name": "parity-skill", "description": "parity battery"},
        capture=[(("data", "id"), "skill_id")],
    )
    req("40_skill_get", "GET", "/skills/{skill_id}")
    req("41_skill_update", "PUT", "/skills/{skill_id}", {"name": "parity-skill-2"})
    req("42_skill_list", "GET", "/skills")
    req("43_skill_delete", "DELETE", "/skills/{skill_id}")
    req("44_skill_get_after_delete", "GET", "/skills/{skill_id}")
    db(
        "45_db_skill_row",
        "SELECT name, is_active FROM skills WHERE id = :id",
        id=ids["skill_id"],
    )

    return results
