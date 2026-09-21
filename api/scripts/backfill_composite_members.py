"""组合模型 config.members 回填（M3/Task 12；默认只读出报告，--apply 幂等写入）。

背景：组合成员从 association（model_config_api_key_association）迁到 config JSON
`members: [{provider, model_name}]`（spec §10.3，不建成员表）。运行期解析只读
members；association 双轨写在 Task 13 前端切 wire 前仍是模型列表显式回显来源。

规则：
- 成员翻译：组合 config 的 association → ModelApiKey 行自带 (provider, model_name)
  去重保序；顺序取旧运行期选 key 口径（ModelApiKeyRepository.get_by_model_config：
  priority asc, created_at asc），保证第一个成员与原"首个可用 key"一致；
- 回填闸门 = 该 (provider, model_name) 至少一把 api_key.is_active=true
  （model_api_key.is_active 是软删除）；**不看成员 config 的 model_config.is_active**
  （启用/禁用是 config 自身状态位，不是成员资格闸门）；
- 类型校验（存在同租户非组合 config 时，与写路径同口径）：类型不符丢弃并列示；
  成员 config 缺失不阻塞回填，标注"无锚点"（config 为可选增强：运行期按声明合成快照，
  类型随组合、能力/参数留空；报告列示供人工补齐以增强解析口径）；
- 写入：合并写 `config["members"]`（保留其余 config 键），重跑覆盖该键（幂等）；
  不翻转 is_active、不动 association（双轨写过渡期）；
- 校验索引一次性批量查（单查询按 (provider, name) 全量取非组合 config——不看
  is_active，再按租户分组比对），无循环查询。

用法（连接与主密钥读 env，自动加载 enterprise 根 .env 其次 core/api/.env）：
    python core/api/scripts/backfill_composite_members.py            # 只读报告
    python core/api/scripts/backfill_composite_members.py --apply    # 预览确认后写入
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # core/api

_REPO_CANDIDATES = [
    Path(__file__).resolve().parents[3] / ".env",   # MemoryBear-Enterprise 根
    Path(__file__).resolve().parents[1] / ".env",   # core/api
]

# 独立脚本不引 app 包：字面量与 app.models.models_model.LLM_FAMILY_TYPES 同口径
_COMPATIBLE_TYPES = {"llm", "chat"}


@dataclass
class CompositePlan:
    """单个组合 config 的回填决策。"""

    config_id: object
    name: str
    tenant_id: object
    model_type: str
    is_active: bool
    existing_members: list
    members: list = field(default_factory=list)      # [(provider, model_name)]
    dropped: list = field(default_factory=list)      # [(pair, reason)]
    unanchored: list = field(default_factory=list)   # [(provider, model_name)] 无同租户 config 锚点
    key_rows: list = field(default_factory=list)

    @property
    def writable(self) -> bool:
        return bool(self.members)

    @property
    def unchanged(self) -> bool:
        return self.existing_members == [
            {"provider": p, "model_name": n} for p, n in self.members
        ]


def _member_rows(db, key_rows: list) -> dict:
    """全量成员配置索引 {(tenant_id, provider, name): row}（单查询，不看 config.is_active）。

    同键多行时优先启用、较新者（与写路径校验/运行期解析同口径）。
    """
    from sqlalchemy import select, tuple_

    from app.models.models_model import ModelConfig, ModelProvider

    keys = sorted({(k.provider, k.model_name) for k in key_rows})
    if not keys:
        return {}
    rows = db.execute(
        select(ModelConfig)
        .where(
            ModelConfig.provider != ModelProvider.COMPOSITE,
            tuple_(ModelConfig.provider, ModelConfig.name).in_(keys),
        )
        .order_by(
            ModelConfig.is_active.desc(),
            ModelConfig.created_at.desc().nullslast(),
        )
    ).scalars().all()
    index: dict = {}
    for row in rows:
        index.setdefault((row.tenant_id, row.provider, row.name), row)
    return index


def _analyze(db) -> tuple[list[CompositePlan], dict]:
    """读组合 config + association → 逐组合决策（成员/丢弃/无锚点/是否已回填）。

    成员 = association 绑定 key 行的 (provider, model_name) 去重保序；闸门 =
    至少一把 key.is_active=true；不看成员 config.is_active；config 缺失 → 无锚点标注。
    """
    from sqlalchemy import select

    from app.models.models_model import (
        ModelApiKey,
        ModelConfig,
        ModelProvider,
        model_config_api_key_association,
    )

    composites = list(
        db.execute(
            select(ModelConfig).where(ModelConfig.provider == ModelProvider.COMPOSITE)
        ).scalars()
    )
    keys = {k.id: k for k in db.execute(select(ModelApiKey)).scalars()}
    assoc = db.execute(
        select(
            model_config_api_key_association.c.model_config_id,
            model_config_api_key_association.c.api_key_id,
        )
    ).all()

    # 按旧运行期选 key 顺序（priority asc, created_at asc）排列每个组合的关联 key
    by_config: dict = {}
    for config_id, key_id in assoc:
        key = keys.get(key_id)
        if key is not None:
            by_config.setdefault(config_id, []).append(key)

    all_keys = [key for rows in by_config.values() for key in rows]
    for rows in by_config.values():
        rows.sort(key=lambda k: (str(k.priority or ""), k.created_at or datetime.min))

    member_index = _member_rows(db, all_keys)

    plans: list[CompositePlan] = []
    for config in composites:
        plan = CompositePlan(
            config_id=config.id,
            name=config.name,
            tenant_id=config.tenant_id,
            model_type=str(config.type),
            is_active=bool(config.is_active),
            existing_members=list((config.config or {}).get("members") or []),
            key_rows=by_config.get(config.id, []),
        )
        pairs: list = []
        seen: set = set()
        for key in plan.key_rows:
            pair = (key.provider, key.model_name)
            if pair not in seen:
                seen.add(pair)
                pairs.append(pair)
        keys_by_pair: dict = {}
        for key in plan.key_rows:
            keys_by_pair.setdefault((key.provider, key.model_name), []).append(key)

        for pair in pairs:
            if not any(key.is_active for key in keys_by_pair[pair]):
                plan.dropped.append((pair, "api_key 已软删除（is_active=false）"))
                continue
            member = member_index.get((config.tenant_id, pair[0], pair[1]))
            if member is None:
                plan.members.append(pair)
                plan.unanchored.append(pair)
                continue
            member_type = str(member.type)
            if not (
                member_type == plan.model_type
                or (member_type in _COMPATIBLE_TYPES and plan.model_type in _COMPATIBLE_TYPES)
            ):
                plan.dropped.append((pair, f"类型不符（成员 {member_type} vs 组合 {plan.model_type}）"))
                continue
            plan.members.append(pair)
        plans.append(plan)

    stats = {
        "composite_total": len(composites),
        "with_association": sum(1 for p in plans if p.key_rows),
        "already_backfilled": sum(1 for p in plans if p.existing_members and p.unchanged),
        "stale_members": sum(1 for p in plans if p.existing_members and not p.unchanged),
        "writable": sum(1 for p in plans if p.writable),
        "unanchored_members": sum(len(p.unanchored) for p in plans),
        "dropped_members": sum(len(p.dropped) for p in plans),
    }
    return plans, stats


def _render_report(plans: list[CompositePlan], stats: dict) -> str:
    lines = [
        f"组合 config：{stats['composite_total']}（有 association：{stats['with_association']}；"
        f"本次可写：{stats['writable']}；已回填且一致：{stats['already_backfilled']}；"
        f"已有 members 但与本次决策不一致：{stats['stale_members']}）",
        f"成员：无锚点（同租户无 config，运行期按声明合成，可人工补齐）={stats['unanchored_members']}；"
        f"淘汰（软删 key / 类型不符）={stats['dropped_members']}",
        "明细：",
    ]
    for plan in sorted(plans, key=lambda p: (str(p.tenant_id), p.name)):
        members = ", ".join(f"{p}/{n}" for p, n in plan.members) or "(空)"
        head = (
            f"  {plan.name:<28} id={str(plan.config_id)[:8]} tenant={plan.tenant_id}"
            f" type={plan.model_type} active={plan.is_active}"
            f" keys={len(plan.key_rows)} members={len(plan.members)}"
            f" 淘汰={len(plan.dropped)} 无锚点={len(plan.unanchored)}"
        )
        if not plan.writable:
            head += "  [不写：无可写成员，人工确认]"
        elif plan.unchanged:
            head += "  [幂等：与现有 members 一致]"
        lines.append(head)
        lines.append(f"      members: {members}")
        for provider, model_name in plan.unanchored:
            lines.append(
                f"      [无锚点] {provider}/{model_name}: 已回填；运行期按声明合成快照（能力/参数留空），"
                f"可人工补同租户 config 增强"
            )
        for pair, reason in plan.dropped:
            lines.append(f"      [淘汰] {pair[0]}/{pair[1]}: {reason}")
    return "\n".join(lines)


def _run_apply(db, plans: list[CompositePlan]) -> dict:
    from app.models.models_model import ModelConfig

    updated = skipped = 0
    for plan in plans:
        if not plan.writable:
            skipped += 1
            continue
        config_row = db.get(ModelConfig, plan.config_id)
        if config_row is None:
            skipped += 1
            continue
        merged = dict(config_row.config or {})
        merged["members"] = [
            {"provider": provider, "model_name": model_name}
            for provider, model_name in plan.members
        ]
        config_row.config = merged
        updated += 1
    db.commit()
    return {"updated": updated, "skipped": skipped}


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="预览确认后写入 config.members")
    args = parser.parse_args()

    _load_env()
    for var in ("DB_HOST", "DB_USER", "DB_PASSWORD", "DB_NAME"):
        if not os.getenv(var):
            print(f"[err] 缺少 env {var}；请在 MemoryBear-Enterprise 根或 core/api 目录运行")
            return 2

    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    engine = create_engine(
        f"postgresql+psycopg2://{os.environ['DB_USER']}:{os.environ['DB_PASSWORD']}"
        f"@{os.environ['DB_HOST']}:{os.environ.get('DB_PORT', '5432')}/{os.environ['DB_NAME']}",
        pool_pre_ping=True,
    )
    with Session(engine) as db:
        try:
            plans, stats = _analyze(db)
            print(_render_report(plans, stats))
            if not args.apply:
                return 0
            writable = [p for p in plans if p.writable]
            if not writable:
                print("无可写组合")
                return 0
            confirm = input(f"写入上述 {len(writable)} 个组合的 config.members？（y/N）")
            if confirm.strip().lower() != "y":
                print("已取消")
                return 0
            print("执行完成：", _run_apply(db, plans))
        finally:
            db.rollback()
    return 0


def _load_env() -> None:
    try:
        import dotenv
    except ImportError:
        return
    for env_path in _REPO_CANDIDATES:
        if env_path.is_file():
            dotenv.load_dotenv(env_path)


if __name__ == "__main__":
    raise SystemExit(main())
