"""Trial-run services: probes and previews that never write ``memory_config``.

Everything here reads configuration and calls an external or static computation;
none of it persists. Keeping them apart from the table-operating services makes
the write boundary visible: only :mod:`src.services.memory_config` mutates rows.
"""

from src.services.memory_config_trial.community_preview import (
    SceneCommunityPreviewService,
    UnsupportedPreviewCaseError,
)
from src.services.memory_config_trial.scene_demo import SceneSplitDemoService
from src.services.memory_config_trial.validation import ValidConfigService

__all__ = [
    "SceneCommunityPreviewService",
    "SceneSplitDemoService",
    "UnsupportedPreviewCaseError",
    "ValidConfigService",
]
