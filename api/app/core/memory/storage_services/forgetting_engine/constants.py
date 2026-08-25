"""Shared Redis keys and limits for the production forgetting flow."""
FORGET_CANDIDATES_KEY = "forget:candidates"
FORGET_INFLIGHT_KEY = "forget:inflight"
DIALOGUE_AUDIT_CONTENT_MAX_LENGTH = 2_000

# 核心池每批候选上限（设计 §4.2「核心每批 ≤50」）。
FORGET_CORE_BATCH_SIZE = 50
