"""BERT 场景切分演示：单次只读判定预览。

按生产 Scene 切分的规则顺序复现一次判定（最大场景轮次 → 最小保护轮次 → BERT 语义），
不读写库、不落 Scene 记录、不影响生产切分行为。
"""

from __future__ import annotations

from decimal import ROUND_DOWN, Decimal

from app.core.memory.scene.scene_continuity_bert_client import SceneContinuityBertClient
from app.i18n.service import t
from app.schemas.scene_memory_schema import SceneSplitDemoResponse

CLS_TOKEN = "[CLS]"
SEP_TOKEN = "[SEP]"


def truncate_two_decimals(value: float) -> float:
    """按两位小数截断（不进位），避免 0.9976 被四舍五入成 1.00。"""
    return float(Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_DOWN))


class SceneSplitDemoService:

    @staticmethod
    def build_sequence(history_messages: list[str], current_query: str) -> str:
        """拼接 [CLS] + 每条历史 + [SEP] + 当前 Query + [SEP]。"""
        parts = [CLS_TOKEN]
        for message in history_messages:
            parts.append(message)
            parts.append(SEP_TOKEN)
        parts.append(current_query)
        parts.append(SEP_TOKEN)
        return "".join(parts)

    @staticmethod
    def _scene_summary(prediction: str, locale: str) -> dict:
        suffix = "continue" if prediction == "CONTINUE" else "shifted"
        return {
            "text": t(f"memory_config.scene_demo.summary_{suffix}_text", locale=locale),
            "hint": t(f"memory_config.scene_demo.summary_{suffix}_hint", locale=locale),
        }

    @staticmethod
    async def run(
        *,
        history_messages: list[str],
        current_query: str,
        current_scene_turns: int,
        threshold: float,
        window_size: int,
        min_turns: int,
        max_turns: int,
        locale: str,
    ) -> SceneSplitDemoResponse:
        window = history_messages[-window_size:]
        sequence = SceneSplitDemoService.build_sequence(window, current_query)
        shared = {
            "threshold": threshold,
            "window_size": len(window),
            "input_chars": len(sequence),
            "sequence": sequence,
        }

        # 硬规则 1：无历史消息即首个 Scene 的起点，强制开启新 Scene，不调用模型。
        if not history_messages:
            return SceneSplitDemoResponse(
                score=0.0,
                prediction="SHIFTED",
                short_circuit="first_scene",
                reason=t(
                    "memory_config.scene_demo.reason_first_scene",
                    locale=locale,
                    turns=current_scene_turns,
                ),
                scene_summary=SceneSplitDemoService._scene_summary("SHIFTED", locale),
                **shared,
            )

        # 硬规则 2：当前 Scene 已达最大场景轮次，强制开启新 Scene，不调用模型。
        if current_scene_turns >= max_turns:
            return SceneSplitDemoResponse(
                score=0.0,
                prediction="SHIFTED",
                short_circuit="max_turns",
                reason=t(
                    "memory_config.scene_demo.reason_max_turns",
                    locale=locale,
                    turns=current_scene_turns,
                ),
                scene_summary=SceneSplitDemoService._scene_summary("SHIFTED", locale),
                **shared,
            )

        # 保护规则：当前 Scene 轮次低于最小保护轮次，强制延续，不调用模型。
        if current_scene_turns < min_turns:
            return SceneSplitDemoResponse(
                score=0.0,
                prediction="CONTINUE",
                short_circuit="min_turns",
                reason=t(
                    "memory_config.scene_demo.reason_min_turns",
                    locale=locale,
                    turns=current_scene_turns,
                ),
                scene_summary=SceneSplitDemoService._scene_summary("CONTINUE", locale),
                **shared,
            )

        raw_score = await SceneContinuityBertClient().predict(window, current_query)
        # 判定用模型原始分数，严格大于：score > threshold 判 CONTINUE，否则 SHIFTED。
        prediction = "CONTINUE" if raw_score > threshold else "SHIFTED"
        # 展示值截断到两位小数，与 reason 文案保持一致。
        score = truncate_two_decimals(raw_score)
        reason_key = (
            "memory_config.scene_demo.reason_continue"
            if prediction == "CONTINUE"
            else "memory_config.scene_demo.reason_shifted"
        )
        return SceneSplitDemoResponse(
            score=score,
            prediction=prediction,
            short_circuit=None,
            reason=t(
                reason_key,
                locale=locale,
                score=f"{score:.2f}",
                threshold=f"{threshold:.2f}",
            ),
            scene_summary=SceneSplitDemoService._scene_summary(prediction, locale),
            **shared,
        )
