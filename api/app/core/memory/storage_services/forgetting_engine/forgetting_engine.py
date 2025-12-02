"""遗忘引擎实现

该模块实现基于改进的艾宾浩斯遗忘曲线的记忆遗忘机制。

遗忘曲线公式：
R(t, S) = offset + (1 - offset) * exp(-λ_time * t / (λ_mem * S))

其中：
- R: 记忆保持率 (0 到 1)
- t: 自学习以来经过的时间
- S: 记忆强度（值越高表示记忆越强）
- offset: 最小保持率（防止完全遗忘）
- λ_time: 控制时间效应的 Lambda 参数
- λ_mem: 控制记忆强度效应的 Lambda 参数
"""

import math
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta
from app.core.memory.models.variate_config import ForgettingEngineConfig


class ForgettingEngine:
    """遗忘引擎 - 实现记忆遗忘机制

    该引擎基于改进的艾宾浩斯遗忘曲线计算记忆保持率，
    结合时间衰减和记忆强度因素，支持可配置的遗忘行为。

    Attributes:
        config: 遗忘引擎配置
        offset: 最小保持率（防止完全遗忘）
        lambda_time: 控制时间衰减效应的参数
        lambda_mem: 控制记忆强度效应的参数
    """

    def __init__(self, config: Optional[ForgettingEngineConfig] = None):
        """初始化遗忘引擎

        Args:
            config: ForgettingEngineConfig 实例，包含遗忘参数配置
        """
        if config is None:
            config = ForgettingEngineConfig()

        self.config = config
        self.offset = config.offset
        self.lambda_time = config.lambda_time
        self.lambda_mem = config.lambda_mem

    def forgetting_curve(self, t: float, S: float) -> float:
        """使用改进的艾宾浩斯遗忘曲线计算记忆保持率

        公式: R = offset + (1-offset) * e^(-λ_time * t / (λ_mem * S))

        Args:
            t: 自学习以来经过的时间
            S: 记忆的相对强度

        Returns:
            记忆保持率，值在 0 到 1 之间
        """
        if S <= 0:
            return self.offset

        exponent = -self.lambda_time * t / (self.lambda_mem * S)
        retention = self.offset + (1 - self.offset) * math.exp(exponent)

        # 确保保持率在 0 到 1 之间
        return max(0.0, min(1.0, retention))

    def calculate_forgetting_score(self, time_elapsed: float, memory_strength: float) -> float:
        """计算记忆项的遗忘分数

        遗忘分数 = 1 - 保持率，值越高表示越容易被遗忘

        Args:
            time_elapsed: 自记忆创建/最后访问以来的时间
            memory_strength: 记忆强度（值越高表示越难忘记）

        Returns:
            遗忘分数，值在 0 到 1 之间
        """
        retention = self.forgetting_curve(time_elapsed, memory_strength)
        return 1.0 - retention

    def calculate_weight(self, time_elapsed: float, memory_strength: float) -> float:
        """计算记忆项的权重（即保持率）

        Args:
            time_elapsed: 自记忆创建/最后访问以来的时间
            memory_strength: 记忆强度（值越高表示越难忘记）

        Returns:
            权重值，值在 0 到 1 之间
        """
        return self.forgetting_curve(time_elapsed, memory_strength)

    def apply_forgetting_weights(
        self,
        items: List[dict],
        time_key: str = 'time_elapsed',
        strength_key: str = 'strength'
    ) -> List[dict]:
        """为记忆项列表应用遗忘权重

        Args:
            items: 包含记忆项的字典列表
            time_key: 每个项中时间经过的键名
            strength_key: 每个项中记忆强度的键名

        Returns:
            添加了 'forgetting_weight' 字段的项列表
        """
        weighted_items = []

        for item in items:
            item_copy = item.copy()
            time_elapsed = item.get(time_key, 0)
            strength = item.get(strength_key, 1.0)

            weight = self.calculate_weight(time_elapsed, strength)
            item_copy['forgetting_weight'] = weight

            weighted_items.append(item_copy)

        return weighted_items

    def mark_items_for_forgetting(
        self,
        items: List[dict],
        forgetting_threshold: float = 0.5,
        time_key: str = 'time_elapsed',
        strength_key: str = 'strength'
    ) -> tuple[List[dict], List[dict]]:
        """标记应该被遗忘的记忆项

        Args:
            items: 包含记忆项的字典列表
            forgetting_threshold: 遗忘阈值，遗忘分数超过此值的项将被标记
            time_key: 每个项中时间经过的键名
            strength_key: 每个项中记忆强度的键名

        Returns:
            元组 (应保留的项列表, 应遗忘的项列表)
        """
        to_keep = []
        to_forget = []

        for item in items:
            time_elapsed = item.get(time_key, 0)
            strength = item.get(strength_key, 1.0)

            forgetting_score = self.calculate_forgetting_score(time_elapsed, strength)

            item_copy = item.copy()
            item_copy['forgetting_score'] = forgetting_score

            if forgetting_score > forgetting_threshold:
                to_forget.append(item_copy)
            else:
                to_keep.append(item_copy)

        return to_keep, to_forget

    def get_forgetting_statistics(
        self,
        items: List[dict],
        forgetting_threshold: float = 0.5,
        time_key: str = 'time_elapsed',
        strength_key: str = 'strength'
    ) -> Dict[str, Any]:
        """获取记忆项的遗忘统计信息

        Args:
            items: 包含记忆项的字典列表
            forgetting_threshold: 遗忘阈值
            time_key: 每个项中时间经过的键名
            strength_key: 每个项中记忆强度的键名

        Returns:
            包含统计信息的字典：
            - total_items: 总项数
            - items_to_keep: 应保留的项数
            - items_to_forget: 应遗忘的项数
            - forgetting_rate: 遗忘率
            - average_retention: 平均保持率
            - average_forgetting_score: 平均遗忘分数
        """
        if not items:
            return {
                "total_items": 0,
                "items_to_keep": 0,
                "items_to_forget": 0,
                "forgetting_rate": 0.0,
                "average_retention": 0.0,
                "average_forgetting_score": 0.0
            }

        to_keep, to_forget = self.mark_items_for_forgetting(
            items, forgetting_threshold, time_key, strength_key
        )

        total = len(items)
        keep_count = len(to_keep)
        forget_count = len(to_forget)

        # 计算平均保持率和遗忘分数
        total_retention = 0.0
        total_forgetting_score = 0.0

        for item in items:
            time_elapsed = item.get(time_key, 0)
            strength = item.get(strength_key, 1.0)

            retention = self.calculate_weight(time_elapsed, strength)
            forgetting_score = self.calculate_forgetting_score(time_elapsed, strength)

            total_retention += retention
            total_forgetting_score += forgetting_score

        avg_retention = total_retention / total
        avg_forgetting_score = total_forgetting_score / total

        return {
            "total_items": total,
            "items_to_keep": keep_count,
            "items_to_forget": forget_count,
            "forgetting_rate": forget_count / total,
            "average_retention": avg_retention,
            "average_forgetting_score": avg_forgetting_score
        }

    def calculate_time_elapsed_days(
        self,
        created_at: datetime,
        current_time: Optional[datetime] = None
    ) -> float:
        """计算经过的天数

        Args:
            created_at: 创建时间
            current_time: 当前时间，如果为 None 则使用当前系统时间

        Returns:
            经过的天数（浮点数）
        """
        if current_time is None:
            current_time = datetime.now()

        time_diff = current_time - created_at
        return time_diff.total_seconds() / (24 * 3600)

    def calculate_time_elapsed_hours(
        self,
        created_at: datetime,
        current_time: Optional[datetime] = None
    ) -> float:
        """计算经过的小时数

        Args:
            created_at: 创建时间
            current_time: 当前时间，如果为 None 则使用当前系统时间

        Returns:
            经过的小时数（浮点数）
        """
        if current_time is None:
            current_time = datetime.now()

        time_diff = current_time - created_at
        return time_diff.total_seconds() / 3600
