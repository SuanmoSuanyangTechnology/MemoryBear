"""Pipeline stage snapshot — dump each extraction stage's output to OSS for comparison.

Usage:
    snapshot = PipelineSnapshot(end_user_id="abc123-def456")
    snapshot.save_stage("1_statements", data)
    snapshot.save_stage("2_triplets", data)
    ...

Output structure (OSS):
    redbear-files/snapshot/
        {end_user_id}_{YYYYmmdd_HHMMSS}/
            0_summary.json
            1_statements.json
            2_triplets.json
            ...

Controlled by env var PIPELINE_SNAPSHOT_ENABLED (default: false).
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_ENABLED: Optional[bool] = None
_OSS_BUCKET: Optional[Any] = None

# OSS 上快照文件的根前缀（对应 bucket 内的 "目录"）
_OSS_SNAPSHOT_PREFIX = "snapshot"


def _is_enabled() -> bool:
    global _ENABLED
    if _ENABLED is None:
        _ENABLED = os.getenv("PIPELINE_SNAPSHOT_ENABLED", "false").lower() == "true"
    return _ENABLED


def _get_oss_bucket():
    """获取 oss2.Bucket 单例（同步），用于快照上传。"""
    global _OSS_BUCKET
    if _OSS_BUCKET is None:
        import oss2
        from app.core.config import settings

        auth = oss2.Auth(settings.OSS_ACCESS_KEY_ID, settings.OSS_ACCESS_KEY_SECRET)
        _OSS_BUCKET = oss2.Bucket(auth, settings.OSS_ENDPOINT, settings.OSS_BUCKET_NAME)
    return _OSS_BUCKET


def _safe_serialize(obj: Any) -> Any:
    """Convert objects to JSON-serializable form."""
    if obj is None:
        return None
    if isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, (list, tuple)):
        return [_safe_serialize(item) for item in obj]
    if isinstance(obj, dict):
        return {str(k): _safe_serialize(v) for k, v in obj.items()}
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    if hasattr(obj, "__dataclass_fields__"):
        from dataclasses import asdict
        return asdict(obj)
    if hasattr(obj, "__dict__"):
        return {k: _safe_serialize(v) for k, v in obj.__dict__.items()
                if not k.startswith("_")}
    return str(obj)


class PipelineSnapshot:
    """Dump each pipeline stage's output to OSS."""

    def __init__(self, end_user_id: str):
        """
        Args:
            end_user_id: 终端用户 ID，用于快照目录命名以便快速定位。
        """
        self.enabled = _is_enabled()
        self.end_user_id = end_user_id
        self._oss_prefix: Optional[str] = None

        if self.enabled:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            self._oss_prefix = f"{_OSS_SNAPSHOT_PREFIX}/{end_user_id}_{ts}"
            logger.debug(f"[Snapshot] 已启用，OSS 前缀: {self._oss_prefix}")

    @property
    def directory(self) -> Optional[str]:
        """OSS 前缀路径，未启用时返回 None。"""
        return self._oss_prefix

    def save_stage(self, stage_name: str, data: Any) -> None:
        """Save a stage's output as JSON to OSS.

        Args:
            stage_name: e.g. "1_statements", "2_triplets"
            data: Any serializable data (Pydantic models, dicts, lists, dataclasses)
        """
        if not self.enabled or self._oss_prefix is None:
            return

        try:
            serialized = _safe_serialize(data)
            json_bytes = json.dumps(
                serialized, ensure_ascii=False, indent=2, default=str
            ).encode("utf-8")

            oss_key = f"{self._oss_prefix}/{stage_name}.json"
            bucket = _get_oss_bucket()
            bucket.put_object(oss_key, json_bytes)
            logger.debug(f"[Snapshot] {stage_name} → oss://{oss_key}")
        except Exception as e:
            logger.warning(f"[Snapshot] 保存 {stage_name} 失败: {e}")

    def save_summary(self, stats: Dict[str, Any]) -> None:
        """Save a summary with pipeline metadata and stats."""
        if not self.enabled or self._oss_prefix is None:
            return

        summary = {
            "end_user_id": self.end_user_id,
            "timestamp": datetime.now().isoformat(),
            "stats": stats,
        }
        self.save_stage("0_summary", summary)
