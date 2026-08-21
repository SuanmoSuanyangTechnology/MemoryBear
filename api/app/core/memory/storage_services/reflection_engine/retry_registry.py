"""反思失败用户重试登记（纯 Redis 运维态）。

两个 key 成对（pipeline 原子）：
- ZSet  reflection:retry:{task_type}            member=end_user_id, score=下次可见时间戳(epoch 秒)
- String reflection:retry:meta:{task_type}:{uid} JSON：重跑参数 + 状态

task_type ∈ {"high_freq", "dedup"}。completion ∈ {in_progress, partial, failed, exhausted}。
rc 为 None（Redis 不可用）时所有写接口安全 no-op（仅 warning），不影响反思主流程。
"""
import json
import logging
import time
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Dict, List, Optional, Tuple

from app.core.memory.storage_services.reflection_engine.errors import (
    ReflectionFailureReason,
    ReflectionModelType,
)
from app.core.utils.datetime_utils import to_timestamp_ms, utcnow

logger = logging.getLogger(__name__)

# 重试登记内部策略常量（运维态调参，改这一处即可；非部署必填项，故不进 Settings）。
# 派发扫描间隔在 settings.REFLECTION_RETRY_SCAN_INTERVAL_MINUTES（beat 注册需从 settings 读）。
RETRY_LEASE_SECONDS = 900       # 开工租约时长（秒），必须 > 硬超时 600，确保任务必然已结束才判进程死亡
RETRY_MAX = 5                   # 「跑完但无推进」最大重试次数，超过置 exhausted 出队
RETRY_DEAD_MAX = 3              # 「进程死亡（租约到期）」最大兜底次数，超过置 exhausted
RETRY_BACKOFF_BASE = 1800       # 指数退避基数（秒），30 分钟
RETRY_BACKOFF_CAP = 21600       # 退避封顶（秒），6 小时
RETRY_TTL_SECONDS = 604800      # meta key TTL（秒），7 天，到期自动放弃永久不活跃用户
RETRY_BATCH = 200               # 派发任务每轮每队列最多取多少到点用户，防集体失败灌爆队列


class RetryRecordOutcome(StrEnum):
    RETRY_SCHEDULED = "retry_scheduled"
    EXHAUSTED = "exhausted"
    REDIS_UNAVAILABLE = "redis_unavailable"


@dataclass(frozen=True, slots=True)
class RetryRecordResult:
    outcome: RetryRecordOutcome
    last_failed_at_ms: int | None = None


def _normalize_reason_code(value: object) -> ReflectionFailureReason | None:
    try:
        return ReflectionFailureReason(value)
    except (TypeError, ValueError):
        return None


def _normalize_model_type(value: object) -> ReflectionModelType:
    try:
        return ReflectionModelType(value)
    except (TypeError, ValueError):
        return ReflectionModelType.UNKNOWN


def _optional_reason_code(value: object) -> ReflectionFailureReason | None:
    if value is None or value == "":
        return None
    return _normalize_reason_code(value)


def _zkey(task_type: str) -> str:
    return f"reflection:retry:{task_type}"


def _mkey(task_type: str, uid: str) -> str:
    return f"reflection:retry:meta:{task_type}:{uid}"


def load_meta(rc, task_type: str, uid: str) -> Optional[Dict[str, Any]]:
    """读 meta JSON；不存在 / Redis 不可用 / 解析失败 → None。"""
    if rc is None:
        return None
    try:
        raw = rc.get(_mkey(task_type, uid))
        if not raw:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        return json.loads(raw)
    except Exception as e:
        logger.warning(f"[ReflectionRetry] load_meta 失败 task_type={task_type} uid={uid}: {e}")
        return None


def lease(rc, task_type: str, uid: str, params: Dict[str, Any],
          from_retry: bool) -> None:
    """开工登记：标 in_progress，score=now+租约。

    from_retry=True（重试派发续约）→ 保留 retry_count/dead_count；
    from_retry=False（正常 scan 新一轮）→ 重置计数、清 exhausted（给新调度周期新预算）。
    """
    if rc is None:
        logger.warning(f"[ReflectionRetry] Redis 不可用，跳过 lease uid={uid}")
        return
    try:
        old = load_meta(rc, task_type, uid) or {}
        if from_retry:
            retry_count = old.get("retry_count", 0)
            dead_count = old.get("dead_count", 0)
        else:
            retry_count = 0
            dead_count = 0
        meta = {
            **params,                      # config_id / workspace_id /（高频）iteration_period
            "completion": "in_progress",
            "retry_count": retry_count,
            "dead_count": dead_count,
            "leased_at": to_timestamp_ms(utcnow()),
        }
        score = time.time() + RETRY_LEASE_SECONDS
        pipe = rc.pipeline()
        pipe.set(_mkey(task_type, uid), json.dumps(meta, ensure_ascii=False),
                 ex=RETRY_TTL_SECONDS)
        pipe.zadd(_zkey(task_type), {uid: score})
        pipe.execute()
    except Exception as e:
        logger.warning(f"[ReflectionRetry] lease 失败 task_type={task_type} uid={uid}: {e}")


def resolve(rc, task_type: str, uid: str) -> None:
    """completion==full：成对删除 member + meta。"""
    if rc is None:
        return
    try:
        pipe = rc.pipeline()
        pipe.zrem(_zkey(task_type), uid)
        pipe.delete(_mkey(task_type, uid))
        pipe.execute()
    except Exception as e:
        logger.warning(f"[ReflectionRetry] resolve 失败 task_type={task_type} uid={uid}: {e}")


def record(
    rc,
    task_type: str,
    uid: str,
    completion: str,
    progressed: bool,
    skipped_steps: Optional[List[str]] = None,
    reason_code: ReflectionFailureReason | str | None = None,
    last_error: str = "",
    model_type: ReflectionModelType | str | None = None,
) -> RetryRecordResult:
    """登记反思重试，并保留三类模型错误的结构化信息。"""
    if rc is None:
        logger.warning(f"[ReflectionRetry] Redis 不可用，跳过 record uid={uid}")
        return RetryRecordResult(RetryRecordOutcome.REDIS_UNAVAILABLE)
    try:
        old = load_meta(rc, task_type, uid) or {}
        if old.get("completion") == "exhausted":
            return RetryRecordResult(
                RetryRecordOutcome.EXHAUSTED,
                last_failed_at_ms=old.get("last_failed_at"),
            )
        selected_reason = _optional_reason_code(reason_code)
        effective_progressed = progressed and selected_reason is None
        retry_count = 0 if effective_progressed else old.get("retry_count", 0) + 1
        last_failed_at_ms = to_timestamp_ms(utcnow())
        if retry_count >= RETRY_MAX:
            if selected_reason is not None:
                return _set_exhausted(
                    rc,
                    task_type,
                    uid,
                    old,
                    selected_reason,
                    skipped_steps=skipped_steps,
                    last_failed_at_ms=last_failed_at_ms,
                    model_type=model_type,
                )
            _set_exhausted(
                rc, task_type, uid, old, last_error or "retry_max",
                last_failed_at_ms=last_failed_at_ms,
            )
            return RetryRecordResult(
                RetryRecordOutcome.EXHAUSTED,
                last_failed_at_ms=last_failed_at_ms,
            )
        # 有推进：score=now，下次扫描立即可见；无推进：指数退避
        if progressed:
            score = time.time()
        else:
            backoff = min(RETRY_BACKOFF_BASE * (2 ** retry_count), RETRY_BACKOFF_CAP)
            score = time.time() + backoff
        meta: Dict[str, Any] = {
            **old,
            "completion": completion,
            "retry_count": retry_count,
            "skipped_steps": skipped_steps or [],
            "last_error": selected_reason.value if selected_reason else last_error,
            "last_failed_at": last_failed_at_ms,
        }
        if selected_reason is not None:
            meta["model_type"] = _normalize_model_type(model_type).value
        else:
            meta.pop("model_type", None)
        pipe = rc.pipeline()
        pipe.set(_mkey(task_type, uid), json.dumps(meta, ensure_ascii=False),
                 ex=RETRY_TTL_SECONDS)
        pipe.zadd(_zkey(task_type), {uid: score})
        pipe.execute()
        return RetryRecordResult(
            RetryRecordOutcome.RETRY_SCHEDULED,
            last_failed_at_ms=last_failed_at_ms,
        )
    except Exception as e:
        logger.warning(
            f"[ReflectionRetry] record 失败 task_type={task_type} uid={uid}: {e}",
            exc_info=True,
        )
        return RetryRecordResult(RetryRecordOutcome.REDIS_UNAVAILABLE)


def mark_dead(rc, task_type: str, uid: str) -> bool:
    """登记过期 Worker 租约；进程异常不进入模型失败上报。"""
    if rc is None:
        return False
    try:
        old = load_meta(rc, task_type, uid) or {}
        if old.get("completion") == "exhausted":
            return False
        dead_count = old.get("dead_count", 0) + 1
        last_failed_at_ms = to_timestamp_ms(utcnow())
        if dead_count >= RETRY_DEAD_MAX:
            _set_exhausted(
                rc, task_type, uid, old, "process_dead_max",
                last_failed_at_ms=last_failed_at_ms,
            )
            return False
        meta = {
            **old,
            "dead_count": dead_count,
            "last_failed_at": last_failed_at_ms,
        }
        rc.set(_mkey(task_type, uid), json.dumps(meta, ensure_ascii=False),
               ex=RETRY_TTL_SECONDS)
        return True
    except Exception as e:
        logger.warning(
            f"[ReflectionRetry] mark_dead 失败 task_type={task_type} uid={uid}: {e}",
            exc_info=True,
        )
        return False


def _set_exhausted(
    rc,
    task_type: str,
    uid: str,
    old: Dict[str, Any],
    last_error: ReflectionFailureReason | str,
    *,
    skipped_steps: Optional[List[str]] = None,
    last_failed_at_ms: int | None = None,
    model_type: ReflectionModelType | str | None = None,
) -> RetryRecordResult:
    """置 exhausted 并移出重试队列。"""

    reason = _normalize_reason_code(last_error)
    failed_at = last_failed_at_ms or to_timestamp_ms(utcnow())
    meta = {
        **old,
        "completion": "exhausted",
        "last_error": reason.value if reason else str(last_error),
        "last_failed_at": failed_at,
    }
    if skipped_steps is not None:
        meta["skipped_steps"] = skipped_steps
    if reason is not None:
        meta["model_type"] = _normalize_model_type(model_type).value
    else:
        meta.pop("model_type", None)
    pipe = rc.pipeline()
    pipe.set(_mkey(task_type, uid), json.dumps(meta, ensure_ascii=False),
             ex=RETRY_TTL_SECONDS)
    pipe.zrem(_zkey(task_type), uid)
    pipe.execute()
    logger.warning(
        f"[ReflectionRetry] 置 exhausted 出队 task_type={task_type} uid={uid} "
        f"reason={meta['last_error']}"
    )
    return RetryRecordResult(
        RetryRecordOutcome.EXHAUSTED,
        last_failed_at_ms=failed_at,
    )


# ---- completion / progressed 解析（从 run() 返回的 results 读，不额外探测）----

_LAYER2_STEPS = ["unresolved_entity", "alias_merge", "entity_dedup",
                 "metadata_extraction", "description_merge"]


def completion_of_layer2(results: Dict[str, Any]) -> str:
    """根据步骤状态和业务失败判断高频反思是否完整完成。"""
    if results.get("status") == "error":
        return "partial"
    if int(results.get("business_failure_count", 0) or 0) > 0:
        return "partial"
    for k in _LAYER2_STEPS:
        step = results.get(k) or {}
        if step.get("status") in ("timeout", "skipped", "error"):
            return "partial"
        if int(step.get("business_failure_count", 0) or 0) > 0:
            return "partial"
    return "full"


def completion_of_dedup(r: Dict[str, Any]) -> str:
    """根据截断状态和业务失败判断低频去重是否完整完成。"""
    if (
        r.get("status") == "error"
        or r.get("reason_code")
        or r.get("truncated")
        or r.get("had_type_error")
        or int(r.get("business_failure_count", 0) or 0) > 0
        or r.get("reason_codes")
    ):
        return "partial"
    return "full"


def progressed_layer2(results: Dict[str, Any]) -> bool:
    """高频是否有推进（覆盖 5 个子步骤中任一产生真实写入）：
    未识别实体解析/强制入库、别名归并合并/丢弃边、实体去重合并、
    元数据提取写入、描述合并。任一 > 0 视为本轮收缩了后续工作量，用于重置 retry_count。

    字段名以各 _run_* 返回为准：alias_merge 用 merge_count/drop_count（非 merged_count），
    metadata_extraction 用 extracted（仅统计真正写入 patch 的实体，静态数据不会虚报）。"""
    unresolved = results.get("unresolved_entity") or {}
    alias = results.get("alias_merge") or {}
    dedup = results.get("entity_dedup") or {}
    meta = results.get("metadata_extraction") or {}
    desc = results.get("description_merge") or {}
    return bool(unresolved.get("resolved", 0) or unresolved.get("forced", 0)
                or alias.get("merge_count", 0) or alias.get("drop_count", 0)
                or dedup.get("merged_count", 0)
                or meta.get("extracted", 0)
                or desc.get("merged_count", 0))


def progressed_dedup(r: Dict[str, Any]) -> bool:
    """低频是否有推进：有合并或扫描到类型。"""
    return bool(r.get("merged_count", 0) or r.get("scanned_types", 0))


def skipped_steps_of_layer2(results: Dict[str, Any]) -> List[str]:
    """返回未完成或失败步骤的固定操作名。"""
    skipped: List[str] = list(results.get("failed_operations") or [])
    for key in _LAYER2_STEPS:
        step = results.get(key) or {}
        if step.get("status") in ("timeout", "skipped", "error"):
            skipped.append(key)
        if int(step.get("business_failure_count", 0) or 0) > 0:
            skipped.extend(step.get("failed_operations") or [key])
    return list(dict.fromkeys(skipped))


def reason_codes_of_layer2(results: Dict[str, Any]) -> List[str]:
    return _layer2_codes_and_types(results)[0]


def model_types_of_layer2(results: Dict[str, Any]) -> List[str]:
    """与 ``reason_codes_of_layer2`` 同索引对齐的模型类型列表。"""
    return _layer2_codes_and_types(results)[1]


def _layer2_codes_and_types(results: Dict[str, Any]) -> Tuple[List[str], List[str]]:
    """聚合 reason_code 与 model_type，两者同索引对齐（去重后仍保持一一对应）。

    同一 reason 首次出现时记类型；若首次为 UNKNOWN 而后续出现具体类型，
    用具体类型补齐。步骤或顶层未提供 model_types 时按 UNKNOWN 占位。
    """
    codes: List[str] = []
    types: List[str] = []

    def _merge(step_codes: List[str], step_types: List[str]) -> None:
        for idx, code in enumerate(step_codes):
            reason = _normalize_reason_code(code)
            if reason is None:
                continue
            normalized = reason.value
            model = _normalize_model_type(
                step_types[idx] if idx < len(step_types) else None
            ).value
            if normalized not in codes:
                codes.append(normalized)
                types.append(model)
            else:
                existing = types[codes.index(normalized)]
                if existing == ReflectionModelType.UNKNOWN.value and model != ReflectionModelType.UNKNOWN.value:
                    types[codes.index(normalized)] = model

    top_codes = list(results.get("reason_codes") or [])
    if results.get("reason_code"):
        top_codes = [*top_codes, results["reason_code"]]
    top_types = list(results.get("model_types") or [])
    if results.get("model_type"):
        top_types = [*top_types, results["model_type"]]
    _merge(top_codes, top_types)
    for key in _LAYER2_STEPS:
        step = results.get(key) or {}
        _merge(list(step.get("reason_codes") or []), list(step.get("model_types") or []))
    return codes, types


def reason_codes_of_dedup(result: Dict[str, Any]) -> List[str]:
    return _dedup_codes_and_types(result)[0]


def model_types_of_dedup(result: Dict[str, Any]) -> List[str]:
    """与 ``reason_codes_of_dedup`` 同索引对齐的模型类型列表。"""
    return _dedup_codes_and_types(result)[1]


def _dedup_codes_and_types(result: Dict[str, Any]) -> Tuple[List[str], List[str]]:
    codes: List[str] = []
    types: List[str] = []
    reason_codes = list(result.get("reason_codes") or [])
    if result.get("reason_code"):
        reason_codes = [*reason_codes, result["reason_code"]]
    model_types = list(result.get("model_types") or [])
    if result.get("model_type"):
        model_types = [*model_types, result["model_type"]]
    for idx, code in enumerate(reason_codes):
        reason = _normalize_reason_code(code)
        if reason is None:
            continue
        normalized = reason.value
        model = _normalize_model_type(
            model_types[idx] if idx < len(model_types) else None
        ).value
        if normalized not in codes:
            codes.append(normalized)
            types.append(model)
        else:
            existing = types[codes.index(normalized)]
            if existing == ReflectionModelType.UNKNOWN.value and model != ReflectionModelType.UNKNOWN.value:
                types[codes.index(normalized)] = model
    return codes, types


def select_primary_reason(reason_codes: List[str]) -> str | None:
    """在受控错误码中按固定优先级选择主原因。"""

    priority = [
        ReflectionFailureReason.REFLECTION_MODEL_UNAVAILABLE.value,
        ReflectionFailureReason.MODEL_CALL_FAILED.value,
        ReflectionFailureReason.RESULT_PARSE_FAILED.value,
    ]
    normalized = {
        parsed.value
        for reason in reason_codes
        if (parsed := _normalize_reason_code(reason)) is not None
    }
    return next((reason for reason in priority if reason in normalized), None)


def select_primary_model_type(
    reason_codes: List[str],
    model_types: List[str],
    primary_reason: str | None,
) -> str | None:
    """返回主原因对应的模型类型；无主原因或类型未知时返回 None（正文省略该行）。"""
    if primary_reason is None:
        return None
    for code, model in zip(reason_codes, model_types):
        if code == primary_reason:
            normalized = _normalize_model_type(model)
            if normalized is not ReflectionModelType.UNKNOWN:
                return normalized.value
    return None
