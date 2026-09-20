"""图片检索前置校验（业务侧，工作流知识库节点与 agent 应用共用）。

记忆熊知识库对「拿图片当 query」有一组硬性边界，命中即返回 400。业务侧在提交检索之前
用同一组判据先拦一次：用户在编排 / 调试阶段就能拿到具体原因，也不把注定失败的组合发给
下游。

判据逐条对齐 mem-knowledge 的 ``_validated_image_query`` / ``_validate_image_targets``
（``core/mem-knowledge/src/services/knowledge_retrieval.py``）：

    1. metadata_filter_mode=auto               -> KB_IMAGE_AUTO_METADATA_UNSUPPORTED
    2. retrieve_type=participle                -> KB_IMAGE_PARTICIPLE_UNSUPPORTED
    3. retrieve_type=graph                     -> KB_IMAGE_TARGET_CONFIG_UNSUPPORTED
    4. hybrid 叠加图谱召回                      -> KB_IMAGE_TARGET_CONFIG_UNSUPPORTED
    5. 有效向量模型非 qwen3-vl-embedding        -> KB_IMAGE_EMBEDDING_MODEL_UNSUPPORTED
    6. hybrid 无模型重排（加权打分 / 缺方案）    -> KB_IMAGE_HYBRID_RERANK_REQUIRED
    7. hybrid 有效重排模型非 qwen3-vl-rerank    -> KB_IMAGE_RERANK_MODEL_UNSUPPORTED
    8. 多知识库全局重排用加权打分                -> KB_IMAGE_WEIGHTED_GLOBAL_RERANK_UNSUPPORTED
    9. 多知识库全局重排模型非 qwen3-vl-rerank    -> KB_IMAGE_GLOBAL_RERANK_MODEL_UNSUPPORTED

两点口径约定：

- 文案与 ``mem-knowledge/src/locales/zh.json:116-123`` 逐字一致，这样前置拦与下游拦
  对用户呈现的是同一句话，排查时不会被两套措辞带偏。
- 模型能力判定复用 ``redbear_model`` 的 ``is_qwen3_vl_embedding`` / ``is_qwen3_vl_reranker``，
  喂进去的快照字段口径与 mem-knowledge 的模型 registry 一致（provider / type / name /
  capability，其中 name 即解析锚点名），因此两侧判定结果不会漂移。

命中模型类边界（#5/#7/#9）时，报错会带上「实际生效的模型记录」与「缺失的具体条件」
（provider / type / 名称 / vision 能力）——模型显示名看着对、但记录上没勾选「视觉」能力
是最常见的坑，只给一句「所选重排模型不支持图片检索」根本定位不到。

本模块只读「KB 绑定了哪个模型」用于判定，不承担授权职责：检索时的租户/空间校验仍在
下游完成，这里读到配置不构成越权。
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, Callable, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.error_codes import BizCode
from app.core.exceptions import BusinessException
from app.db import get_async_db_context
from app.models.knowledge_model import Knowledge
from app.models.models_model import ModelConfig
from app.schemas.chunk_schema import RetrieveType
from app.schemas.knowledge_metadata_schema import MetadataFilterMode
from app.schemas.rerank_schema import RerankMode

logger = logging.getLogger(__name__)

# 与 mem-knowledge/src/locales/zh.json:116-123 逐字一致。
_IMAGE_BOUNDARY_MESSAGES: dict[str, str] = {
    "KB_IMAGE_AUTO_METADATA_UNSUPPORTED": "图片检索不支持自动元数据筛选",
    "KB_IMAGE_PARTICIPLE_UNSUPPORTED": "图片检索不支持全文检索模式",
    "KB_IMAGE_TARGET_CONFIG_UNSUPPORTED": "图片检索目标配置不受支持",
    "KB_IMAGE_EMBEDDING_MODEL_UNSUPPORTED": "所选向量模型不支持图片检索，请选择兼容的图片向量模型",
    "KB_IMAGE_HYBRID_RERANK_REQUIRED": "图片混合检索需要模型重排",
    "KB_IMAGE_RERANK_MODEL_UNSUPPORTED": "所选重排模型不支持图片检索，请选择兼容的图片重排模型",
    "KB_IMAGE_WEIGHTED_GLOBAL_RERANK_UNSUPPORTED": "图片检索不支持全局加权重排",
    "KB_IMAGE_GLOBAL_RERANK_MODEL_UNSUPPORTED": "所选全局重排模型不支持图片检索，请选择兼容的图片重排模型",
}


@dataclass(frozen=True, slots=True)
class _ModelAbility:
    """能力判定所需的模型快照（有意不含凭据，判定不碰密钥）。"""

    model_config_id: str
    provider: Any
    model_type: Any
    model_name: str | None
    capabilities: tuple[Any, ...]


@dataclass(frozen=True, slots=True)
class _ImageTarget:
    """单个检索目标（一个知识库）在图片检索维度的关键配置。"""

    kb_id: str
    retrieve_type: RetrieveType
    graph_enabled: bool
    local_rerank_mode: RerankMode | None
    embedding: _ModelAbility | None
    reranker: _ModelAbility | None


@dataclass(frozen=True, slots=True)
class _RequestScope:
    """请求级重排配置（节点 / agent 传入，用于覆盖 KB 默认）。"""

    has_rerank_id: bool
    reranker: _ModelAbility | None
    rerank_mode: RerankMode | None


_SHARED_CHECKERS: (
    tuple[Callable[[Any], bool], Callable[[Any], bool]] | None
) = None

_SHARED_ENUMS: tuple[Any, Any, Any] | None = None

# 模型类边界的期望签名，与 redbear_model 的 is_qwen3_vl_embedding /
# is_qwen3_vl_reranker 逐条对应：(报错用名词, 模型名, 模型类型)。
_EMBEDDING_EXPECTATION = ("向量模型", "qwen3-vl-embedding", "embedding")
_RERANK_EXPECTATION = ("重排模型", "qwen3-vl-rerank", "rerank")
_GLOBAL_RERANK_EXPECTATION = ("全局重排模型", "qwen3-vl-rerank", "rerank")


def _shared_qwen3_vl_checkers() -> tuple[Callable[[Any], bool], Callable[[Any], bool]]:
    """取共享的多模态模型判定函数（延迟导入，避免拖慢 api 启动）。"""
    global _SHARED_CHECKERS
    if _SHARED_CHECKERS is None:
        from redbear_model.providers.dashscope import (
            is_qwen3_vl_embedding,
            is_qwen3_vl_reranker,
        )

        _SHARED_CHECKERS = (is_qwen3_vl_embedding, is_qwen3_vl_reranker)
    return _SHARED_CHECKERS


def _shared_enums() -> tuple[Any, Any, Any]:
    """取共享枚举 (ModelProvider, ModelType, ModelCapability)，用于生成缺失项说明。"""
    global _SHARED_ENUMS
    if _SHARED_ENUMS is None:
        from redbear_model import ModelCapability, ModelProvider, ModelType

        _SHARED_ENUMS = (ModelProvider, ModelType, ModelCapability)
    return _SHARED_ENUMS


def _value_of(value: Any) -> str | None:
    if value is None:
        return None
    text = str(getattr(value, "value", value)).strip()
    return text or None


def _field(source: Any, name: str) -> Any:
    """兼容 dict（agent 侧）与 pydantic 配置对象（节点侧）两种载荷。"""
    if source is None:
        return None
    if isinstance(source, dict):
        return source.get(name)
    return getattr(source, name, None)


def _as_uuid(value: Any) -> uuid.UUID | None:
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError, AttributeError):
        return None


def _as_retrieve_type(value: Any) -> RetrieveType | None:
    if isinstance(value, RetrieveType):
        return value
    text = _value_of(value)
    if text is None:
        return None
    try:
        return RetrieveType(text.lower())
    except ValueError:
        return None


def _as_rerank_mode(value: Any) -> RerankMode | None:
    if isinstance(value, RerankMode):
        return value
    text = _value_of(value)
    if text is None:
        return None
    try:
        return RerankMode(text.lower())
    except ValueError:
        return None


class _ImageRetrievalBoundary(Exception):
    """内部信号：命中一条不支持边界，由调用方决定抛错还是降级处理。"""

    def __init__(self, code: str, detail: str | None = None) -> None:
        message = _IMAGE_BOUNDARY_MESSAGES[code]
        self.code = code
        self.message = f"{message}（{detail}）" if detail else message
        super().__init__(self.message)


def _reject(code: str, *, detail: str | None = None) -> None:
    """命中一条不支持边界：记 info 日志并中断判定（第一命中即停）。"""
    logger.info(
        "image_retrieval_rejected code=%s detail=%s",
        code,
        detail or "-",
    )
    raise _ImageRetrievalBoundary(code, detail)


def _to_ability(row: Any) -> _ModelAbility | None:
    """DB 行 → 能力快照；枚举转换失败按「不可能匹配」处理，与 registry 同口径。"""
    from redbear_model import (
        ModelCapability as SharedModelCapability,
    )
    from redbear_model import (
        ModelProvider as SharedModelProvider,
    )
    from redbear_model import (
        ModelType as SharedModelType,
    )

    try:
        provider = SharedModelProvider(_value_of(row.provider))
    except (ValueError, TypeError):
        provider = None
    try:
        model_type = SharedModelType(_value_of(row.type))
    except (ValueError, TypeError):
        model_type = None

    capabilities = []
    for item in row.capability or []:
        try:
            capabilities.append(SharedModelCapability(_value_of(item)))
        except (ValueError, TypeError):
            # 未识别的能力标签直接跳过（与 mem-knowledge 的 _capabilities 一致）
            continue

    return _ModelAbility(
        model_config_id=str(row.id),
        provider=provider,
        model_type=model_type,
        model_name=row.name,
        capabilities=tuple(capabilities),
    )


def _ability_mismatch(
    ability: _ModelAbility,
    expectation: tuple[str, str, str],
) -> list[str]:
    """列出该模型记录不满足图片检索要求的点（空列表表示满足）。

    判定口径与 ``redbear_model.providers.dashscope.is_qwen3_vl_*`` 一致，这里只
    负责把不一致的地方逐条说清楚，供报错与日志定位。
    """
    _, expected_name, expected_type = expectation
    model_provider, model_type, model_capability = _shared_enums()
    try:
        expected_type_enum = model_type(expected_type)
    except ValueError:  # pragma: no cover - 共享枚举固定含 embedding/rerank
        expected_type_enum = None

    problems: list[str] = []
    if ability.provider is not model_provider.DASHSCOPE:
        problems.append(
            f"provider={_value_of(ability.provider) or '空'}≠dashscope"
        )
    if ability.model_type is not expected_type_enum:
        problems.append(f"type={_value_of(ability.model_type) or '空'}≠{expected_type}")
    if ability.model_name != expected_name:
        problems.append(f"名称={ability.model_name or '空'}≠{expected_name}")
    if model_capability.VISION not in ability.capabilities:
        current = ",".join(_value_of(item) or "?" for item in ability.capabilities) or "无"
        problems.append(f"未勾选「视觉」能力（当前能力：{current}）")
    return problems


def _probe(
    checker: Callable[[Any], bool],
    ability: _ModelAbility | None,
    *,
    expectation: tuple[str, str, str],
    source: str,
) -> tuple[bool, str]:
    """探测模型能力。

    Returns:
        (是否满足, 不满足时的人类可读原因)。原因里带上「来源」（知识库绑定 / 节点
        请求指定）与模型记录签名，避免用户只知道"模型不支持"却不知道被判的是哪条记录。
    """
    label = expectation[0]
    if ability is None:
        return False, f"{label}未绑定 / 不存在 / 已停用（来源：{source}）"
    try:
        satisfied = bool(
            checker(
                SimpleNamespace(
                    provider=ability.provider,
                    model_type=ability.model_type,
                    model_name=ability.model_name,
                    capabilities=ability.capabilities,
                )
            )
        )
    except Exception as exc:  # noqa: BLE001 - 判定异常不能拖垮检索前置校验
        logger.warning(
            "image_retrieval_model_probe_failed model_config_id=%s error=%s",
            ability.model_config_id,
            type(exc).__name__,
        )
        return False, f"{label}能力判定异常（{type(exc).__name__}，来源：{source}）"

    if satisfied:
        return True, ""

    problems = _ability_mismatch(ability, expectation)
    reason = "、".join(problems) if problems else "不满足多模态条件"
    return (
        False,
        f"{label}「{ability.model_name or '未命名'}」"
        f"(id={ability.model_config_id}) {reason}（来源：{source}）",
    )


async def _load_model_abilities(
    session: AsyncSession,
    model_ids: set[uuid.UUID],
) -> dict[uuid.UUID, _ModelAbility]:
    if not model_ids:
        return {}
    rows = (
        await session.execute(
            select(
                ModelConfig.id,
                ModelConfig.provider,
                ModelConfig.type,
                ModelConfig.name,
                ModelConfig.capability,
                ModelConfig.is_active,
            ).where(ModelConfig.id.in_(model_ids))
        )
    ).all()
    abilities: dict[uuid.UUID, _ModelAbility] = {}
    for row in rows:
        if not row.is_active:
            # 未激活的模型等同于「不可用」，交由调用方按边界提示，不静默放行
            logger.info(
                "image_retrieval_model_inactive model_config_id=%s",
                row.id,
            )
            continue
        ability = _to_ability(row)
        if ability is not None:
            abilities[row.id] = ability
    return abilities


async def _load_targets_with_session(
    session: AsyncSession,
    *,
    kb_ids: Sequence[uuid.UUID],
    knowledge_bases: Sequence[Any] | None,
    retrieve_type: Any,
    rerank_id: Any,
    rerank_mode: Any,
    enable_graph_retrieval: Any,
) -> tuple[list[_ImageTarget], _RequestScope]:
    knowledge_rows = (
        await session.execute(
            select(
                Knowledge.id,
                Knowledge.embedding_id,
                Knowledge.reranker_id,
            ).where(Knowledge.id.in_(list(kb_ids)))
        )
    ).all()
    kb_rows = {row.id: row for row in knowledge_rows}

    # 每个 KB 的检索配置以「节点/agent 传进来的配置」为准，顺序与 kb_ids 对齐
    config_by_kb: dict[uuid.UUID, Any] = {}
    for item in knowledge_bases or []:
        kb_uuid = _as_uuid(_field(item, "kb_id"))
        if kb_uuid is not None:
            config_by_kb.setdefault(kb_uuid, item)

    request_rerank_uuid = _as_uuid(rerank_id)
    model_ids: set[uuid.UUID] = set()
    for row in knowledge_rows:
        if row.embedding_id:
            model_ids.add(row.embedding_id)
        if row.reranker_id:
            model_ids.add(row.reranker_id)
    if request_rerank_uuid:
        model_ids.add(request_rerank_uuid)
    abilities = await _load_model_abilities(session, model_ids)

    request_retrieve_type = _as_retrieve_type(retrieve_type)
    request_graph_enabled = bool(int(enable_graph_retrieval or 0) == 1)

    targets: list[_ImageTarget] = []
    for kb_uuid in kb_ids:
        row = kb_rows.get(kb_uuid)
        config = config_by_kb.get(kb_uuid)
        kb_retrieve_type = (
            _as_retrieve_type(_field(config, "retrieve_type"))
            or request_retrieve_type
            or RetrieveType.PARTICIPLE
        )
        graph_value = _field(config, "enable_graph_retrieval")
        graph_enabled = (
            bool(int(graph_value) == 1)
            if graph_value is not None
            else request_graph_enabled
        )
        targets.append(
            _ImageTarget(
                kb_id=str(kb_uuid),
                retrieve_type=kb_retrieve_type,
                graph_enabled=graph_enabled,
                local_rerank_mode=_as_rerank_mode(_field(config, "rerank_mode")),
                embedding=abilities.get(row.embedding_id) if row else None,
                reranker=abilities.get(row.reranker_id) if row else None,
            )
        )

    return targets, _RequestScope(
        has_rerank_id=request_rerank_uuid is not None,
        reranker=abilities.get(request_rerank_uuid) if request_rerank_uuid else None,
        rerank_mode=_as_rerank_mode(rerank_mode),
    )


async def _load_targets(
    *,
    kb_ids: Sequence[uuid.UUID],
    knowledge_bases: Sequence[Any] | None,
    retrieve_type: Any,
    rerank_id: Any,
    rerank_mode: Any,
    enable_graph_retrieval: Any,
    db: AsyncSession | None,
) -> tuple[list[_ImageTarget], _RequestScope]:
    if db is not None:
        return await _load_targets_with_session(
            db,
            kb_ids=kb_ids,
            knowledge_bases=knowledge_bases,
            retrieve_type=retrieve_type,
            rerank_id=rerank_id,
            rerank_mode=rerank_mode,
            enable_graph_retrieval=enable_graph_retrieval,
        )
    async with get_async_db_context() as session:
        return await _load_targets_with_session(
            session,
            kb_ids=kb_ids,
            knowledge_bases=knowledge_bases,
            retrieve_type=retrieve_type,
            rerank_id=rerank_id,
            rerank_mode=rerank_mode,
            enable_graph_retrieval=enable_graph_retrieval,
        )


def _validate_target(
    target: _ImageTarget,
    scope: _RequestScope,
    *,
    target_count: int,
) -> None:
    kb_label = f"知识库 {target.kb_id}"

    # 边界 2/3/4：只有语义与混合两种模式支持图片 query
    if target.retrieve_type is not RetrieveType.SEMANTIC and (
        target.retrieve_type is not RetrieveType.HYBRID
    ):
        code = (
            "KB_IMAGE_PARTICIPLE_UNSUPPORTED"
            if target.retrieve_type is RetrieveType.PARTICIPLE
            else "KB_IMAGE_TARGET_CONFIG_UNSUPPORTED"
        )
        _reject(code, detail=kb_label)
    if target.graph_enabled:
        _reject("KB_IMAGE_TARGET_CONFIG_UNSUPPORTED", detail=kb_label)

    embedding_checker, rerank_checker = _shared_qwen3_vl_checkers()

    # 边界 5：每个目标的向量模型都必须是多模态嵌入模型
    embedding_ok, embedding_reason = _probe(
        embedding_checker,
        target.embedding,
        expectation=_EMBEDDING_EXPECTATION,
        source="知识库绑定",
    )
    if not embedding_ok:
        _reject(
            "KB_IMAGE_EMBEDDING_MODEL_UNSUPPORTED",
            detail=f"{kb_label}：{embedding_reason}",
        )

    if target.retrieve_type is not RetrieveType.HYBRID:
        return

    # 边界 6：混合检索必须走模型重排（KB 配置优先，单目标时才看请求级）
    local_mode = target.local_rerank_mode
    if local_mode is None and target_count == 1:
        local_mode = scope.rerank_mode
    if local_mode is RerankMode.WEIGHTED_SCORE:
        _reject(
            "KB_IMAGE_HYBRID_RERANK_REQUIRED",
            detail=f"{kb_label}：混合检索的重排模式为加权打分",
        )

    # 边界 7：与知识库侧 ``_target_reranker_required`` 对齐——单目标混合检索且请求
    # 指定了重排模型时，局部重排用请求/节点指定的模型（覆盖知识库默认）；其余情况
    # 用知识库绑定的重排模型。
    uses_request_reranker = target_count == 1 and scope.has_rerank_id
    effective_reranker = scope.reranker if uses_request_reranker else target.reranker
    rerank_ok, rerank_reason = _probe(
        rerank_checker,
        effective_reranker,
        expectation=_RERANK_EXPECTATION,
        source="节点/请求指定" if uses_request_reranker else "知识库绑定",
    )
    if not rerank_ok:
        _reject(
            "KB_IMAGE_RERANK_MODEL_UNSUPPORTED",
            detail=f"{kb_label}：{rerank_reason}",
        )


def _validate_global_rerank(targets: Sequence[_ImageTarget], scope: _RequestScope) -> None:
    """边界 8/9：多知识库时还会叠加一层全局重排。"""
    mode = scope.rerank_mode or RerankMode.RERANKING_MODEL
    if mode is RerankMode.WEIGHTED_SCORE:
        _reject(
            "KB_IMAGE_WEIGHTED_GLOBAL_RERANK_UNSUPPORTED",
            detail="多知识库全局重排使用加权打分",
        )
    _, rerank_checker = _shared_qwen3_vl_checkers()
    uses_request_reranker = scope.has_rerank_id
    effective_reranker = scope.reranker if uses_request_reranker else targets[0].reranker
    rerank_ok, rerank_reason = _probe(
        rerank_checker,
        effective_reranker,
        expectation=_GLOBAL_RERANK_EXPECTATION,
        source=(
            "节点/请求指定"
            if uses_request_reranker
            else f"知识库绑定（知识库 {targets[0].kb_id}）"
        ),
    )
    if not rerank_ok:
        _reject(
            "KB_IMAGE_GLOBAL_RERANK_MODEL_UNSUPPORTED",
            detail=rerank_reason,
        )


async def _evaluate_image_retrieval_boundaries(
    *,
    kb_ids: Sequence[Any],
    knowledge_bases: Sequence[Any] | None,
    retrieve_type: Any,
    rerank_id: Any,
    rerank_mode: Any,
    enable_graph_retrieval: Any,
    metadata_filter_mode: Any,
    db: AsyncSession | None,
) -> None:
    """执行全部边界判定；命中即抛 ``_ImageRetrievalBoundary``。"""
    normalized_kb_ids = [item for item in (_as_uuid(kb) for kb in kb_ids) if item]
    if not normalized_kb_ids:
        return

    # 边界 1：图片 query 不做基于 LLM 的自动元数据过滤
    if _value_of(metadata_filter_mode) == MetadataFilterMode.AUTO.value:
        _reject("KB_IMAGE_AUTO_METADATA_UNSUPPORTED")

    targets, scope = await _load_targets(
        kb_ids=normalized_kb_ids,
        knowledge_bases=knowledge_bases,
        retrieve_type=retrieve_type,
        rerank_id=rerank_id,
        rerank_mode=rerank_mode,
        enable_graph_retrieval=enable_graph_retrieval,
        db=db,
    )
    if not targets:
        return

    for target in targets:
        _validate_target(target, scope, target_count=len(targets))
    if len(targets) > 1:
        _validate_global_rerank(targets, scope)

    logger.debug("image_retrieval_precheck_passed kb_count=%s", len(targets))


async def check_image_retrieval(
    *,
    kb_ids: Sequence[Any],
    knowledge_bases: Sequence[Any] | None = None,
    retrieve_type: Any = None,
    rerank_id: Any = None,
    rerank_mode: Any = None,
    enable_graph_retrieval: Any = None,
    metadata_filter_mode: Any = None,
    db: AsyncSession | None = None,
) -> str | None:
    """判定图片检索是否命中不支持边界，命中返回用户可见原因，通过返回 ``None``。

    适合「图片检索失败不应中断主流程」的调用方——例如 agent 工具调用，图片检索
    不可用时降级为文本检索，并把原因反馈给模型。

    Args:
        kb_ids: 本次检索命中的知识库 ID（顺序即目标顺序）。
        knowledge_bases: 每个 KB 的检索配置（dict 或 KnowledgeBaseConfig），
            缺省时回退到请求级参数。
        retrieve_type: 请求级检索模式（通常取第一个 KB 的配置）。
        rerank_id: 请求级重排模型 ID，存在时覆盖 KB 默认重排模型。
        rerank_mode: 请求级重排模式（hybrid 且单目标时生效）。
        enable_graph_retrieval: 请求级图谱召回开关（1 开启）。
        metadata_filter_mode: 元数据过滤模式，``auto`` 与图片 query 互斥。
        db: 已有会话；不传则内部开一个短会话（用完即关，不参与调用方事务）。
    """
    try:
        await _evaluate_image_retrieval_boundaries(
            kb_ids=kb_ids,
            knowledge_bases=knowledge_bases,
            retrieve_type=retrieve_type,
            rerank_id=rerank_id,
            rerank_mode=rerank_mode,
            enable_graph_retrieval=enable_graph_retrieval,
            metadata_filter_mode=metadata_filter_mode,
            db=db,
        )
    except _ImageRetrievalBoundary as boundary:
        return boundary.message
    except Exception as exc:  # noqa: BLE001 - 预检本身不应成为新的失败点
        # 预检是「提前给出原因」的优化，不是新的失败边界：读库/枚举异常时放行，
        # 由下游的策略探测与检索校验兜底，避免把本可成功的检索拦死。
        logger.warning(
            "image_retrieval_precheck_failed error=%s",
            type(exc).__name__,
            exc_info=True,
        )
        return None
    return None


async def ensure_image_retrieval_supported(
    *,
    kb_ids: Sequence[Any],
    knowledge_bases: Sequence[Any] | None = None,
    retrieve_type: Any = None,
    rerank_id: Any = None,
    rerank_mode: Any = None,
    enable_graph_retrieval: Any = None,
    metadata_filter_mode: Any = None,
    db: AsyncSession | None = None,
) -> None:
    """图片检索前置校验：命中任一不支持边界即抛 ``BusinessException``。

    参数含义见 :func:`check_image_retrieval`。适合工作流节点这类「配置不合法就该
    直接失败并提示用户」的调用方。
    """
    reason = await check_image_retrieval(
        kb_ids=kb_ids,
        knowledge_bases=knowledge_bases,
        retrieve_type=retrieve_type,
        rerank_id=rerank_id,
        rerank_mode=rerank_mode,
        enable_graph_retrieval=enable_graph_retrieval,
        metadata_filter_mode=metadata_filter_mode,
        db=db,
    )
    if reason is not None:
        raise BusinessException(reason, BizCode.INVALID_PARAMETER)


__all__ = ["check_image_retrieval", "ensure_image_retrieval_supported"]
