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

  request(method, path, json_body=None, params=None, face="manager",
          files=None, headers=None) -> (status, body)
      ``path`` is the canonical suffix (for example ``/apps/{app_id}`` with
      the placeholders already substituted); each runner prepends its own
      prefix (``/api`` + ``/v1`` vs ``/internal/v1``) and attaches its own
      auth depending on ``face``:

        manager  user-JWT plane (``/api``; the application-service uses the
                 internal identity headers)
        v1       API-key plane (``/v1``; the monolith sends the seeded API
                 key value, the application-service sends the binding id)
        public   anonymous share view (no credentials on either side)

      ``files`` (multipart uploads) replaces ``json_body`` when given.
      ``body`` is the decoded JSON when possible, otherwise the raw text.

  fetch_row(sql, **params) -> tuple | None
      Runs the query on the runner's scenario connection so uncommitted
      scenario state is visible; uuid/datetime values are converted to
      JSON-able primitives before returning.

  exec_sql(sql, **params) -> tuple | None
      Runs a DML statement on the runner's scenario connection (state that
      lives inside the battery's rollback boundary, e.g. the API-key row or
      seeded conversations/messages). Handler sessions join that same
      connection, so the rows are visible to both stacks without committing.
"""

from __future__ import annotations

import json
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

# The v1 face resolves its app from one API-key binding row; the scenario
# seeds it inside the rollback boundary once the flow app is published. The
# monolith sends API_KEY_VALUE on v1 requests (its real validator queries the
# row), the application-service sends the row id via the binding header.
API_KEY = {
    "id": "aaaaaaa1-0000-4000-8000-000000000006",
    "value": "parity-key-0001",
}

# Deterministic P2 fixtures seeded via ``exec_sql`` inside the rollback
# boundary. Fixed ids make uuid5-derived app-log virtual message ids identical
# across recordings; they are pre-registered as role tokens so both sides
# normalize them the same way.
FIXTURES = {
    "conversation_id": "aaaaaaa1-0000-4000-8000-000000000007",
    "user_message_id": "aaaaaaa1-0000-4000-8000-000000000008",
    "assistant_message_id": "aaaaaaa1-0000-4000-8000-000000000009",
    "assistant_message_v2_id": "aaaaaaa1-0000-4000-8000-00000000000a",
    "workflow_execution_id": "aaaaaaa1-0000-4000-8000-00000000000b",
}

# Fixed timestamps for the seeded fixture rows (both recordings read the same
# absolute values; only the relative ordering matters).
FIXTURE_TS = {
    "api_key": "2026-01-05 03:04:00",
    "conversation": "2026-01-05 03:04:05",
    "user_message": "2026-01-05 03:04:06",
    "assistant_message": "2026-01-05 03:04:07",
    "assistant_message_v2": "2026-01-05 03:04:08",
    "execution_started": "2026-01-05 03:04:05",
    "execution_completed": "2026-01-05 03:04:09",
}

# P4 fixtures: one conversation owned by the share viewer. The P4 section
# re-creates a release share inside the rollback boundary (the P2 one is
# deleted at its last step), exchanges it for a viewer bearer token, and
# seeds this conversation against the viewer's end user once that row exists.
P4_VIEWER_ID = "parity-viewer-0001"
P4_UNKNOWN_EXECUTION_ID = "parity-missing-exec"
P4_FIXTURES = {
    "p4_conversation_id": "aaaaaaa1-0000-4000-8000-00000000000c",
    "p4_user_message_id": "aaaaaaa1-0000-4000-8000-00000000000d",
    "p4_assistant_message_id": "aaaaaaa1-0000-4000-8000-00000000000e",
    "p4_assistant_v2_id": "aaaaaaa1-0000-4000-8000-00000000000f",
}
P4_FIXTURE_TS = {
    "conversation": "2026-01-05 04:04:05",
    "user_message": "2026-01-05 04:04:06",
    "assistant_message": "2026-01-05 04:04:07",
    "assistant_message_v2": "2026-01-05 04:04:08",
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
            "name": "parity trigger",
            # webhook keeps the debug trigger payload static (empty
            # body/query/headers plus config-derived meta). The schedule
            # branch embeds a wall-clock ``now`` block (iso/timestamp/date/
            # time) that no normalization rule can scrub, and the host
            # rejects any other trigger type. route_key must be given
            # explicitly: the host default is a random ``wh_<uuid>``.
            "config": {
                "trigger_type": "webhook",
                "enabled": True,
                "method": "POST",
                "route_key": "parity-hook",
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

# Fixed well-formed ids that resolve on neither side: the negative-path P3
# requests stay byte-identical across both recordings.
P3_UNKNOWN_SESSION_ID = "00000000-0000-4000-8000-000000000000"
P3_UNKNOWN_MODEL_ID = "00000000-0000-4000-8000-000000000001"
P3_UNKNOWN_TOOL_ID = "00000000-0000-4000-8000-000000000002"

ISO_TS = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:?\d{2})?$")
EPOCH_MS_THRESHOLD = 10**12

# Wall-clock durations (seconds) that can never match across two runs; masked
# by key wherever they appear as numbers inside dict payloads.
VOLATILE_KEYS = frozenset({
    "elapsed_time",
    "execution_time",
    "cache_origin_elapsed_time",
    "first_token_time",
})


def _reverse_ids(ids: dict) -> dict:
    return {str(value).lower(): role for role, value in ids.items() if value}


def _raw_ids(ids: dict) -> dict:
    return {str(value): role for role, value in ids.items() if value}


def normalize(value, ids: dict):
    """Recursively replace run-scoped ids and timestamps with placeholders."""
    return _normalize(value, _reverse_ids(ids), _raw_ids(ids))


def _scrub_embedded(text: str, raw: dict) -> str:
    """Replace run-scoped ids embedded inside longer strings.

    Share urls carry the token inside a longer url, so exact-match
    normalization cannot catch them; the raw id values are substituted
    wherever they appear.
    """
    for value, role in raw.items():
        if value in text:
            text = text.replace(value, f"<{role}>")
    return text


def _normalize(value, reverse: dict, raw: dict):
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        role = reverse.get(value.lower())
        if role:
            return f"<{role}>"
        if raw:
            scrubbed = _scrub_embedded(value, raw)
            if scrubbed != value:
                return scrubbed
        if ISO_TS.match(value):
            return "<ts>"
        if value.isdigit() and len(value) >= 12:
            return "<ts>"
        return value
    if isinstance(value, (int, float)) and value >= EPOCH_MS_THRESHOLD:
        return "<ts>"
    if isinstance(value, dict):
        normalized = {}
        for key, item in value.items():
            if (
                key in VOLATILE_KEYS
                and isinstance(item, (int, float))
                and not isinstance(item, bool)
            ):
                normalized[key] = "<elapsed>"
            else:
                normalized[key] = _normalize(item, reverse, raw)
        return normalized
    if isinstance(value, (list, tuple)):
        return [_normalize(v, reverse, raw) for v in value]
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
    # Pre-registered constant ids (the seeded API key plus the P2/P4 fixtures)
    # and everything captured from responses as the scenario advances.
    ids: dict[str, str | None] = {
        "api_key_id": API_KEY["id"],
        **FIXTURES,
        **P4_FIXTURES,
    }

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
        face="manager",
        files=None,
        headers=None,
    ):
        """Capture ids first, then record the normalized response, so ids
        established by this very step are already role tokens in the log."""
        concrete = path.format(**{k: (v or "none") for k, v in ids.items()})
        status, body = runner.request(
            method, concrete, json_body, params, face=face, files=files, headers=headers
        )
        if isinstance(body, str):
            body = normalize_text(body, ids)
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

    def exec_sql(name: str, sql: str, capture: str | None = None, **params):
        """DML inside the rollback boundary (seeded fixtures like the API-key
        row or conversations/messages); records the RETURNING row if any.

        ``capture`` registers the first returned column as a role token: ids
        minted by handler-side inserts (for example the auto-created end-user
        info row) must not leak verbatim into the recordings.
        """
        row = runner.exec_sql(sql, **params)
        if capture and row is not None:
            ids[capture] = row[0]
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

    # -- P2: end-user face (v1 create + manager/v1 reads and updates) -------
    # The key is seeded after the flow app is published (the binding's
    # publish-state check reads the current release) and carries both scopes
    # the P2 v1 faces require (app for messages/annotations, memory for
    # end-user).
    exec_sql(
        "46_seed_api_key",
        "INSERT INTO api_keys (id, name, api_key, type, scopes, workspace_id,"
        " resource_id, rate_limit_disabled, is_active, created_by, created_at,"
        " updated_at) VALUES (:id, 'parity-key', :api_key, 'pure_workflow',"
        " CAST(:scopes AS jsonb), :workspace_id, :resource_id, true, true,"
        " :created_by, :created_at, :created_at) RETURNING id",
        id=API_KEY["id"],
        api_key=API_KEY["value"],
        scopes=json.dumps(["app", "memory"]),
        workspace_id=SEED["workspace_id"],
        resource_id=ids["flow_app_id"],
        created_by=SEED["actor_id"],
        created_at=FIXTURE_TS["api_key"],
    )
    req(
        "47_v1_create_end_user", "POST", "/end_user/create",
        {"other_id": "parity-ext-user-1", "other_name": "Parity External User"},
        capture=[(("data", "id"), "end_user_id")],
        face="v1",
    )
    # The create above also inserts an empty end-user info row; fill it in
    # place and capture its minted id so the info endpoints stay diffable.
    exec_sql(
        "48_seed_end_user_info",
        "UPDATE end_user_info SET other_name = :other_name, aliases = :aliases,"
        " meta_data = CAST(:meta_data AS jsonb) WHERE end_user_id = :end_user_id"
        " RETURNING id",
        capture="end_user_info_id",
        other_name="Parity External User",
        aliases=["parity-alias-A", "parity-alias-B"],
        # The new-structure entries exercise the 13-key display projection:
        # duplicate content dedups, unknown keys / empty content drop, and the
        # 11 education entries hit the per-key cap of 10.
        meta_data=json.dumps({
            "goals": ["parity goal"],
            "core_facts": ["parity fact"],
            "interests": ["parity interest"],
            "traits": ["parity trait"],
            "identity_background": [
                "occupation | 职业 | 工程师",
                "occupation | | 工程师",
                "name | | Parity External User",
                "age | | 30",
                "unknown_key | | dropped",
                "education | | ",
                *[f"education | | education {i}" for i in range(1, 12)],
            ],
            "relationships": [
                "person | | friend A",
                "pet | | cat",
                "occupation | | 工程师",
            ],
            "domain_specific_info": {"scene-1": ["field-1 | | value-1"]},
        }),
        end_user_id=ids["end_user_id"],
    )
    req(
        "49_manager_get_end_user_info", "GET", "/end_user/info",
        params={"end_user_id": ids["end_user_id"]},
    )
    req(
        "50_manager_update_end_user_info", "POST", "/end_user/info/update",
        {"end_user_id": ids["end_user_id"], "other_name": "Parity Renamed"},
    )
    req(
        "51_manager_confirm_identity", "POST", "/end_user/identity/update",
        {
            "end_user_id": ids["end_user_id"],
            "identity_features": "parity-identity-1",
        },
    )
    db(
        "52_db_end_users",
        "SELECT other_id, other_name, identity_features, identity_status"
        " FROM end_users WHERE id = :id",
        id=ids["end_user_id"],
    )
    req(
        "53_v1_update_end_user_info", "POST", "/end_user/info/update",
        {"end_user_id": ids["end_user_id"], "other_name": "Parity V1 Renamed"},
        face="v1",
    )
    req(
        "54_v1_confirm_identity", "POST", "/end_user/identity/update",
        {
            "end_user_id": ids["end_user_id"],
            "identity_features": "parity-identity-1",
        },
        face="v1",
    )
    req(
        "55_v1_get_end_user_info", "GET", "/end_user/info",
        params={"end_user_id": ids["end_user_id"]}, face="v1",
    )
    req(
        "56_v1_get_end_user_mapping", "GET", "/end_user/mapping",
        params={"other_id": "parity-ext-user-1"}, face="v1",
    )

    # -- P2: annotation face (manager 11 + v1 6) -----------------------------
    # enabled=1 with no embedding model configured keeps every write off the
    # model-service transport (the port degrades to a missing vector).
    req(
        "57_manager_annotation_settings_put", "PUT",
        "/apps/{flow_app_id}/annotations/settings",
        {"enabled": 1, "similarity_threshold": 0.9, "model_config_id": None},
    )
    req(
        "58_manager_annotation_settings_get", "GET",
        "/apps/{flow_app_id}/annotations/settings",
    )
    req(
        "59_manager_annotation_create", "POST",
        "/apps/{flow_app_id}/annotations",
        {"question": "parity question 1", "answer": "parity answer 1"},
        capture=[(("data", "id"), "annotation_id")],
    )
    req("60_manager_annotation_list", "GET", "/apps/{flow_app_id}/annotations")
    req(
        "61_manager_annotation_get", "GET",
        "/apps/{flow_app_id}/annotations/{annotation_id}",
    )
    req(
        "62_manager_annotation_update", "PUT",
        "/apps/{flow_app_id}/annotations/{annotation_id}",
        {"answer": "parity answer 1 v2"},
    )
    req(
        "63_manager_annotation_hit_logs", "GET",
        "/apps/{flow_app_id}/annotations/{annotation_id}/hit-logs",
    )
    req(
        "64_manager_annotation_export", "GET",
        "/apps/{flow_app_id}/annotations/export", params={"format": "json"},
    )
    req(
        "65_manager_annotation_import", "POST",
        "/apps/{flow_app_id}/annotations/import",
        files={
            "file": (
                "parity.csv",
                "问题,答案\nparity q2,parity a2\nparity q3,parity a3\n",
                "text/csv",
            )
        },
    )
    req(
        "66_manager_annotation_delete", "DELETE",
        "/apps/{flow_app_id}/annotations/{annotation_id}",
    )
    req(
        "67_manager_annotation_delete_all", "DELETE",
        "/apps/{flow_app_id}/annotations",
    )
    db(
        "68_db_annotations_count",
        "SELECT count(*) FROM app_annotations WHERE app_id = :app_id",
        app_id=ids["flow_app_id"],
    )
    req(
        "69_v1_annotation_create", "POST", "/app/annotations",
        {"question": "parity v1 question", "answer": "parity v1 answer"},
        capture=[(("data", "id"), "v1_annotation_id")], face="v1",
    )
    req("70_v1_annotation_list", "GET", "/app/annotations", face="v1")
    req(
        "71_v1_annotation_settings_get", "GET", "/app/annotations/settings",
        face="v1",
    )
    req(
        "72_v1_annotation_settings_put", "PUT", "/app/annotations/settings",
        {"similarity_threshold": 0.95}, face="v1",
    )
    req(
        "73_v1_annotation_update", "PUT",
        "/app/annotations/{v1_annotation_id}",
        {"answer": "parity v1 answer v2"}, face="v1",
    )
    req(
        "74_v1_annotation_delete", "DELETE",
        "/app/annotations/{v1_annotation_id}", face="v1",
    )
    db(
        "75_db_annotations_count_after_v1",
        "SELECT count(*) FROM app_annotations WHERE app_id = :app_id",
        app_id=ids["flow_app_id"],
    )

    # -- P2: seeded conversation + messages + workflow execution -------------
    # conversation.user_id carries the internal end-user uuid (the /v1 face
    # resolves other_id to it before the ownership guards), is_draft=false and
    # is_active=true are required by the same guards, and the assistant
    # message's meta_data.execution_id ties the row to the seeded execution
    # for the app-log detail view.
    exec_sql(
        "76_seed_conversation",
        "INSERT INTO conversations (id, app_id, workspace_id, user_id, title,"
        " is_draft, message_count, is_active, created_at, updated_at)"
        " VALUES (:id, :app_id, :workspace_id, :user_id, 'parity conversation',"
        " false, 3, true, :created_at, :created_at) RETURNING id",
        id=FIXTURES["conversation_id"],
        app_id=ids["flow_app_id"],
        workspace_id=SEED["workspace_id"],
        user_id=ids["end_user_id"],
        created_at=FIXTURE_TS["conversation"],
    )
    exec_sql(
        "77_seed_user_message",
        "INSERT INTO messages (id, conversation_id, role, content, version,"
        " is_current, parent_message_id, is_deleted, status, like_count,"
        " dislike_count, report_count, created_at)"
        " VALUES (:id, :conversation_id, 'user', 'parity question', 1, true,"
        " NULL, false, 'completed', 0, 0, 0, :created_at) RETURNING id",
        id=FIXTURES["user_message_id"],
        conversation_id=FIXTURES["conversation_id"],
        created_at=FIXTURE_TS["user_message"],
    )
    exec_sql(
        "78_seed_assistant_message",
        "INSERT INTO messages (id, conversation_id, role, content, version,"
        " is_current, parent_message_id, is_deleted, meta_data, status,"
        " like_count, dislike_count, report_count, created_at)"
        " VALUES (:id, :conversation_id, 'assistant',"
        " 'parity answer', 1, true, :parent_message_id, false,"
        " CAST(:meta_data AS json), 'completed', 0, 0, 0, :created_at)"
        " RETURNING id",
        id=FIXTURES["assistant_message_id"],
        conversation_id=FIXTURES["conversation_id"],
        parent_message_id=FIXTURES["user_message_id"],
        meta_data=json.dumps({
            "execution_id": "parity-exec-0001",
            "suggested_questions": ["parity follow-up 1", "parity follow-up 2"],
            "usage": {"total_tokens": 10},
        }),
        created_at=FIXTURE_TS["assistant_message"],
    )
    exec_sql(
        "79_seed_assistant_message_v2",
        "INSERT INTO messages (id, conversation_id, role, content, version,"
        " is_current, parent_message_id, is_deleted, meta_data, status,"
        " like_count, dislike_count, report_count, created_at)"
        " VALUES (:id, :conversation_id, 'assistant',"
        " 'parity answer v2', 2, false, :parent_message_id, false,"
        " CAST(:meta_data AS json), 'completed', 0, 0, 0, :created_at)"
        " RETURNING id",
        id=FIXTURES["assistant_message_v2_id"],
        conversation_id=FIXTURES["conversation_id"],
        parent_message_id=FIXTURES["user_message_id"],
        meta_data=json.dumps({"usage": {"total_tokens": 12}}),
        created_at=FIXTURE_TS["assistant_message_v2"],
    )
    exec_sql(
        "80_seed_workflow_execution",
        "INSERT INTO workflow_executions (id, workflow_config_id, release_id,"
        " app_id, conversation_id, execution_id, trigger_type, triggered_by,"
        " input_data, output_data, context, status, elapsed_time, token_usage,"
        " started_at, completed_at, created_at)"
        " VALUES (:id, :workflow_config_id, NULL, :app_id, :conversation_id,"
        " 'parity-exec-0001', 'manual', :triggered_by,"
        " CAST(:input_data AS jsonb), CAST(:output_data AS jsonb),"
        " CAST('{}' AS jsonb), 'completed', 4.0, CAST(:token_usage AS jsonb),"
        " :started_at, :completed_at, :started_at) RETURNING id",
        id=FIXTURES["workflow_execution_id"],
        workflow_config_id=ids["workflow_config_id"],
        app_id=ids["flow_app_id"],
        conversation_id=FIXTURES["conversation_id"],
        triggered_by=SEED["actor_id"],
        input_data=json.dumps({"message": "parity run input"}),
        output_data=json.dumps({
            "output": "parity run output",
            "node_outputs": {
                "end": {
                    "node_type": "end",
                    "node_name": "end",
                    "status": "completed",
                    "output": "parity run output",
                    "execution_order": 1,
                }
            },
        }),
        token_usage=json.dumps({"total_tokens": 12}),
        started_at=FIXTURE_TS["execution_started"],
        completed_at=FIXTURE_TS["execution_completed"],
    )

    # -- P2: /v1 app + message faces ----------------------------------------
    req("81_v1_app_placeholder", "GET", "/app", face="v1")
    req("82_v1_app_variable", "GET", "/app/variable", face="v1")
    req(
        "83_v1_list_conversations", "GET", "/app/conversations",
        params={"user_id": "parity-ext-user-1"}, face="v1",
    )
    req(
        "84_v1_conversation_messages", "GET",
        "/app/conversations/{conversation_id}/messages",
        params={"user_id": "parity-ext-user-1"}, face="v1",
    )
    req(
        "85_v1_message_feedback", "POST",
        "/app/messages/{assistant_message_id}/feedback",
        {"feedback_type": "dislike", "feedback_content": "parity dislike reason"},
        params={"user_id": "parity-ext-user-1"}, face="v1",
    )
    req(
        "86_v1_conversation_feedbacks", "GET",
        "/app/conversations/{conversation_id}/messages/feedbacks",
        params={"user_id": "parity-ext-user-1"}, face="v1",
    )
    req(
        "87_v1_message_suggested", "GET",
        "/app/messages/{assistant_message_id}/suggested", face="v1",
    )
    req("88_v1_app_info", "GET", "/app/info", face="v1")

    # -- P2: manager message face -------------------------------------------
    req(
        "89_manager_message_versions", "GET",
        "/apps/{flow_app_id}/messages/{assistant_message_id}/versions",
    )
    req(
        "90_manager_switch_version_branch", "POST",
        "/apps/{flow_app_id}/messages/{assistant_message_v2_id}"
        "/switch-version/1/branch",
    )
    req(
        "91_manager_message_feedback", "POST",
        "/apps/{flow_app_id}/messages/{assistant_message_id}/feedback",
        {"feedback_type": "like"},
    )
    req(
        "92_manager_get_message_feedback", "GET",
        "/apps/{flow_app_id}/messages/{assistant_message_id}/feedback",
    )
    req(
        "93_manager_message_favorite", "POST",
        "/apps/{flow_app_id}/messages/{assistant_message_id}/favorite",
    )
    req(
        "94_manager_message_report", "POST",
        "/apps/{flow_app_id}/messages/{assistant_message_id}/report",
        {
            "report_type": "quality",
            "report_reason": "parity report",
            "text_start_offset": 0,
            "text_end_offset": 5,
            "selected_text": "parity",
        },
        capture=[(("data", "report_id"), "report_id")],
    )
    req("95_manager_report_types_enum", "GET", "/apps/enums/message_report_types")
    req("96_manager_reports_list", "GET", "/apps/messages/reports")
    req(
        "97_manager_review_report", "POST", "/apps/reports/{report_id}/review",
        {
            "severity": "low",
            "action_taken": "no_action",
            "review_note": "parity review",
        },
    )
    req(
        "98_manager_reports_statistics", "GET",
        "/apps/messages/reports/statistics",
    )

    # -- P2: app-log face ----------------------------------------------------
    req("99_app_log_conversations", "GET", "/apps/{flow_app_id}/logs")
    req(
        "100_app_log_executions", "GET",
        "/apps/{flow_app_id}/workflow-executions",
    )
    req(
        "101_app_log_execution_detail", "GET",
        "/apps/{flow_app_id}/workflow-executions/parity-exec-0001",
    )
    req(
        "102_app_log_conversation_detail", "GET",
        "/apps/{flow_app_id}/logs/{conversation_id}",
    )

    # -- P2: conversation share face ----------------------------------------
    req(
        "103_conversation_share_create", "POST",
        "/apps/{flow_app_id}/conversations/{conversation_id}/share",
        {"allow_copy": True},
        capture=[
            (("data", "share_id"), "conversation_share_id"),
            (("data", "share_uuid"), "share_uuid"),
        ],
    )
    req(
        "104_conversation_share_view", "GET", "/apps/share/{share_uuid}",
        face="public",
    )
    req(
        "105_conversation_share_list", "GET",
        "/apps/{flow_app_id}/conversations/{conversation_id}/shares",
    )
    req(
        "106_conversation_share_revoke", "DELETE",
        "/apps/{flow_app_id}/conversations/{conversation_id}/share/{share_uuid}",
    )
    db(
        "107_db_conversation_shares",
        "SELECT view_count, is_active FROM conversation_shares WHERE id = :id",
        id=ids["conversation_share_id"],
    )

    # -- P2: release share face ---------------------------------------------
    req(
        "108_release_share_create", "POST",
        "/apps/{flow_app_id}/releases/{release_v1_id}/share",
        {"is_enabled": True, "require_password": False, "allow_embed": False},
        capture=[
            (("data", "id"), "release_share_id"),
            (("data", "share_token"), "share_token"),
        ],
    )
    req(
        "109_release_share_get", "GET",
        "/apps/{flow_app_id}/releases/{release_v1_id}/share",
    )
    req(
        "110_release_share_update", "PUT",
        "/apps/{flow_app_id}/releases/{release_v1_id}/share",
        {"allow_embed": True, "embed_domains": ["https://embed.parity"]},
    )
    req(
        "111_release_share_regenerate", "POST",
        "/apps/{flow_app_id}/releases/{release_v1_id}/share/regenerate-token",
        capture=[(("data", "share_token"), "share_token")],
    )
    req(
        "112_release_share_delete", "DELETE",
        "/apps/{flow_app_id}/releases/{release_v1_id}/share",
    )
    db(
        "113_db_release_shares",
        "SELECT is_enabled FROM release_shares WHERE release_id = :id",
        id=ids["release_v1_id"],
    )

    # -- P2: message deletion (last; shares and app-log ran on live rows) ----
    req(
        "114_manager_delete_message", "DELETE",
        "/apps/{flow_app_id}/messages/{assistant_message_id}",
    )
    db(
        "115_db_message_deleted",
        "SELECT is_deleted FROM messages WHERE id = :id",
        id=ids["assistant_message_id"],
    )
    db(
        "116_db_report_status",
        "SELECT status FROM message_reports WHERE id = :id",
        id=ids["report_id"],
    )

    # -- P3: draft run + workflow debug face (A9) ----------------------------
    # The draft run mints a fresh execution and message ids per run; the
    # trigger node is webhook, so its debug payload is static and the run
    # stays free of wall-clock blocks (elapsed_time is masked by
    # VOLATILE_KEYS downstream).
    req(
        "117_p3_draft_run_workflow", "POST", "/apps/{flow_app_id}/draft/run",
        {"message": "parity draft message", "stream": False},
        capture=lambda body: {
            "draft_execution_id": _dig(body, "data", "execution_id"),
            "draft_message_id": _dig(body, "data", "message_id"),
            "draft_user_message_id": _dig(body, "data", "user_message_id"),
        },
    )
    # The route resolves (and here creates) the caller's end-user row for the
    # app; its id is run-scoped and can surface in later debug snapshots.
    exec_sql(
        "118_p3_db_draft_end_user",
        "SELECT id FROM end_users WHERE workspace_id = :workspace_id"
        " AND other_id = :other_id",
        capture="draft_end_user_id",
        workspace_id=SEED["workspace_id"],
        other_id=SEED["actor_id"],
    )
    db(
        "119_p3_db_draft_execution",
        "SELECT status, trigger_type FROM workflow_executions"
        " WHERE execution_id = :execution_id",
        execution_id=ids["draft_execution_id"],
    )
    req(
        "120_p3_debug_state_after_draft", "GET",
        "/apps/{flow_app_id}/workflow/debug/state",
    )
    req(
        "121_p3_last_run_end_workflow_execution", "GET",
        "/apps/{flow_app_id}/workflow/nodes/end/last_run",
        params={"source": "workflow_execution"},
    )

    req(
        "122_p3_node_run_end", "POST",
        "/apps/{flow_app_id}/workflow/nodes/end/run",
        {"inputs": {}, "stream": False},
        capture=lambda body: {"node_run_id": _dig(body, "data", "execution_id")},
    )
    req(
        "123_p3_debug_state_after_node_run", "GET",
        "/apps/{flow_app_id}/workflow/debug/state",
    )
    req(
        "124_p3_last_run_end_single_node_debug", "GET",
        "/apps/{flow_app_id}/workflow/nodes/end/last_run",
        params={"source": "single_node_debug"},
    )
    req(
        "125_p3_last_run_end_default", "GET",
        "/apps/{flow_app_id}/workflow/nodes/end/last_run",
    )
    req(
        "126_p3_last_run_invalid_source", "GET",
        "/apps/{flow_app_id}/workflow/nodes/end/last_run",
        params={"source": "bogus"},
    )

    req(
        "127_p3_cache_put_end", "PUT",
        "/apps/{flow_app_id}/workflow/nodes/end/cache",
        {"result_data": {"output": "parity cached output"}},
        capture=lambda body: {"node_cache_id": _dig(body, "data", "id")},
    )
    req(
        "128_p3_debug_state_after_cache_put", "GET",
        "/apps/{flow_app_id}/workflow/debug/state",
    )
    req(
        "129_p3_cache_delete_end", "DELETE",
        "/apps/{flow_app_id}/workflow/nodes/end/cache",
    )
    db(
        "130_p3_db_cache_invalidated",
        "SELECT status FROM workflow_node_caches WHERE id = :id",
        id=ids["node_cache_id"],
    )
    req(
        "131_p3_debug_state_after_cache_invalidate", "GET",
        "/apps/{flow_app_id}/workflow/debug/state",
    )

    req(
        "132_p3_cache_reset", "POST",
        "/apps/{flow_app_id}/workflow/cache/reset",
    )
    req(
        "133_p3_debug_state_after_reset", "GET",
        "/apps/{flow_app_id}/workflow/debug/state",
    )
    # After the reset the rerun input resolves from the latest base execution
    # (the draft run above), so both sides rebuild the same deterministic
    # payload; the trigger node never ran single-node debug and must 404.
    req(
        "134_p3_rerun_end", "POST",
        "/apps/{flow_app_id}/workflow/nodes/end/rerun",
        {},
        capture=lambda body: {"rerun_run_id": _dig(body, "data", "execution_id")},
    )
    req(
        "135_p3_rerun_trigger_missing", "POST",
        "/apps/{flow_app_id}/workflow/nodes/trigger/rerun",
        {},
    )

    # -- P3: draft-run compare (A9 negative paths) ---------------------------
    req(
        "136_p3_compare_on_workflow_app", "POST",
        "/apps/{flow_app_id}/draft/run/compare",
        {
            "message": "parity compare",
            "models": [{"model_config_id": SEED["model_config_id"]}],
        },
    )
    req(
        "137_p3_compare_missing_model", "POST",
        "/apps/{agent_app_id}/draft/run/compare",
        {
            "message": "parity compare",
            "models": [{"model_config_id": P3_UNKNOWN_MODEL_ID}],
        },
    )

    # -- P3: tool runtime face (B3) ------------------------------------------
    req(
        "138_p3_tool_execute_missing", "POST", "/tools/execution/execute",
        {"tool_id": P3_UNKNOWN_TOOL_ID, "parameters": {}},
    )
    req(
        "139_p3_tool_test_workflow_tool", "POST",
        "/tools/{tool_id}/test",
    )
    req(
        "140_p3_tool_sync_mcp_non_mcp", "POST",
        "/tools/{tool_id}/sync_mcp_tools",
    )

    # -- P3: prompt optimizer family (D6) ------------------------------------
    req(
        "141_p3_prompt_create_session", "POST", "/prompt/sessions",
        capture=[(("data", "id"), "prompt_session_id")],
    )
    req(
        "142_p3_prompt_get_session", "GET",
        "/prompt/sessions/{prompt_session_id}",
    )
    # The unknown-session stream short-circuits to the monolith's error frame
    # before any model call; the frame embeds the fixed request id and the
    # seeded actor id only.
    req(
        "143_p3_prompt_stream_unknown_session", "POST",
        "/prompt/sessions/" + P3_UNKNOWN_SESSION_ID + "/messages",
        {"model_id": P3_UNKNOWN_MODEL_ID, "message": "parity prompt"},
    )
    req(
        "144_p3_prompt_save_release", "POST", "/prompt/releases",
        {
            "session_id": ids["prompt_session_id"],
            "title": "parity release",
            "prompt": "parity prompt body",
        },
        capture=[(("data", "id"), "prompt_release_id")],
    )
    req("145_p3_prompt_release_list", "GET", "/prompt/releases/list")
    req(
        "146_p3_prompt_delete_release", "DELETE",
        "/prompt/releases/{prompt_release_id}",
    )
    req("147_p3_prompt_release_list_after_delete", "GET", "/prompt/releases/list")

    # -- P4: public share view face ------------------------------------------
    # The P2 release share was deleted at the last P2 step; recreate one
    # inside the rollback boundary (create-or-update re-enables the release),
    # then exchange it for a viewer access token. Viewer steps authenticate
    # with that bearer token only: ``face="public"`` sends no platform
    # credentials on either side, mirroring the anonymous share plane.
    req(
        "148_p4_release_share_recreate", "POST",
        "/apps/{flow_app_id}/releases/{release_v1_id}/share",
        {"is_enabled": True, "require_password": False, "allow_embed": True},
        capture=[
            (("data", "id"), "p4_release_share_id"),
            (("data", "share_token"), "p4_share_token"),
        ],
    )
    req(
        "149_p4_share_token_exchange", "POST",
        "/public/share/{p4_share_token}/token",
        {"user_id": P4_VIEWER_ID},
        capture=[(("data", "access_token"), "viewer_access_token")],
    )

    def viewer(name, method, path, json_body=None, params=None, capture=None):
        """Share-plane request with the exchanged viewer bearer token."""
        return req(
            name, method, path, json_body, params,
            capture=capture, face="public",
            headers={"Authorization": f"Bearer {ids['viewer_access_token']}"},
        )

    viewer("150_p4_share_info", "GET", "/public/share")
    # A passwordless share accepts any submitted password (verify short-circuits).
    viewer(
        "151_p4_share_verify", "POST", "/public/share/verify",
        {"password": "parity-wrong"},
    )
    viewer(
        "152_p4_share_embed", "GET", "/public/share/embed",
        params={"width": "320px", "height": "480px"},
    )
    viewer("153_p4_share_config", "GET", "/public/share/config")
    # First list call admits the viewer (its end-user row is created here), so
    # the list is still empty; the fixture conversation is seeded afterwards.
    viewer("154_p4_conversations_empty", "GET", "/public/share/conversations")
    exec_sql(
        "155_p4_viewer_end_user",
        "SELECT id FROM end_users WHERE workspace_id = :workspace_id"
        " AND other_id = :other_id",
        capture="p4_viewer_end_user_id",
        workspace_id=SEED["workspace_id"],
        other_id=P4_VIEWER_ID,
    )
    exec_sql(
        "156_p4_seed_share_conversation",
        "INSERT INTO conversations (id, app_id, workspace_id, user_id, title,"
        " is_draft, message_count, is_active, created_at, updated_at)"
        " VALUES (:id, :app_id, :workspace_id, :user_id,"
        " 'parity share conversation', false, 3, true, :created_at, :created_at)"
        " RETURNING id",
        id=P4_FIXTURES["p4_conversation_id"],
        app_id=ids["flow_app_id"],
        workspace_id=SEED["workspace_id"],
        user_id=ids["p4_viewer_end_user_id"],
        created_at=P4_FIXTURE_TS["conversation"],
    )
    exec_sql(
        "157_p4_seed_user_message",
        "INSERT INTO messages (id, conversation_id, role, content, version,"
        " is_current, parent_message_id, is_deleted, status, like_count,"
        " dislike_count, report_count, created_at)"
        " VALUES (:id, :conversation_id, 'user', 'parity share question', 1,"
        " true, NULL, false, 'completed', 0, 0, 0, :created_at) RETURNING id",
        id=P4_FIXTURES["p4_user_message_id"],
        conversation_id=P4_FIXTURES["p4_conversation_id"],
        created_at=P4_FIXTURE_TS["user_message"],
    )
    exec_sql(
        "158_p4_seed_assistant_message",
        "INSERT INTO messages (id, conversation_id, role, content, version,"
        " is_current, parent_message_id, is_deleted, meta_data, status,"
        " like_count, dislike_count, report_count, created_at)"
        " VALUES (:id, :conversation_id, 'assistant', 'parity share answer', 1,"
        " true, :parent_message_id, false, CAST(:meta_data AS json),"
        " 'completed', 0, 0, 0, :created_at) RETURNING id",
        id=P4_FIXTURES["p4_assistant_message_id"],
        conversation_id=P4_FIXTURES["p4_conversation_id"],
        parent_message_id=P4_FIXTURES["p4_user_message_id"],
        meta_data=json.dumps({"usage": {"total_tokens": 10}}),
        created_at=P4_FIXTURE_TS["assistant_message"],
    )
    exec_sql(
        "159_p4_seed_assistant_message_v2",
        "INSERT INTO messages (id, conversation_id, role, content, version,"
        " is_current, parent_message_id, is_deleted, meta_data, status,"
        " like_count, dislike_count, report_count, created_at)"
        " VALUES (:id, :conversation_id, 'assistant', 'parity share answer v2',"
        " 2, false, :parent_message_id, false, CAST(:meta_data AS json),"
        " 'completed', 0, 0, 0, :created_at) RETURNING id",
        id=P4_FIXTURES["p4_assistant_v2_id"],
        conversation_id=P4_FIXTURES["p4_conversation_id"],
        parent_message_id=P4_FIXTURES["p4_user_message_id"],
        meta_data=json.dumps({"usage": {"total_tokens": 12}}),
        created_at=P4_FIXTURE_TS["assistant_message_v2"],
    )
    viewer("160_p4_conversations_listed", "GET", "/public/share/conversations")
    viewer(
        "161_p4_conversation_detail", "GET",
        "/public/share/conversations/{p4_conversation_id}",
    )
    viewer(
        "162_p4_message_like", "POST",
        "/public/share/messages/{p4_assistant_message_id}/feedback",
        {"feedback_type": "like"},
    )
    viewer(
        "163_p4_message_feedback_get", "GET",
        "/public/share/messages/{p4_assistant_message_id}/feedback",
    )
    viewer(
        "164_p4_message_favorite", "POST",
        "/public/share/messages/{p4_assistant_message_id}/favorite",
    )
    viewer(
        "165_p4_message_report", "POST",
        "/public/share/messages/{p4_assistant_message_id}/report",
        {
            "report_type": "quality",
            "report_reason": "parity share report",
            "text_start_offset": 0,
            "text_end_offset": 6,
            "selected_text": "parity",
        },
        capture=[(("data", "report_id"), "p4_report_id")],
    )
    viewer(
        "166_p4_conversation_share_create", "POST",
        "/public/share/conversations/{p4_conversation_id}/share",
        {"allow_copy": True},
        capture=[
            (("data", "share_id"), "p4_conversation_share_id"),
            (("data", "share_uuid"), "p4_conversation_share_uuid"),
        ],
    )
    viewer(
        "167_p4_conversation_share_list", "GET",
        "/public/share/conversations/{p4_conversation_id}/shares",
    )
    viewer(
        "168_p4_conversation_share_revoke", "DELETE",
        "/public/share/conversations/share/{p4_conversation_share_uuid}",
    )
    # v1 -> 2: the sibling picks up is_current while the response reports the
    # requested version's id/content.
    viewer(
        "169_p4_switch_message_version", "POST",
        "/public/share/messages/{p4_assistant_message_id}/switch-version/2",
    )
    viewer(
        "170_p4_intervention_unknown", "POST",
        "/public/share/workflow/interventions/" + P4_UNKNOWN_EXECUTION_ID + "/submit",
        {"node_id": "end", "action_id": "approve"},
    )
    # Deleting one version drags the whole assistant group of the turn.
    viewer(
        "171_p4_delete_message", "DELETE",
        "/public/share/messages/{p4_assistant_message_id}",
    )
    db(
        "172_p4_db_message_feedbacks",
        "SELECT feedback_type, is_favorite FROM message_feedbacks"
        " WHERE message_id = :message_id AND user_id = :user_id",
        message_id=P4_FIXTURES["p4_assistant_message_id"],
        user_id=P4_VIEWER_ID,
    )
    db(
        "173_p4_db_message_deleted",
        "SELECT is_deleted FROM messages WHERE id = :id",
        id=P4_FIXTURES["p4_assistant_message_id"],
    )
    db(
        "174_p4_db_message_reports",
        "SELECT report_type, status FROM message_reports WHERE id = :id",
        id=ids["p4_report_id"],
    )
    db(
        "175_p4_db_conversation_shares",
        "SELECT is_active FROM conversation_shares WHERE id = :id",
        id=ids["p4_conversation_share_id"],
    )

    # -- end-user other_name sync on an existing user (idempotent upsert) -----
    # Re-creating with the same other_id and a whitespace-padded name must
    # normalize the name and sync both end_users and end_user_info in place.
    req(
        "176_v1_recreate_end_user_spaced_name", "POST", "/end_user/create",
        {"other_id": "parity-ext-user-1", "other_name": "  Parity Recreated  "},
        face="v1",
    )
    # An omitted name must keep the stored one (insert ... on conflict do
    # nothing), leaving both tables untouched.
    req(
        "177_v1_recreate_end_user_no_name", "POST", "/end_user/create",
        {"other_id": "parity-ext-user-1"},
        face="v1",
    )
    db(
        "178_db_end_user_name_sync",
        "SELECT u.other_name AS user_name, i.other_name AS info_name"
        " FROM end_users u JOIN end_user_info i ON i.end_user_id = u.id"
        " WHERE u.id = :id",
        id=ids["end_user_id"],
    )

    return results
