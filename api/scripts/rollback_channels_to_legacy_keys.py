"""model_channels 渠道 → 旧表 model_api_keys + association 反向回滚（幂等可重跑）。

背景（docs/superpowers/specs/2026-09-12-model-rollback-switch-design.md §5.1）：
切流后管理面只写渠道表，旧逻辑（off 档 / 旧镜像）只读旧表；回滚时刻须先用本
脚本把渠道表补回旧表。正向脚本 core/api/scripts/migrate_api_keys_to_channels.py
为映射权威，本脚本逐条反向对照。

规则：
- 范围：source=manual 渠道；source=platform（speedbear 绑定）由企业脚本
  scripts/rollback_speedbear_channels_to_bindings.py 负责，跳过并列示；
  未知 source 不写、报告人工确认。
- 解密：ChannelService.reveal（AAD=provider:tenant_id）；解密失败渠道不写、
  报告列示。任何输出只出 masked。
- 展开（渠道 × 租户 → 旧表可解析形态）：
  * 点名渠道（model_names 非空）→ 逐名关联该租户锚点：非组合 config
    （tenant/provider/name 精确匹配，与本轮幂等无关地保留）+ 组合 config
    （config.members 含该 (provider, model_name) 成员声明 —— 旧世界组合 config
    读自身 association keys，成员 key 必须回挂到组合 config 上）；
  * provider 级 [] 渠道 → 该租户 + 该 provider 的活跃非组合 config 全覆盖展开
    （model_name=config.name），另含组合 config 中 provider 匹配的成员声明
    （model_name=成员名）——正向时旧表无 [] 形态，反向按语义展开，报告列示。
- key 行字段：api_key=reveal、api_base=渠道原值（provider 级 NULL）、
  description=remark、priority=str(渠道 priority)（正向映射之逆）、
  capability/is_omni 取锚点 config 派生视图（新列优先，旧列停写冻结）、
  is_active 对齐渠道（软停用 → 旧行停用，
  spec §5.3；既有行恰为同凭据时做一次对齐更新并报告，不产生重复行）。
- 幂等：按（明文, provider, api_base, model_name）四元组精确匹配既有行 →
  复用并补 association；同 (provider, api_base, model_name) 但明文不同 →
  冲突：新增本行并报告（旧世界两把 key 并存，旧凭据人工处置）。
- 安全：只写 model_api_keys + association，不写/不改 model_channels；
  默认 dry-run 出四类报告（新增/已存在/冲突/未映射），--execute 交互确认；
  跑完 reveal 逐条对照校验 + 旧表计数。

用法（连接与主密钥读 env：DB_* + MODEL_CREDENTIALS_KEY，自动加载 enterprise
根 .env 其次 core/api/.env）：
    python core/api/scripts/rollback_channels_to_legacy_keys.py
    python core/api/scripts/rollback_channels_to_legacy_keys.py --execute
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # core/api

_REPO_CANDIDATES = [
    Path(__file__).resolve().parents[3] / ".env",   # MemoryBear-Enterprise 根
    Path(__file__).resolve().parents[1] / ".env",   # core/api
]


def _masked(api_key: str) -> str:
    if len(api_key) < 8:
        return "****"
    return f"{api_key[:3]}****{api_key[-4:]}"


def _norm_base(value) -> str | None:
    if value is None:
        return None
    value = str(value).strip()
    return value or None


@dataclass
class KeyPlan:
    """一条将回写旧表的 key 行 + 关联目标集。"""

    plain: str
    provider: str
    api_base: str | None
    model_name: str
    remark: str | None = None
    priority: str = "1"
    is_active: bool = True
    capability: list = field(default_factory=list)
    is_omni: bool = False
    channel_ids: list = field(default_factory=list)
    associations: list = field(default_factory=list)   # [(config_id, kind, label)]
    notes: list = field(default_factory=list)
    status: str = "new"          # new | exists | conflict（analyze 末尾判定）
    existing_row: object = None  # status=exists 时的既有 ModelApiKey 行
    assoc_existing: int = 0
    assoc_new: int = 0
    reapply_active: bool = False  # 既有行 is_active 与渠道不一致，执行时对齐

    @property
    def identity(self) -> tuple:
        return (self.plain, self.provider, self.api_base, self.model_name)

    @property
    def label(self) -> str:
        return (
            f"{_masked(self.plain):<20} {self.provider}/{self.model_name} "
            f"base={str(self.api_base or '(空)')[:38]}"
        )


@dataclass
class Unmapped:
    """不写旧表的条目（渠道 id + 模型名/通配 + 原因）。"""

    channel_id: object
    provider: str
    model_name: str
    reason: str


def _analyze(db) -> tuple[list[KeyPlan], dict]:
    """读渠道表 + 旧表 → 展开/判重 → 计划 + 四类报告桶。全量读入内存（行数 < 300）。"""
    from cryptography.exceptions import InvalidTag
    from sqlalchemy import select

    from app.models.models_model import (
        ModelApiKey,
        ModelChannel,
        ModelConfig,
        model_config_api_key_association,
    )
    from app.services.channel_registry import parse_members
    from app.services.channel_service import ChannelService
    from app.services.model_profile_view import legacy_view

    channels = list(db.execute(select(ModelChannel)).scalars())
    configs = list(db.execute(select(ModelConfig)).scalars())
    key_rows = list(db.execute(select(ModelApiKey)).scalars())
    assoc_rows = db.execute(
        select(
            model_config_api_key_association.c.model_config_id,
            model_config_api_key_association.c.api_key_id,
        )
    ).all()
    existing_assoc = {(row[0], row[1]) for row in assoc_rows}

    by_pn: dict = {}          # (tenant, provider, name) -> [非组合 config]
    active_by_p: dict = {}    # (tenant, provider) -> [活跃非组合 config]
    composites: list = []     # [(组合 config, [(provider, member_name)])]
    member_index: dict = {}   # (tenant, provider, member_name) -> [组合 config]
    for row in configs:
        if row.provider == "composite":
            pairs = parse_members(row.config)
            composites.append((row, pairs))
            for provider, name in pairs:
                member_index.setdefault((row.tenant_id, provider, name), []).append(row)
            continue
        by_pn.setdefault((row.tenant_id, row.provider, row.name), []).append(row)
        if row.is_active:
            active_by_p.setdefault((row.tenant_id, row.provider), []).append(row)

    exact: dict = {}   # (明文, provider, base, model_name) -> 行
    triple: dict = {}  # (provider, base, model_name) -> [行]（冲突检测）
    for row in key_rows:
        base = _norm_base(row.api_base)
        exact.setdefault((row.api_key, row.provider, base, row.model_name), row)
        triple.setdefault((row.provider, base, row.model_name), []).append(row)

    svc = ChannelService(db)  # 主密钥缺失/非法在此快速失败
    plans: dict = {}
    skipped_sources: dict = {}
    unmapped: list[Unmapped] = []
    decrypt_failures: list = []

    for ch in channels:
        if ch.source != "manual":
            skipped_sources[ch.source] = skipped_sources.get(ch.source, 0) + 1

    for ch in channels:
        if ch.source != "manual":
            continue
        if ch.provider == "composite":  # 理论不可达（ChannelService 已禁 composite）
            unmapped.append(Unmapped(ch.id, ch.provider, "*", "provider=composite 非法渠道"))
            continue
        try:
            plain = svc.reveal(ch)
        except (ValueError, InvalidTag) as exc:
            decrypt_failures.append((ch, str(exc)))
            continue
        base = _norm_base(ch.api_base)

        targets: dict = {}  # model_name -> [(config 行, kind 标签)]
        if ch.model_names:
            for name in ch.model_names:
                anchors = [
                    (cfg, "config")
                    for cfg in by_pn.get((ch.tenant_id, ch.provider, name), [])
                ]
                anchors += [
                    (comp, "composite")
                    for comp in member_index.get((ch.tenant_id, ch.provider, name), [])
                ]
                targets[name] = anchors
        else:
            for cfg in active_by_p.get((ch.tenant_id, ch.provider), []):
                targets.setdefault(cfg.name, []).append((cfg, "config"))
            for comp, pairs in composites:
                if comp.tenant_id != ch.tenant_id:
                    continue
                for provider, name in pairs:
                    if provider == ch.provider:
                        targets.setdefault(name, []).append((comp, "composite"))

        for name, anchors in sorted(targets.items()):
            if not anchors:
                unmapped.append(Unmapped(ch.id, ch.provider, name, "无锚点 config/组合成员"))
                continue
            key = (plain, ch.provider, base, name)
            plan = plans.get(key)
            if plan is None:
                plan = plans[key] = KeyPlan(
                    plain=plain, provider=ch.provider, api_base=base, model_name=name,
                    remark=ch.remark, priority=str(ch.priority), is_active=bool(ch.is_active),
                )
                if not ch.model_names:
                    plan.notes.append("provider级展开（活跃非组合 config + 组合成员声明）")
                anchor_cfg = next((cfg for cfg, kind in anchors if kind == "config"), None)
                if anchor_cfg is None:
                    comp = anchors[0][0]
                    peer = by_pn.get((comp.tenant_id, ch.provider, name))
                    anchor_cfg = peer[0] if peer else comp
                # 旧列停写后锚点能力取派生视图（新列优先，空则旧列回退）
                plan.capability, plan.is_omni = legacy_view(anchor_cfg)
            elif bool(ch.is_active) and not plan.is_active:
                plan.is_active = True  # 跨租户同凭据：活跃语义取并（任一活跃即可用）
            if ch.priority != int(plan.priority or 0) and "priority 跨渠道不一致" not in plan.notes:
                plan.notes.append("priority 跨渠道不一致（取首个）")
            plan.channel_ids.append(ch.id)
            seen_ids = {item[0] for item in plan.associations}
            for cfg in anchors:
                cfg_row, kind = cfg
                if cfg_row.id in seen_ids:
                    continue
                seen_ids.add(cfg_row.id)
                plan.associations.append(
                    (cfg_row.id, kind, f"{cfg_row.name}@{str(cfg_row.tenant_id)[:8]}")
                )
                if kind == "config" and cfg_row.provider == "speedbear" and cfg_row.is_public:
                    note = "speedbear 公共模型旧世界走绑定表（企业脚本口径），本关联仅非公共场景生效"
                    if note not in plan.notes:
                        plan.notes.append(note)

    for plan in plans.values():
        hit = exact.get(plan.identity)
        if hit is not None:
            plan.status = "exists"
            plan.existing_row = hit
            plan.reapply_active = bool(hit.is_active) != plan.is_active
        elif triple.get((plan.provider, plan.api_base, plan.model_name)):
            plan.status = "conflict"
        else:
            plan.status = "new"
        for config_id, _kind, _label in plan.associations:
            if hit is not None and (config_id, hit.id) in existing_assoc:
                plan.assoc_existing += 1
            else:
                plan.assoc_new += 1

    report = {
        "channels_total": len(channels),
        "manual_channels": sum(1 for ch in channels if ch.source == "manual"),
        "skipped_sources": skipped_sources,
        "decrypt_failures": decrypt_failures,
        "unmapped": unmapped,
    }
    ordered = sorted(
        plans.values(), key=lambda p: (p.provider, p.model_name, str(p.api_base or ""))
    )
    return ordered, report


def _render_plan(plans: list[KeyPlan], report: dict) -> str:
    n_new = sum(1 for p in plans if p.status == "new")
    n_exists = sum(1 for p in plans if p.status == "exists")
    n_conflict = sum(1 for p in plans if p.status == "conflict")
    assoc_new = sum(p.assoc_new for p in plans)
    assoc_kept = sum(p.assoc_existing for p in plans)
    reapply = sum(1 for p in plans if p.reapply_active)
    head = [
        f"渠道合计：{report['channels_total']}（manual：{report['manual_channels']} → "
        f"{len(plans)} 组 key 行）",
        f"key 行计划：{len(plans)}（新增={n_new}，已存在复用={n_exists}，冲突新增={n_conflict}）",
        f"association：新增 {assoc_new} 条，已存在 {assoc_kept} 条；is_active 对齐更新 {reapply} 行",
        f"未映射（不写）：{len(report['unmapped'])}；解密失败：{len(report['decrypt_failures'])}；"
        f"跳过 source：{report['skipped_sources'] or '无'}",
        "明细：",
    ]
    lines = []
    for p in plans:
        tag = {"new": "新增", "exists": "已存在", "conflict": "冲突"}[p.status]
        suffix = "  [对齐is_active]" if p.reapply_active else ""
        lines.append(
            f"  [{tag}] {p.label} active={p.is_active}"
            f" 关联={len(p.associations)}(补{p.assoc_new}){suffix}"
        )
        for _cid, kind, label in p.associations[:4]:
            lines.append(f"        - {kind}: {label}")
        if len(p.associations) > 4:
            lines.append(f"        - ...(共 {len(p.associations)})")
        for note in p.notes:
            lines.append(f"        ⚠ {note}")
    for item in report["unmapped"]:
        lines.append(
            f"  [未映射] channel={str(item.channel_id)[:8]} {item.provider}/{item.model_name}: {item.reason}"
        )
    for ch, err in report["decrypt_failures"]:
        lines.append(
            f"  [解密失败] channel={str(ch.id)[:8]} {ch.provider}/{_masked(ch.credential_masked)}: {err}"
        )
    for source, count in report["skipped_sources"].items():
        reason = "platform 归企业反向脚本" if source == "platform" else "未知 source，人工确认"
        lines.append(f"  [跳过] source={source} 渠道 {count}（{reason}）")
    return "\n".join(head + lines)


def _run_execute(db, plans: list[KeyPlan]) -> dict:
    """写 key 行 + association（同四元组精确匹配复用）；返回计数 + reveal 对照校验。"""
    from cryptography.exceptions import InvalidTag
    from sqlalchemy import func, select

    from app.models.models_model import (
        ModelApiKey,
        ModelChannel,
        model_config_api_key_association,
    )
    from app.services.channel_service import ChannelService

    assoc_rows = db.execute(
        select(
            model_config_api_key_association.c.model_config_id,
            model_config_api_key_association.c.api_key_id,
        )
    ).all()
    existing_assoc = {(row[0], row[1]) for row in assoc_rows}

    created = reused = aligned = assoc_added = 0
    for plan in plans:
        row = plan.existing_row
        if row is None:
            row = ModelApiKey(
                model_name=plan.model_name,
                description=plan.remark,
                provider=plan.provider,
                api_key=plan.plain,
                api_base=plan.api_base,
                capability=plan.capability,
                is_omni=plan.is_omni,
                priority=plan.priority,
                is_active=plan.is_active,
            )
            db.add(row)
            db.flush()
            plan.existing_row = row
            created += 1
        else:
            reused += 1
            if plan.reapply_active:
                row.is_active = plan.is_active
                aligned += 1
        for config_id, _kind, _label in plan.associations:
            if (config_id, row.id) in existing_assoc:
                continue
            db.execute(
                model_config_api_key_association.insert().values(
                    model_config_id=config_id, api_key_id=row.id
                )
            )
            existing_assoc.add((config_id, row.id))
            assoc_added += 1
    db.commit()

    svc = ChannelService(db)
    ok = fail = 0
    for plan in plans:
        fresh = db.get(ModelApiKey, plan.existing_row.id)
        source_channel = db.get(ModelChannel, plan.channel_ids[0])
        try:
            revealed = svc.reveal(source_channel)
        except (ValueError, InvalidTag):
            revealed = None
        if fresh is not None and revealed is not None and fresh.api_key == revealed:
            ok += 1
        else:
            fail += 1
    return {
        "created": created,
        "reused": reused,
        "aligned_active": aligned,
        "assoc_added": assoc_added,
        "row_verify_ok": ok,
        "row_verify_fail": fail,
        "key_total": db.execute(select(func.count()).select_from(ModelApiKey)).scalar(),
        "assoc_total": db.execute(
            select(func.count()).select_from(model_config_api_key_association)
        ).scalar(),
    }


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true", help="预览确认后执行回写（只写旧表）")
    args = parser.parse_args()

    _load_env()
    for var in ("DB_HOST", "DB_USER", "DB_PASSWORD", "DB_NAME", "MODEL_CREDENTIALS_KEY"):
        if not os.getenv(var):
            print(f"[err] 缺少 env {var}；请在 MemoryBear-Enterprise 根或 core/api 目录运行（解密需主密钥）")
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
            plans, report = _analyze(db)
            print(_render_plan(plans, report))
            if not args.execute:
                return 0
            if not plans:
                print("无待回写渠道")
                return 0
            confirm = input(f"回写上述 {len(plans)} 组 key 行到 model_api_keys + association？（y/N）")
            if confirm.strip().lower() != "y":
                print("已取消")
                return 0
            print("执行完成：", _run_execute(db, plans))
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
