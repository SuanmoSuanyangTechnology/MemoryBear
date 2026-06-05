import logging
from typing import Any

from app.core.moderation.factory import ModerationFactory

logger = logging.getLogger(__name__)


class OutputModeration:
    CHECK_INTERVAL = 1

    def __init__(
        self,
        app_id: str,
        tenant_id: str,
        moderation_type: str,
        moderation_config: dict[str, Any],
    ):
        self._app_id = app_id
        self._tenant_id = tenant_id
        self._moderation_type = moderation_type
        self._config = moderation_config or {}
        self._accumulated_text: str = ""
        self._last_checked_length: int = 0
        self._flagged = False
        self._preset_response = ""

    @property
    def enabled(self) -> bool:
        if not self._config.get("enabled"):
            return False
        outputs_config = self._config.get("config", {}).get("outputs_config") or {}
        return outputs_config.get("enabled", False)

    @property
    def is_flagged(self) -> bool:
        return self._flagged

    @property
    def preset_response(self) -> str:
        return self._preset_response

    def accumulate(self, chunk: str) -> bool:
        """累积 chunk，每达到 CHECK_INTERVAL 增量检查一次，返回是否触发审查"""
        self._accumulated_text += chunk
        if len(self._accumulated_text) - self._last_checked_length >= self.CHECK_INTERVAL:
            return self._check()
        return False

    def check_final(self) -> bool:
        """流结束时检查完整累积文本"""
        return self._check()

    def _check(self) -> bool:
        if self._flagged:
            return True

        text_to_check = self._accumulated_text[self._last_checked_length:]
        if not text_to_check.strip():
            return False

        try:
            moderation = ModerationFactory.create(
                moderation_type=self._moderation_type,
                app_id=self._app_id,
                tenant_id=self._tenant_id,
                config=self._config.get("config", {}),
            )
            result = moderation.moderation_for_outputs(self._accumulated_text)
            self._last_checked_length = len(self._accumulated_text)

            if result.flagged:
                self._flagged = True
                self._preset_response = result.preset_response
                return True

            return False

        except Exception as e:
            logger.error(f"Output moderation failed: {e}")
            return False
