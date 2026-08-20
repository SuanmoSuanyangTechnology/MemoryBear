"""Shared Redis keys and limits for the production forgetting flow."""
FORGET_CANDIDATES_KEY = "forget:candidates"
FORGET_INFLIGHT_KEY = "forget:inflight"
FORGET_COOLDOWN_KEY_PREFIX = "forget:cooldown:"
FORGET_COOLDOWN_TTL_SECONDS = 6 * 60 * 60
DIALOGUE_AUDIT_CONTENT_MAX_LENGTH = 2_000
MILLISECONDS_PER_DAY = 86_400_000

# 核心池每批候选上限（设计 §4.2「核心每批 ≤50」）。
FORGET_CORE_BATCH_SIZE = 50

# 单轮辅助池删除上限（设计 §4.2 的 AUXILIARY_MAX_PER_RUN，整轮上限而非每批上限）。
AUXILIARY_MAX_PER_RUN = 100


def forget_cooldown_key(end_user_id: str) -> str:
    return f"{FORGET_COOLDOWN_KEY_PREFIX}{end_user_id}"
