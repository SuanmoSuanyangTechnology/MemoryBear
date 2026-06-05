from typing import Any

from app.core.moderation.base import (
    ModerationAction,
    ModerationBase,
    ModerationInputsResult,
    ModerationOutputsResult,
)


class KeywordsModeration(ModerationBase):
    MAX_KEYWORDS_ROWS = 100
    MAX_KEYWORD_LENGTH = 100

    @classmethod
    def validate_config(cls, config: dict[str, Any]) -> None:
        cls._validate_inputs_outputs_config(config, is_preset_response_required=True)

        keywords = config.get("keywords", "")
        if not keywords:
            raise ValueError("keywords is required")

        keywords_rows = keywords.split("\n")
        if len(keywords_rows) > cls.MAX_KEYWORDS_ROWS:
            raise ValueError(f"keywords rows must be less than {cls.MAX_KEYWORDS_ROWS}")

        for row in keywords_rows:
            if len(row) > cls.MAX_KEYWORD_LENGTH:
                raise ValueError(f"each keyword row must be less than {cls.MAX_KEYWORD_LENGTH} characters")

    def moderation_for_inputs(self, inputs: dict[str, Any], query: str = "") -> ModerationInputsResult:
        if not self.config.get("inputs_config", {}).get("enabled"):
            return ModerationInputsResult(flagged=False)

        preset_response = self.config["inputs_config"]["preset_response"]
        check_inputs = dict(inputs)
        if query:
            check_inputs["query__"] = query

        keywords_list = self._parse_keywords()
        flagged = self._is_violated(check_inputs, keywords_list)

        return ModerationInputsResult(
            flagged=flagged,
            action=ModerationAction.DIRECT_OUTPUT,
            preset_response=preset_response,
        )

    def moderation_for_outputs(self, text: str) -> ModerationOutputsResult:
        if not self.config.get("outputs_config", {}).get("enabled"):
            return ModerationOutputsResult(flagged=False)

        preset_response = self.config["outputs_config"]["preset_response"]
        keywords_list = self._parse_keywords()
        flagged = self._is_violated({"text": text}, keywords_list)

        return ModerationOutputsResult(
            flagged=flagged,
            action=ModerationAction.DIRECT_OUTPUT,
            preset_response=preset_response,
        )

    def _parse_keywords(self) -> list[str]:
        keywords = self.config.get("keywords", "")
        return [kw.strip() for kw in keywords.split("\n") if kw.strip()]

    def _is_violated(self, data: dict[str, Any], keywords_list: list[str]) -> bool:
        for value in data.values():
            value_str = str(value).lower()
            for keyword in keywords_list:
                if keyword.lower() in value_str:
                    return True
        return False
