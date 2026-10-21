"""Host channel-registry adapter + runtime resolution entry points.

- In-package SQL registry base with this repository's ORM injected: RegistrySQLSource
  projection (ModelConfig/ModelChannel rows → package contract snapshots; DateTime time
  columns are converted to unix ms at the boundary)
- Process-wide shared ChannelSnapshotCache (TTL 60s): per-request registries reuse the same
  instance; write paths (ChannelService) invalidate eagerly via invalidate_channel_cache,
  with no reliance on TTL expiry
- Dual sync/async reads: management plane/worker/sync paths use resolve_config_sync; async
  paths use resolve_config_async (never use a sync session inside an async context)
- Host tenant_id is normalized to UUID at the entry points (_normalize_tenant_id): the host
  may pass the same tenant identity as str (e.g. the Redis-cached workspace→tenant lookup),
  while in-package identity comparison is UUID-based
- Composite configs (provider=composite) go through resolve_composite_sync/async: config JSON
  `members[]` → batch member config lookup (own tenant/non-composite; config is an optional
  enhancement, missing ones get synthesized snapshots; member snapshots all use the composite
  id as model_config_id — usage attribution follows the entry config)
  → member channel matching and flattening live in the package (resolve_composite_head);
  this layer takes the first decryptable candidate and the chain slice after it
- Failover plan: resolve_config_plan_sync/async and resolve_composite_plan_sync/async return
  ResolvedWithPlan(resolved, FailoverPlan) — same path and query count as plain resolution;
  the plan carries only snapshot/ciphertext candidates (candidates[0] is always the actual
  first channel); the cipher is injected by the consumer
- Decryption converges in resolve_and_chain_from_pool / resolve_composite_head (cipher
  injected); this module never stores plaintext
- Candidate probing (shared by management redacted display / channel availability / pre-enable
  checks): candidate_channels_sync/async (single config) and candidate_channels_batch_sync
  (list pages, at most two queries); matching only, no decryption; composite availability =
  the union of member candidates is non-empty
- least-used: within the same specificity and priority, pick by ascending window usage derived
  from usage_load (channel rows keep no counter); switch/window/degradation semantics live in
  app.services.usage_load, degrading to created_at asc when disabled or when aggregation fails

Error contract: a missing config row returns None; an existing but unresolvable one
(inactive/no candidate/decryption failure etc.) raises a RedBearModelError subclass, and the
caller decides between fallback and degradation based on the resolution switch.
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
    CompositeMember,
    CompositeMemberConfig,
    FailoverCandidate,
    FailoverPlan,
    LoadBalanceStrategy,
    ModelConfigSnapshot,
    ModelProfile,
    ModelProvider,
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

from ..utils.datetime_utils import to_timestamp_ms
from ..models.models_model import ModelBase, ModelChannel, ModelConfig
from .channel_service import cipher_from_env
from .usage_load import channel_loads_async, channel_loads_sync
from ..utils.redis_cache import invalidate_workspace_model_options

_CHANNEL_CACHE_TTL_MS = 60_000
_shared_cache = ChannelSnapshotCache(ttl_ms=_CHANNEL_CACHE_TTL_MS)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ResolvedWithPlan:
    """Runtime resolution result + failover plan (plan.candidates[0] is always the channel actually used first)."""

    resolved: ResolvedModelConfig
    plan: FailoverPlan


def _normalize_tenant_id(tenant_id: uuid.UUID | str | None) -> uuid.UUID | None:
    """Normalize host tenant identities to UUID at the package boundary.

    Several host call sites (e.g. the Redis-cached workspace→tenant lookup) pass the same
    identity as str, while in-package identity comparison is direct UUID comparison: a str
    would be misjudged as cross-tenant (ModelAccessDeniedError) or make all composite members
    invisible. Normalization keeps semantics unchanged; invalid input fails fast as a
    programming error.
    """
    if tenant_id is None or isinstance(tenant_id, uuid.UUID):
        return tenant_id
    return uuid.UUID(str(tenant_id))


def _created_ms(value) -> int:
    """Naive datetimes are interpreted as UTC (the DB stores naive UTC); the snapshot contract is non-optional, so the default is 0."""

    ms = to_timestamp_ms(value)
    return ms if ms is not None else 0


def _profile_members(row: ModelConfig) -> tuple[CompositeMember, ...]:
    """Composite declaration assembly (members[] is parsed only for provider=composite; always empty otherwise)."""
    try:
        provider = ModelProvider(row.provider)
    except ValueError:
        return ()
    if provider is not ModelProvider.COMPOSITE:
        return ()
    return tuple(
        CompositeMember(provider=member_provider, model_name=model_name)
        for member_provider, model_name in parse_members(row.config)
    )


def to_profile(row: ModelConfig) -> ModelProfile:
    """ORM row → contract profile (the only construction point on the read side).

    Two states (`from_stored_fields`): the three new columns non-empty = new-scheme row, read
    only from them; empty = fall back to converting the legacy columns (rows written by old
    images during the rollback window); chat→llm normalization and unknown-enum skipping are
    handled inside the package.
    """
    return ModelProfile.from_stored_fields(
        model_id=row.id,
        tenant_id=row.tenant_id,
        type=row.type,
        provider=row.provider,
        input_modalities=getattr(row, "input_modalities", None) or (),
        output_modalities=getattr(row, "output_modalities", None) or (),
        features=getattr(row, "features", None) or (),
        capabilities=row.capability or (),
        is_omni=bool(row.is_omni),
        members=_profile_members(row),
    )


def _config_snapshot(row: ModelConfig) -> ModelConfigSnapshot:
    return ModelConfigSnapshot(
        model_config_id=row.id,
        tenant_id=row.tenant_id,
        provider=ModelProvider(row.provider),
        name=row.name,
        is_active=row.is_active,
        is_public=row.is_public,
        is_deprecated=bool(row.model_base and row.model_base.is_deprecated),
        load_balance_strategy=LoadBalanceStrategy(
            row.load_balance_strategy or LoadBalanceStrategy.NONE
        ),
        profile=to_profile(row),
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
    config_load_options=(joinedload(ModelConfig.model_base),),
)


def _resolve_plan_sync(
    registry: SyncSQLChannelRegistry,
    config_id: uuid.UUID,
    tenant_id: uuid.UUID | None,
    config_row: ModelConfig | None = None,
) -> ResolvedWithPlan | None:
    # config_row: project the already-loaded ORM row directly to save a SELECT (both paths are semantically identical)
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
    """Async mirror: AsyncSQLChannelRegistry's two read methods are coroutines and must be awaited."""
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
    """Sync resolution (management plane/worker/sync paths): no DB hit on a snapshot-cache hit.

    A non-empty config_row is projected directly (the caller already holds the ORM instance,
    saving one SELECT).
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
    """Async resolution (runtime async paths); config_row semantics match the sync version."""
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
    """Sync resolution + failover plan; same path and query count as resolve_config_sync."""
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
    """Async resolution + failover plan (async paths must never use a sync session)."""
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
    """Write paths invalidate the channel snapshot cache eagerly (all keys of the tenant are invalidated together; see the in-package cache semantics)."""
    _shared_cache.invalidate(tenant_id, provider)
    # Workspace model options are filtered by channel availability, so channel-state changes must invalidate them too (no-op when tenant_id is empty)
    invalidate_workspace_model_options((tenant_id,))


def parse_members(config: dict | None) -> list[tuple[str, str]]:
    """Composite config JSON `members[]` → [(provider, model_name)]; dirty entries are skipped with a warning; deduplicated, order preserved."""
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


def _member_pairs(config: ModelConfigSnapshot) -> list[tuple[str, str]]:
    """Composite member declaration pairs: read from profile.members (single source, consistent with display/probing/runtime)."""
    return [(member.provider, member.model_name) for member in config.profile.members]


@dataclass(frozen=True)
class _MemberIndex:
    """Member resolution index: config rows + deprecated pair set, backing the single-point exclusion in `_composite_members`."""

    rows: dict[tuple[str, str], ModelConfig]
    deprecated_pairs: frozenset[tuple[str, str]]

    def is_deprecated(self, pair: tuple[str, str]) -> bool:
        """Pairs with a same-tenant config are judged by that row's base (missing base = custom model,
        no deprecation flag); synthesized members without a config are checked against model_bases
        by (provider, name)."""
        row = self.rows.get(pair)
        if row is not None:
            base = getattr(row, "model_base", None)
            return base is not None and base.is_deprecated
        return pair in self.deprecated_pairs


def _deprecated_base_pairs_sync(
    db: Session, pairs: Sequence[tuple[str, str]]
) -> frozenset[tuple[str, str]]:
    """Deprecated model_bases among the declared pairs (the sole authority for synthesized members; single query)."""
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
    """Batch member-resolution-index query (two queries: config rows + deprecated pairs): own tenant + non-composite.

    Ignores `config.is_active` (member availability is decided by active channels and
    credentials); among duplicate (provider, name) rows, prefer enabled and newer ones,
    consistent with the write-path validation. See `_composite_members` for deprecated-member
    exclusion.
    """
    stmt = (
        select(ModelConfig)
        .options(joinedload(ModelConfig.model_base))
        .where(
            ModelConfig.tenant_id == tenant_id,
            ModelConfig.provider != ModelProvider.COMPOSITE,
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
            ModelConfig.provider != ModelProvider.COMPOSITE,
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
    """Member declarations without a same-tenant config → synthesized snapshot from the declaration (config is an optional enhancement).

    The composite name is an alias rather than a real model name; the declared
    (provider, model_name) is the real invocation name (backfilled from the provider/model_name
    of association-bound key rows). Synthesis rules: type follows the composite; capabilities/
    parameters stay empty (nothing is overstated; a manually added config enhances it
    naturally); usage is attributed to the composite id; a non-enum provider or a nested
    composite → skipped with a warning.
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
        name=pair[1],
        is_active=True,
        is_public=False,
        profile=ModelProfile.from_legacy_fields(
            model_id=composite.model_config_id,
            tenant_id=tenant_id,
            type=composite.profile.type,
            provider=provider,
        ),
    )


def _composite_members(
    composite: ModelConfigSnapshot,
    tenant_id: uuid.UUID,
    index: _MemberIndex,
    pairs: Sequence[tuple[str, str]],
) -> list[CompositeMemberConfig]:
    """Member declarations → CompositeMemberConfig (declaration order): same-tenant configs contribute their snapshot; missing ones are synthesized from the declaration.

    Deprecated members are excluded at a single point during resolution (declarations stay
    reversible): config rows are judged by their `model_base`; synthesized members without a
    config are checked against model_bases by (provider, name). Member snapshots all use the
    composite id as model_config_id (usage attribution follows the entry config's `config_id`):
    real member configs behave like synthesized ones, and the hit member's identity stays in
    the event's provider/model_name/channel_id supply-side snapshot.
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
                update={
                    "model_config_id": composite.model_config_id,
                    "profile": snapshot.profile.model_copy(
                        update={"model_id": composite.model_config_id}
                    ),
                }
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
    """Composite config resolution (sync): members → batch member config lookup/synthesis → full channel pool → in-package orchestration picks the first candidate."""
    return resolve_composite_plan_sync(db, config_row, tenant_id).resolved


async def resolve_composite_async(
    db: AsyncSession,
    config_row: ModelConfig,
    tenant_id: uuid.UUID | None = None,
) -> ResolvedModelConfig:
    """Composite config resolution (async; async paths must never use a sync session)."""
    return (await resolve_composite_plan_async(db, config_row, tenant_id)).resolved


def resolve_composite_plan_sync(
    db: Session,
    config_row: ModelConfig,
    tenant_id: uuid.UUID | None = None,
) -> ResolvedWithPlan:
    """Composite resolution + failover plan (sync): takes the first decryptable candidate; the plan chain is sliced from that position."""
    effective_tenant = _normalize_tenant_id(tenant_id) or config_row.tenant_id
    composite = SOURCE.config_snapshot(config_row)
    pairs = _member_pairs(composite)
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
    """Composite resolution + failover plan (async; async paths must never use a sync session)."""
    effective_tenant = _normalize_tenant_id(tenant_id) or config_row.tenant_id
    composite = SOURCE.config_snapshot(config_row)
    pairs = _member_pairs(composite)
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
    """Plain-model candidate chain (anchor name = config.name); speedbear public models use platform matching.

    Probing path: validate_access=False (inactive/cross-tenant errors are not raised here; an
    empty chain is self-explanatory).
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
    """Composite candidate chain: member declarations × per-member ordered channel chains, flattened and deduplicated by channel id (display/probing semantics).

    The member set matches the runtime one (deprecated members already excluded), so availability
    probing and detail candidates share the same source as rotation.
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
    """Single-config channel candidate probing (shared by redacted display / availability / pre-enable checks; no decryption, no DB writes).

    Plain model = active pool of its provider × coverage matching (a provider-level [] counts as
    the shared fallback); composite = member declarations matched separately, then flattened.
    An empty list = unresolvable at runtime (same when all composite members are unavailable).
    """
    effective_tenant = _normalize_tenant_id(tenant_id) or config_row.tenant_id
    config = SOURCE.config_snapshot(config_row)
    registry = SyncSQLChannelRegistry(db, SOURCE, cache=_shared_cache)
    loads = channel_loads_sync(db, effective_tenant)
    if config.provider is ModelProvider.COMPOSITE:
        pairs = _member_pairs(config)
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
    """Async candidate probing (never use a sync session inside an async context)."""
    effective_tenant = _normalize_tenant_id(tenant_id) or config_row.tenant_id
    config = SOURCE.config_snapshot(config_row)
    registry = AsyncSQLChannelRegistry(db, SOURCE, cache=_shared_cache)
    loads = await channel_loads_async(db, effective_tenant)
    if config.provider is ModelProvider.COMPOSITE:
        pairs = _member_pairs(config)
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
    """Batch candidate probing (list pages only): a fixed upper bound of three queries (full active pool + member configs/deprecated pairs).

    Plain models match in memory by provider (the pool covers all providers); composites reuse
    the same member index; tenant semantics follow the caller (effective_tenant), matching the
    tenant_id parameter of the resolve_* functions.
    """
    if not config_rows:
        return {}
    tenant_id = _normalize_tenant_id(tenant_id)
    pool = SyncSQLChannelRegistry(db, SOURCE, cache=_shared_cache).get_active_channels(
        tenant_id, provider=None
    )
    loads = channel_loads_sync(db, tenant_id)
    snapshots = {row.id: SOURCE.config_snapshot(row) for row in config_rows}
    composite_pairs: set[tuple[str, str]] = set()
    for config in snapshots.values():
        if config.provider is ModelProvider.COMPOSITE:
            composite_pairs.update(_member_pairs(config))
    member_index = (
        _member_configs_sync(db, tenant_id, sorted(composite_pairs))
        if composite_pairs
        else _MemberIndex(rows={}, deprecated_pairs=frozenset())
    )

    result: dict[uuid.UUID, list[ChannelSnapshot]] = {}
    for row in config_rows:
        config = snapshots[row.id]
        if config.provider is ModelProvider.COMPOSITE:
            pairs = _member_pairs(config)
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
    """Reverse lookup of the impact set of a channel change (used by write paths to invalidate the Redis runtime cache).

    model_names=None → provider-level channel, affecting every config of that tenant+provider;
    non-None → named channel, only configs whose name ∈ model_names (a conservative superset).
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
    "to_profile",
]
