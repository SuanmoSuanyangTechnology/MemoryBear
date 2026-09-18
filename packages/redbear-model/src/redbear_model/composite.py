"""组合模型成员候选编排（spec §10.1#1 / §10.3 / §11.2）。

组合 config 无单渠道解析（resolver.resolve_from_channel_pool 直接拒绝），候选来自
config JSON `members[]` 声明的成员模型：每个成员是一个普通 config 快照，其渠道按普通
规则自动匹配（provider 相等 + 覆盖集命中 + is_active），随后「成员声明顺序 × 成员内
有序渠道链」展平为统一候选链。宿主负责成员 config 查找与渠道池查询；本模块纯函数。

成员 config 为可选增强：宿主对无同租户 config 的成员按声明合成快照（provider=
声明 provider、name=声明模型名、model_type 随组合、能力/参数留空），成员声明名即
真实调用名（组合 name 是别名）。

- 成员不可访问（租户不符且非公开）/嵌套/无候选 → 跳过该成员，不中断整链；
  全链为空 → NoAvailableChannelError（§10.3：不可用成员只让该次调用报"无可用成员"）。
  成员 config 的 is_active（启用/禁用）不是成员闸门——成员可用性由渠道与凭据活跃决定。
- 候选链返回整链：宿主当前取首个可解密候选（MVP 与旧单 key 行为对齐），请求内换渠道
  failover（§11.2）直接消费整链。成员内 least-used 分流由宿主传入 loads（usage_records 派生）。
- 请求能力需求过滤（§10.1#1 成员按 request_capabilities 预筛）不在本层：宿主解析入口
  无请求上下文，随运行期调用点收敛接入。
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from uuid import UUID

from pydantic import Field

from .contracts import (
    ChannelSnapshot,
    ContractModel,
    ModelConfigSnapshot,
    ModelProvider,
    ModelRuntimeOptions,
    ResolvedModelConfig,
)
from .crypto import CredentialCipher
from .errors import CredentialDecryptError, NoAvailableChannelError
from .resolver import (
    build_resolved_from_channel,
    match_channel_candidates,
    order_channel_candidates,
)

logger = logging.getLogger(__name__)


class CompositeMemberConfig(ContractModel):
    """已解析成员输入：成员 config 快照 + 声明名（调用锚点，= 成员 config.name）。"""
    config: ModelConfigSnapshot
    model_name: str = Field(min_length=1)


class CompositeCandidate(ContractModel):
    """组合候选：成员 config 快照 + 声明名 + 命中渠道。"""
    member: ModelConfigSnapshot
    model_name: str = Field(min_length=1)
    channel: ChannelSnapshot


def _member_accessible(config: ModelConfigSnapshot, tenant_id: UUID) -> bool:
    if config.provider is ModelProvider.COMPOSITE:
        return False
    return config.tenant_id == tenant_id or config.is_public


def composite_candidate_chain(
    composite: ModelConfigSnapshot,
    members: Sequence[CompositeMemberConfig],
    channel_pool: Sequence[ChannelSnapshot],
    *,
    tenant_id: UUID,
    loads: Mapping[UUID, int] | None = None,
) -> list[CompositeCandidate]:
    """成员声明顺序 × 成员内有序渠道链展平（纯函数，无 I/O、不解密）。

    嵌套组合成员（provider=composite）天然无渠道命中（登记层守卫 composite 不入渠道），
    此处显式跳过以省一次匹配。
    """
    chain: list[CompositeCandidate] = []
    for member in members:
        if not _member_accessible(member.config, tenant_id):
            continue
        candidates = match_channel_candidates(
            member.config,
            channel_pool,
            model_name=member.model_name,
        )
        if not candidates:
            continue
        for channel in order_channel_candidates(
            candidates, model_name=member.model_name, loads=loads
        ):
            chain.append(
                CompositeCandidate(
                    member=member.config,
                    model_name=member.model_name,
                    channel=channel,
                )
            )
    if not chain:
        raise NoAvailableChannelError(
            composite.model_config_id,
            str(composite.provider),
            composite.name,
            "no available composite member channel",
        )
    return chain


def resolve_composite_candidates(
    composite: ModelConfigSnapshot,
    members: Sequence[CompositeMemberConfig],
    channel_pool: Sequence[ChannelSnapshot],
    *,
    tenant_id: UUID,
    cipher: CredentialCipher,
    runtime_options: ModelRuntimeOptions | None = None,
    loads: Mapping[UUID, int] | None = None,
) -> list[ResolvedModelConfig]:
    """候选链逐个解密，返回可用有序列表（坏凭据跳过不阻断，全败 → NoAvailableChannelError）。

    返回项的 model_config_id 取自传入的成员快照——usage 归因口径由宿主决定（组合入口
    统一改写为组合 id，见宿主 channel_registry）。loads 为渠道滚动窗口用量（宿主派生）。
    """
    chain = composite_candidate_chain(
        composite, members, channel_pool, tenant_id=tenant_id, loads=loads
    )
    resolved: list[ResolvedModelConfig] = []
    for candidate in chain:
        try:
            resolved.append(
                build_resolved_from_channel(
                    candidate.member,
                    candidate.channel,
                    tenant_id=tenant_id,
                    model_name=candidate.model_name,
                    cipher=cipher,
                    runtime_options=runtime_options,
                )
            )
        except CredentialDecryptError as exc:
            logger.warning(
                "composite %s member %s channel %s credential decrypt failed: %s",
                composite.model_config_id,
                candidate.model_name,
                candidate.channel.id,
                exc,
            )
            continue
    if not resolved:
        raise NoAvailableChannelError(
            composite.model_config_id,
            str(composite.provider),
            composite.name,
            "all composite member channels failed credential decryption",
        )
    return resolved


def resolve_composite_head(
    composite: ModelConfigSnapshot,
    members: Sequence[CompositeMemberConfig],
    channel_pool: Sequence[ChannelSnapshot],
    *,
    tenant_id: UUID,
    cipher: CredentialCipher,
    loads: Mapping[UUID, int] | None = None,
) -> tuple[ResolvedModelConfig, list[CompositeCandidate]]:
    """首个可解密候选及其后有序切片（failover plan 构建用）。

    保持 resolve_composite_candidates 的「首个可解密」壳语义，但不预解整链：仅自链头
    逐个解密到首个成功为止，返回 (该 resolved, 自该位的候选切片)，故切片头部恒等于
    实际首发渠道。坏凭据跳过不阻断（warning）；全败 → NoAvailableChannelError（同文案）。
    """
    chain = composite_candidate_chain(
        composite, members, channel_pool, tenant_id=tenant_id, loads=loads
    )
    for index, candidate in enumerate(chain):
        try:
            resolved = build_resolved_from_channel(
                candidate.member,
                candidate.channel,
                tenant_id=tenant_id,
                model_name=candidate.model_name,
                cipher=cipher,
            )
        except CredentialDecryptError as exc:
            logger.warning(
                "composite %s member %s channel %s credential decrypt failed: %s",
                composite.model_config_id,
                candidate.model_name,
                candidate.channel.id,
                exc,
            )
            continue
        return resolved, chain[index:]
    raise NoAvailableChannelError(
        composite.model_config_id,
        str(composite.provider),
        composite.name,
        "all composite member channels failed credential decryption",
    )
