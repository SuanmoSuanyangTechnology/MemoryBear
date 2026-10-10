"""Pipeline stage snapshot — dump each extraction stage's output to object storage for comparison.

Usage:
    snapshot = PipelineSnapshot(end_user_id="abc123-def456")
    snapshot.save_stage("1_statements", data)
    snapshot.save_stage("2_triplets", data)
    ...

存储后端跟随 STORAGE_TYPE（local / oss / s3 / minio），通过 ``StorageFactory`` 获取，
与系统文件存储共用同一个 bucket，仅以 ``extract_snapshot/`` 前缀区分。

Output structure:

    Sliding-window 写入（推荐路径，含完整定位上下文）:
        {bucket}/extract_snapshot/
            {end_user_id}/
                {conversation_id}/
                    seq_{message_seq:06d}_{YYYYmmdd_HHMMSS}/
                        0_summary.json
                        1_user_assistant_pruning.json
                        2_statement_outputs.json
                        ...

Controlled by env var EXTRACT_SNAPSHOT_ENABLED (default: false).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
from collections.abc import Callable, Coroutine
from typing import Any, Dict, Optional

from app.core.utils.datetime_utils import to_iso_z, utcnow_naive

logger = logging.getLogger(__name__)

_ENABLED: Optional[bool] = None

# 快照文件的根前缀（对应 bucket 内的 "目录"）
_SNAPSHOT_PREFIX = "extract_snapshot"

# 快照统一以 JSON 落盘；必须显式传给后端，否则 S3/MinIO 会落为 binary/octet-stream
_SNAPSHOT_CONTENT_TYPE = "application/json"


def _is_enabled() -> bool:
    global _ENABLED
    if _ENABLED is None:
        _ENABLED = os.getenv("EXTRACT_SNAPSHOT_ENABLED", "false").lower() == "true"
    return _ENABLED


def _run_async(coro_factory: Callable[[], Coroutine[Any, Any, Any]]) -> Any:
    """在同步上下文中执行协程（仿照 rag/chunk/parser/image_storage.py 的 _run_async）。

    - 当前线程无运行中的事件循环（Celery prefork worker、脚本、测试）→ 直接 asyncio.run()
    - 已在事件循环内（write_pipeline / layer2_inspector 等 async 调用点）→ 不能嵌套
      asyncio.run，改为在临时线程里执行，调用线程 join 等待，异常原样回抛

    传工厂而非协程本身，保证协程在真正执行它的事件循环里创建。
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro_factory())

    result: dict[str, Any] = {}

    def runner() -> None:
        try:
            result["value"] = asyncio.run(coro_factory())
        except Exception as exc:
            result["error"] = exc

    thread = threading.Thread(target=runner, daemon=True)
    thread.start()
    thread.join()
    if "error" in result:
        raise result["error"]
    return result.get("value")


def upload_stage_snapshot(
    snapshot_dir: str, stage_name: str, data: Any
) -> bool:
    """将一个 stage 的数据序列化为 JSON 并上传到对象存储。

    后端由 STORAGE_TYPE 决定（local / oss / s3 / minio），与系统文件存储共用
    同一个 bucket 和凭据。

    供没有 ``PipelineSnapshot`` 实例的调用方使用（典型场景：Celery worker
    任务在主流水线之后异步落盘补充数据，需要写入主流水线已创建的同一个
    前缀下）。

    Args:
        snapshot_dir: 快照前缀路径（例如
            ``extract_snapshot/{end_user_id}/{conversation_id}/seq_xxx_时间戳``）。
        stage_name: 落盘的 stage 名（不带 ``.json`` 后缀），最终 key 为
            ``<snapshot_dir>/<stage_name>.json``。
        data: 任意可序列化对象（Pydantic 模型 / dict / list / dataclass）。

    Returns:
        上传成功返回 True，失败返回 False（失败仅打 warning，不抛异常）。
    """
    file_key = f"{snapshot_dir}/{stage_name}.json"
    try:
        # 延迟导入：避免模块加载时拉起全部存储后端依赖（boto3 / oss2 / aiofiles）
        from app.core.storage.factory import StorageFactory

        serialized = _safe_serialize(data)
        json_bytes = json.dumps(
            serialized, ensure_ascii=False, indent=2, default=str
        ).encode("utf-8")

        # 在调用线程获取单例，避免首次创建发生在 _run_async 的临时线程里
        storage = StorageFactory.get_storage()
        # upload 是 async，而本函数的调用方多数已在事件循环内，需经 _run_async 桥接
        _run_async(
            lambda: storage.upload(
                file_key=file_key,
                content=json_bytes,
                content_type=_SNAPSHOT_CONTENT_TYPE,
            )
        )
        logger.debug(f"[Snapshot] {stage_name} → {file_key}")
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
    """Dump each pipeline stage's output to object storage."""

    def __init__(
        self,
        end_user_id: str,
        conversation_id: Optional[str] = None,
        message_seq: Optional[int] = None,
        source: Optional[str] = None,
        extra_metadata: Optional[Dict[str, Any]] = None,
    ):
        """
        Args:
            end_user_id: 终端用户 ID，作为第一级目录。
            conversation_id: 对话 ID（滑动窗口写入时传入）。
                提供后会把它作为第二级目录，便于按对话归集快照。
            message_seq: 目标 user 消息的 message_seq（滑动窗口写入时传入）。
                提供后会写入叶子目录名（``seq_{message_seq:06d}_{时间戳}``），
                字典序与数值序一致，方便在存储客户端里顺序定位。
            source: 写入来源（'service_api' / 'mcp'）。当 conversation_id 为空时
                用作第二级目录名，按来源分类快照输出。
            extra_metadata: 任意可序列化的额外字段，会写入 ``0_summary.json``，
                典型字段：ref_id / dispatch_at / dialog_at / language /
                target_content_preview。
        """
        self.enabled = _is_enabled()
        self.end_user_id = end_user_id
        self.conversation_id = conversation_id
        self.message_seq = message_seq
        self.source = source
        self.extra_metadata: Dict[str, Any] = dict(extra_metadata or {})
        self._prefix: Optional[str] = None

        if self.enabled:
            ts = utcnow_naive().strftime("%Y%m%d_%H%M%S")
            seq_part = (
                f"seq_{int(message_seq):06d}_{ts}"
                if message_seq is not None
                else f"seq_unknown_{ts}"
            )
            if conversation_id:
                # Agent/Workflow 路径：按 user / conversation / seq_xxx_时间戳 三级组织
                self._prefix = (
                    f"{_SNAPSHOT_PREFIX}/{end_user_id}/"
                    f"{conversation_id}/{seq_part}"
                )
            elif source:
                # API/MCP 路径：按 user / source / seq_xxx_时间戳 三级组织
                self._prefix = (
                    f"{_SNAPSHOT_PREFIX}/{end_user_id}/"
                    f"{source}/{seq_part}"
                )
            else:
                # 兼容旧路径（未传 conversation_id 也未传 source）
                self._prefix = f"{_SNAPSHOT_PREFIX}/{end_user_id}_{ts}"
            logger.debug(f"[Snapshot] 已启用，前缀: {self._prefix}")

    @property
    def directory(self) -> Optional[str]:
        """对象存储前缀路径，未启用时返回 None。"""
        return self._prefix

    def save_stage(self, stage_name: str, data: Any) -> None:
        """Save a stage's output as JSON to object storage.

        Args:
            stage_name: e.g. "1_statements", "2_triplets"
            data: Any serializable data (Pydantic models, dicts, lists, dataclasses)
        """
        if not self.enabled or self._prefix is None:
            return
        upload_stage_snapshot(self._prefix, stage_name, data)

    def save_summary(self, stats: Dict[str, Any]) -> None:
        """Save a summary with pipeline metadata and stats.

        除统计信息外，还会写入定位元信息（end_user_id / conversation_id /
        message_seq）以及构造时传入的 ``extra_metadata``，便于在存储上
        通过 ``0_summary.json`` 直接确认是哪一次写入产生的快照。
        """
        if not self.enabled or self._prefix is None:
            return

        summary: Dict[str, Any] = {
            "end_user_id": self.end_user_id,
            "conversation_id": self.conversation_id,
            "message_seq": self.message_seq,
            "timestamp": to_iso_z(utcnow_naive()),
            "stats": stats,
        }
        if self.extra_metadata:
            summary.update(_safe_serialize(self.extra_metadata) or {})
        self.save_stage("0_summary", summary)
