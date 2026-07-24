"""遗忘引擎模块

该模块实现记忆的遗忘机制，基于改进的艾宾浩斯遗忘曲线和 ACT-R 认知架构理论。
"""

from app.core.memory.storage_services.forgetting_engine.forgetting_engine import ForgettingEngine
from app.core.memory.storage_services.forgetting_engine.actr_calculator import (
    ACTRCalculator,
    calculate_activation,
    generate_forgetting_curve
)
from app.core.memory.storage_services.forgetting_engine.access_history_manager import (
    AccessHistoryManager,
    ConsistencyCheckResult
)
from app.core.memory.storage_services.forgetting_engine.forgetting_strategy import (
    ForgettingStrategy
)
from app.core.memory.storage_services.forgetting_engine.forgetting_scheduler import (
    ForgettingScheduler
)
from app.core.memory.storage_services.forgetting_engine.config_utils import (
    calculate_forgetting_rate,
    load_actr_config_from_db,
    create_actr_calculator_from_config
)

__all__ = [
    "ForgettingEngine",
    "ACTRCalculator",
    "calculate_activation",
    "generate_forgetting_curve",
    "AccessHistoryManager",
    "ConsistencyCheckResult",
    "ForgettingStrategy",
    "ForgettingScheduler",
    "calculate_forgetting_rate",
    "load_actr_config_from_db",
    "create_actr_calculator_from_config"
]
