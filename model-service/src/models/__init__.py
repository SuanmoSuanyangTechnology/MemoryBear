"""认领表的聚合入口：导入即把四表 + FK 锚点注册进 ServiceBase.metadata。

迁移链 env.py 依赖本模块（`import src.models`）填充 target_metadata；
只读映射（references/*）属另一 metadata，不在此聚合。
"""

from . import fk_anchors  # noqa: F401  FK 锚点，供 model_configs/model_channels 的 tenant_id 解析
from .model_usage_record import ModelUsageRecord
from .models_model import (
    ModelApiKey,
    ModelBase,
    ModelChannel,
    ModelConfig,
    model_config_api_key_association,
)

__all__ = [
    "ModelApiKey",
    "ModelBase",
    "ModelChannel",
    "ModelConfig",
    "ModelUsageRecord",
    "model_config_api_key_association",
]
