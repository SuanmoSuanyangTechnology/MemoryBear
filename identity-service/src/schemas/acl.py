"""ACL 规则维护：CRUD 直连 DB（表 acl_rules）+ 全量下发 Redis `acl:rules`。

表结构由独立 alembic 迁移链管理（migrations/，version_table=alembic_version_identity），
部署时执行 `uv run alembic upgrade head`（见 services/README.md）。
"""
import json


def rules_to_redis(rules: list[dict]) -> str:
    return json.dumps([{
        "caller": r["caller_service"], "target": r["target_service"],
        "endpoint": r["endpoint"], "effect": r["effect"],
    } for r in rules], ensure_ascii=False)


def load_rules(blob: str | bytes) -> list[dict]:
    return json.loads(blob.decode() if isinstance(blob, bytes) else blob)
