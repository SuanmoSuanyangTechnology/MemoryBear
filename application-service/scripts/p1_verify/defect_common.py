"""Defect battery: post-fix one-shot scenario for the definition-plane defect
batch (multi-agent routing/config crashes, foreign-app guard, tool statistics
enabled count).

Recorded in the parity recording shape (step / status / data), so
``parity_compare.py --monolith ... --app-service ...`` can deep-diff the two
sides, and ``defect_expect_check.py`` can assert the fixed wire semantics
absolutely against a recording.

Every step runs inside the runner's outer transaction (savepoint discipline),
so the scratch database keeps no rows after either side finishes.
"""

from __future__ import annotations

import parity_common
from parity_common import SEED, normalize

FOREIGN_WORKSPACE_ID = "aaaaaaa1-0000-4000-8000-0000000000f0"
FOREIGN_APP_ID = "aaaaaaa1-0000-4000-8000-0000000000f1"
TOOL_AVAILABLE_1 = "aaaaaaa1-0000-4000-8000-0000000000f2"
TOOL_AVAILABLE_2 = "aaaaaaa1-0000-4000-8000-0000000000f3"
TOOL_UNCONFIGURED = "aaaaaaa1-0000-4000-8000-0000000000f4"

TOOL_INSERT_SQL = (
    "INSERT INTO tool_configs (id, name, tool_type, tenant_id, status,"
    " is_active, created_at, updated_at)"
    " VALUES (:id, :name, 'custom', :tenant_id, :status, true, now(), now())"
)

APP_INSERT_SQL = (
    "INSERT INTO apps (id, workspace_id, created_by, name, type, is_active,"
    " created_at, updated_at)"
    " VALUES (:id, :workspace_id, :created_by, 'defect-foreign', 'agent',"
    " true, now(), now())"
)


def run_defect_scenario(runner) -> list[dict]:
    """Execute the defect scenario; return the recorded [step] list."""
    results: list[dict] = []
    ids: dict[str, str | None] = {}

    def record(name: str, status, data):
        results.append({"step": name, "status": status, "data": data})

    def req(name, method, path, json_body=None, params=None, capture=None):
        concrete = path.format(**{k: (v or "none") for k, v in ids.items()})
        status, body = runner.request(method, concrete, json_body, params)
        if capture and isinstance(body, dict):
            for keys, role in capture:
                ids[role] = parity_common._dig(body, *keys)
        record(name, status, normalize(body, ids))
        return body

    def db(name: str, sql: str, **params):
        row = runner.fetch_row(sql, **params)
        record(name, None, normalize(list(row) if row is not None else None, ids))

    # -- statistics baseline: the scenario owns every tenant tool row ------
    db(
        "00_db_tool_baseline",
        "SELECT count(*) FROM tool_configs WHERE tenant_id = :tenant_id",
        tenant_id=SEED["tenant_id"],
    )

    # -- multi-agent: no-config / empty-switch / keep-existing -------------
    req(
        "01_create_cluster_app", "POST", "/apps",
        {"name": "defect-cluster", "type": "multi_agent"},
        capture=[(("data", "id"), "cluster_app_id")],
    )
    req(
        "02_routing_without_config", "POST",
        "/apps/{cluster_app_id}/handoffs/test-routing",
        params={"message": "hi"},
    )
    req(
        "03_put_create_without_sub_agents", "PUT",
        "/apps/{cluster_app_id}/multi-agent",
        {"master_agent_name": "M", "aggregation_strategy": "merge"},
        capture=[(("data", "id"), "multi_agent_config_id")],
    )
    db(
        "04_db_sub_agents_after_create",
        "SELECT sub_agents FROM multi_agent_configs"
        " WHERE app_id = :app_id AND is_active = true",
        app_id=ids["cluster_app_id"],
    )
    req(
        "05_routing_empty_sub_agents", "POST",
        "/apps/{cluster_app_id}/handoffs/test-routing",
        params={"message": "hi"},
    )

    req(
        "06_create_sub_app", "POST", "/apps",
        {"name": "defect-sub", "type": "agent"},
        capture=[(("data", "id"), "sub_app_id")],
    )
    req(
        "07_put_sub_config", "PUT", "/apps/{sub_app_id}/config",
        {"system_prompt": "sub", "default_model_config_id": SEED["model_config_id"]},
        capture=[(("data", "id"), "sub_config_id")],
    )
    req(
        "08_publish_sub_app", "POST", "/apps/{sub_app_id}/publish",
        {"version_name": "v1", "release_notes": "defect battery", "name": "defect-sub"},
        capture=[(("data", "id"), "sub_release_id")],
    )
    req(
        "09_put_with_sub_agents", "PUT",
        "/apps/{cluster_app_id}/multi-agent",
        {
            "sub_agents": [
                {
                    "agent_id": ids["sub_app_id"],
                    "name": "小助手",
                    "role": "assistant",
                    "capabilities": ["chat"],
                    "release_policy": "current",
                }
            ],
            "aggregation_strategy": "merge",
        },
    )
    db(
        "10_db_sub_agents_after_put",
        "SELECT sub_agents FROM multi_agent_configs"
        " WHERE app_id = :app_id AND is_active = true",
        app_id=ids["cluster_app_id"],
    )
    req(
        "11_routing_keyword_match", "POST",
        "/apps/{cluster_app_id}/handoffs/test-routing",
        params={"message": "chat chat"},
    )
    req(
        "12_put_without_sub_agents_keeps_stored", "PUT",
        "/apps/{cluster_app_id}/multi-agent",
        {"master_agent_name": "M2"},
    )
    db(
        "13_db_sub_agents_after_keep",
        "SELECT sub_agents FROM multi_agent_configs"
        " WHERE app_id = :app_id AND is_active = true",
        app_id=ids["cluster_app_id"],
    )
    req("14_get_multi_agent_after_keep", "GET", "/apps/{cluster_app_id}/multi-agent")

    # -- foreign app: guard rejects cross-workspace routing preflight ------
    runner.insert(
        APP_INSERT_SQL,
        id=FOREIGN_APP_ID,
        workspace_id=FOREIGN_WORKSPACE_ID,
        created_by=SEED["actor_id"],
    )
    ids["foreign_app_id"] = FOREIGN_APP_ID
    record("15_insert_foreign_app", None, "inserted")
    req(
        "16_routing_foreign_app", "POST",
        "/apps/{foreign_app_id}/handoffs/test-routing",
        params={"message": "hi"},
    )

    # -- tools statistics: enabled count matches the stored statuses -------
    for tool_id, name, status in (
        (TOOL_AVAILABLE_1, "defect-tool-a", "available"),
        (TOOL_AVAILABLE_2, "defect-tool-b", "available"),
        (TOOL_UNCONFIGURED, "defect-tool-c", "unconfigured"),
    ):
        runner.insert(
            TOOL_INSERT_SQL,
            id=tool_id,
            name=name,
            tenant_id=SEED["tenant_id"],
            status=status,
        )
    record("17_insert_tool_rows", None, "inserted")
    req("18_tools_statistics", "GET", "/tools/statistics")
    db(
        "19_db_enabled_count",
        "SELECT count(*) FROM tool_configs WHERE tenant_id = :tenant_id"
        " AND is_active = true AND status = 'available'",
        tenant_id=SEED["tenant_id"],
    )

    return results
