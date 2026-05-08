"""Pipeline stage snapshot — dump each extraction stage's output to JSON for comparison.

Usage:
    snapshot = PipelineSnapshot("legacy")   # or "new"
    snapshot.save_stage("1_statements", data)
    snapshot.save_stage("2_triplets", data)
    ...

Output structure:
    logs/memory-output/snapshots/
        legacy_20260422_123456/
            1_statements.json
            2_triplets.json
            3_nodes_edges.json
            4_dedup.json
        new_20260422_123500/
            1_statements.json
            2_triplets.json
            3_nodes_edges.json
            4_dedup.json

Controlled by env var PIPELINE_SNAPSHOT_ENABLED (default: false).
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_ENABLED: Optional[bool] = None


def _is_enabled() -> bool:
    global _ENABLED
    if _ENABLED is None:
        _ENABLED = os.getenv("PIPELINE_SNAPSHOT_ENABLED", "false").lower() == "true"
    return _ENABLED


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
    """Dump each pipeline stage's output to a timestamped directory."""

    def __init__(self, pipeline_name: str):
        """
        Args:
            pipeline_name: "legacy" or "new", used as directory prefix.
        """
        self.enabled = _is_enabled()
        self.pipeline_name = pipeline_name
        self._dir: Optional[Path] = None

        if self.enabled:
            from app.core.config import settings
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            self._dir = Path(settings.MEMORY_OUTPUT_DIR) / "snapshots" / f"{pipeline_name}_{ts}"
            self._dir.mkdir(parents=True, exist_ok=True)
            logger.debug(f"[Snapshot] 已启用，输出目录: {self._dir}")

    @property
    def directory(self) -> Optional[str]:
        """Absolute path (str) of this snapshot's output directory, or None when disabled."""
        return str(self._dir) if self._dir is not None else None

    def save_stage(self, stage_name: str, data: Any) -> None:
        """Save a stage's output as JSON.

        Args:
            stage_name: e.g. "1_statements", "2_triplets"
            data: Any serializable data (Pydantic models, dicts, lists, dataclasses)
        """
        if not self.enabled or self._dir is None:
            return

        try:
            path = self._dir / f"{stage_name}.json"
            serialized = _safe_serialize(data)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(serialized, f, ensure_ascii=False, indent=2, default=str)
            logger.debug(f"[Snapshot] {stage_name} → {path}")
        except Exception as e:
            logger.warning(f"[Snapshot] 保存 {stage_name} 失败: {e}")

    def save_summary(self, stats: Dict[str, Any]) -> None:
        """Save a summary with pipeline metadata and stats."""
        if not self.enabled or self._dir is None:
            return

        summary = {
            "pipeline": self.pipeline_name,
            "timestamp": datetime.now().isoformat(),
            "stats": stats,
        }
        self.save_stage("0_summary", summary)
