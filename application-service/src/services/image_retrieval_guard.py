"""Image retrieval pre-check (business side, shared by workflow knowledge-base nodes and agent apps).

The MemoryBear knowledge base enforces a set of hard boundaries for "image as query"; any hit
returns 400. The business side applies the same criteria before submitting retrieval, so users
get the exact reason during orchestration / debugging and combinations doomed to fail never
reach downstream.

The criteria mirror mem-knowledge's ``_validated_image_query`` / ``_validate_image_targets``
(``core/mem-knowledge/src/services/knowledge_retrieval.py``) one by one:

    1. metadata_filter_mode=auto               -> KB_IMAGE_AUTO_METADATA_UNSUPPORTED
    2. retrieve_type=participle                -> KB_IMAGE_PARTICIPLE_UNSUPPORTED
    3. retrieve_type=graph                     -> KB_IMAGE_TARGET_CONFIG_UNSUPPORTED
    4. hybrid combined with graph retrieval    -> KB_IMAGE_TARGET_CONFIG_UNSUPPORTED
    5. effective embedding model is not qwen3-vl-embedding
                                               -> KB_IMAGE_EMBEDDING_MODEL_UNSUPPORTED
    6. hybrid without model rerank (weighted score / missing mode)
                                               -> KB_IMAGE_HYBRID_RERANK_REQUIRED
    7. effective hybrid rerank model is not qwen3-vl-rerank
                                               -> KB_IMAGE_RERANK_MODEL_UNSUPPORTED
    8. multi-KB global rerank uses weighted score
                                               -> KB_IMAGE_WEIGHTED_GLOBAL_RERANK_UNSUPPORTED
    9. multi-KB global rerank model is not qwen3-vl-rerank
                                               -> KB_IMAGE_GLOBAL_RERANK_MODEL_UNSUPPORTED

Two conventions:

- Messages are word-for-word identical to ``mem-knowledge/src/locales/zh.json:116-123``, so the
  pre-check and the downstream check show users the same sentence and debugging is not misled
  by two different wordings.
- Model ability checks reuse ``redbear_model``'s ``is_qwen3_vl_embedding`` / ``is_qwen3_vl_reranker``.
  The snapshot fed in is isomorphic to the contract v2 ``ResolvedModelConfig`` decision surface
  (provider / model_name / profile): profile is derived from the model row via
  ``ModelProfile.from_stored_fields`` (the three new columns input/output_modalities and
  features win; empty values fall back to the legacy capability column), matching the api-side
  model_service, so the two sides cannot drift.

When a model-class boundary (#5/#7/#9) is hit, the error carries the "model record actually in
effect" and the "specific missing conditions" (provider / type / name / input modalities) --
the most common pitfall is a model whose display name looks right while its recorded input
modalities exclude images, which a bare "所选重排模型不支持图片检索" cannot pinpoint.

This module only reads which model a KB is bound to for the purpose of the check and takes no
authorization responsibility: tenant/space validation at retrieval time still happens
downstream, so reading the config here does not constitute privilege escalation.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, Callable, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..errors import BizCode
from ..errors import BusinessException
from ..db import get_async_db_context
from ..models.knowledge_model import Knowledge
from ..models.models_model import ModelConfig
from ..schemas.chunk_schema import RetrieveType
from ..schemas.knowledge_metadata_schema import MetadataFilterMode
from ..schemas.rerank_schema import RerankMode

logger = logging.getLogger(__name__)

# Word-for-word identical to mem-knowledge/src/locales/zh.json:116-123.
_IMAGE_BOUNDARY_MESSAGES: dict[str, str] = {
    "KB_IMAGE_AUTO_METADATA_UNSUPPORTED": "图片检索不支持自动元数据筛选",
    "KB_IMAGE_PARTICIPLE_UNSUPPORTED": "图片检索不支持全文检索模式",
    "KB_IMAGE_TARGET_CONFIG_UNSUPPORTED": "图片检索仅支持语义检索和混合检索，且不能开启图谱召回",
    "KB_IMAGE_EMBEDDING_MODEL_UNSUPPORTED": "所选向量模型不支持图片检索，请选择兼容的图片向量模型",
    "KB_IMAGE_HYBRID_RERANK_REQUIRED": "图片混合检索需要模型重排",
    "KB_IMAGE_RERANK_MODEL_UNSUPPORTED": "所选重排模型不支持图片检索，请选择兼容的图片重排模型",
    "KB_IMAGE_WEIGHTED_GLOBAL_RERANK_UNSUPPORTED": "图片检索不支持全局加权重排",
    "KB_IMAGE_GLOBAL_RERANK_MODEL_UNSUPPORTED": "所选全局重排模型不支持图片检索，请选择兼容的图片重排模型",
}


@dataclass(frozen=True, slots=True)
class _ModelAbility:
    """Model snapshot needed for ability checks (deliberately credential-free; checks never touch keys).

    ``profile`` is isomorphic to contract v2 ``ResolvedModelConfig.profile`` and is the only
    ability carrier read by the ``is_qwen3_vl_*`` checkers (decision surface: profile.type /
    profile.input_modalities).
    """

    model_config_id: str
    provider: Any
    model_name: str | None
    profile: Any


@dataclass(frozen=True, slots=True)
class _ImageTarget:
    """Key image-retrieval configuration of a single retrieval target (one knowledge base)."""

    kb_id: str
    retrieve_type: RetrieveType
    graph_enabled: bool
    local_rerank_mode: RerankMode | None
    embedding: _ModelAbility | None
    reranker: _ModelAbility | None


@dataclass(frozen=True, slots=True)
class _RequestScope:
    """Request-level rerank configuration (passed by node / agent, overrides KB defaults)."""

    has_rerank_id: bool
    reranker: _ModelAbility | None
    rerank_mode: RerankMode | None


_SHARED_CHECKERS: (
    tuple[Callable[[Any], bool], Callable[[Any], bool]] | None
) = None

_SHARED_ENUMS: tuple[Any, Any, Any] | None = None

# Expected signature of each model-class boundary, matching redbear_model's
# is_qwen3_vl_embedding / is_qwen3_vl_reranker one-to-one: (noun used in errors, model name,
# model type).
_EMBEDDING_EXPECTATION = ("向量模型", "qwen3-vl-embedding", "embedding")
_RERANK_EXPECTATION = ("重排模型", "qwen3-vl-rerank", "rerank")
_GLOBAL_RERANK_EXPECTATION = ("全局重排模型", "qwen3-vl-rerank", "rerank")


def _shared_qwen3_vl_checkers() -> tuple[Callable[[Any], bool], Callable[[Any], bool]]:
    """Load the shared multimodal model checkers (lazy import, so api startup is not slowed)."""
    global _SHARED_CHECKERS
    if _SHARED_CHECKERS is None:
        from redbear_model.providers.dashscope import (
            is_qwen3_vl_embedding,
            is_qwen3_vl_reranker,
        )

        _SHARED_CHECKERS = (is_qwen3_vl_embedding, is_qwen3_vl_reranker)
    return _SHARED_CHECKERS


def _shared_enums() -> tuple[Any, Any, Any]:
    """Load the shared enums (ModelProvider, ModelType, Modality) used to describe missing items."""
    global _SHARED_ENUMS
    if _SHARED_ENUMS is None:
        from redbear_model import Modality, ModelProvider, ModelType

        _SHARED_ENUMS = (ModelProvider, ModelType, Modality)
    return _SHARED_ENUMS


def _value_of(value: Any) -> str | None:
    if value is None:
        return None
    text = str(getattr(value, "value", value)).strip()
    return text or None


def _field(source: Any, name: str) -> Any:
    """Handle both payload shapes: dict (agent side) and pydantic config object (node side)."""
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
    """Internal signal: an unsupported boundary was hit; the caller decides whether to raise or degrade."""

    def __init__(self, code: str, detail: str | None = None) -> None:
        message = _IMAGE_BOUNDARY_MESSAGES[code]
        self.code = code
        self.message = f"{message}（{detail}）" if detail else message
        super().__init__(self.message)


def _reject(code: str, *, detail: str | None = None) -> None:
    """Record an unsupported-boundary hit: log at info level and abort evaluation (stop at first hit)."""
    logger.info(
        "image_retrieval_rejected code=%s detail=%s",
        code,
        detail or "-",
    )
    raise _ImageRetrievalBoundary(code, detail)


def _to_ability(row: Any) -> _ModelAbility | None:
    """DB row -> ability snapshot; enum/profile construction failures count as "can never match",
    same criteria as the registry.

    profile derivation matches the api-side model_service and goes through
    ``ModelProfile.from_stored_fields``: the three new columns (input/output_modalities,
    features) are read when non-empty; empty values fall back to the legacy capability column.
    """
    from redbear_model import (
        ModelProfile as SharedModelProfile,
    )
    from redbear_model import (
        ModelProvider as SharedModelProvider,
    )

    try:
        provider = SharedModelProvider(_value_of(row.provider))
    except (ValueError, TypeError):
        provider = None

    try:
        profile = SharedModelProfile.from_stored_fields(
            model_id=row.id,
            tenant_id=None,
            type=row.type,
            provider=row.provider,
            input_modalities=tuple(row.input_modalities or ()),
            output_modalities=tuple(row.output_modalities or ()),
            features=tuple(row.features or ()),
            capabilities=tuple(row.capability or ()),
            is_omni=bool(getattr(row, "is_omni", False)),
        )
    except (ValueError, TypeError):
        return None

    return _ModelAbility(
        model_config_id=str(row.id),
        provider=provider,
        model_name=row.name,
        profile=profile,
    )


def _ability_mismatch(
    ability: _ModelAbility,
    expectation: tuple[str, str, str],
) -> list[str]:
    """List the points where this model record fails the image-retrieval requirements (empty list
    means satisfied).

    The criteria match ``redbear_model.providers.dashscope.is_qwen3_vl_*``; this helper only
    spells out each mismatch so errors and logs can pinpoint it.
    """
    _, expected_name, expected_type = expectation
    model_provider, model_type, modality = _shared_enums()
    profile = ability.profile
    try:
        expected_type_enum = model_type(expected_type)
    except ValueError:  # pragma: no cover - shared enums always contain embedding/rerank
        expected_type_enum = None

    problems: list[str] = []
    if ability.provider is not model_provider.DASHSCOPE:
        problems.append(
            f"provider={_value_of(ability.provider) or '空'}≠dashscope"
        )
    if profile.type is not expected_type_enum:
        problems.append(f"type={_value_of(profile.type) or '空'}≠{expected_type}")
    if ability.model_name != expected_name:
        problems.append(f"名称={ability.model_name or '空'}≠{expected_name}")
    if modality.IMAGE not in profile.input_modalities:
        current = (
            ",".join(_value_of(item) or "?" for item in profile.input_modalities)
            or "无"
        )
        problems.append(f"输入模态不含图片（当前：{current}）")
    return problems


def _probe(
    checker: Callable[[Any], bool],
    ability: _ModelAbility | None,
    *,
    expectation: tuple[str, str, str],
    source: str,
) -> tuple[bool, str]:
    """Probe a model's ability.

    Returns:
        (satisfied, human-readable reason when not satisfied). The reason carries the "source"
        (KB-bound / node or request specified) and the model record signature, so users do not
        merely learn that "the model is unsupported" without knowing which record was judged.
    """
    label = expectation[0]
    if ability is None:
        return False, f"{label}未绑定 / 不存在 / 已停用（来源：{source}）"
    try:
        satisfied = bool(
            checker(
                SimpleNamespace(
                    provider=ability.provider,
                    model_name=ability.model_name,
                    profile=ability.profile,
                )
            )
        )
    except Exception as exc:  # noqa: BLE001 - a probe failure must not break retrieval pre-check
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
                ModelConfig.is_omni,
                ModelConfig.input_modalities,
                ModelConfig.output_modalities,
                ModelConfig.features,
                ModelConfig.is_active,
            ).where(ModelConfig.id.in_(model_ids))
        )
    ).all()
    abilities: dict[uuid.UUID, _ModelAbility] = {}
    for row in rows:
        if not row.is_active:
            # An inactive model counts as unavailable: let the caller surface a boundary hint,
            # never pass silently
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

    # Each KB's retrieval config comes from the node/agent payload; order follows kb_ids
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

    # Boundaries 2/3/4: only the semantic and hybrid modes support image queries
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

    # Boundary 5: every target's embedding model must be the multimodal embedding model
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

    # Boundary 6: hybrid retrieval must use model rerank (KB config wins; the request level is
    # consulted only with a single target)
    local_mode = target.local_rerank_mode
    if local_mode is None and target_count == 1:
        local_mode = scope.rerank_mode
    if local_mode is RerankMode.WEIGHTED_SCORE:
        _reject(
            "KB_IMAGE_HYBRID_RERANK_REQUIRED",
            detail=f"{kb_label}：混合检索的重排模式为加权打分",
        )

    # Boundary 7: mirrors the knowledge base side ``_target_reranker_required`` -- for a
    # single-target hybrid retrieval with a request-specified rerank model, local rerank uses
    # the request/node-specified model (overriding the KB default); otherwise the KB-bound
    # rerank model is used.
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
    """Boundaries 8/9: multi-KB retrieval adds a global rerank layer on top."""
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


async def _expand_to_leaf_ids(
    kb_ids: Sequence[uuid.UUID],
    *,
    db: AsyncSession | None,
) -> list[uuid.UUID]:
    """Expand folder-type knowledge bases into retrievable leaf knowledge base IDs
    (deduplicated, order preserved).

    Expansion criteria match knowledge_retrieval_preparation._expand_knowledge_to_leaf_refs_async:
    a leaf is a non-folder with a valid status; folders are expanded recursively by parent_id,
    skipping cycles.
    """

    async def _expand(session: AsyncSession) -> list[uuid.UUID]:
        result = await session.execute(
            select(
                Knowledge.id,
                Knowledge.type,
                Knowledge.parent_id,
                Knowledge.status,
                Knowledge.chunk_num,
            ).where(Knowledge.id.in_(list(kb_ids)))
        )
        rows = {row.id: row for row in result.all()}

        ordered: list[uuid.UUID] = []
        seen: set[uuid.UUID] = set()
        visited_folders: set[uuid.UUID] = set()

        async def _descend(node_id: uuid.UUID) -> None:
            row = rows.get(node_id)
            if row is None or row.status != 1 or node_id in seen:
                return
            is_folder = row.type == "Folder"
            if not is_folder:
                seen.add(node_id)
                ordered.append(node_id)
                return
            if node_id in visited_folders:
                return
            visited_folders.add(node_id)
            child_result = await session.execute(
                select(
                    Knowledge.id,
                    Knowledge.type,
                    Knowledge.parent_id,
                    Knowledge.status,
                    Knowledge.chunk_num,
                ).where(Knowledge.parent_id == node_id)
            )
            for child in child_result.all():
                rows.setdefault(child.id, child)
                await _descend(child.id)

        for node_id in kb_ids:
            await _descend(node_id)
        return ordered

    if db is not None:
        return await _expand(db)
    async with get_async_db_context() as session:
        return await _expand(session)


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
    """Run all boundary evaluations; raises ``_ImageRetrievalBoundary`` on any hit."""
    normalized_kb_ids = [item for item in (_as_uuid(kb) for kb in kb_ids) if item]
    if not normalized_kb_ids:
        return

    # Expand folder-type knowledge bases to leaf KBs first. A folder carries no chunks itself;
    # downstream retrieval recursively expands it into its sub-KBs, and the image ability hit
    # depends on the model bound to each leaf. Judging with the folder ID directly would
    # pre-empt using the folder-level (possibly vision-incapable) model and leave sub-KBs that
    # do have a vision model never evaluated.
    leaf_kb_ids = await _expand_to_leaf_ids(normalized_kb_ids, db=db)
    if not leaf_kb_ids:
        return

    # Boundary 1: image queries skip LLM-based automatic metadata filtering
    if _value_of(metadata_filter_mode) == MetadataFilterMode.AUTO.value:
        _reject("KB_IMAGE_AUTO_METADATA_UNSUPPORTED")

    targets, scope = await _load_targets(
        kb_ids=leaf_kb_ids,
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
    """Check whether image retrieval hits an unsupported boundary; returns the user-visible
    reason on a hit and ``None`` when it passes.

    Suitable for callers where "image retrieval failure must not interrupt the main flow" --
    for example agent tool calls, which degrade to text retrieval when image retrieval is
    unavailable and feed the reason back to the model.

    Args:
        kb_ids: Knowledge base IDs hit by this retrieval (order defines target order).
        knowledge_bases: Retrieval config per KB (dict or KnowledgeBaseConfig); when absent,
            falls back to the request-level parameters.
        retrieve_type: Request-level retrieval mode (usually the first KB's config).
        rerank_id: Request-level rerank model ID; when present, overrides the KB default
            rerank model.
        rerank_mode: Request-level rerank mode (effective for hybrid with a single target).
        enable_graph_retrieval: Request-level graph retrieval switch (1 = on).
        metadata_filter_mode: Metadata filter mode; ``auto`` is mutually exclusive with image
            queries.
        db: Existing session; when omitted, a short internal session is opened (closed right
            after use, does not join the caller's transaction).
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
    except Exception as exc:  # noqa: BLE001 - the pre-check itself must not become a new failure point
        # The pre-check is an optimization that surfaces reasons early, not a new failure
        # boundary: on DB/enum errors it lets the request through and leaves coverage to
        # downstream policy probing and retrieval validation, instead of blocking a retrieval
        # that could have succeeded.
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
    """Image retrieval pre-check: raises ``BusinessException`` when any unsupported boundary is hit.

    Parameter meanings are documented in :func:`check_image_retrieval`. Suitable for callers
    such as workflow nodes, where an invalid configuration should fail immediately and tell
    the user.
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


__all__ = [
    "check_image_retrieval",
    "ensure_image_retrieval_supported",
    "expand_knowledge_to_leaf_ids",
]
expand_knowledge_to_leaf_ids = _expand_to_leaf_ids
