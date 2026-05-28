"""Pipeline stage snapshot — dump each extraction stage's output to OSS for comparison.

Usage:
    snapshot = PipelineSnapshot(end_user_id="abc123-def456")
    snapshot.save_stage("1_statements", data)
    snapshot.save_stage("2_triplets", data)
    ...

Output structure (OSS):

    Sliding-window 写入（推荐路径，含完整定位上下文）:
        redbear-files/snapshot/
            {end_user_id}/
                {conversation_id}/
                    seq_{message_seq:06d}_{YYYYmmdd_HHMMSS}/
                        0_summary.json
                        1_assistant_pruning.json
                        2_statement_outputs.json
                        ...

    旧的整轮写入（无 conversation/seq 时的兼容路径）:
        redbear-files/snapshot/
            {end_user_id}_{YYYYmmdd_HHMMSS}/
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


def upload_stage_snapshot(
    snapshot_dir: str, stage_name: str, data: Any
) -> bool:
    """将一个 stage 的数据序列化为 JSON 并上传到 OSS。

    供没有 ``PipelineSnapshot`` 实例的调用方使用（典型场景：Celery worker
    任务在主流水线之后异步落盘补充数据，需要写入主流水线已创建的同一个
    OSS 前缀下）。

    Args:
        snapshot_dir: 主流水线在 OSS 上创建的前缀路径（例如
            ``snapshot/{end_user_id}/{conversation_id}/seq_xxx_时间戳``）。
        stage_name: 落盘的 stage 名（不带 ``.json`` 后缀），最终路径为
            ``<snapshot_dir>/<stage_name>.json``。
        data: 任意可序列化对象（Pydantic 模型 / dict / list / dataclass）。

    Returns:
        上传成功返回 True，失败返回 False（失败仅打 warning，不抛异常）。
    """
    try:
        serialized = _safe_serialize(data)
        json_bytes = json.dumps(
            serialized, ensure_ascii=False, indent=2, default=str
        ).encode("utf-8")

        oss_key = f"{snapshot_dir}/{stage_name}.json"
        _get_oss_bucket().put_object(oss_key, json_bytes)
        logger.debug(f"[Snapshot] {stage_name} → oss://{oss_key}")
        return True
    except Exception as e:
        logger.warning(f"[Snapshot] 保存 {stage_name} 失败: {e}")
        return False


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

    def __init__(
        self,
        end_user_id: str,
        conversation_id: Optional[str] = None,
        message_seq: Optional[int] = None,
        extra_metadata: Optional[Dict[str, Any]] = None,
    ):
        """
        Args:
            end_user_id: 终端用户 ID，作为 OSS 第一级目录。
            conversation_id: 对话 ID（滑动窗口写入时传入）。
                提供后会把它作为第二级目录，便于按对话归集快照。
            message_seq: 目标 user 消息的 message_seq（滑动窗口写入时传入）。
                提供后会写入叶子目录名（``seq_{message_seq:06d}_{时间戳}``），
                字典序与数值序一致，方便在 OSS Browser 里顺序定位。
            extra_metadata: 任意可序列化的额外字段，会写入 ``0_summary.json``，
                典型字段：ref_id / dispatch_at / dialog_at / language /
                target_content_preview。
        """
        self.enabled = _is_enabled()
        self.end_user_id = end_user_id
        self.conversation_id = conversation_id
        self.message_seq = message_seq
        self.extra_metadata: Dict[str, Any] = dict(extra_metadata or {})
        self._oss_prefix: Optional[str] = None

        if self.enabled:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            if conversation_id:
                # 滑动窗口路径：按 user / conversation / seq_xxx_时间戳 三级组织
                seq_part = (
                    f"seq_{int(message_seq):06d}_{ts}"
                    if message_seq is not None
                    else f"seq_unknown_{ts}"
                )
                self._oss_prefix = (
                    f"{_OSS_SNAPSHOT_PREFIX}/{end_user_id}/"
                    f"{conversation_id}/{seq_part}"
                )
            else:
                # 兼容旧路径（整轮 messages 写入）
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
        upload_stage_snapshot(self._oss_prefix, stage_name, data)

    def save_summary(self, stats: Dict[str, Any]) -> None:
        """Save a summary with pipeline metadata and stats.

        除统计信息外，还会写入定位元信息（end_user_id / conversation_id /
        message_seq）以及构造时传入的 ``extra_metadata``，便于在 OSS 上
        通过 ``0_summary.json`` 直接确认是哪一次写入产生的快照。
        """
        if not self.enabled or self._oss_prefix is None:
            return

        summary: Dict[str, Any] = {
            "end_user_id": self.end_user_id,
            "conversation_id": self.conversation_id,
            "message_seq": self.message_seq,
            "timestamp": datetime.now().isoformat(),
            "stats": stats,
        }
        if self.extra_metadata:
            summary.update(_safe_serialize(self.extra_metadata) or {})
        self.save_stage("0_summary", summary)
