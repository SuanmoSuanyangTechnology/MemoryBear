"""旧表明文 key → model_channels 渠道登记（M1 一次性数据迁移；幂等可重跑）。

规则（spec §8.2 步骤 1-2 / D16，M1 范围；2026-09-09 用户最终拍板，推翻
"绑定面判定 + 默认端点归一为空"）：
- 聚合键 = (api_key 明文, provider, api_base)；key 行无租户，租户经
  model_config_api_key_association → model_configs.tenant_id 归属；复合模型
  成员 key 与普通 key 同一套归集规则（按聚合键 × 租户展开），不特殊处理；
- 组 × 租户逐条决策：
  * api_base 非空 → **点名渠道**，model_names = 该租户引用 key 行的
    model_name 并集，api_base 原样保留（含恰等于 provider 默认端点的情形，
    一律不归一为空）；
  * api_base 为空 → **provider 级 [] 渠道**（provider 默认端点通用 key；
    当前旧表无此形态行，保留分支仅为 NULL 兼容）；
- 不做绑定面全量推断（不能因某 provider 域只有这把 key 就定 []），显式端点
  key 一律点名：防止渠道被顺延去调不兼容端点/未绑定模型（dashscope rerank
  不可走 compatible-mode/v1——端点能力知识属 M3 运行时范围，M1 迁移不落语义）。
- 跨租户凭据 → 逐租户各复刻一条；孤儿 key（无任何 config 引用）不进表，
  报告列示。
- 写入走 ChannelService.register_*（幂等合并 + DB 唯一约束兜底）→ 断点重跑
  不产生半态；加密 AAD=provider:tenant_id、信封 v{ver}:{iv}:{tag}:{ct}，
  任何输出只出 masked；usage_count/last_used_at/priority(String) 不迁移
  （spec §7 D14）；is_active 恒 True（旧表无软停用）；source=manual。

用法（默认只读出报告；--execute 先预览后交互确认再写入）：
    python core/api/scripts/migrate_api_keys_to_channels.py
    python core/api/scripts/migrate_api_keys_to_channels.py --execute
连接与主密钥读 env（DB_HOST/DB_USER/DB_PASSWORD/DB_NAME + MODEL_CREDENTIALS_KEY），
自动加载 enterprise 根 .env（其次 core/api/.env）——旧表与 model_channels 同库。
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

@dataclass
class KeyGroup:
    """同凭据聚合组：(明文, provider, 原始 api_base)。"""

    plain: str
    provider: str
    api_base: str | None
    key_rows: list = field(default_factory=list)
    ref_configs: list = field(default_factory=list)

    @property
    def tenants(self) -> set:
        return {c.tenant_id for c in self.ref_configs}


@dataclass
class Target:
    """一条待落库渠道（组 × 租户决策结果）。"""

    kind: str            # "provider" | "named"
    provider: str
    tenant_id: object
    api_base: str | None
    plain: str
    model_names: list = field(default_factory=list)
    remark: str | None = None
    priority: int = 0
    note: str = ""

    @property
    def key_label(self) -> str:
        return f"{_masked(self.plain)} {self.provider} "


def _masked(api_key: str) -> str:
    if len(api_key) < 8:
        return "****"
    return f"{api_key[:3]}****{api_key[-4:]}"


def _analyze(db) -> tuple[list[Target], list]:
    """读旧表 → 组 × 租户决策（Target）列表 + 孤儿 key。全量读入内存（行数 < 200）。"""
    from sqlalchemy import select

    from app.models.models_model import (
        ModelApiKey,
        ModelConfig,
        model_config_api_key_association,
    )

    keys = list(db.execute(select(ModelApiKey)).scalars())
    configs = {c.id: c for c in db.execute(select(ModelConfig)).scalars()}
    assoc = db.execute(
        select(
            model_config_api_key_association.c.model_config_id,
            model_config_api_key_association.c.api_key_id,
        )
    ).all()

    refs: dict = {k.id: [] for k in keys}
    for config_id, key_id in assoc:
        if key_id in refs and config_id in configs:
            refs[key_id].append(configs[config_id])

    groups: dict[tuple, KeyGroup] = {}
    orphans: list = []
    for key in keys:
        key_refs = refs[key.id]
        if not key_refs:
            orphans.append(key)
            continue
        gk = (key.api_key, key.provider, key.api_base or None)
        group = groups.get(gk)
        if group is None:
            group = groups[gk] = KeyGroup(
                plain=key.api_key, provider=key.provider, api_base=key.api_base or None
            )
        group.key_rows.append(key)
        group.ref_configs.extend(key_refs)

    targets: list[Target] = []
    for group in groups.values():
        remark = next((k.description for k in group.key_rows if k.description), None)
        priority = 0
        for k in group.key_rows:
            try:
                priority = max(priority, int(k.priority or 0))
                break
            except ValueError:
                continue
        for tenant_id in sorted(group.tenants, key=str):
            names = sorted(
                {k.model_name for k in group.key_rows if tenant_id in {c.tenant_id for c in refs[k.id]}}
            )
            if group.api_base is None:
                targets.append(
                    Target(
                        kind="provider", provider=group.provider, tenant_id=tenant_id,
                        api_base=None, plain=group.plain, remark=remark, priority=priority,
                        note="空api_base → provider级[]（当前旧表无此形态）",
                    )
                )
                continue
            targets.append(
                Target(
                    kind="named", provider=group.provider, tenant_id=tenant_id,
                    api_base=group.api_base, plain=group.plain, model_names=names,
                    remark=remark, priority=priority,
                    note="显式api_base → 点名（复合成员key同规则归集）",
                )
            )
    return targets, orphans


def _render_plan(targets: list[Target], orphans: list) -> str:
    n_provider = sum(1 for t in targets if t.kind == "provider")
    lines = []
    for t in sorted(targets, key=lambda t: (t.provider, str(t.tenant_id))):
        if t.kind == "provider":
            shape = "[] provider级"
        else:
            shape = f"点名({len(t.model_names)}个): " + ", ".join(t.model_names[:3]) + (
                "..." if len(t.model_names) > 3 else ""
            )
        lines.append(
            f"  {_masked(t.plain):<20} {t.provider:<10} tenant={t.tenant_id}"
            f" base={str(t.api_base)[:42] or '(空)'}  -> {shape}  [{t.note}]"
        )
    head = [
        f"待落渠道：{len(targets)}（provider级={n_provider}，点名={len(targets) - n_provider}；"
        f"跨租户复刻已按租户展开）",
        f"孤儿 key（无 config 引用，不进表）：{len(orphans)}"
        + ("  " + ", ".join(str(o.id)[:8] for o in orphans) if orphans else ""),
        "明细：",
    ]
    return "\n".join(head + lines)


def _run_execute(db, targets: list[Target]) -> dict:
    """加密落渠道（register_* 幂等合并）；返回 created/merged + 解密抽样校验。"""
    from app.services.channel_service import ChannelService

    svc = ChannelService(db)
    created = merged = 0
    channels = []  # (channel, plain)
    for t in targets:
        if t.kind == "provider":
            channel, status = svc.register_provider_channel(
                provider=t.provider, tenant_id=t.tenant_id, api_key=t.plain,
                api_base=None, remark=t.remark, priority=t.priority,
            )
            if status == "created":
                created += 1
            else:
                merged += 1
            channels.append((channel, t.plain))
        else:
            for name in t.model_names:
                channel, status = svc.register_for_model(
                    provider=t.provider, tenant_id=t.tenant_id, model_name=name,
                    api_key=t.plain, api_base=t.api_base, remark=t.remark,
                    priority=t.priority,
                )
                if status == "created":
                    created += 1
                else:
                    merged += 1
                channels.append((channel, t.plain))
    db.commit()
    return {"created": created, "merged": merged, **_verify(db, channels)}


def _verify(db, channels) -> dict:
    """解密抽样比对：所有写入渠道逐条 reveal 对照源明文；fail=0 才算迁移正确。"""
    from sqlalchemy import func, select

    from app.models.models_model import ModelChannel
    from app.services.channel_service import ChannelService

    svc = ChannelService(db)
    by_id: dict = {}
    for channel, plain in channels:
        by_id[channel.id] = (channel, plain)
    ok = fail = 0
    for channel, plain in by_id.values():
        try:
            if svc.reveal(channel) == plain:
                ok += 1
            else:
                fail += 1
        except Exception:
            fail += 1
    total = db.execute(select(func.count()).select_from(ModelChannel)).scalar()
    return {"channel_total": total, "decrypt_ok": ok, "decrypt_fail": fail}


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true", help="预览确认后执行写入")
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
        targets, orphans = _analyze(db)
        print(_render_plan(targets, orphans))
        if not args.execute:
            return 0
        if not targets:
            return 0
        confirm = input("写入上述渠道到 model_channels？（y/N）")
        if confirm.strip().lower() != "y":
            print("已取消")
            return 0
        summary = _run_execute(db, targets)
        print("执行完成：", summary)
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
