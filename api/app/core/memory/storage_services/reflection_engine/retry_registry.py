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
from typing import Any, Dict, List, Optional

from app.core.utils.datetime_utils import utcnow, to_timestamp_ms

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


def record(rc, task_type: str, uid: str, completion: str, progressed: bool,
           skipped_steps: Optional[List[str]] = None, last_error: str = "") -> None:
    """completion==partial/failed：累计 retry_count（有推进则重置 0），指数退避更新 score；
    达 REFLECTION_RETRY_MAX → exhausted 出队（meta 留待 TTL）。"""
    if rc is None:
        logger.warning(f"[ReflectionRetry] Redis 不可用，跳过 record uid={uid}")
        return
    try:
        old = load_meta(rc, task_type, uid) or {}
        retry_count = 0 if progressed else old.get("retry_count", 0) + 1
        if retry_count >= RETRY_MAX:
            _set_exhausted(rc, task_type, uid, old, last_error or "retry_max")
            return
        # 有推进：score=now，下次扫描立即可见；无推进：指数退避
        if progressed:
            score = time.time()
        else:
            backoff = min(RETRY_BACKOFF_BASE * (2 ** retry_count), RETRY_BACKOFF_CAP)
            score = time.time() + backoff
        meta = {
            **old,
            "completion": completion,
            "retry_count": retry_count,
            "skipped_steps": skipped_steps or [],
            "last_error": last_error,
            "last_failed_at": to_timestamp_ms(utcnow()),
        }
        pipe = rc.pipeline()
        pipe.set(_mkey(task_type, uid), json.dumps(meta, ensure_ascii=False),
                 ex=RETRY_TTL_SECONDS)
        pipe.zadd(_zkey(task_type), {uid: score})
        pipe.execute()
    except Exception as e:
        logger.warning(f"[ReflectionRetry] record 失败 task_type={task_type} uid={uid}: {e}")


def mark_dead(rc, task_type: str, uid: str) -> bool:
    """租约到期且仍 in_progress：判定上次开工后进程死亡，dead_count+1。

    未超 REFLECTION_RETRY_DEAD_MAX → 留在队列等重派，返回 True（可重派）；
    达上限 → exhausted 出队，返回 False（不再重派）。
    """
    if rc is None:
        return False
    try:
        old = load_meta(rc, task_type, uid) or {}
        dead_count = old.get("dead_count", 0) + 1
        if dead_count >= RETRY_DEAD_MAX:
            _set_exhausted(rc, task_type, uid, old, "process_dead_max")
            return False
        meta = {**old, "dead_count": dead_count, "last_failed_at": to_timestamp_ms(utcnow())}
        rc.set(_mkey(task_type, uid), json.dumps(meta, ensure_ascii=False),
               ex=RETRY_TTL_SECONDS)
        return True
    except Exception as e:
        logger.warning(f"[ReflectionRetry] mark_dead 失败 task_type={task_type} uid={uid}: {e}")
        return False


def _set_exhausted(rc, task_type: str, uid: str, old: Dict[str, Any], last_error: str) -> None:
    """置 exhausted、移出 ZSet；meta 保留（completion=exhausted）等 TTL 过期，便于查询。"""
    meta = {**old, "completion": "exhausted", "last_error": last_error,
            "last_failed_at": to_timestamp_ms(utcnow())}
    pipe = rc.pipeline()
    pipe.set(_mkey(task_type, uid), json.dumps(meta, ensure_ascii=False),
             ex=RETRY_TTL_SECONDS)
    pipe.zrem(_zkey(task_type), uid)
    pipe.execute()
    logger.warning(f"[ReflectionRetry] 置 exhausted 出队 task_type={task_type} uid={uid} reason={last_error}")


# ---- completion / progressed 解析（从 run() 返回的 results 读，不额外探测）----

_LAYER2_STEPS = ["unresolved_entity", "alias_merge", "entity_dedup",
                 "metadata_extraction", "description_merge"]


def completion_of_layer2(results: Dict[str, Any]) -> str:
    """高频：任一步骤 status ∈ {timeout, skipped, error} → partial；否则 full。
    条目级 failed_count 不算未完成（步骤 status 仍为 success）。"""
    for k in _LAYER2_STEPS:
        st = (results.get(k) or {}).get("status")
        if st in ("timeout", "skipped", "error"):
            return "partial"
    return "full"


def completion_of_dedup(r: Dict[str, Any]) -> str:
    """低频：truncated 或 had_type_error → partial；否则 full。"""
    if r.get("truncated") or r.get("had_type_error"):
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
    """高频命中的未完成步骤名，供 record 记录便于排查。"""
    return [k for k in _LAYER2_STEPS
            if (results.get(k) or {}).get("status") in ("timeout", "skipped", "error")]
