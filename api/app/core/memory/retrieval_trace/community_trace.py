"""Safe community telemetry: never serialize vectors or model credentials."""

import logging
import time
from contextlib import contextmanager

from app.core.memory.models.community_retrieval_models import CommunityRetrievalTrace

logger = logging.getLogger(__name__)


@contextmanager
def timed_stage(trace: CommunityRetrievalTrace, stage: str):
    started = time.perf_counter()
    try:
        yield
    finally:
        trace.stage_duration_ms[stage] = round((time.perf_counter() - started) * 1000, 3)


def log_community_trace(trace: CommunityRetrievalTrace, *, operation_id: str, end_user_id: str) -> None:
    logger.info("community_retrieval operation_id=%s end_user_id=%s trace=%s",
                operation_id, end_user_id, trace.model_dump_json())
