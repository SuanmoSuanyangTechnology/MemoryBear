"""wire 面词域翻译层（Task 13 §15.1）：对外 apikey 词域 ↔ model_channels 渠道。

- 模型域 `/{model_id}/apikeys`：模型级（点名）渠道列表（含停用，启停/编辑/解绑的管理面；
  非运行期候选链，不透出公共备援）、登记点名凭据、解绑（解绑后候选链为空的自有启用模型
  置停用——公开不联动，维持"启用 ⟹ 有可用渠道"不变式，2026-09-15 决策；组合模型不走登记/解绑，
  仅列表按成员声明序展开）
- Provider 域 `/provider/apikeys`：provider 级公共凭据列表/登记（model_names=[]，覆盖该
  供应商全部未点名模型）、渠道属性维护（model_names 不可改）、凭据删除（影响面前置提示，
  前端弹确认；删除后候选链为空的自有启用模型（公开不联动）同事务置停用）
- 对外只见 `credential_masked`；明文只进不出（加密落地在 ChannelService，本层不触密文）
- 租户归属强校验：越租户按 404 处理，不泄漏资源存在性
- 事务边界在本层（ChannelService 只 flush）；可用性探测只读不解密
"""
from __future__ import annotations

import asyncio
import logging
import math
import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from redbear_model import ChannelSource
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.error_codes import BizCode
from app.core.exceptions import BusinessException
from app.core.model_provider_config import (
    get_provider_validation_model,
    is_local_deployment_provider,
    uses_custom_api_base,
)
from app.models.models_model import ModelChannel, ModelConfig, ModelProvider, ModelType
from app.repositories.model_channel_repository import ModelChannelRepository
from app.repositories.model_repository import ModelConfigRepository
from app.schemas import model_schema
from app.schemas.response_schema import PageData, PageMeta
from app.services.channel_registry import (
    candidate_channels_batch_sync,
    candidate_channels_sync,
    invalidate_channel_cache,
    parse_members,
)
from app.services.channel_service import ChannelService, describe_channel
from app.services.model_service import (
    ModelConfigService,
    _invalidate_model_option_states,
    _model_option_cache_state,
    _require_media_model_configuration,
    _require_api_base_for_local_provider,
    _require_supported_api_base,
    _require_wellformed_api_base,
    _require_wellformed_bedrock_credential,
    is_media_model,
)

logger = logging.getLogger(__name__)


def _provider_value(provider) -> str:
    return str(getattr(provider, "value", provider))


def _require_provider_level_registration_allowed(provider: str) -> None:
    """渠道域（provider 级）登记前置：本地部署提供商没有公共端点；speedbear 平台代管。"""
    if provider == ModelProvider.SPEEDBEAR.value:
        raise BusinessException(
            "speedbear 为平台代管渠道，不支持自行登记", BizCode.INVALID_PARAMETER
        )
    if is_local_deployment_provider(provider):
        raise BusinessException(
            f"本地部署提供商 {provider} 没有公共端点，不能在渠道域登记密钥，"
            "请在模型域按模型登记并配置 API Base URL",
            BizCode.INVALID_PARAMETER,
        )


def _reject_provider_level_api_base(provider: str, api_base: str | None) -> None:
    """渠道域（provider 级）渠道禁配 api_base：运行时使用 provider 公共基地址。

    模型级（点名）渠道不受限；渠道域恒存 NULL（幂等键含 api_base）。
    """
    if not (isinstance(api_base, str) and api_base.strip()):
        return
    if is_local_deployment_provider(provider):
        message = (
            f"本地部署提供商 {provider} 没有公共端点，不允许配置api_base，"
            "请在模型域按模型登记并配置 API Base URL"
        )
    else:
        message = (
            "渠道域级密钥默认使用公共端点，不允许配置api_base，"
            "请在模型域按模型登记并配置 API Base URL"
        )
    raise BusinessException(message, BizCode.INVALID_PARAMETER)


# D4 兜底类型序：llm/chat 同序（chat 为存量归一口径）；表外类型排最后。
_TYPE_RANK = {"llm": 0, "chat": 0, "embedding": 1, "rerank": 2}


def _pick_validation_anchor(rows: list[ModelConfig]) -> ModelConfig | None:
    """兜底选取（D4）：优先非废弃 → 类型序 → created_at desc。

    先按 created_at 降序稳定排序，再取 (废弃, 类型序) 最小的首个元素 = 组内最新。
    """
    if not rows:
        return None
    ordered = sorted(rows, key=lambda row: row.created_at, reverse=True)
    return min(
        ordered,
        key=lambda row: (
            bool(getattr(getattr(row, "model_base", None), "is_deprecated", False)),
            _TYPE_RANK.get(_provider_value(row.type), len(_TYPE_RANK)),
        ),
    )


@dataclass(frozen=True)
class _ValidationAnchor:
    """provider 级密钥验证锚点：服务端自选，不进 wire（spec D2）。"""

    name: str
    type: str
    is_omni: bool
    capability: list[str]


def _resolve_validation_anchor(
    db: Session, *, provider: str, tenant_id: uuid.UUID
) -> _ValidationAnchor | None:
    """锚点解析（D3/D4）：标准校验模型表优先；表外取租户同供应商非组合模型兜底。"""
    standard = get_provider_validation_model(provider)
    if standard:
        return _ValidationAnchor(
            name=standard, type=ModelType.LLM.value, is_omni=False, capability=[]
        )
    picked = _pick_validation_anchor(
        ModelConfigRepository.list_validation_candidates(
            db, provider=provider, tenant_id=tenant_id
        )
    )
    if picked is None:
        return None
    return _ValidationAnchor(
        name=picked.name,
        type=_provider_value(picked.type),
        is_omni=bool(picked.is_omni),
        capability=picked.capability,
    )


async def _validate_provider_key(
    db: Session, *, provider: str, tenant_id: uuid.UUID, api_key: str
) -> None:
    """保存前活体验证（Dify 式先校验后落库）：失败抛 400，调用方保证零写入。

    api_base 恒 None：provider 级 = 供应商公共端点，与运行期语义一致。
    async-over-sync（先例 add_model_key/create_model_api_key）：探测期间 sync 连接跨网络调用
    持有属已知成本，随阶段二服务化 sync→async 统一整改（见阶段二设计 §2.3）。
    """
    anchor = _resolve_validation_anchor(db, provider=provider, tenant_id=tenant_id)
    if anchor is None:
        raise BusinessException(
            "该供应商暂无可用校验模型，请先添加模型后重试", BizCode.INVALID_PARAMETER
        )
    result = await ModelConfigService.validate_model_config(
        db=db,
        model_name=anchor.name,
        provider=provider,
        api_key=api_key,
        api_base=None,
        model_type=anchor.type,
        test_message="Hello",
        is_omni=anchor.is_omni,
        capability=anchor.capability,
    )
    if not result["valid"]:
        raise BusinessException(
            f"密钥验证失败（校验模型 {anchor.name}）：{result['error']}",
            BizCode.INVALID_PARAMETER,
        )


def _named_channels_for_model(
    rows: list[ModelChannel], *, provider: str, model_name: str
) -> list[ModelChannel]:
    """点名命中渠道（含停用）；序 = priority desc → created_at asc → id。"""
    matched = [
        row
        for row in rows
        if row.provider == provider
        and row.model_names
        and model_name in row.model_names
    ]
    return sorted(matched, key=lambda row: (-(row.priority or 0), row.created_at, str(row.id)))


def _named_channels_for_members(
    rows: list[ModelChannel], pairs: list[tuple[str, str]]
) -> list[ModelChannel]:
    """组合：成员声明序展开点名渠道，按渠道 id 去重（共享渠道保留首现位置）。"""
    ordered: list[ModelChannel] = []
    seen: set[uuid.UUID] = set()
    for provider, model_name in pairs:
        for row in _named_channels_for_model(rows, provider=provider, model_name=model_name):
            if row.id not in seen:
                seen.add(row.id)
                ordered.append(row)
    return ordered


def _auto_disable_unresolvable(
    db: Session, rows: Sequence[ModelConfig], tenant_id: uuid.UUID
) -> list[tuple[uuid.UUID | None, bool]]:
    """删除/解绑联动（2026-09-15）：候选链解析为空的在启用自有非公开模型置停用（不 commit）。

    判据与启用预检（`assert_enableable`）同一口径：candidate_channels_* 非空 = 可解析；
    单模型走单探测，多条走批量探测（固定 ≤2 查询）。公开模型（is_public，含租户自有）
    启用态是跨租户共享目录，租户侧渠道变动不联动。返回被停用模型的工作空间选项缓存态
    （`_model_option_cache_state`），调用方 commit 后失效；空列表 = 无联动。
    """
    owned = [
        row for row in rows if row.is_active and not row.is_public and row.tenant_id == tenant_id
    ]
    if not owned:
        return []
    if len(owned) == 1:
        probe = {
            owned[0].id: bool(candidate_channels_sync(db, owned[0], tenant_id=tenant_id))
        }
    else:
        probe = {
            row_id: bool(chain)
            for row_id, chain in candidate_channels_batch_sync(db, owned, tenant_id).items()
        }
    states: list[tuple[uuid.UUID | None, bool]] = []
    for row in owned:
        if not probe.get(row.id):
            row.is_active = False
            states.append(_model_option_cache_state(row))
            logger.info(
                "联动停用模型配置: model_config_id=%s tenant_id=%s provider=%s（删除/解绑后候选链为空）",
                row.id,
                tenant_id,
                _provider_value(row.provider),
            )
    return states


class ChannelApiKeyService:
    """模型域 / Provider 域 wire 编排（apikey ↔ 渠道词域翻译）。"""

    # ---- 通用守卫 ----
    @staticmethod
    def _config(db: Session, model_id: uuid.UUID, tenant_id: uuid.UUID) -> ModelConfig:
        model_config = ModelConfigRepository.get_by_id(db, model_id, tenant_id=tenant_id)
        if not model_config:
            raise BusinessException("模型配置不存在", BizCode.MODEL_NOT_FOUND)
        if model_config.is_composite:
            raise BusinessException(
                "组合模型不支持直接登记凭据；请为成员模型登记，或在 Provider 域登记公共凭据",
                BizCode.INVALID_PARAMETER,
            )
        return model_config

    @staticmethod
    def _owned_channel(db: Session, apikey_id: uuid.UUID, tenant_id: uuid.UUID) -> ModelChannel:
        row = ModelChannelRepository(db).get(apikey_id)
        if row is None or row.tenant_id != tenant_id:
            raise BusinessException("API Key不存在", BizCode.NOT_FOUND)
        return row

    # ---- 启用预检（列表/详情可用性探测在 channel_registry，不解密、不落库）----
    @staticmethod
    def assert_enableable(db: Session, model_config: ModelConfig, tenant_id: uuid.UUID) -> None:
        """启用预检三态：普通模型需候选非空（409）；组合模型需成员声明非空（400）。

        speedbear 公共模型（M5 起并入渠道）候选 = 该租户 platform 渠道，与普通模型同一
        口径；候选为空时引导绑定（租户不能自助登记，通用"补充 API Key"文案会误导）。
        候选为空但存在覆盖渠道（未按 is_active 过滤）时判为"全部停用"（CHANNEL_DISABLED），
        与"从未登记"区分。禁用不校验（关闭永远放行）。
        """
        if model_config.is_composite:
            if not parse_members(model_config.config):
                raise BusinessException(
                    "组合模型缺少成员，无法启用", BizCode.INVALID_PARAMETER
                )
            return
        if candidate_channels_sync(db, model_config, tenant_id=tenant_id):
            return
        repo = ModelChannelRepository(db)
        provider = _provider_value(model_config.provider)
        if model_config.provider == ModelProvider.SPEEDBEAR and model_config.is_public:
            if repo.exists_covering(
                tenant_id=tenant_id, provider=provider, source=ChannelSource.PLATFORM
            ):
                raise BusinessException(
                    "SpeedBear 公共渠道已全部停用，请前往渠道管理启用渠道后重试",
                    BizCode.CHANNEL_DISABLED,
                )
            raise BusinessException(
                "当前租户未绑定 SpeedBear Key，请联系平台管理员初始化",
                BizCode.SPEEDBEAR_CHANNEL_MISSING,
            )
        if repo.exists_covering(
            tenant_id=tenant_id, provider=provider, model_name=model_config.name
        ):
            raise BusinessException(
                "模型候选渠道已全部停用，请前往渠道管理启用渠道后重试",
                BizCode.CHANNEL_DISABLED,
            )
        raise BusinessException(
            "模型没有可解析的渠道凭据，请先补充 API Key 后启用",
            BizCode.NO_AVAILABLE_CHANNEL,
        )

    # ---- 模型域 ----
    @staticmethod
    def list_model_candidates(
        db: Session, model_id: uuid.UUID, tenant_id: uuid.UUID
    ) -> list[model_schema.ApiKeyItem]:
        """模型级（点名）渠道列表（含停用；模型页启停/编辑/解绑的管理面）。

        与运行期候选链不同：不透出 provider 级公共备援、不按活跃过滤（否则停用后
        无法在管理面重新启用）；组合按成员声明序展开。可用性/启用预检仍走
        candidate_channels_*，不受本口径影响。
        """
        model_config = ModelConfigRepository.get_by_id(db, model_id, tenant_id=tenant_id)
        if not model_config:
            raise BusinessException("模型配置不存在", BizCode.MODEL_NOT_FOUND)
        rows = ChannelService(db).list_tenant(tenant_id=tenant_id)
        if model_config.is_composite:
            rows = _named_channels_for_members(rows, parse_members(model_config.config))
        else:
            rows = _named_channels_for_model(
                rows,
                provider=_provider_value(model_config.provider),
                model_name=model_config.name,
            )
        return [model_schema.ApiKeyItem.model_validate(describe_channel(row)) for row in rows]

    @staticmethod
    def _media_key_snapshot(model_id: uuid.UUID, tenant_id: uuid.UUID) -> dict | None:
        from app.db import get_db_context

        with get_db_context() as db:
            config = ChannelApiKeyService._config(db, model_id, tenant_id)
            if not is_media_model(config.provider, config.name):
                return None
            if config.model_base and config.model_base.is_deprecated:
                raise BusinessException("模型已停用或废弃", BizCode.INVALID_PARAMETER)
            _require_media_model_configuration(
                model_name=config.name,
                model_type=config.type,
                capability=config.capability,
            )
            return {"provider": _provider_value(config.provider), "name": config.name,
                    "type": config.type, "capability": list(config.capability or []),
                    "is_active": config.is_active}

    @staticmethod
    def _register_media_key(model_id, tenant_id, created_by, data, snapshot):
        from app.db import get_db_context

        with get_db_context() as db:
            try:
                config = ChannelApiKeyService._config(db, model_id, tenant_id)
                if (config.is_active != snapshot["is_active"]
                        or (config.model_base and config.model_base.is_deprecated)
                        or config.name != snapshot["name"]
                        or _provider_value(config.provider) != snapshot["provider"]
                        or config.type != snapshot["type"]
                        or list(config.capability or []) != snapshot["capability"]):
                    raise BusinessException("登记期间模型配置已变更，请重试", BizCode.INVALID_PARAMETER)
                row, action = ChannelService(db).register_for_model(
                    provider=snapshot["provider"], tenant_id=tenant_id,
                    model_name=snapshot["name"], api_key=data["api_key"], api_base=data["api_base"],
                    remark=data["remark"], priority=data["priority"], created_by=created_by,
                )
                db.commit()
                db.refresh(row)
                return model_schema.ApiKeyItem.model_validate(describe_channel(row)), action
            except Exception:
                db.rollback()
                raise

    @staticmethod
    async def add_model_key(
        db: Session,
        model_id: uuid.UUID,
        data: model_schema.ApiKeyRegister,
        tenant_id: uuid.UUID,
        created_by: uuid.UUID | None = None,
    ) -> tuple[model_schema.ApiKeyItem, str]:
        """给模型登记点名凭据（真实调用名 = config.name，组合 name 是别名不走本域）。

        同凭据同端点已存在时幂等合并：provider 级渠道吸收为 no-op、点名渠道并入覆盖集。
        音视频理解模型登记时只校验配置结构，凭据在实际调用时验证；其他模型
        仍在登记前试调一次，失败返回 400。
        """
        snapshot = await asyncio.to_thread(ChannelApiKeyService._media_key_snapshot, model_id, tenant_id)
        if snapshot is not None:
            _require_api_base_for_local_provider(snapshot["provider"], data.api_base)
            _require_wellformed_bedrock_credential(snapshot["provider"], data.api_key)
            _require_wellformed_api_base(snapshot["provider"], data.api_base, snapshot["type"])
            _require_supported_api_base(snapshot["provider"], data.api_base, snapshot["type"])
            return await asyncio.to_thread(
                ChannelApiKeyService._register_media_key, model_id, tenant_id,
                created_by, data.model_dump(), snapshot,
            )
        model_config = ChannelApiKeyService._config(db, model_id, tenant_id)
        provider = _provider_value(model_config.provider)
        _require_api_base_for_local_provider(provider, data.api_base)
        _require_wellformed_bedrock_credential(provider, data.api_key)
        _require_wellformed_api_base(provider, data.api_base, model_config.type)
        _require_supported_api_base(provider, data.api_base, model_config.type)

        validation_result = await ModelConfigService.validate_model_config(
            db=db,
            model_name=model_config.name,
            provider=provider,
            api_key=data.api_key,
            api_base=data.api_base,
            model_type=model_config.type,
            test_message="Hello",
            is_omni=model_config.is_omni,
            capability=model_config.capability,
        )
        if not validation_result["valid"]:
            raise BusinessException(
                f"模型配置验证失败: {validation_result['error']}", BizCode.INVALID_PARAMETER
            )

        row, action = ChannelService(db).register_for_model(
            provider=provider,
            tenant_id=tenant_id,
            model_name=model_config.name,
            api_key=data.api_key,
            api_base=data.api_base,
            remark=data.remark,
            priority=data.priority,
            created_by=created_by,
        )
        db.commit()
        db.refresh(row)
        return model_schema.ApiKeyItem.model_validate(describe_channel(row)), action

    @staticmethod
    def unbind_model_key(
        db: Session, model_id: uuid.UUID, apikey_id: uuid.UUID, tenant_id: uuid.UUID
    ) -> dict:
        """解绑：点名渠道移除该模型名（点名为空自动删凭据）。

        解绑确有移除（unbound）且候选链随之为空时，同事务停用该自有启用模型
        （2026-09-15 决策：维持"启用 ⟹ 有可用渠道"不变式；公开模型不联动）。
        返回体不变。
        """
        model_config = ChannelApiKeyService._config(db, model_id, tenant_id)
        row = ChannelApiKeyService._owned_channel(db, apikey_id, tenant_id)
        if row.provider != _provider_value(model_config.provider):
            raise BusinessException("该凭据供应商与模型不匹配", BizCode.INVALID_PARAMETER)
        if not row.model_names:
            raise BusinessException(
                "该凭据为供应商公共凭据，覆盖全部模型；如需停用或删除请前往 Provider 域",
                BizCode.INVALID_PARAMETER,
            )
        if model_config.name not in row.model_names:
            raise BusinessException(
                f"该凭据未绑定模型 {model_config.name}", BizCode.INVALID_PARAMETER
            )

        states: list[tuple[uuid.UUID | None, bool]] = []
        try:
            unbound, deleted = ChannelService(db).unbind_model(row.id, model_config.name)
            if unbound:
                states = _auto_disable_unresolvable(db, [model_config], tenant_id)
            db.commit()
        except Exception:
            # 探测可能已用删除后状态回填渠道快照缓存；回滚后须再失效，防 ≤TTL 的错判
            db.rollback()
            invalidate_channel_cache(row.tenant_id, _provider_value(row.provider))
            raise
        if states:
            _invalidate_model_option_states(*states)
        return {
            "id": str(row.id),
            "model_name": model_config.name,
            "unbound": unbound,
            "deleted": deleted,
        }

    # ---- Provider 域 ----
    @staticmethod
    def list_provider_keys(
        db: Session,
        *,
        tenant_id: uuid.UUID,
        provider: str | None = None,
        is_active: bool | None = None,
        source: str | None = None,
        page: int = 1,
        pagesize: int = 10,
    ) -> PageData:
        """provider 级公共渠道列表（脱敏；model_names 恒为空；点名渠道在模型域管理）。

        分页在内存切片（单查询，租户渠道量级有界）。
        """
        rows = ChannelService(db).list_tenant(
            tenant_id=tenant_id,
            provider=provider,
            is_active=is_active,
            source=source,
        )
        rows = [row for row in rows if not row.model_names]
        total = len(rows)
        pages = math.ceil(total / pagesize) if total > 0 else 0
        start = (page - 1) * pagesize
        items = [
            model_schema.ApiKeyItem.model_validate(describe_channel(row))
            for row in rows[start : start + pagesize]
        ]
        return PageData(
            page=PageMeta(
                page=page, pagesize=pagesize, total=total, hasnext=page < pages
            ),
            items=items,
        )

    @staticmethod
    async def create_provider_key(
        db: Session,
        data: model_schema.ProviderApiKeyCreate,
        tenant_id: uuid.UUID,
        created_by: uuid.UUID | None = None,
    ) -> tuple[model_schema.ApiKeyItem, str]:
        """登记 provider 级公共凭据（model_names=[]）。

        api_base 恒 NULL：运行时使用 provider 公共基地址（本地提供商无公共端点，禁登记）。
        保存前以服务端自选锚点模型（标准校验模型表 → 租户同供应商模型兜底）做活体验证，
        失败 400 零落库；同幂等键命中同凭据点名行 → 原地升级为 provider 级
        （"upgraded"，覆盖集扩展为该供应商全部未点名模型）；已 provider 级 → merged。
        """
        provider = _provider_value(data.provider)
        if provider == ModelProvider.COMPOSITE.value:
            raise BusinessException(
                "组合是配置形态而非供应商，不能登记凭据", BizCode.INVALID_PARAMETER
            )
        _require_provider_level_registration_allowed(provider)
        _reject_provider_level_api_base(provider, data.api_base)
        _require_wellformed_bedrock_credential(provider, data.api_key)
        await _validate_provider_key(
            db, provider=provider, tenant_id=tenant_id, api_key=data.api_key
        )
        try:
            row, action = ChannelService(db).register_provider_channel(
                provider=provider,
                tenant_id=tenant_id,
                api_key=data.api_key,
                api_base=None,
                remark=data.remark,
                priority=data.priority,
                created_by=created_by,
            )
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            raise BusinessException(
                "同凭据同端点的渠道已存在", BizCode.DUPLICATE_NAME
            ) from exc
        db.refresh(row)
        return model_schema.ApiKeyItem.model_validate(describe_channel(row)), action

    @staticmethod
    def get_provider_key(
        db: Session, apikey_id: uuid.UUID, tenant_id: uuid.UUID
    ) -> model_schema.ApiKeyItem:
        row = ChannelApiKeyService._owned_channel(db, apikey_id, tenant_id)
        return model_schema.ApiKeyItem.model_validate(describe_channel(row))

    @staticmethod
    async def update_provider_key(
        db: Session,
        apikey_id: uuid.UUID,
        data: model_schema.ProviderApiKeyUpdate,
        tenant_id: uuid.UUID,
    ) -> model_schema.ApiKeyItem:
        """改渠道属性/重填凭据；model_names 不可改（点名并入走模型域登记）。

        省略 = 不改；api_base/remark 显式 null = 清空；priority/is_active 显式 null 视为省略。
        provider 级渠道禁配 api_base（显式 null/空串允许，落回 NULL）。provider 级渠道
        重填 api_key 时先做活体验证（失败 400 零写入）；点名渠道重填不验证。
        点名渠道改 api_base：格式硬校验；仅当绑定模型全部不读取自定义地址时套官方白名单。
        """
        row = ChannelApiKeyService._owned_channel(db, apikey_id, tenant_id)
        provided = data.model_dump(exclude_unset=True)
        api_key = provided.pop("api_key", None)
        if "api_base" in provided:
            if row.model_names:
                api_base_value = provided["api_base"]
                _require_api_base_for_local_provider(row.provider, api_base_value)
                _require_wellformed_api_base(row.provider, api_base_value)
                # 点名渠道的 api_base 仅被读取自定义地址的成员使用；绑定模型全部
                # 不读取（dashscope embedding/rerank 走原生 SDK）时套同一白名单
                if isinstance(api_base_value, str) and api_base_value.strip():
                    bound = list(
                        ModelConfigRepository.get_members_by_provider_names(
                            db,
                            tenant_id,
                            [(row.provider, name) for name in row.model_names],
                        ).values()
                    )
                    if bound and all(
                        not uses_custom_api_base(row.provider, member.type)
                        for member in bound
                    ):
                        _require_supported_api_base(
                            row.provider, api_base_value, bound[0].type
                        )
            else:
                _reject_provider_level_api_base(row.provider, provided["api_base"])
                if isinstance(provided["api_base"], str):
                    provided["api_base"] = provided["api_base"].strip() or None

        kwargs = {}
        for key in ("priority", "is_active"):
            if key in provided and provided[key] is not None:
                kwargs[key] = provided[key]
        for key in ("remark", "api_base"):
            if key in provided:
                kwargs[key] = provided[key]
        if not kwargs and api_key is None:
            raise BusinessException("未提供任何可更新字段", BizCode.INVALID_PARAMETER)

        if api_key is not None:
            _require_wellformed_bedrock_credential(row.provider, api_key)
            if not row.model_names:
                await _validate_provider_key(
                    db, provider=row.provider, tenant_id=tenant_id, api_key=api_key
                )

        service = ChannelService(db)
        try:
            updated = row
            if kwargs:
                updated = service.update_attributes(row.id, **kwargs)
            if api_key is not None:
                updated = service.replace_credential(row.id, api_key=api_key)
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            raise BusinessException(
                "同凭据同端点的渠道已存在", BizCode.DUPLICATE_NAME
            ) from exc
        db.refresh(updated)
        return model_schema.ApiKeyItem.model_validate(describe_channel(updated))

    @staticmethod
    def delete_provider_key(db: Session, apikey_id: uuid.UUID, tenant_id: uuid.UUID) -> bool:
        """删凭据本体（不可恢复）；影响面（覆盖模型清单）由列表/详情前置提示。

        删除后（2026-09-15 决策）：该租户该供应商下候选链解析为空的启用模型同事务置停用
        （维持"启用 ⟹ 有可用渠道"不变式；公开模型不联动）。
        """
        row = ChannelApiKeyService._owned_channel(db, apikey_id, tenant_id)
        provider = _provider_value(row.provider)
        states: list[tuple[uuid.UUID | None, bool]] = []
        try:
            deleted = ChannelService(db).delete(apikey_id)
            if deleted:
                affected = ModelConfigRepository.list_active_tenant_provider_models(
                    db, provider=provider, tenant_id=tenant_id
                )
                states = _auto_disable_unresolvable(db, affected, tenant_id)
            db.commit()
        except Exception:
            # 探测可能已用删除后状态回填渠道快照缓存；回滚后须再失效，防 ≤TTL 的错判
            db.rollback()
            invalidate_channel_cache(tenant_id, provider)
            raise
        if states:
            _invalidate_model_option_states(*states)
        return deleted


__all__ = ["ChannelApiKeyService"]
