"""ReflectionSnapshotRecorder — 反思引擎 Layer2 各子问题快照记录器。

把反思每个子问题的 input / llm_raw / changes 落盘到 OSS，供人工核对字段级变更
与「被过滤/跳过/拒绝」的中间命运。复用写链路的 OSS 落盘底座
(`pipeline_snapshot.upload_stage_snapshot`)，自管独立前缀 `reflection_snapshot/`。

受 env 变量 REFLECTION_SNAPSHOT_ENABLED 控制（默认 false），关闭时全部方法 no-op。
与写链路 PIPELINE_SNAPSHOT_ENABLED 相互独立。
"""

from __future__ import annotations

import logging
import os
import uuid
from typing import Any, Dict, List, Optional

from app.core.memory.utils.debug.pipeline_snapshot import upload_stage_snapshot
from app.core.utils.datetime_utils import to_iso_z, utcnow_naive

logger = logging.getLogger(__name__)

_ENABLED: Optional[bool] = None

# 反思快照 OSS 根前缀
_OSS_REFLECTION_PREFIX = "reflection_snapshot"


def _is_enabled() -> bool:
    global _ENABLED
    if _ENABLED is None:
        _ENABLED = os.getenv("REFLECTION_SNAPSHOT_ENABLED", "false").lower() == "true"
    return _ENABLED


def change(
    target_type: str,
    action: str,
    *,
    target_id: Optional[str] = None,
    target_name: Optional[str] = None,
    field_changes: Optional[List[Dict[str, Any]]] = None,
    status: str = "applied",
    reason: Optional[str] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """构造一条 ChangeRecord（字段级变更记录）。

    一条记录描述"对某个目标对象做了一次带 old/new 的字段级操作及其结果"，字段含义：
      - target_type: 目标类型（entity / edge / event / metadata_field / statement 等）
      - action: 操作类型（create / merge / delete / rename / add_field / update_field /
                delete_field / append_desc / mark / sync_pg 等）
      - field_changes: 字段级变更列表，每项 {"field": ..., "old": ..., "new": ...}；
                       old/new 空值约定：无前值用 None，被清空用 ""，删除用 new=None。
      - status: applied / skipped / rejected / filtered / truncated
      - reason: skip/reject/filter/truncate 的原因
      - extra: 子问题特有字段（如 confidence / loser_id / alias_id 等）
    """
    return {
        "target_type": target_type,
        "target_id": target_id,
        "target_name": target_name,
        "action": action,
        "field_changes": field_changes or [],
        "status": status,
        "reason": reason,
        "extra": extra or {},
    }


class ReflectionSnapshotRecorder:
    """反思一轮的快照记录器。每个 scan_type 一个实例，按子问题 / 阶段文件落盘。"""

    def __init__(
        self,
        end_user_id: str,
        scan_type: str,
        run_id: Optional[str] = None,
        baseline: str = "HYBRID",
        extra_metadata: Optional[Dict[str, Any]] = None,
    ):
        """
        Args:
            end_user_id: 终端用户 ID，OSS 第一级目录。
            scan_type: "layer2_frequent"（高频 run）/ "dedup_full_scan"（低频全量）。
            run_id: 运行标识，写入 0_summary.json；不传则生成。
            baseline: 反思基线，写入 0_summary.json。
            extra_metadata: 任意可序列化补充字段，并入 0_summary.json。
        """
        self.enabled = _is_enabled()
        self.end_user_id = end_user_id
        self.scan_type = scan_type
        self.run_id = run_id or uuid.uuid4().hex
        self.baseline = baseline
        self.extra_metadata: Dict[str, Any] = dict(extra_metadata or {})
        self._oss_prefix: Optional[str] = None
        self._wrote_any: bool = False  # 本轮是否落过任意 stage 文件

        if self.enabled:
            ts = utcnow_naive().strftime("%Y%m%d_%H%M%S")
            self._oss_prefix = (
                f"{_OSS_REFLECTION_PREFIX}/{end_user_id}/{scan_type}/{ts}"
            )
            logger.debug(f"[ReflectionSnapshot] 已启用，OSS 前缀: {self._oss_prefix}")

    @property
    def directory(self) -> Optional[str]:
        return self._oss_prefix

    def record_stage(self, subproblem: str, stage: str, data: Any) -> None:
        """落 <subproblem>/<stage>.json（如 unresolved_entity/1_input）。"""
        if not self.enabled or self._oss_prefix is None:
            return
        upload_stage_snapshot(f"{self._oss_prefix}/{subproblem}", stage, data)
        self._wrote_any = True  # 标记本轮确有反思活动产生了快照

    def record_changes(self, subproblem: str, change_records: List[Dict[str, Any]]) -> None:
        """落 <subproblem>/3_changes.json。"""
        self.record_stage(subproblem, "3_changes", change_records)

    def record_summary(self, results: Dict[str, Any]) -> None:
        """落根目录 0_summary.json。

        仅在本轮**确有 stage 文件落盘**（`_wrote_any`）时才写：纯空转轮（无任何
        子问题召回/处理）一个文件都不产生，连 0_summary 也不写。
        """
        if not self.enabled or self._oss_prefix is None:
            return
        if not self._wrote_any:
            logger.debug("[ReflectionSnapshot] 本轮无任何快照活动，跳过 0_summary")
            return
        summary: Dict[str, Any] = {
            "run_id": self.run_id,
            "end_user_id": self.end_user_id,
            "scan_type": self.scan_type,
            "baseline": self.baseline,
            "timestamp": to_iso_z(utcnow_naive()),
            "subproblems": results,
        }
        if self.extra_metadata:
            summary.update(self.extra_metadata)
        # 注意：0_summary 落在 run 根目录，subproblem 传空串即可
        upload_stage_snapshot(self._oss_prefix, "0_summary", summary)

    @staticmethod
    def truncate_vectors(data: Any, dims: int = 5) -> Any:
        """递归把向量（float 列表）截断到前 dims 维，其余结构原样保留。

        用于带原始 embedding 的快照（如 unresolved 建实体补 name_embedding 场景）；
        entity_dedup 候选无向量、无需调用。
        """
        if isinstance(data, dict):
            return {k: ReflectionSnapshotRecorder.truncate_vectors(v, dims)
                    for k, v in data.items()}
        if isinstance(data, list):
            if data and all(isinstance(x, (int, float)) for x in data):
                return data[:dims]
            return [ReflectionSnapshotRecorder.truncate_vectors(x, dims) for x in data]
        return data
