"""Scene boundary context preparation and decision orchestration."""

from __future__ import annotations

from app.repositories.memory_message_repository import MemoryMessageRepository
from app.schemas.scene_memory_schema import SceneBoundary, SceneContext


class SceneBoundaryService:

    @staticmethod
    def prepare_context(db, *, resolved_end_user_id: str, memory_message_id: str, config) -> SceneContext | None:
        raw = MemoryMessageRepository(db).get_scene_context(
            resolved_end_user_id=resolved_end_user_id,
            memory_message_id=memory_message_id,
            history_window_size=config.scene_history_window_size,
        )
        if raw is None:
            return None
        direct = None

        # 幂等规则：当前消息已有判断结果时直接复用，不重新执行规则或调用 BERT。
        if raw["existing_boundary"] is not None:
            direct = raw["existing_boundary"]
        # 硬规则 1：不存在历史 SHIFTED，当前 user 作为首个 Scene 的起点。
        elif raw["previous_shifted_message_id"] is None:
            direct = "SHIFTED"
        # 硬规则 2：与同一 Stream 上一条有效消息的静默间隔达到配置阈值，强制开启新 Scene。
        elif raw["previous_message_created_at"] is not None:
            idle = (raw["current_created_at"] - raw["previous_message_created_at"]).total_seconds()
            if idle >= config.scene_idle_timeout_seconds:
                direct = "SHIFTED"
        # 硬规则 3：当前 Scene 已达到最大 user 轮次，当前 user 开启新 Scene。
        if direct is None and raw["current_scene_turn_count"] >= config.scene_max_turns:
            direct = "SHIFTED"
        # 保护规则：最小轮次内强制延续；保护结束且无硬规则命中时才交给 BERT。
        if direct is None and raw["current_scene_turn_count"] < config.scene_min_turns:
            direct = "CONTINUE"
        return SceneContext(
            resolved_end_user_id=resolved_end_user_id,
            current_message_id=memory_message_id,
            current_content=raw["current_content"],
            previous_shifted_message_id=raw["previous_shifted_message_id"],
            history_user_messages=raw["history_user_messages"],
            direct_decision=direct,
        )

    @staticmethod
    def save_initial_decision(db, *, context: SceneContext, decision: SceneBoundary) -> tuple[bool, str | None]:
        return MemoryMessageRepository(db).cas_scene_boundary(
            memory_message_id=context.current_message_id,
            resolved_end_user_id=context.resolved_end_user_id,
            decision=decision,
        )

    @staticmethod
    def claim_summary(db, *, scene_start_message_id: str, end_user_id: str) -> bool:
        return MemoryMessageRepository(db).claim_scene_summary(
            scene_start_message_id=scene_start_message_id,
            end_user_id=end_user_id,
        )

    @staticmethod
    def release_summary_claim(db, *, scene_start_message_id: str, end_user_id: str) -> bool:
        return MemoryMessageRepository(db).release_scene_summary_claim(
            scene_start_message_id=scene_start_message_id,
            end_user_id=end_user_id,
        )
