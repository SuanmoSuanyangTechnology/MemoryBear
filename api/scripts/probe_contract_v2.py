"""契约 v2 出口验收探针（2A）：真库只读核对，逐项打印 + 断言退出码。

只读保障：连接串以 `options=-c default_transaction_read_only=on` 建立会话，
任何写操作会被 PG 直接拒绝（ReadOnlySqlTransaction），脚本自身无 DDL/DML。

检查项（对应本批次验收清单）：
  ① model_configs / model_bases 的 type 分布：无 chat（大小写/空白归一）、无未知类型
  ② 三新列（input/output_modalities、features）填充率（迁移 0e3070fe86df 全量回填后，
     input/output 不应有空行）；features 空行按类型列示
  ③ 迁移保真 + 停写核对：逐行比对三新列与 `legacy_capability_columns(旧列)` 换算结果
     （全等 = 迁移回填或未再改写；差异 = 停写后经新口径改写/新建，列示例）；派生视图必须可算
  ④ 存量 DSL/工作流 JSON 中 `model_ref.type == 'chat'` 计数（读侧容忍项）+ 用真实 ref
     跑导入解析冒烟（`_resolve_model`，含 name 分支），验证 chat→llm 族归一命中
  ⑤ 渠道可用性（运行时同源 `candidate_channels_batch_sync`）：公共 speedbear 按调用租户
     池评估、自有配置零候选按原因归类（无渠道/点名不覆盖/组合成员并集空）
  ⑥ 组合模型：声明成员可解析（同租户锚点 / 按声明合成）、类型与组合同族、is_composite 口径

用法（连接读 env：优先 MemoryBear-Enterprise 根 .env，其次 core/api/.env）：
    cd core/api && env -u VIRTUAL_ENV PYTHONPATH=. .venv/bin/python scripts/probe_contract_v2.py
退出码：0 = 无 FAIL；1 = 存在 FAIL；2 = env 缺失。WARN 不影响退出码。
"""
from __future__ import annotations

import os
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # core/api

_ROOT_ENV = Path(__file__).resolve().parents[3] / ".env"   # MemoryBear-Enterprise 根
_API_ENV = Path(__file__).resolve().parents[1] / ".env"    # core/api

_WARN = "[WARN]"
_OK = "[OK]"
_FAIL = "[FAIL]"

_globals = {"fail": 0, "warn": 0}


def _report(level: str, message: str) -> None:
    if level is _FAIL:
        _globals["fail"] += 1
    elif level is _WARN:
        _globals["warn"] += 1
    print(f"  {level} {message}")


def _load_env() -> None:
    try:
        import dotenv
    except ImportError:
        return
    # override=True：显式对齐企业根 .env（用户指定 enterprise 库），避免宿主 shell 残留变量干扰
    for path in (_ROOT_ENV, _API_ENV):
        if path.is_file():
            dotenv.load_dotenv(path, override=True)


def _engine():
    from sqlalchemy import create_engine
    from sqlalchemy.engine import URL

    url = URL.create(
        "postgresql+psycopg2",
        username=os.environ["DB_USER"],
        password=os.environ["DB_PASSWORD"],
        host=os.environ.get("DB_HOST", "127.0.0.1"),
        port=int(os.environ.get("DB_PORT", "5432")),
        database=os.environ["DB_NAME"],
    )
    return create_engine(
        url,
        connect_args={"options": "-c default_transaction_read_only=on"},
        pool_pre_ping=True,
    )


def _table_of(url) -> str:
    return f"{url.host}:{url.port}/{url.database}"


# ---------------- ① / ② 原始 SQL ----------------


def _type_distribution(db, table: str) -> list[tuple[str, int]]:
    from sqlalchemy import text

    rows = db.execute(
        text(f"SELECT type, count(*) FROM {table} GROUP BY 1 ORDER BY 2 DESC")
    ).all()
    return [(row[0], int(row[1])) for row in rows]


def _column_fill(db, table: str) -> tuple[int, list[tuple]]:
    from sqlalchemy import text

    rows = db.execute(
        text(
            f"""
            SELECT type,
                   count(*) AS total,
                   count(*) FILTER (WHERE coalesce(array_length(input_modalities, 1), 0) = 0) AS input_empty,
                   count(*) FILTER (WHERE coalesce(array_length(output_modalities, 1), 0) = 0) AS output_empty,
                   count(*) FILTER (WHERE coalesce(array_length(features, 1), 0) = 0) AS features_empty
            FROM {table}
            GROUP BY 1
            ORDER BY 2 DESC
            """
        )
    ).all()
    total = sum(int(row[1]) for row in rows)
    return total, [(row[0], int(row[1]), int(row[2]), int(row[3]), int(row[4])) for row in rows]


def _check_schema(db, table: str) -> None:
    from sqlalchemy import text

    rows = db.execute(
        text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = :t AND column_name IN "
            "('input_modalities', 'output_modalities', 'features')"
        ),
        {"t": table},
    ).all()
    found = {row[0] for row in rows}
    missing = {"input_modalities", "output_modalities", "features"} - found
    if missing:
        _report(_FAIL, f"{table} 缺三新列: {sorted(missing)}")
    else:
        print(f"  {_OK} {table} 三新列齐备")


# ---------------- ③ 迁移保真 + 停写核对 ----------------


def _legacy_derived(row) -> tuple[list[str], list[str], list[str]] | None:
    """旧列 → 三新列（包内换算，与迁移 0e3070fe86df 的 SQL 同源口径）。"""
    from redbear_model import legacy_capability_columns

    try:
        input_modalities, output_modalities, features = legacy_capability_columns(
            type=getattr(row, "type", None),
            provider=getattr(row, "provider", None),
            capabilities=list(getattr(row, "capability", None) or ()),
            is_omni=bool(getattr(row, "is_omni", False)),
        )
    except ValueError:
        return None
    return (
        [str(item.value) for item in input_modalities],
        [str(item.value) for item in output_modalities],
        [str(item.value) for item in features],
    )


def _stored_columns(row) -> tuple[list[str], list[str], list[str]]:
    return (
        list(getattr(row, "input_modalities", None) or ()),
        list(getattr(row, "output_modalities", None) or ()),
        list(getattr(row, "features", None) or ()),
    )


def _row_label(row) -> str:
    return (
        f"{getattr(row, 'name', '?')}（{getattr(row, 'id', '?')}）"
        f" type={getattr(row, 'type', '?')} provider={getattr(row, 'provider', '?')}"
    )


def _check_fidelity(rows, label: str) -> None:
    from app.services.model_profile_view import legacy_view

    matched = 0
    legacy_empty = 0
    examples: list[str] = []
    derived_invalid: list[str] = []
    for row in rows:
        derived = _legacy_derived(row)
        if derived is None:
            derived_invalid.append(_row_label(row))
            continue
        if not derived[0]:
            legacy_empty += 1
        if derived == _stored_columns(row):
            matched += 1
        elif len(examples) < 10:
            examples.append(
                f"{_row_label(row)}\n"
                f"        存三新列 = {_stored_columns(row)}\n"
                f"        旧列换算 = {derived}"
            )
        try:
            legacy_view(row)
        except Exception as exc:  # noqa: BLE001 - 探针需列出全部失败行而非中断
            derived_invalid.append(f"{_row_label(row)} → {exc!r}")

    total = len(rows)
    print(f"  {label} 行数 {total}：与旧列换算全等 {matched}，"
          f"差异 {total - matched - len(derived_invalid)}")
    print(f"  其中 capability 为空（旧列从未启用）: {legacy_empty}")
    if examples:
        print("  差异示例（停写后经新口径改写/新建，或旧列本身未迁移）:")
        for item in examples:
            print(f"      {item}")
    if derived_invalid:
        _report(_FAIL, f"派生视图失败 {len(derived_invalid)} 行: {derived_invalid[:5]}")
    else:
        print(f"  {_OK} legacy_view/派生视图全行可算")
    unknown_legacy = sorted({
        str(value)
        for row in rows
        for value in (getattr(row, "capability", None) or ())
        if not _is_known_capability(value)
    })
    if unknown_legacy:
        _report(_WARN, f"旧列 capability 存在未知值（派生时被跳过）: {unknown_legacy}")


def _is_known_capability(value) -> bool:
    from app.models.models_model import ModelCapability

    try:
        ModelCapability(value)
        return True
    except ValueError:
        return False


# ---------------- ④ DSL / 工作流 JSON 中的 chat ref ----------------


def _iter_refs(payload):
    """递归收集 model_ref 形状 dict（有 type 且带 id/name/provider 之一）。"""
    if isinstance(payload, dict):
        if isinstance(payload.get("type"), str) and (
            "id" in payload or "name" in payload or "provider" in payload
        ):
            yield payload
        for value in payload.values():
            yield from _iter_refs(value)
    elif isinstance(payload, list):
        for item in payload:
            yield from _iter_refs(item)


def _ref_label(ref: dict) -> str:
    """引用摘要（**脱敏**：节点 JSON 里可能内联 api_keys 明文，只取标识键）。"""
    keys = ("id", "name", "provider", "type")
    return "{" + ", ".join(f"{key}={ref.get(key)!r}" for key in keys if ref.get(key) is not None) + "}"


def _scan_chat_refs(db) -> list[str]:
    from app.models.app_model import App
    from app.models.app_release_model import AppRelease
    from app.models.workflow_model import WorkflowConfig

    hits: list[str] = []
    drafts = (
        db.query(WorkflowConfig.app_id, WorkflowConfig.nodes, App.name)
        .join(App, App.id == WorkflowConfig.app_id)
        .all()
    )
    for app_id, nodes, app_name in drafts:
        for ref in _iter_refs(nodes):
            if str(ref.get("type", "")).strip().lower() == "chat":
                hits.append(f"[draft] {app_name}/{app_id} ref={_ref_label(ref)}")
    releases = (
        db.query(AppRelease.app_id, AppRelease.version_name, AppRelease.config, App.name)
        .join(App, App.id == AppRelease.app_id)
        .all()
    )
    for app_id, version_name, config, app_name in releases:
        for ref in _iter_refs(config):
            if str(ref.get("type", "")).strip().lower() == "chat":
                hits.append(f"[release] {app_name}@{version_name} ref={_ref_label(ref)}")
    return hits


def _check_import_tolerance(db, configs) -> None:
    """真实旧 ref 走导入解析（只读，真实租户上下文）：type='chat' 应归一命中 llm 族。"""
    from app.models.app_model import App
    from app.models.models_model import LLM_FAMILY_TYPES
    from app.models.workspace_model import Workspace
    from app.models.workflow_model import WorkflowConfig
    from app.services.app_dsl_service import AppDslService

    drafts = (
        db.query(WorkflowConfig.nodes, App.name, App.workspace_id)
        .join(App, App.id == WorkflowConfig.app_id)
        .all()
    )
    refs: list[tuple[str, object, dict]] = []
    for nodes, app_name, workspace_id in drafts:
        hits = [
            ref for ref in _iter_refs(nodes)
            if str(ref.get("type", "")).strip().lower() == "chat"
        ]
        if not hits:
            continue
        tenant_id = (
            db.query(Workspace.tenant_id).filter(Workspace.id == workspace_id).scalar()
        )
        refs.extend((app_name, tenant_id, ref) for ref in hits)

    if not refs:
        print("  无 type='chat' ref，跳过")
        return

    service = AppDslService(db)
    missed: list[str] = []
    for app_name, tenant_id, ref in refs:
        slim = {key: ref.get(key) for key in ("id", "name", "provider", "type")}
        warnings: list = []
        resolved = service._resolve_model(slim, tenant_id, warnings, LLM_FAMILY_TYPES)
        status = "命中" if resolved else "未命中"
        print(f"      {status} {app_name} {_ref_label(slim)} → {resolved} warnings={warnings}")
        if not resolved:
            missed.append(f"{app_name} {_ref_label(slim)}")
    if missed:
        _report(_WARN, f"旧 ref 解析未命中 {len(missed)} 处: {missed}")
    else:
        print(f"  {_OK} {len(refs)} 处旧 chat ref 经导入解析全部命中（读侧容忍生效）")

    # name 分支容忍：真实活跃 llm 行构造 {name, provider, type='chat'}（无 id），验证族过滤命中
    samples = [
        row for row in configs
        if str(row.type) == "llm" and row.is_active and row.provider != "composite"
    ][:3]
    name_missed: list[str] = []
    for row in samples:
        warnings: list = []
        resolved = service._resolve_model(
            {"name": row.name, "provider": row.provider, "type": "chat"},
            row.tenant_id,
            warnings,
            LLM_FAMILY_TYPES,
        )
        print(f"      name 分支 {row.name}（{row.provider}）→ {resolved} warnings={warnings}")
        if not resolved or warnings:
            name_missed.append(f"{row.name}（{row.provider}）")
    if name_missed:
        _report(_WARN, f"name 分支 chat→llm 归一未命中: {name_missed}")
    elif samples:
        print(f"  {_OK} name 分支 {len(samples)} 例 type='chat' 归 llm 族命中")


# ---------------- ⑤ 渠道可用性 ----------------


def _channel_pool_overview(db) -> None:
    from sqlalchemy import text

    rows = db.execute(
        text(
            "SELECT tenant_id, provider, source, "
            "count(*) FILTER (WHERE is_active) AS active, count(*) AS total "
            "FROM model_channels GROUP BY 1, 2, 3 ORDER BY 1, 2, 3"
        )
    ).all()
    print(f"  model_channels 池（{len(rows)} 组）:")
    for tenant_id, provider, source, active, total in rows:
        print(f"      tenant={tenant_id} provider={provider} source={source} 活跃 {active}/{total}")


def _pool_index(db) -> dict:
    """租户 → 活跃渠道池（provider/model_names/source），供零候选原因归类。"""
    from app.models.models_model import ModelChannel

    index: dict = defaultdict(list)
    rows = (
        db.query(
            ModelChannel.tenant_id,
            ModelChannel.provider,
            ModelChannel.model_names,
            ModelChannel.source,
        )
        .filter(ModelChannel.is_active.is_(True))
        .all()
    )
    for tenant_id, provider, model_names, source in rows:
        index[tenant_id].append(
            {"provider": provider, "model_names": list(model_names or []), "source": source}
        )
    return index


def _zero_reason(row, pool: list) -> str:
    if row.provider == "composite":
        return "组合：成员渠道并集为空（成员弃用或无渠道）"
    provider_pool = [ch for ch in pool if ch["provider"] == row.provider]
    if not provider_pool:
        return f"无 {row.provider} 活跃渠道"
    return f"{row.provider} 渠道点名不覆盖（{len(provider_pool)} 条渠道均未列出本模型名）"


def _check_availability(db, rows) -> None:
    from app.services.channel_registry import candidate_channels_batch_sync

    pool_index = _pool_index(db)
    is_public_speedbear = lambda row: row.provider == "speedbear" and bool(row.is_public)  # noqa: E731
    public_rows = [row for row in rows if is_public_speedbear(row)]
    own_rows = [row for row in rows if not is_public_speedbear(row)]

    # 公共 speedbear：解析池取调用租户（配置行挂系统租户，系统租户无渠道），按持平台渠道租户逐个评估
    if public_rows:
        platform_tenants = sorted({
            tenant_id
            for tenant_id, pool in pool_index.items()
            if any(ch["provider"] == "speedbear" and ch["source"] == "platform" for ch in pool)
        })
        resolved_tenants = 0
        for tenant_id in platform_tenants:
            result = candidate_channels_batch_sync(db, public_rows, tenant_id)
            missing = [row.name for row in public_rows if not result.get(row.id)]
            if missing:
                _report(_WARN, f"租户 {tenant_id} 下公共 speedbear 未解析: {missing}")
            else:
                resolved_tenants += 1
        print(
            f"  公共 speedbear 配置 {len(public_rows)} 个 × 持平台渠道租户 {len(platform_tenants)} 个："
            f"全租户可解析 {resolved_tenants}/{len(platform_tenants)}"
        )

    by_tenant: dict = defaultdict(list)
    for row in own_rows:
        by_tenant[row.tenant_id].append(row)

    zero: list = []
    for tenant_id, tenant_rows in by_tenant.items():
        try:
            result = candidate_channels_batch_sync(db, tenant_rows, tenant_id)
        except Exception as exc:  # noqa: BLE001 - 逐租户报告，不中断整探针
            _report(_WARN, f"租户 {tenant_id} 候选探测失败: {exc!r}")
            continue
        for row in tenant_rows:
            if not result.get(row.id):
                zero.append(row)

    active_zero = [row for row in zero if row.is_active]
    by_type = Counter(str(row.type) for row in zero)
    print(f"  自有配置 {len(own_rows)}，零候选 {len(zero)}（其中启用 {len(active_zero)}）: {dict(by_type)}")
    reasons = Counter(
        _zero_reason(row, pool_index.get(row.tenant_id, [])) for row in active_zero
    )
    for reason, count in reasons.most_common():
        print(f"      {count} 处：{reason}")
    for row in active_zero[:15]:
        print(
            f"      {_row_label(row)} is_public={row.is_public} "
            f"composite={row.provider == 'composite'} tenant={row.tenant_id}"
        )
    if active_zero:
        _report(_WARN, f"{len(active_zero)} 个启用配置无渠道候选（多为渠道覆盖集/成员弃用数据面，需人工确认）")
    else:
        print(f"  {_OK} 所有启用配置均有渠道候选")


# ---------------- ⑥ 组合成员 ----------------


def _check_composite(db, rows) -> None:
    from sqlalchemy import select, tuple_

    from app.models.models_model import ModelConfig, ModelProvider
    from app.services.channel_registry import parse_members

    composites = [row for row in rows if row.provider == "composite"]
    flag_mismatch = [
        row for row in rows
        if (row.provider == "composite") != bool(row.is_composite)
    ]
    if flag_mismatch:
        _report(_WARN, f"provider/is_composite 口径不一致 {len(flag_mismatch)} 行: "
                       f"{[_row_label(row) for row in flag_mismatch[:5]]}")

    pairs = {pair for row in composites for pair in parse_members(row.config)}
    anchors: dict[tuple, object] = {}
    if pairs:
        member_rows = db.execute(
            select(ModelConfig).where(
                ModelConfig.provider != ModelProvider.COMPOSITE,
                tuple_(ModelConfig.provider, ModelConfig.name).in_(sorted(pairs)),
            )
        ).scalars().all()
        for row in member_rows:
            anchors.setdefault((row.tenant_id, row.provider, row.name), row)

    llm_family = {"llm", "chat"}
    incompatible: list[str] = []
    unanchored: list[str] = []
    bad_provider: list[str] = []
    total_members = 0
    for row in composites:
        declared = parse_members(row.config)
        if not declared:
            _report(_WARN, f"组合无成员声明: {_row_label(row)}")
            continue
        for provider, model_name in declared:
            total_members += 1
            try:
                parsed_provider = ModelProvider(provider)
            except ValueError:
                bad_provider.append(f"{_row_label(row)} → 成员 provider={provider!r}")
                continue
            if parsed_provider is ModelProvider.COMPOSITE:
                bad_provider.append(f"{_row_label(row)} → 嵌套组合成员 {provider}/{model_name}")
                continue
            anchor = anchors.get((row.tenant_id, provider, model_name))
            if anchor is None:
                unanchored.append(f"{_row_label(row)} → {provider}/{model_name}")
                continue
            member_type = str(anchor.type)
            composite_type = str(row.type)
            if not (
                member_type == composite_type
                or (member_type in llm_family and composite_type in llm_family)
            ):
                incompatible.append(
                    f"{_row_label(row)} → 成员 {provider}/{model_name} type={member_type}"
                )

    print(f"  组合 config {len(composites)} 个，声明成员 {total_members} 处")
    print(f"  同租户锚点 {total_members - len(unanchored) - len(bad_provider)}，"
          f"无锚点（按声明合成）{len(unanchored)}")
    for item in unanchored[:10]:
        print(f"      无锚点: {item}")
    if bad_provider:
        _report(_FAIL, f"成员 provider 非法/嵌套组合 {len(bad_provider)}: {bad_provider[:5]}")
    if incompatible:
        _report(_FAIL, f"成员类型与组合不符 {len(incompatible)}: {incompatible[:5]}")
    if not bad_provider and not incompatible:
        print(f"  {_OK} 成员 provider 合法、类型同族")


# ---------------- 主流程 ----------------


def main() -> int:
    _load_env()
    for var in ("DB_HOST", "DB_USER", "DB_PASSWORD", "DB_NAME"):
        if not os.getenv(var):
            print(f"{_FAIL} 缺少 env {var}；请从 MemoryBear-Enterprise 根或 core/api 运行")
            return 2

    engine = _engine()
    with engine.connect() as url_check:
        print(f"目标库: {_table_of(url_check.engine.url)}（只读会话）")

    from sqlalchemy.orm import Session

    from app.models.models_model import ModelBase, ModelConfig, ModelType

    valid_types = {item.value for item in ModelType}
    with Session(engine) as db:
        try:
            print("\n[0] schema 前置")
            for table in ("model_configs", "model_bases"):
                _check_schema(db, table)

            print("\n[1] type 分布")
            for table in ("model_configs", "model_bases"):
                dist = _type_distribution(db, table)
                print(f"  {table}: " + ", ".join(f"{k}={v}" for k, v in dist))
                chat_rows = sum(v for k, v in dist if k.strip().lower() == "chat")
                if chat_rows:
                    _report(_FAIL, f"{table} 存在 {chat_rows} 行 type='chat'（迁移未归一）")
                else:
                    print(f"  {_OK} {table} 无 chat 行")
                unknown = {k for k, _ in dist if k.strip().lower() not in valid_types}
                if unknown:
                    _report(_WARN, f"{table} 未知类型（读侧会抛错）: {sorted(unknown)}")

            print("\n[2] 三新列填充率")
            for table in ("model_configs", "model_bases"):
                total, stats = _column_fill(db, table)
                print(f"  {table}（{total} 行）")
                for name, count, input_empty, output_empty, features_empty in stats:
                    print(
                        f"      {name}: {count} 行，input 空 {input_empty}，"
                        f"output 空 {output_empty}，features 空 {features_empty}"
                    )
                bad = sum(input_empty + output_empty for _, _, input_empty, output_empty, _ in stats)
                if bad:
                    _report(_FAIL, f"{table} input/output 空列 {bad} 行（写路径未落三新列）")
                else:
                    print(f"  {_OK} {table} input/output 全表非空")

            print("\n[3] 迁移保真 + 停写核对")
            configs = db.query(ModelConfig).all()
            _check_fidelity(configs, "model_configs")
            bases = db.query(ModelBase).all()
            _check_fidelity(bases, "model_bases")

            print("\n[4] 存量 DSL/工作流 ref 类型（读侧容忍统计 + 导入解析冒烟）")
            hits = _scan_chat_refs(db)
            print(f"  model_ref.type='chat' 命中 {len(hits)} 处")
            for item in hits[:10]:
                print(f"      {item}")
            _check_import_tolerance(db, configs)

            print("\n[5] 渠道可用性（运行时同源探测）")
            _channel_pool_overview(db)
            _check_availability(db, configs)

            print("\n[6] 组合成员口径")
            _check_composite(db, configs)
        finally:
            db.rollback()

    print(f"\n结论: FAIL {_globals['fail']} 项，WARN {_globals['warn']} 项")
    return 1 if _globals["fail"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
