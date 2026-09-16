"""宿主渠道 registry 适配 + 运行期解析入口（M3，spec §10/§11）。

- 包内 SQL registry 基座 + 本库 ORM 注入：RegistrySQLSource 投影（ModelConfig/
  ModelChannel 行 → 包契约快照；时间列 DateTime → unix ms 在边界换算）
- 进程级共享 ChannelSnapshotCache（TTL 60s）：per-request registry 复用同一实例，
  写路径（ChannelService）经 invalidate_channel_cache 主动失效，不依赖 TTL 兜底
- sync/async 双读：管理面/worker/同步链路用 resolve_config_sync；异步链路用
  resolve_config_async（GC#11：async 上下文禁止 sync session）
- 宿主 tenant_id 在入口归一为 UUID（_normalize_tenant_id）：同一租户身份在宿主存在
  str 形态（如 Redis 缓存命中的 workspace→tenant 查询），包内身份比较按 UUID 语义
- 组合 config（provider=composite）走 resolve_composite_sync/async：config JSON `members[]`
  → 成员 config 批量查（本租户/非组合；config 为可选增强，缺失时按声明合成快照；成员快照
  model_config_id 统一归到组合 id——usage 归因口径 = 调用入口 config）
  → 成员渠道匹配与展平编排在包内（resolve_composite_head），本层取首个可解密候选
  及其后候选切片
- 换渠道计划（spec §11.2）：resolve_config_plan_sync/async、resolve_composite_plan_sync/async
  返回 ResolvedWithPlan(resolved, FailoverPlan)——与解析同路径同查询次数；plan 只含快照/
  密文候选（candidates[0] 恒为实际首发渠道），cipher 由消费方注入
- 密文解密收敛在 resolve_from_channel_pool / resolve_composite_head（cipher 注入）；本模块不落明文
- 候选探测（管理面脱敏展示/渠道可用性/启用预检共用）：candidate_channels_sync/async
  （单 config）与 candidate_channels_batch_sync（列表页，两次查询上限）；只匹配不解密，
  组合可用性 = 成员候选并集非空
- least-used（D14/§11.1）：同精确度同 priority 内按 usage_load 派生的窗口用量升序选路
  （渠道行不落计数器）；开关/窗口/降级语义在 app.services.usage_load，关闭或聚合失败时
  退化为 created_at asc

错误契约：config 行不存在返回 None；存在但不可解析（未启用/无候选/解密失败等）
抛 RedBearModelError 子类，由调用方按解析开关决定兜底或降级。
"""
from __future__ import annotations

import logging
import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from redbear_model import (
    AsyncSQLChannelRegistry,
    ChannelSnapshot,
    ChannelSnapshotCache,
    CompositeMemberConfig,
    FailoverCandidate,
    FailoverPlan,
    LoadBalanceStrategy,
    ModelCapability,
    ModelConfigSnapshot,
    ModelProvider,
    ModelType,
    NoAvailableChannelError,
    RegistrySQLSource,
    ResolvedModelConfig,
    SyncSQLChannelRegistry,
    match_channel_candidates,
    order_channel_candidates,
    ordered_channel_candidates,
    resolve_and_chain_from_pool,
    resolve_composite_head,
)
from sqlalchemy import select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session, joinedload

from app.models.models_model import ModelBase, ModelChannel, ModelConfig
from app.services.channel_service import cipher_from_env
from app.services.usage_load import channel_loads_async, channel_loads_sync

_CHANNEL_CACHE_TTL_MS = 60_000
_shared_cache = ChannelSnapshotCache(ttl_ms=_CHANNEL_CACHE_TTL_MS)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ResolvedWithPlan:
    """运行期解析结果 + 换渠道计划（plan.candidates[0] 恒为本次实际首发渠道）。"""

    resolved: ResolvedModelConfig
    plan: FailoverPlan


def _normalize_tenant_id(tenant_id: uuid.UUID | str | None) -> uuid.UUID | None:
    """宿主租户身份过包边界归一为 UUID。

    宿主多处（如 Redis 缓存的 workspace→tenant 查询）会把同一身份以 str 传递，
    而包内身份比较是 UUID 直接比较：str 会被误判为跨租户（ModelAccessDeniedError）
    或令组合成员全部不可见。归一后语义不变，非法输入按编程错误快速失败。
    """
    if tenant_id is None or isinstance(tenant_id, uuid.UUID):
        return tenant_id
    return uuid.UUID(str(tenant_id))


def _capabilities(values: list[str] | None) -> tuple[ModelCapability, ...]:
    result = []
    for value in values or []:
        try:
            result.append(ModelCapability(value))
        except ValueError:
            continue
    return tuple(result)


def _created_ms(value) -> int:
    return int(value.timestamp() * 1000) if value is not None else 0


def _config_snapshot(row: ModelConfig) -> ModelConfigSnapshot:
    return ModelConfigSnapshot(
        model_config_id=row.id,
        tenant_id=row.tenant_id,
        provider=ModelProvider(row.provider),
        model_type=ModelType(row.type),
        name=row.name,
        is_active=row.is_active,
        is_public=row.is_public,
        load_balance_strategy=LoadBalanceStrategy(
            row.load_balance_strategy or LoadBalanceStrategy.NONE
        ),
        capabilities=_capabilities(row.capability),
        is_omni=row.is_omni,
        config=dict(row.config or {}),
    )


def _channel_snapshot(row: ModelChannel) -> ChannelSnapshot:
    return ChannelSnapshot(
        id=row.id,
        tenant_id=row.tenant_id,
        provider=row.provider,
        model_names=tuple(row.model_names or []),
        api_base=row.api_base,
        credential_encrypted=row.credential_encrypted,
        credential_sha256=row.credential_sha256,
        credential_masked=row.credential_masked,
        is_active=row.is_active,
        priority=row.priority,
        cooldown_until_ms=row.cooldown_until_ms,
        source=row.source,
        extra=dict(row.extra or {}),
        created_at_ms=_created_ms(row.created_at),
        updated_at_ms=_created_ms(row.updated_at),
    )


SOURCE = RegistrySQLSource(
    config_mapper=ModelConfig,
    channel_mapper=ModelChannel,
    config_snapshot=_config_snapshot,
    channel_snapshot=_channel_snapshot,
)


def _resolve_plan_sync(
    registry: SyncSQLChannelRegistry,
    config_id: uuid.UUID,
    tenant_id: uuid.UUID | None,
    config_row: ModelConfig | None = None,
) -> ResolvedWithPlan | None:
    # config_row：调用方已加载 ORM 行时直接投影，省一次 SELECT（两者语义一致）
    config = SOURCE.config_snapshot(config_row) if config_row is not None else registry.get_config(config_id)
    if config is None:
        return None
    effective_tenant = _normalize_tenant_id(tenant_id) or config.tenant_id
    channels = registry.get_active_channels(effective_tenant, provider=config.provider)
    loads = channel_loads_sync(registry.db, effective_tenant)
    resolved, chain = resolve_and_chain_from_pool(
        config,
        channels,
        tenant_id=effective_tenant,
        cipher=cipher_from_env(),
        loads=loads,
    )
    plan = FailoverPlan(
        entry=config,
        tenant_id=effective_tenant,
        candidates=tuple(
            FailoverCandidate(config=config, channel=channel) for channel in chain
        ),
    )
    return ResolvedWithPlan(resolved=resolved, plan=plan)


async def _resolve_plan_async(
    registry: AsyncSQLChannelRegistry,
    config_id: uuid.UUID,
    tenant_id: uuid.UUID | None,
    config_row: ModelConfig | None = None,
) -> ResolvedWithPlan | None:
    """异步镜像（GC#11）：AsyncSQLChannelRegistry 的两个读方法均为协程，必须 await。"""
    config = (
        SOURCE.config_snapshot(config_row)
        if config_row is not None
        else await registry.get_config(config_id)
    )
    if config is None:
        return None
    effective_tenant = _normalize_tenant_id(tenant_id) or config.tenant_id
    channels = await registry.get_active_channels(effective_tenant, provider=config.provider)
    loads = await channel_loads_async(registry.db, effective_tenant)
    resolved, chain = resolve_and_chain_from_pool(
        config,
        channels,
        tenant_id=effective_tenant,
        cipher=cipher_from_env(),
        loads=loads,
    )
    plan = FailoverPlan(
        entry=config,
        tenant_id=effective_tenant,
        candidates=tuple(
            FailoverCandidate(config=config, channel=channel) for channel in chain
        ),
    )
    return ResolvedWithPlan(resolved=resolved, plan=plan)


def resolve_config_sync(
    db: Session,
    config_id: uuid.UUID,
    tenant_id: uuid.UUID | None = None,
    config_row: ModelConfig | None = None,
) -> ResolvedModelConfig | None:
    """同步解析（管理面/worker/同步链路）：快照池命中即不查库。

    config_row 非空时直接投影该行（调用方已持有 ORM 实例，省一次 SELECT）。
    """
    outcome = _resolve_plan_sync(
        SyncSQLChannelRegistry(db, SOURCE, cache=_shared_cache),
        config_id,
        tenant_id,
        config_row,
    )
    return None if outcome is None else outcome.resolved


async def resolve_config_async(
    db: AsyncSession,
    config_id: uuid.UUID,
    tenant_id: uuid.UUID | None = None,
    config_row: ModelConfig | None = None,
) -> ResolvedModelConfig | None:
    """异步解析（运行期 async 链路）；config_row 语义同 sync 版。"""
    outcome = await _resolve_plan_async(
        AsyncSQLChannelRegistry(db, SOURCE, cache=_shared_cache),
        config_id,
        tenant_id,
        config_row,
    )
    return None if outcome is None else outcome.resolved


def resolve_config_plan_sync(
    db: Session,
    config_id: uuid.UUID,
    tenant_id: uuid.UUID | None = None,
    config_row: ModelConfig | None = None,
) -> ResolvedWithPlan | None:
    """同步解析 + 换渠道计划（spec §11.2）；与 resolve_config_sync 同路径同查询次数。"""
    return _resolve_plan_sync(
        SyncSQLChannelRegistry(db, SOURCE, cache=_shared_cache),
        config_id,
        tenant_id,
        config_row,
    )


async def resolve_config_plan_async(
    db: AsyncSession,
    config_id: uuid.UUID,
    tenant_id: uuid.UUID | None = None,
    config_row: ModelConfig | None = None,
) -> ResolvedWithPlan | None:
    """异步态解析 + 换渠道计划（GC#11：async 链路禁止 sync session）。"""
    return await _resolve_plan_async(
        AsyncSQLChannelRegistry(db, SOURCE, cache=_shared_cache),
        config_id,
        tenant_id,
        config_row,
    )


def invalidate_channel_cache(
    tenant_id: uuid.UUID | None = None,
    provider: str | None = None,
) -> None:
    """写路径主动失效渠道快照缓存（同租户全量键一并失效，见包内缓存语义）。"""
    _shared_cache.invalidate(tenant_id, provider)


def parse_members(config: dict | None) -> list[tuple[str, str]]:
    """组合 config JSON `members[]` → [(provider, model_name)]，脏项跳过告警，去重保序。"""
    raw = (config or {}).get("members")
    if not isinstance(raw, list):
        return []
    parsed: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for item in raw:
        if not isinstance(item, dict):
            logger.warning("composite member entry is not an object: %r", item)
            continue
        provider = item.get("provider")
        model_name = item.get("model_name")
        if not isinstance(provider, str) or not provider or not isinstance(model_name, str) or not model_name:
            logger.warning("composite member entry missing provider/model_name: %r", item)
            continue
        pair = (provider, model_name)
        if pair in seen:
            continue
        seen.add(pair)
        parsed.append(pair)
    return parsed


@dataclass(frozen=True)
class _MemberIndex:
    """成员解析索引（D15④）：config 行 + 已弃用 pair 集合，供 `_composite_members` 单点剔除。"""

    rows: dict[tuple[str, str], ModelConfig]
    deprecated_pairs: frozenset[tuple[str, str]]

    def is_deprecated(self, pair: tuple[str, str]) -> bool:
        """有同租户 config 的按该行 base 判（base 缺失 = 自定义模型，无弃用标记）；
        无 config 的合成成员按 (provider, name) 查 model_bases。"""
        row = self.rows.get(pair)
        if row is not None:
            base = getattr(row, "model_base", None)
            return base is not None and base.is_deprecated
        return pair in self.deprecated_pairs


def _deprecated_base_pairs_sync(
    db: Session, pairs: Sequence[tuple[str, str]]
) -> frozenset[tuple[str, str]]:
    """声明 pair 中已弃用/下线的 model_bases（合成成员唯一权威来源；单查询）。"""
    stmt = select(ModelBase.provider, ModelBase.name).where(
        ModelBase.is_deprecated.is_(True),
        tuple_(ModelBase.provider, ModelBase.name).in_(list(pairs)),
    )
    return frozenset((row[0], row[1]) for row in db.execute(stmt).all())


async def _deprecated_base_pairs_async(
    db: AsyncSession, pairs: Sequence[tuple[str, str]]
) -> frozenset[tuple[str, str]]:
    stmt = select(ModelBase.provider, ModelBase.name).where(
        ModelBase.is_deprecated.is_(True),
        tuple_(ModelBase.provider, ModelBase.name).in_(list(pairs)),
    )
    result = await db.execute(stmt)
    return frozenset((row[0], row[1]) for row in result.all())


def _member_configs_sync(
    db: Session,
    tenant_id: uuid.UUID,
    pairs: Sequence[tuple[str, str]],
) -> _MemberIndex:
    """成员解析索引批量查询（两查询：config 行 + 弃用 pair）：本租户 + 非组合。

    不看 `config.is_active`（成员可用性由渠道与凭据活跃决定）；同 (provider, name)
    多行时优先启用、较新者，与写路径校验同口径。弃用成员剔除见 `_composite_members`。
    """
    stmt = (
        select(ModelConfig)
        .options(joinedload(ModelConfig.model_base))
        .where(
            ModelConfig.tenant_id == tenant_id,
            ModelConfig.is_composite.is_(False),
            tuple_(ModelConfig.provider, ModelConfig.name).in_(list(pairs)),
        )
        .order_by(
            ModelConfig.is_active.desc(),
            ModelConfig.created_at.desc().nullslast(),
        )
    )
    index: dict[tuple[str, str], ModelConfig] = {}
    for row in db.execute(stmt).scalars().all():
        index.setdefault((row.provider, row.name), row)
    return _MemberIndex(
        rows=index, deprecated_pairs=_deprecated_base_pairs_sync(db, pairs)
    )


async def _member_configs_async(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    pairs: Sequence[tuple[str, str]],
) -> _MemberIndex:
    stmt = (
        select(ModelConfig)
        .options(joinedload(ModelConfig.model_base))
        .where(
            ModelConfig.tenant_id == tenant_id,
            ModelConfig.is_composite.is_(False),
            tuple_(ModelConfig.provider, ModelConfig.name).in_(list(pairs)),
        )
        .order_by(
            ModelConfig.is_active.desc(),
            ModelConfig.created_at.desc().nullslast(),
        )
    )
    result = await db.execute(stmt)
    index: dict[tuple[str, str], ModelConfig] = {}
    for row in result.scalars().all():
        index.setdefault((row.provider, row.name), row)
    return _MemberIndex(
        rows=index, deprecated_pairs=await _deprecated_base_pairs_async(db, pairs)
    )


def _synthesized_member(
    composite: ModelConfigSnapshot,
    tenant_id: uuid.UUID,
    pair: tuple[str, str],
) -> ModelConfigSnapshot | None:
    """无同租户 config 的成员声明 → 按声明合成快照（config 为可选增强）。

    组合 name 是别名而非真实模型名，成员声明的 (provider, model_name) 即真实调用名
    （回填来源：association 绑定 key 行的 provider/model_name）。合成口径：类型随组合、
    能力/参数留空（不虚报，人工补 config 后自然增强）、usage 归因到组合 id；
    provider 非枚举 / 嵌套组合 → 跳过告警。
    """
    try:
        provider = ModelProvider(pair[0])
    except ValueError:
        logger.warning("composite member provider unknown, skipped: %s/%s", pair[0], pair[1])
        return None
    if provider is ModelProvider.COMPOSITE:
        logger.warning("nested composite member skipped: %s/%s", pair[0], pair[1])
        return None
    return ModelConfigSnapshot(
        model_config_id=composite.model_config_id,
        tenant_id=tenant_id,
        provider=provider,
        model_type=composite.model_type,
        name=pair[1],
        is_active=True,
        is_public=False,
    )


def _composite_members(
    composite: ModelConfigSnapshot,
    tenant_id: uuid.UUID,
    index: _MemberIndex,
    pairs: Sequence[tuple[str, str]],
) -> list[CompositeMemberConfig]:
    """成员声明 → CompositeMemberConfig（声明顺序）：有同租户 config 用其快照，缺失按声明合成。

    弃用成员在解析期单点剔除（D15④，声明保留可逆）：config 行以 `model_base` 为准，
    无 config 的合成成员按 (provider, name) 查 model_bases。成员快照的 model_config_id
    统一归到组合 id（usage 归因口径 = 调用入口 config，spec §13.1
    `config_id` 注释）：真实成员配置与合成成员一致，命中成员身份留在事件的
    provider/model_name/channel_id 供应面快照里（§5.2）。
    """
    members: list[CompositeMemberConfig] = []
    for pair in pairs:
        if index.is_deprecated(pair):
            logger.warning("composite member deprecated, skipped: %s/%s", pair[0], pair[1])
            continue
        row = index.rows.get(pair)
        snapshot = SOURCE.config_snapshot(row) if row is not None else _synthesized_member(
            composite, tenant_id, pair
        )
        if snapshot is None:
            continue
        if snapshot.model_config_id != composite.model_config_id:
            snapshot = snapshot.model_copy(
                update={"model_config_id": composite.model_config_id}
            )
        members.append(CompositeMemberConfig(config=snapshot, model_name=pair[1]))
    return members


def _composite_unresolvable(composite: ModelConfigSnapshot, detail: str) -> NoAvailableChannelError:
    return NoAvailableChannelError(
        composite.model_config_id,
        str(composite.provider),
        composite.name,
        detail,
    )


def resolve_composite_sync(
    db: Session,
    config_row: ModelConfig,
    tenant_id: uuid.UUID | None = None,
) -> ResolvedModelConfig:
    """组合 config 解析（sync）：members → 成员 config 批量查/按声明合成 → 全量渠道池 → 包内编排取首候选。"""
    return resolve_composite_plan_sync(db, config_row, tenant_id).resolved


async def resolve_composite_async(
    db: AsyncSession,
    config_row: ModelConfig,
    tenant_id: uuid.UUID | None = None,
) -> ResolvedModelConfig:
    """组合 config 解析（async，GC#11：异步链路禁止 sync session）。"""
    return (await resolve_composite_plan_async(db, config_row, tenant_id)).resolved


def resolve_composite_plan_sync(
    db: Session,
    config_row: ModelConfig,
    tenant_id: uuid.UUID | None = None,
) -> ResolvedWithPlan:
    """组合解析 + 换渠道计划（sync）：取首个可解密候选，计划链自该位切片。"""
    effective_tenant = _normalize_tenant_id(tenant_id) or config_row.tenant_id
    composite = SOURCE.config_snapshot(config_row)
    pairs = parse_members(config_row.config)
    if not pairs:
        raise _composite_unresolvable(composite, "composite config declares no members")
    members = _composite_members(
        composite, effective_tenant, _member_configs_sync(db, effective_tenant, pairs), pairs
    )
    if not members:
        raise _composite_unresolvable(
            composite, "composite members unresolved (deprecated/nested/invalid provider)"
        )
    channels = SyncSQLChannelRegistry(db, SOURCE, cache=_shared_cache).get_active_channels(
        effective_tenant, provider=None
    )
    resolved, chain = resolve_composite_head(
        composite,
        members,
        channels,
        tenant_id=effective_tenant,
        cipher=cipher_from_env(),
        loads=channel_loads_sync(db, effective_tenant),
    )
    plan = FailoverPlan(
        entry=composite,
        tenant_id=effective_tenant,
        candidates=tuple(
            FailoverCandidate(
                config=candidate.member,
                channel=candidate.channel,
                model_name=candidate.model_name,
            )
            for candidate in chain
        ),
    )
    return ResolvedWithPlan(resolved=resolved, plan=plan)


async def resolve_composite_plan_async(
    db: AsyncSession,
    config_row: ModelConfig,
    tenant_id: uuid.UUID | None = None,
) -> ResolvedWithPlan:
    """组合解析 + 换渠道计划（async，GC#11：异步链路禁止 sync session）。"""
    effective_tenant = _normalize_tenant_id(tenant_id) or config_row.tenant_id
    composite = SOURCE.config_snapshot(config_row)
    pairs = parse_members(config_row.config)
    if not pairs:
        raise _composite_unresolvable(composite, "composite config declares no members")
    members = _composite_members(
        composite, effective_tenant, await _member_configs_async(db, effective_tenant, pairs), pairs
    )
    if not members:
        raise _composite_unresolvable(
            composite, "composite members unresolved (deprecated/nested/invalid provider)"
        )
    channels = (
        await AsyncSQLChannelRegistry(db, SOURCE, cache=_shared_cache).get_active_channels(
            effective_tenant, provider=None
        )
    )
    resolved, chain = resolve_composite_head(
        composite,
        members,
        channels,
        tenant_id=effective_tenant,
        cipher=cipher_from_env(),
        loads=await channel_loads_async(db, effective_tenant),
    )
    plan = FailoverPlan(
        entry=composite,
        tenant_id=effective_tenant,
        candidates=tuple(
            FailoverCandidate(
                config=candidate.member,
                channel=candidate.channel,
                model_name=candidate.model_name,
            )
            for candidate in chain
        ),
    )
    return ResolvedWithPlan(resolved=resolved, plan=plan)


def _single_candidate_chain(
    config: ModelConfigSnapshot,
    pool: Sequence[ChannelSnapshot],
    loads: dict[uuid.UUID, int] | None = None,
) -> list[ChannelSnapshot]:
    """普通模型候选链（锚点名 = config.name）；speedbear 公共模型走 §10.1.4 platform 匹配。

    探测路径：validate_access=False（inactive/跨租户不在此报错，空链自解释）。
    """
    return ordered_channel_candidates(
        config,
        pool,
        tenant_id=config.tenant_id,
        loads=loads,
        validate_access=False,
    )


def _composite_candidate_chain(
    composite: ModelConfigSnapshot,
    tenant_id: uuid.UUID,
    index: _MemberIndex,
    pairs: Sequence[tuple[str, str]],
    pool: Sequence[ChannelSnapshot],
    loads: dict[uuid.UUID, int] | None = None,
) -> list[ChannelSnapshot]:
    """组合候选链：成员声明 × 成员内有序渠道链展平，按渠道 id 去重（展示/探测口径）。

    成员集合与运行期一致（弃用成员已剔除，D15④），故可用性探测/详情候选与轮换同源。
    """
    chain: list[ChannelSnapshot] = []
    seen: set[uuid.UUID] = set()
    for member in _composite_members(composite, tenant_id, index, pairs):
        candidates = match_channel_candidates(member.config, pool, model_name=member.model_name)
        for channel in order_channel_candidates(
            candidates, model_name=member.model_name, loads=loads
        ):
            if channel.id in seen:
                continue
            seen.add(channel.id)
            chain.append(channel)
    return chain


def candidate_channels_sync(
    db: Session,
    config_row: ModelConfig,
    tenant_id: uuid.UUID | None = None,
) -> list[ChannelSnapshot]:
    """单 config 渠道候选探测（脱敏展示 / 可用性 / 启用预检三处共用；不解密、不落库）。

    普通模型 = provider 活跃池 × 覆盖匹配（provider 级 [] 记公共备援）；组合 = 成员声明
    分别匹配后展平。空列表 = 运行期不可解析（组合成员全部不可用同理）。
    """
    effective_tenant = _normalize_tenant_id(tenant_id) or config_row.tenant_id
    config = SOURCE.config_snapshot(config_row)
    registry = SyncSQLChannelRegistry(db, SOURCE, cache=_shared_cache)
    loads = channel_loads_sync(db, effective_tenant)
    if config.provider is ModelProvider.COMPOSITE:
        pairs = parse_members(config_row.config)
        if not pairs:
            return []
        pool = registry.get_active_channels(effective_tenant, provider=None)
        index = _member_configs_sync(db, effective_tenant, pairs)
        return _composite_candidate_chain(config, effective_tenant, index, pairs, pool, loads)
    pool = registry.get_active_channels(effective_tenant, provider=config.provider)
    return _single_candidate_chain(config, pool, loads)


async def candidate_channels_async(
    db: AsyncSession,
    config_row: ModelConfig,
    tenant_id: uuid.UUID | None = None,
) -> list[ChannelSnapshot]:
    """异步态候选探测（GC#11：async 上下文禁止 sync session）。"""
    effective_tenant = _normalize_tenant_id(tenant_id) or config_row.tenant_id
    config = SOURCE.config_snapshot(config_row)
    registry = AsyncSQLChannelRegistry(db, SOURCE, cache=_shared_cache)
    loads = await channel_loads_async(db, effective_tenant)
    if config.provider is ModelProvider.COMPOSITE:
        pairs = parse_members(config_row.config)
        if not pairs:
            return []
        pool = await registry.get_active_channels(effective_tenant, provider=None)
        index = await _member_configs_async(db, effective_tenant, pairs)
        return _composite_candidate_chain(config, effective_tenant, index, pairs, pool, loads)
    pool = await registry.get_active_channels(effective_tenant, provider=config.provider)
    return _single_candidate_chain(config, pool, loads)


def candidate_channels_batch_sync(
    db: Session,
    config_rows: Sequence[ModelConfig],
    tenant_id: uuid.UUID,
) -> dict[uuid.UUID, list[ChannelSnapshot]]:
    """批量候选探测（列表页专用）：固定上限三次查询（全量活跃池 + 成员 config/弃用 pair）。

    普通模型按 provider 在内存匹配（池含全部 provider），组合复用同一成员索引；
    租户语义 = 调用方（effective_tenant），与 resolve_* 的 tenant_id 参数一致。
    """
    if not config_rows:
        return {}
    tenant_id = _normalize_tenant_id(tenant_id)
    pool = SyncSQLChannelRegistry(db, SOURCE, cache=_shared_cache).get_active_channels(
        tenant_id, provider=None
    )
    loads = channel_loads_sync(db, tenant_id)
    composite_pairs: set[tuple[str, str]] = set()
    for row in config_rows:
        if row.is_composite:
            composite_pairs.update(parse_members(row.config))
    member_index = (
        _member_configs_sync(db, tenant_id, sorted(composite_pairs))
        if composite_pairs
        else _MemberIndex(rows={}, deprecated_pairs=frozenset())
    )

    result: dict[uuid.UUID, list[ChannelSnapshot]] = {}
    for row in config_rows:
        config = SOURCE.config_snapshot(row)
        if config.provider is ModelProvider.COMPOSITE:
            pairs = parse_members(row.config)
            result[row.id] = (
                _composite_candidate_chain(config, tenant_id, member_index, pairs, pool, loads)
                if pairs
                else []
            )
        else:
            result[row.id] = _single_candidate_chain(config, pool, loads)
    return result


def affected_config_ids(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    provider: str,
    model_names: Sequence[str] | None,
) -> list[uuid.UUID]:
    """渠道变更影响面反查（供写路径失效 Redis 运行时缓存）。

    model_names=None → provider 级渠道，影响该租户该 provider 全部 config；
    非 None → 点名渠道，仅 name ∈ model_names 的 config（保守超集）。
    """
    stmt = select(ModelConfig.id).where(
        ModelConfig.tenant_id == tenant_id,
        ModelConfig.provider == provider,
    )
    if model_names is not None:
        stmt = stmt.where(ModelConfig.name.in_(list(model_names)))
    return list(db.execute(stmt).scalars().all())


__all__ = [
    "SOURCE",
    "ResolvedWithPlan",
    "affected_config_ids",
    "candidate_channels_async",
    "candidate_channels_batch_sync",
    "candidate_channels_sync",
    "invalidate_channel_cache",
    "parse_members",
    "resolve_composite_async",
    "resolve_composite_plan_async",
    "resolve_composite_plan_sync",
    "resolve_composite_sync",
    "resolve_config_async",
    "resolve_config_plan_async",
    "resolve_config_plan_sync",
    "resolve_config_sync",
]
