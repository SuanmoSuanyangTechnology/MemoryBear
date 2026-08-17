import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any, List, Tuple

from pydantic import BaseModel, Field

from app.core.memory.pipelines.base_pipeline import ModelClientMixin
from app.core.validators.memory_config_validators import validate_and_resolve_model_id
from app.db import get_db_context
from app.repositories.memory_config_repository import MemoryConfigRepository
from app.repositories.neo4j.neo4j_connector import Neo4jConnector
from app.repositories.neo4j.statement_repository import StatementRepository
from app.services.memory_config_service import (
    MemoryConfigService,
    _effective_workspace_models,
    _get_default_model_preset,
)

logger = logging.getLogger(__name__)

INTEREST_DISTRIBUTION_LLM_TIMEOUT_SECONDS = 30
INTEREST_DISTRIBUTION_TOTAL_TIMEOUT_SECONDS = 45
INTEREST_DISTRIBUTION_MIN_FALLBACK_TIMEOUT_SECONDS = 5
INTEREST_CANDIDATE_LIMIT = 40
INTEREST_DISPLAY_LIMIT = 5
INTEREST_EVIDENCE_ENTITY_LIMIT = 12
INTEREST_EVIDENCE_PER_ENTITY_LIMIT = 2
INTEREST_EVIDENCE_LIMIT = 20
INTEREST_EVIDENCE_MAX_CHARS = 300
INTEREST_EXCLUDED_NAMES = [
    "用户",
    "user",
    "ai",
    "ai助手",
    "助手",
    "assistant",
    "ai回复",
    "系统",
    "system",
    "时间戳",
    "timestamp",
]
INTEREST_ISO_DATETIME_PATTERN = (
    r"(?i)^\d{4}-\d{2}-\d{2}[t ]\d{2}:\d{2}:\d{2}"
    r"(?:\.\d+)?(?:z|[+-]\d{2}:?\d{2})?$"
)
INTEREST_UNIX_TIMESTAMP_PATTERN = r"^\d{10}(?:\d{3})?$"


# 定义用于LLM结构化输出的Pydantic模型
class FilteredTags(BaseModel):
    """用于接收LLM筛选后的核心标签列表的模型。"""
    meaningful_tags: List[str] = Field(..., description="从原始列表中筛选出的具有核心代表意义的名词列表。")


class InterestEntityCandidate(BaseModel):
    """带真实 Statement 频次的兴趣候选实体。"""

    entity_id: str
    name: str
    entity_type: str = ""
    frequency: int = Field(..., ge=1)


class InterestStatementEvidence(BaseModel):
    """候选实体关联的 Statement 语境证据。"""

    statement_id: str
    entity_ids: List[str]
    statement_text: str


class InterestGroup(BaseModel):
    """LLM 归纳出的兴趣及其候选来源。"""

    name: str
    entity_ids: List[str]


class InterestTags(BaseModel):
    """用于接收 LLM 归纳后的兴趣活动列表。"""

    interests: List[InterestGroup] = Field(default_factory=list)


class InterestDistributionGeneration(BaseModel):
    """兴趣分布生成结果及其缓存决策。"""

    items: List[Tuple[str, int]] = Field(default_factory=list)
    cacheable: bool = True

#增加一个只校验llm的薄封装，不应该校验功能以外的模型。
def _get_interest_llm_client(db: Any, end_user_id: str):
    """只解析兴趣分布需要的 LLM，不校验其他工作空间模型。"""
    config_service = MemoryConfigService(db)
    config_id = config_service.get_config_id_by_end_user(end_user_id)
    if not config_id:
        raise ValueError(
            f"No memory_config_id found for end_user_id: {end_user_id}."
        )

    config_with_workspace = MemoryConfigRepository(db).get_config_with_workspace(
        config_id
    )
    if not config_with_workspace:
        raise ValueError(f"Memory configuration not found: {config_id}")

    _, workspace = config_with_workspace
    preset = (
        _get_default_model_preset(db)
        if workspace.is_default_config
        else None
    )
    llm_model_id = _effective_workspace_models(workspace, preset)["llm"]
    llm_uuid, _ = validate_and_resolve_model_id(
        llm_model_id,
        "llm",
        db,
        workspace.tenant_id,
        required=True,
        config_id=config_id,
        workspace_id=workspace.id,
    )
    return ModelClientMixin.get_llm_client(
        db,
        llm_uuid,
        workspace.tenant_id,
    )


async def filter_tags_with_llm(tags: List[str], end_user_id: str) -> List[str]:
    """
    使用LLM筛选标签列表，仅保留具有代表性的核心名词。
    
    Args:
        tags: 原始标签列表
        end_user_id: 用户组ID，用于获取配置
        
    Returns:
        筛选后的标签列表
        
    Raises:
        ValueError: 如果无法获取有效的LLM配置
    """
    try:
        # Get config_id using get_end_user_connected_config
        with get_db_context() as db:
            config_service = MemoryConfigService(db)
            config_id = config_service.get_config_id_by_end_user(end_user_id)

            if not config_id:
                raise ValueError(
                    f"No memory_config_id found for end_user_id: {end_user_id}. "
                    "Please ensure the user has a valid memory configuration."
                )

            # Use the config_id to get the proper LLM client with workspace fallback

            memory_config = config_service.load_memory_config(
                config_id=config_id
            )

       
            llm_client = ModelClientMixin.get_llm_client(
                db, memory_config.llm_model_id, memory_config.tenant_id
            )

        # 3. 构建Prompt
        tag_list_str = ", ".join(tags)
        messages = [
            {
                "role": "system",
                "content": "你是一位顶级的文本分析专家，任务是提炼、筛选并合并最具体、最核心的名词。你的目标是识别具体的事件、地点、物体或作品，并严格执行以下步骤：\n\n1. **筛选**: 严格过滤掉以下类型的词语：\n    *   **抽象概念或训练活动**: 任何描述抽象品质、训练项目或研究过程的词语（例如：'核心力量', '实际的历史研究', '团队合作'）。\n    *   **动作或过程词**: 任何描述具体动作或过程的词语（例如：'打篮球', '快攻', '远投'）。\n    *   **描述性短语**: 任何描述状态、关系或感受的短语（例如：'配合越来越默契'）。\n    *   **过于宽泛的类别**: 过于笼统的分类（例如：'历史剧'）。\n\n2. **合并**: 在筛选后，对语义相近或存在包含关系的词语进行合并，只保留最核心、最具代表性的一个。\n    *   例如，在“篮球赛”和“篮球场”中，“篮球赛”是更核心的事件，应保留“篮球赛”。\n\n你的最终输出应该是一个精炼的、无重复概念的列表，只包含最具体、最具有代表性的名词。\n\n**示例**:\n输入: ['篮球赛', '篮球场', '核心力量', '实际的历史研究', '《二战全史》', '攀岩']\n筛选后: ['篮球赛', '篮球场', '《二战全史》', '攀岩']\n合并后最终输出: ['篮球赛', '《二战全史》', '攀岩']"
            },
            {
                "role": "user",
                "content": f"请从以下标签列表中筛选出核心名词: {tag_list_str},以json形式输出，格式为{{meaningful_tags: List[str]}}"
            }
        ]

        # 调用LLM进行结构化输出
        structured_response = await llm_client.call_structured(messages, FilteredTags)

        return structured_response.meaningful_tags

    except Exception as e:
        # LLM 不可用/不支持结构化输出时降级：不打印堆栈，仅一条简洁告警，
        # 返回原始标签确保流程继续（标签未经 LLM 精筛，但功能可用）
        logger.warning(f"LLM筛选不可用，降级使用原始标签: {e}")
        return tags


async def filter_interests_with_llm(
    candidates: List[InterestEntityCandidate],
    end_user_id: str,
    language: str = "zh",
    evidence: List[InterestStatementEvidence] | None = None,
    existing_interests: List[str] | None = None,
    timeout_seconds: float = INTEREST_DISTRIBUTION_LLM_TIMEOUT_SECONDS,
) -> InterestTags:
    """让 LLM 筛选并合并兴趣，可选使用 Statement 证据补充实体语境。"""
    with get_db_context() as db:
        llm_client = _get_interest_llm_client(db, end_user_id)

    from app.core.memory.utils.prompt.prompt_utils import render_interest_filter_prompt

    rendered_prompt = render_interest_filter_prompt(
        [candidate.model_dump() for candidate in candidates],
        language=language,
        evidence=[item.model_dump() for item in evidence] if evidence else None,
        existing_interests=existing_interests,
    )
    messages = [{"role": "user", "content": rendered_prompt}]
    started_at = time.monotonic()
    stage = "llm_fallback" if evidence else "llm_call"
    if timeout_seconds <= 0:
        raise TimeoutError(f"interest distribution {stage} has no remaining time budget")
    try:
        raw_response = await asyncio.wait_for(
            llm_client.call_structured(messages, InterestTags),
            timeout=timeout_seconds,
        )
        response = (
            raw_response
            if isinstance(raw_response, InterestTags)
            else InterestTags.model_validate(raw_response)
        )
    except Exception as exc:
        logger.error(
            "interest_distribution stage=%s_failed end_user_id=%s "
            "candidate_count=%s evidence_count=%s timeout=%.3fs elapsed=%.3fs "
            "error_type=%s error=%r",
            stage,
            end_user_id,
            len(candidates),
            len(evidence or []),
            timeout_seconds,
            time.monotonic() - started_at,
            type(exc).__name__,
            exc,
            exc_info=True,
        )
        raise

    logger.info(
        "interest_distribution stage=%s end_user_id=%s candidate_count=%s "
        "evidence_count=%s interest_count=%s timeout=%.3fs elapsed=%.3fs",
        stage,
        end_user_id,
        len(candidates),
        len(evidence or []),
        len(response.interests),
        timeout_seconds,
        time.monotonic() - started_at,
    )
    return response


def _remaining_interest_budget(started_at: float) -> float:
    """根据统一起算时间计算本次兴趣分布生成的剩余总预算。"""
    return max(
        0.0,
        INTEREST_DISTRIBUTION_TOTAL_TIMEOUT_SECONDS - (time.monotonic() - started_at),
    )


async def _await_with_interest_budget(
    operation: Callable[[], Awaitable[Any]],
    started_at: float,
) -> Any:
    """在共享总预算内执行一个异步阶段，避免单个查询或调用越过总截止时间。"""
    remaining = _remaining_interest_budget(started_at)
    if remaining <= 0:
        raise TimeoutError("interest distribution total time budget exhausted")
    return await asyncio.wait_for(operation(), timeout=remaining)


def _log_interest_budget(
    *,
    end_user_id: str,
    stage: str,
    started_at: float,
    second_call_executed: bool,
    degradation_reason: str | None = None,
) -> None:
    """统一记录预算阶段、总耗时、剩余时间、第二次调用状态和降级原因。"""
    elapsed = time.monotonic() - started_at
    remaining = max(0.0, INTEREST_DISTRIBUTION_TOTAL_TIMEOUT_SECONDS - elapsed)
    log_method = logger.warning if degradation_reason else logger.info
    log_method(
        "interest_distribution_budget end_user_id=%s stage=%s elapsed=%.3fs "
        "remaining=%.3fs second_call_executed=%s degradation_reason=%s",
        end_user_id,
        stage,
        elapsed,
        remaining,
        second_call_executed,
        degradation_reason or "none",
    )


def normalize_interest_name(name: str) -> str:
    """去除兴趣名首尾空白并折叠连续空白。"""
    return " ".join(name.split())


def validate_interest_groups(
    candidates: List[InterestEntityCandidate],
    groups: List[InterestGroup],
) -> List[InterestGroup]:
    """校验并规范化兴趣组，保留后端实际接受的来源实体。"""
    valid_entity_ids = {candidate.entity_id for candidate in candidates}
    claimed_entity_ids: set[str] = set()
    validated_groups: dict[str, InterestGroup] = {}
    has_named_interest = False

    for group in groups:
        name = normalize_interest_name(group.name)
        if not name:
            continue

        has_named_interest = True
        accepted_entity_ids: list[str] = []
        for entity_id in group.entity_ids:
            if entity_id not in valid_entity_ids or entity_id in claimed_entity_ids:
                continue
            claimed_entity_ids.add(entity_id)
            accepted_entity_ids.append(entity_id)
        if not accepted_entity_ids:
            continue

        normalized_name = name.casefold()
        validated_group = validated_groups.get(normalized_name)
        if validated_group is None:
            validated_groups[normalized_name] = InterestGroup(
                name=name,
                entity_ids=accepted_entity_ids,
            )
        else:
            validated_group.entity_ids.extend(accepted_entity_ids)

    if has_named_interest and not validated_groups:
        raise ValueError("LLM returned interests without valid source entity IDs")

    return list(validated_groups.values())


def aggregate_interest_groups(
    candidates: List[InterestEntityCandidate],
    groups: List[InterestGroup],
    limit: int = INTEREST_DISPLAY_LIMIT,
) -> List[Tuple[str, int]]:
    """校验 LLM 来源并按候选实体真实频次汇总兴趣。"""
    candidate_frequency = {
        candidate.entity_id: candidate.frequency for candidate in candidates
    }
    validated_groups = validate_interest_groups(candidates, groups)

    result = [
        (
            group.name,
            sum(candidate_frequency[entity_id] for entity_id in group.entity_ids),
        )
        for group in validated_groups
    ]
    result.sort(key=lambda item: (-item[1], normalize_interest_name(item[0]).casefold()))
    return result[:limit]


async def get_raw_tags_from_db(
        connector: Neo4jConnector,
        end_user_id: str,
        limit: int,
        by_user: bool = False
) -> List[Tuple[str, int]]:
    """
    TODO: not accurate tag extraction
    从数据库查询原始的、未经过滤的实体标签及其频率。
    
    使用项目的Neo4jConnector进行查询，遵循仓储模式。

    Args:
        connector: Neo4j连接器实例
        end_user_id: 如果by_user=False，则为end_user_id；如果by_user=True，则为user_id
        limit: 返回的标签数量限制
        by_user: 是否按user_id查询（默认False，按end_user_id查询）
        
    Returns:
        List[Tuple[str, int]]: 标签名称和频率的元组列表
    """
    names_to_exclude = ['AI', 'Caroline', 'Melanie', 'Jon', 'Gina', '用户', 'AI助手', 'John', 'Maria']

    if by_user:
        query = (
            "MATCH (e:ExtractedEntity) "
            "WHERE e.user_id = $id AND e.entity_type <> '生命体' AND e.name IS NOT NULL AND NOT e.name IN $names_to_exclude "
            "RETURN e.name AS name, count(e) AS frequency "
            "ORDER BY frequency DESC "
            "LIMIT $limit"
        )
    else:
        query = (
            "MATCH (e:ExtractedEntity) "
            "WHERE e.end_user_id = $id AND e.entity_type <> '生命体' AND e.name IS NOT NULL AND NOT e.name IN $names_to_exclude "
            "RETURN e.name AS name, count(e) AS frequency "
            "ORDER BY frequency DESC "
            "LIMIT $limit"
        )

    # 使用项目的Neo4jConnector执行查询
    results = await connector.execute_query(
        query,
        id=end_user_id,
        limit=limit,
        names_to_exclude=names_to_exclude
    )

    return [(record["name"], record["frequency"]) for record in results]


async def get_raw_tags_batch(
        connector: Neo4jConnector,
        end_user_ids: List[str],
        limit: int
) -> List[Tuple[str, int]]:
    """
    批量查询多个用户的实体标签频率（单次 Cypher 查询替代 N 次循环）。
    
    在数据库侧完成聚合和排序，减少网络往返和应用层计算。

    Args:
        connector: Neo4j连接器实例
        end_user_ids: end_user_id 列表
        limit: 返回的标签数量限制
        
    Returns:
        List[Tuple[str, int]]: 标签名称和频率的元组列表，按频率降序
    """
    names_to_exclude = ['AI', 'Caroline', 'Melanie', 'Jon', 'Gina', '用户', 'AI助手', 'John', 'Maria']

    query = (
        "MATCH (e:ExtractedEntity) "
        "WHERE e.end_user_id IN $ids "
        "AND e.entity_type <> '生命体' "
        "AND e.name IS NOT NULL "
        "AND NOT e.name IN $names_to_exclude "
        "RETURN e.name AS name, count(e) AS frequency "
        "ORDER BY frequency DESC "
        "LIMIT $limit"
    )

    results = await connector.execute_query(
        query,
        ids=end_user_ids,
        limit=limit,
        names_to_exclude=names_to_exclude
    )

    return [(record["name"], record["frequency"]) for record in results]


async def get_hot_memory_tags(end_user_id: str, limit: int = 10, by_user: bool = False) -> List[Tuple[str, int]]:
    """
    获取原始标签，然后使用LLM进行筛选，返回最终的热门标签列表。
    查询更多的标签(40条)给LLM提供更丰富的上下文进行筛选，但最终返回数量由limit参数控制。

    Args:
        end_user_id: 必需参数。如果by_user=False，则为end_user_id；如果by_user=True，则为user_id
        limit: 最终返回的标签数量限制（默认10）
        by_user: 是否按user_id查询（默认False，按end_user_id查询）
        
    Raises:
        ValueError: 如果end_user_id未提供或为空
    """
    # 验证end_user_id必须提供且不为空
    if not end_user_id or not end_user_id.strip():
        raise ValueError(
            "end_user_id is required. Please provide a valid end_user_id or user_id."
        )

    # 使用项目的Neo4jConnector
    connector = Neo4jConnector()
    try:
        # 1. 从数据库获取原始排名靠前的标签（查询40条给LLM提供更丰富的上下文）
        query_limit = 40
        raw_tags_with_freq = await get_raw_tags_from_db(connector, end_user_id, query_limit, by_user=by_user)
        if not raw_tags_with_freq:
            return []

        raw_tag_names = [tag for tag, freq in raw_tags_with_freq]

        # 2. 初始化LLM客户端并使用LLM筛选出有意义的标签
        meaningful_tag_names = await filter_tags_with_llm(raw_tag_names, end_user_id)

        # 3. 根据LLM的筛选结果，构建最终的标签列表（保留原始频率和顺序）
        final_tags = []
        for tag, freq in raw_tags_with_freq:
            if tag in meaningful_tag_names:
                final_tags.append((tag, freq))

        # 4. 限制返回的标签数量
        return final_tags[:limit]
    finally:
        # 确保关闭连接
        await connector.close()


async def generate_interest_distribution(
    end_user_id: str,
    limit: int = INTEREST_DISPLAY_LIMIT,
    by_user: bool = False,
    language: str = "zh",
) -> InterestDistributionGeneration:
    """生成兴趣分布；有效兴趣不超过两项时使用关联 Statement 补充判断。"""
    # 总预算从生成入口统一起算，后续查询、LLM 调用和结果校验共享同一截止时间。
    started_at = time.monotonic()
    second_call_executed = False
    primary_items: List[Tuple[str, int]] = []
    failure_stage = "input_validation"
    if not end_user_id or not end_user_id.strip():
        raise ValueError(
            "end_user_id is required. Please provide a valid end_user_id or user_id."
        )

    connector: Neo4jConnector | None = None
    try:
        failure_stage = "connector_init"
        connector = Neo4jConnector()
        repository = StatementRepository(connector)
        # 候选查询也属于完整生成链路，不能只限制 LLM 调用时间。
        failure_stage = "candidate_query"
        try:
            candidate_records = await _await_with_interest_budget(
                lambda: repository.find_interest_entity_candidates(
                    user_id=end_user_id,
                    limit=INTEREST_CANDIDATE_LIMIT,
                    excluded_names=INTEREST_EXCLUDED_NAMES,
                    iso_datetime_pattern=INTEREST_ISO_DATETIME_PATTERN,
                    unix_timestamp_pattern=INTEREST_UNIX_TIMESTAMP_PATTERN,
                    by_user=by_user,
                ),
                started_at,
            )
        except Exception:
            _log_interest_budget(
                end_user_id=end_user_id,
                stage="candidate_query",
                started_at=started_at,
                second_call_executed=second_call_executed,
                degradation_reason=(
                    "total_budget_exhausted"
                    if _remaining_interest_budget(started_at) <= 0
                    else "candidate_query_failed"
                ),
            )
            raise
        failure_stage = "candidate_validation"
        candidates = [
            InterestEntityCandidate.model_validate(record)
            for record in candidate_records
        ]
        # 首次有效结果尚未完成校验，此时预算耗尽不能返回任何部分结果。
        if _remaining_interest_budget(started_at) <= 0:
            _log_interest_budget(
                end_user_id=end_user_id,
                stage="candidate_validation",
                started_at=started_at,
                second_call_executed=second_call_executed,
                degradation_reason="total_budget_exhausted",
            )
            raise TimeoutError(
                "interest distribution total time budget exhausted before primary validation"
            )
        if not candidates:
            _log_interest_budget(
                end_user_id=end_user_id,
                stage="no_candidates",
                started_at=started_at,
                second_call_executed=second_call_executed,
            )
            return InterestDistributionGeneration(items=[], cacheable=True)

        # 每次业务级 LLM 调用最多 30 秒，同时不能超过当前剩余总预算。
        primary_timeout = min(
            INTEREST_DISTRIBUTION_LLM_TIMEOUT_SECONDS,
            _remaining_interest_budget(started_at),
        )
        failure_stage = "primary_llm"
        try:
            interests = await _await_with_interest_budget(
                lambda: filter_interests_with_llm(
                    candidates,
                    end_user_id,
                    language=language,
                    timeout_seconds=primary_timeout,
                ),
                started_at,
            )
        except Exception:
            _log_interest_budget(
                end_user_id=end_user_id,
                stage="primary_llm",
                started_at=started_at,
                second_call_executed=second_call_executed,
                degradation_reason=(
                    "total_budget_exhausted"
                    if _remaining_interest_budget(started_at) <= 0
                    else "primary_llm_failed"
                ),
            )
            raise
        # LLM 虽已返回，但首次结果还未完成来源校验，预算耗尽仍按生成失败处理。
        if _remaining_interest_budget(started_at) <= 0:
            _log_interest_budget(
                end_user_id=end_user_id,
                stage="primary_llm",
                started_at=started_at,
                second_call_executed=second_call_executed,
                degradation_reason="total_budget_exhausted",
            )
            raise TimeoutError(
                "interest distribution total time budget exhausted before primary validation"
            )
        failure_stage = "primary_validation"
        validated_primary_groups = validate_interest_groups(
            candidates,
            interests.interests,
        )
        combined_groups = list(validated_primary_groups)
        primary_items = aggregate_interest_groups(
            candidates,
            validated_primary_groups,
            limit=INTEREST_DISPLAY_LIMIT,
        )
        # 首次结果已完成校验，可以作为降级结果返回，但预算耗尽的结果不可缓存。
        if _remaining_interest_budget(started_at) <= 0:
            _log_interest_budget(
                end_user_id=end_user_id,
                stage="primary_validation",
                started_at=started_at,
                second_call_executed=second_call_executed,
                degradation_reason="total_budget_exhausted",
            )
            return InterestDistributionGeneration(
                items=primary_items[:limit],
                cacheable=False,
            )

        if len(primary_items) <= 2:
            claimed_entity_ids = {
                entity_id
                for group in validated_primary_groups
                for entity_id in group.entity_ids
            }
            evidence_candidates = [
                candidate
                for candidate in candidates
                if candidate.entity_id not in claimed_entity_ids
            ][:INTEREST_EVIDENCE_ENTITY_LIMIT]

            if evidence_candidates:
                try:
                    # Statement 证据查询继续消耗同一总预算，超时后只能降级到首次结果。
                    failure_stage = "statement_evidence"
                    evidence_records = await _await_with_interest_budget(
                        lambda: repository.find_interest_statement_evidence(
                            user_id=end_user_id,
                            entity_ids=[candidate.entity_id for candidate in evidence_candidates],
                            per_entity_limit=INTEREST_EVIDENCE_PER_ENTITY_LIMIT,
                            limit=INTEREST_EVIDENCE_LIMIT,
                            max_chars=INTEREST_EVIDENCE_MAX_CHARS,
                            by_user=by_user,
                        ),
                        started_at,
                    )
                    evidence = [
                        InterestStatementEvidence.model_validate(record)
                        for record in evidence_records
                    ]
                    if _remaining_interest_budget(started_at) <= 0:
                        _log_interest_budget(
                            end_user_id=end_user_id,
                            stage="statement_evidence",
                            started_at=started_at,
                            second_call_executed=second_call_executed,
                            degradation_reason="total_budget_exhausted",
                        )
                        return InterestDistributionGeneration(
                            items=primary_items[:limit],
                            cacheable=False,
                        )
                    if evidence:
                        evidenced_entity_ids = {
                            entity_id
                            for item in evidence
                            for entity_id in item.entity_ids
                        }
                        fallback_candidates = [
                            candidate
                            for candidate in evidence_candidates
                            if candidate.entity_id in evidenced_entity_ids
                        ]
                        if fallback_candidates:
                            remaining = _remaining_interest_budget(started_at)
                            # 恰好 5 秒仍允许调用；不足 5 秒时避免发起大概率无法完成的第二次调用。
                            if remaining < INTEREST_DISTRIBUTION_MIN_FALLBACK_TIMEOUT_SECONDS:
                                _log_interest_budget(
                                    end_user_id=end_user_id,
                                    stage="before_second_llm",
                                    started_at=started_at,
                                    second_call_executed=second_call_executed,
                                    degradation_reason="total_budget_exhausted",
                                )
                                return InterestDistributionGeneration(
                                    items=primary_items[:limit],
                                    cacheable=False,
                                )
                            second_call_executed = True
                            # 第二次调用沿用单次 30 秒上限，但通常只会获得剩余预算。
                            fallback_timeout = min(
                                INTEREST_DISTRIBUTION_LLM_TIMEOUT_SECONDS,
                                remaining,
                            )
                            failure_stage = "second_llm"
                            supplemental_interests = await _await_with_interest_budget(
                                lambda: filter_interests_with_llm(
                                    fallback_candidates,
                                    end_user_id,
                                    language=language,
                                    evidence=evidence,
                                    existing_interests=[name for name, _ in primary_items],
                                    timeout_seconds=fallback_timeout,
                                ),
                                started_at,
                            )
                            # 第二次结果尚未进入最终聚合时耗尽预算，仍只返回已验证的首次结果。
                            if _remaining_interest_budget(started_at) <= 0:
                                _log_interest_budget(
                                    end_user_id=end_user_id,
                                    stage="second_llm",
                                    started_at=started_at,
                                    second_call_executed=second_call_executed,
                                    degradation_reason="total_budget_exhausted",
                                )
                                return InterestDistributionGeneration(
                                    items=primary_items[:limit],
                                    cacheable=False,
                                )
                            combined_groups.extend(supplemental_interests.interests)
                except Exception as exc:
                    _log_interest_budget(
                        end_user_id=end_user_id,
                        stage="statement_fallback",
                        started_at=started_at,
                        second_call_executed=second_call_executed,
                        degradation_reason=(
                            "total_budget_exhausted"
                            if _remaining_interest_budget(started_at) <= 0
                            else "statement_fallback_failed"
                        ),
                    )
                    logger.warning(
                        "interest_distribution stage=statement_fallback_failed "
                        "end_user_id=%s error_type=%s error=%r",
                        end_user_id,
                        type(exc).__name__,
                        exc,
                        exc_info=True,
                    )
                    return InterestDistributionGeneration(
                        items=primary_items[:limit],
                        cacheable=False,
                    )

        # 聚合与排序也计入总预算；若处理期间越界，不返回未经预算内确认的补充结果。
        failure_stage = "final_validation"
        items = aggregate_interest_groups(candidates, combined_groups, limit=limit)
        if _remaining_interest_budget(started_at) <= 0:
            _log_interest_budget(
                end_user_id=end_user_id,
                stage="final_validation",
                started_at=started_at,
                second_call_executed=second_call_executed,
                degradation_reason="total_budget_exhausted",
            )
            return InterestDistributionGeneration(
                items=primary_items[:limit],
                cacheable=False,
            )
        _log_interest_budget(
            end_user_id=end_user_id,
            stage="completed",
            started_at=started_at,
            second_call_executed=second_call_executed,
        )
        return InterestDistributionGeneration(
            items=items,
            cacheable=bool(items),
        )
    except Exception as exc:
        # 兴趣分布是展示型增强数据。合法请求的生成依赖失败时，不让异常进入页面；
        # 首轮已有可信结果则保留首轮，否则返回并缓存空列表，24 小时后再生成。
        # 同一个人结构化输出失败，两天同时发生的概率较低
        fallback_items = primary_items[:limit]
        cacheable = not fallback_items
        logger.error(
            "interest_distribution stage=%s_failed end_user_id=%s language=%s "
            "elapsed=%.3fs second_call_executed=%s fallback=%s cacheable=%s "
            "error_type=%s error=%r",
            failure_stage,
            end_user_id,
            language,
            time.monotonic() - started_at,
            second_call_executed,
            "primary_items" if fallback_items else "empty",
            cacheable,
            type(exc).__name__,
            exc,
            exc_info=True,
        )
        return InterestDistributionGeneration(
            items=fallback_items,
            cacheable=cacheable,
        )
    finally:
        # Connector 清理不受业务预算限制，任何返回或异常路径都必须等待关闭完成。
        if connector is not None:
            try:
                await connector.close()
            except Exception as exc:
                logger.error(
                    "interest_distribution stage=connector_close_failed "
                    "end_user_id=%s language=%s error_type=%s error=%r",
                    end_user_id,
                    language,
                    type(exc).__name__,
                    exc,
                    exc_info=True,
                )


async def get_interest_distribution(
    end_user_id: str,
    limit: int = INTEREST_DISPLAY_LIMIT,
    by_user: bool = False,
    language: str = "zh",
) -> List[Tuple[str, int]]:
    """获取兴趣分布列表，保留既有调用合同。"""
    generation = await generate_interest_distribution(
        end_user_id=end_user_id,
        limit=limit,
        by_user=by_user,
        language=language,
    )
    return generation.items
