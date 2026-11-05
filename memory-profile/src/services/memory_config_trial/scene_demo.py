"""BERT scene-split demo: one read-only prediction outside the pipelines."""

from __future__ import annotations

from decimal import ROUND_DOWN, Decimal

from src.i18n import t
from src.infrastructure.scene_continuity import SceneContinuityClient
from src.schemas.scene_demo import SceneSplitDemoResponse

CLS_TOKEN = "[CLS]"
SEP_TOKEN = "[SEP]"


def truncate_two_decimals(value: float) -> float:
    return float(Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_DOWN))


class SceneSplitDemoService:
    def __init__(self, client: SceneContinuityClient) -> None:
        self.client = client

    @staticmethod
    def build_sequence(history_messages: list[str], current_query: str) -> str:
        parts = [CLS_TOKEN]
        for message in history_messages:
            parts.append(message)
            parts.append(SEP_TOKEN)
        parts.append(current_query)
        parts.append(SEP_TOKEN)
        return "".join(parts)

    @staticmethod
    def _scene_summary(prediction: str, locale: str) -> dict[str, str]:
        suffix = "continue" if prediction == "CONTINUE" else "shifted"
        return {
            "text": t(f"memory_config.scene_demo.summary_{suffix}_text", locale),
            "hint": t(f"memory_config.scene_demo.summary_{suffix}_hint", locale),
        }

    async def run(
        self,
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
        sequence = self.build_sequence(window, current_query)
        shared = {
            "threshold": threshold,
            "window_size": len(window),
            "input_chars": len(sequence),
            "sequence": sequence,
        }

        if not history_messages:
            return SceneSplitDemoResponse(
                score=0.0,
                prediction="SHIFTED",
                short_circuit="first_scene",
                reason=t(
                    "memory_config.scene_demo.reason_first_scene",
                    locale,
                    turns=current_scene_turns,
                ),
                scene_summary=self._scene_summary("SHIFTED", locale),
                **shared,
            )

        if current_scene_turns >= max_turns:
            return SceneSplitDemoResponse(
                score=0.0,
                prediction="SHIFTED",
                short_circuit="max_turns",
                reason=t(
                    "memory_config.scene_demo.reason_max_turns",
                    locale,
                    turns=current_scene_turns,
                ),
                scene_summary=self._scene_summary("SHIFTED", locale),
                **shared,
            )

        if current_scene_turns < min_turns:
            return SceneSplitDemoResponse(
                score=0.0,
                prediction="CONTINUE",
                short_circuit="min_turns",
                reason=t(
                    "memory_config.scene_demo.reason_min_turns",
                    locale,
                    turns=current_scene_turns,
                ),
                scene_summary=self._scene_summary("CONTINUE", locale),
                **shared,
            )

        raw_score = await self.client.predict(window, current_query)
        prediction = "CONTINUE" if raw_score > threshold else "SHIFTED"
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
                locale,
                score=f"{score:.2f}",
                threshold=f"{threshold:.2f}",
            ),
            scene_summary=self._scene_summary(prediction, locale),
            **shared,
        )
