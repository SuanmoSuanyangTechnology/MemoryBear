import logging
from typing import Any

from app.core.moderation.base import ModerationAction
from app.core.moderation.factory import ModerationFactory

logger = logging.getLogger(__name__)


class InputModeration:
    def check(
        self,
        app_id: str,
        tenant_id: str,
        moderation_type: str,
        moderation_config: dict[str, Any],
        inputs: dict[str, Any],
        query: str,
    ) -> tuple[bool, str, dict[str, Any], str]:
        """
        执行输入审查

        Returns:
            (stop, preset_response, new_inputs, new_query)
        """
        if not moderation_config.get("enabled"):
            return False, "", inputs, query

        try:
            moderation = ModerationFactory.create(
                moderation_type=moderation_type,
                app_id=app_id,
                tenant_id=tenant_id,
                config=moderation_config.get("config", {}),
            )
            result = moderation.moderation_for_inputs(inputs, query)

            if not result.flagged:
                return False, "", inputs, query

            if result.action == ModerationAction.DIRECT_OUTPUT:
                return True, result.preset_response, inputs, query
            elif result.action == ModerationAction.OVERRIDDEN:
                return False, "", result.inputs, result.query

            return False, "", inputs, query

        except Exception as e:
            logger.error(f"Input moderation failed: {e}")
            return False, "", inputs, query
