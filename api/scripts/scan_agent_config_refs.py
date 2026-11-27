"""Report invalid or unavailable Agent references in a read-only PostgreSQL transaction."""

import argparse
import json
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import URL, create_engine, select, text
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def references(retrieval, tools, skills):
    if isinstance(retrieval, dict):
        bases = retrieval.get("knowledge_bases")
        if isinstance(bases, list):
            for entry in bases:
                yield "kb_id", entry.get("kb_id") if isinstance(entry, dict) else None
        if retrieval.get("reranker_id") is not None:
            yield "reranker_id", retrieval["reranker_id"]

    if isinstance(tools, list):
        for entry in tools:
            if not isinstance(entry, dict):
                yield "tool_id", None
            elif entry.get("tool_id") is not None or entry.get("enabled"):
                yield "tool_id", entry.get("tool_id")

    if isinstance(skills, dict) and skills.get("skill_ids") is not None:
        ids = skills["skill_ids"]
        for value in ids if isinstance(ids, list) else [None]:
            yield "skill_id", value


def report(db):
    from app.models import (
        AgentConfig,
        App,
        AppRelease,
        Knowledge,
        ModelConfig,
        Skill,
        Workspace,
    )
    from app.models.tool_model import ToolConfig
    from app.repositories.agent_config_reference_repository import (
        available_knowledge_ids,
        available_reranker_ids,
        available_skill_ids,
        tenant_tool_ids,
    )
    from app.services.agent_config_references import parse_reference_id

    drafts = (
        select(
            App.id,
            App.workspace_id,
            Workspace.tenant_id,
            AgentConfig.knowledge_retrieval,
            AgentConfig.tools,
            AgentConfig.skills,
        )
        .select_from(AgentConfig)
        .join(App, App.id == AgentConfig.app_id)
        .join(Workspace, Workspace.id == App.workspace_id)
        .where(App.type == "agent")
    )
    releases = (
        select(
            App.id,
            App.workspace_id,
            Workspace.tenant_id,
            AppRelease.id,
            App.current_release_id,
            AppRelease.config,
        )
        .select_from(AppRelease)
        .join(App, App.id == AppRelease.app_id)
        .join(Workspace, Workspace.id == App.workspace_id)
        .where(AppRelease.type == "agent")
    )
    rows_scanned = Counter({"draft": 0, "active_release": 0, "historical_release": 0})
    counts, apps, versions = Counter(), defaultdict(set), defaultdict(set)

    def record(kind, field, reason, app_id, version_id):
        key = (kind, field, reason)
        counts[key] += 1
        apps[key].add(app_id)
        if version_id is not None:
            versions[key].add(version_id)

    for is_release, stmt in ((False, drafts), (True, releases)):
        for batch in db.execute(stmt.execution_options(yield_per=250)).partitions():
            pending = defaultdict(list)
            for app_id, workspace_id, tenant_id, first, second, third in batch:
                version_id = first if is_release else None
                if is_release:
                    kind = "active_release" if first == second else "historical_release"
                    config = third if isinstance(third, dict) else {}
                    blocks = (
                        config.get("knowledge_retrieval"),
                        config.get("tools"),
                        config.get("skills"),
                    )
                else:
                    kind, blocks = "draft", (first, second, third)
                rows_scanned[kind] += 1
                for field, value in references(*blocks):
                    reference_id = parse_reference_id(value)
                    if reference_id is None:
                        record(kind, field, "invalid_id", app_id, version_id)
                    else:
                        pending[workspace_id, tenant_id, field].append(
                            (reference_id, kind, app_id, version_id)
                        )

            for (workspace_id, tenant_id, field), entries in pending.items():
                ids = {reference_id for reference_id, *_ in entries}
                model = {
                    "kb_id": Knowledge,
                    "skill_id": Skill,
                    "tool_id": ToolConfig,
                    "reranker_id": ModelConfig,
                }[field]
                existing = set(db.scalars(select(model.id).where(model.id.in_(ids))))
                if field == "kb_id":
                    available = available_knowledge_ids(db, ids, workspace_id)
                elif field == "skill_id":
                    available = available_skill_ids(db, ids, tenant_id)
                elif field == "tool_id":
                    available = tenant_tool_ids(db, ids, tenant_id, active_only=True)
                else:
                    available = available_reranker_ids(db, ids, tenant_id)
                for reference_id, kind, app_id, version_id in entries:
                    if reference_id not in available:
                        reason = (
                            "unavailable" if reference_id in existing else "missing"
                        )
                        record(kind, field, reason, app_id, version_id)

    return {
        "rows_scanned": dict(rows_scanned),
        "issues": [
            {
                "source": source,
                "field": field,
                "reason": reason,
                "references": counts[source, field, reason],
                "apps": len(apps[source, field, reason]),
                "versions": len(versions[source, field, reason]),
            }
            for source, field, reason in sorted(counts)
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--env-file", type=Path, help="Load DB_* variables from this file"
    )
    args = parser.parse_args()
    if args.env_file is not None:
        if not args.env_file.is_file():
            parser.error("the selected env file does not exist")
        load_dotenv(args.env_file)
    required = ("DB_HOST", "DB_NAME", "DB_USER", "DB_PASSWORD")
    if missing := [key for key in required if key not in os.environ]:
        parser.error(f"missing database configuration: {', '.join(missing)}")
    url = URL.create(
        "postgresql+psycopg2",
        username=os.environ["DB_USER"],
        password=os.environ["DB_PASSWORD"],
        host=os.environ["DB_HOST"],
        port=int(os.environ.get("DB_PORT", "5432")),
        database=os.environ["DB_NAME"],
    )
    engine = create_engine(url, connect_args={"connect_timeout": 5})
    with Session(engine) as db:
        db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        print(json.dumps(report(db), indent=2))


if __name__ == "__main__":
    main()
