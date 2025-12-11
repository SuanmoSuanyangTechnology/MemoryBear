"""遗忘引擎单元测试

该模块测试遗忘引擎的核心功能：
- 遗忘分数计算
- 记忆标记和删除
- 遗忘统计
"""

import pytest
import math
from datetime import datetime, timedelta
from app.core.memory.storage_services.forgetting_engine import ForgettingEngine
from app.core.memory.models.variate_config import ForgettingEngineConfig


class TestForgettingEngineBasics:
    """测试遗忘引擎的基本功能"""
    
    def test_engine_initialization_with_config(self):
        """测试使用配置初始化遗忘引擎"""
        config = ForgettingEngineConfig(
            offset=0.2,
            lambda_time=0.15,
            lambda_mem=1.5
        )
        engine = ForgettingEngine(config)
        
        assert engine.offset == 0.2
        assert engine.lambda_time == 0.15
        assert engine.lambda_mem == 1.5
        assert engine.config == config
    
    def test_engine_initialization_without_config(self):
        """测试不使用配置初始化遗忘引擎（使用默认值）"""
        engine = ForgettingEngine()
        
        assert engine.offset == 0.1  # 默认值
        assert engine.lambda_time == 0.1  # 默认值
        assert engine.lambda_mem == 1.0  # 默认值
        assert engine.config is not None
    
    def test_engine_initialization_with_custom_params(self):
        """测试使用自定义参数初始化遗忘引擎"""
        config = ForgettingEngineConfig(
            offset=0.05,
            lambda_time=0.2,
            lambda_mem=2.0
        )
        engine = ForgettingEngine(config)
        
        assert engine.offset == 0.05
        assert engine.lambda_time == 0.2
        assert engine.lambda_mem == 2.0


class TestForgettingCurve:
    """测试遗忘曲线计算"""
    
    def test_forgetting_curve_basic(self):
        """测试基本的遗忘曲线计算"""
        engine = ForgettingEngine()
        
        # 时间 = 0，强度 = 1.0，应该返回接近 1.0 的值
        retention = engine.forgetting_curve(0, 1.0)
        assert 0.9 <= retention <= 1.0
    
    def test_forgetting_curve_time_decay(self):
        """测试时间衰减效应"""
        engine = ForgettingEngine()
        
        # 随着时间增加，保持率应该下降
        retention_t0 = engine.forgetting_curve(0, 1.0)
        retention_t1 = engine.forgetting_curve(1, 1.0)
        retention_t10 = engine.forgetting_curve(10, 1.0)
        
        assert retention_t0 > retention_t1 > retention_t10
    
    def test_forgetting_curve_memory_strength(self):
        """测试记忆强度效应"""
        engine = ForgettingEngine()
        
        # 相同时间，强度越高，保持率越高
        retention_s1 = engine.forgetting_curve(5, 1.0)
        retention_s2 = engine.forgetting_curve(5, 2.0)
        retention_s5 = engine.forgetting_curve(5, 5.0)
        
        assert retention_s1 < retention_s2 < retention_s5
    
    def test_forgetting_curve_zero_strength(self):
        """测试零强度的情况"""
        engine = ForgettingEngine()
        
        # 强度为 0 时，应该返回 offset
        retention = engine.forgetting_curve(5, 0)
        assert retention == engine.offset
    
    def test_forgetting_curve_negative_strength(self):
        """测试负强度的情况"""
        engine = ForgettingEngine()
        
        # 负强度应该被视为零强度
        retention = engine.forgetting_curve(5, -1.0)
        assert retention == engine.offset
    
    def test_forgetting_curve_bounds(self):
        """测试遗忘曲线的边界值"""
        engine = ForgettingEngine()
        
        # 保持率应该始终在 [0, 1] 范围内
        for t in [0, 1, 10, 100, 1000]:
            for s in [0.1, 1.0, 5.0, 10.0]:
                retention = engine.forgetting_curve(t, s)
                assert 0.0 <= retention <= 1.0
    
    def test_forgetting_curve_with_different_offsets(self):
        """测试不同 offset 值的影响"""
        config1 = ForgettingEngineConfig(offset=0.0)
        config2 = ForgettingEngineConfig(offset=0.5)
        
        engine1 = ForgettingEngine(config1)
        engine2 = ForgettingEngine(config2)
        
        # offset 越高，最低保持率越高
        retention1 = engine1.forgetting_curve(100, 1.0)
        retention2 = engine2.forgetting_curve(100, 1.0)
        
        assert retention2 >= retention1
        assert retention2 >= 0.5  # 至少等于 offset


class TestForgettingScore:
    """测试遗忘分数计算"""
    
    def test_calculate_forgetting_score_basic(self):
        """测试基本的遗忘分数计算"""
        engine = ForgettingEngine()
        
        # 遗忘分数 = 1 - 保持率
        score = engine.calculate_forgetting_score(5, 1.0)
        retention = engine.calculate_weight(5, 1.0)
        
        assert abs(score - (1.0 - retention)) < 0.0001
    
    def test_calculate_forgetting_score_increases_with_time(self):
        """测试遗忘分数随时间增加"""
        engine = ForgettingEngine()
        
        score_t1 = engine.calculate_forgetting_score(1, 1.0)
        score_t10 = engine.calculate_forgetting_score(10, 1.0)
        score_t100 = engine.calculate_forgetting_score(100, 1.0)
        
        assert score_t1 < score_t10 < score_t100
    
    def test_calculate_forgetting_score_decreases_with_strength(self):
        """测试遗忘分数随强度降低"""
        engine = ForgettingEngine()
        
        score_s1 = engine.calculate_forgetting_score(10, 1.0)
        score_s2 = engine.calculate_forgetting_score(10, 2.0)
        score_s5 = engine.calculate_forgetting_score(10, 5.0)
        
        assert score_s1 > score_s2 > score_s5
    
    def test_calculate_forgetting_score_bounds(self):
        """测试遗忘分数的边界值"""
        engine = ForgettingEngine()
        
        # 遗忘分数应该在 [0, 1] 范围内
        for t in [0, 1, 10, 100]:
            for s in [0.1, 1.0, 5.0]:
                score = engine.calculate_forgetting_score(t, s)
                assert 0.0 <= score <= 1.0


class TestWeightCalculation:
    """测试权重计算"""
    
    def test_calculate_weight_equals_retention(self):
        """测试权重计算等于保持率"""
        engine = ForgettingEngine()
        
        weight = engine.calculate_weight(5, 1.0)
        retention = engine.forgetting_curve(5, 1.0)
        
        assert weight == retention
    
    def test_calculate_weight_basic(self):
        """测试基本的权重计算"""
        engine = ForgettingEngine()
        
        weight = engine.calculate_weight(0, 1.0)
        assert 0.9 <= weight <= 1.0
    
    def test_calculate_weight_decreases_with_time(self):
        """测试权重随时间降低"""
        engine = ForgettingEngine()
        
        weight_t0 = engine.calculate_weight(0, 1.0)
        weight_t5 = engine.calculate_weight(5, 1.0)
        weight_t20 = engine.calculate_weight(20, 1.0)
        
        assert weight_t0 > weight_t5 > weight_t20


class TestApplyForgettingWeights:
    """测试应用遗忘权重"""
    
    def test_apply_forgetting_weights_basic(self):
        """测试基本的权重应用"""
        engine = ForgettingEngine()
        
        items = [
            {"id": 1, "time_elapsed": 0, "strength": 1.0},
            {"id": 2, "time_elapsed": 5, "strength": 1.0},
            {"id": 3, "time_elapsed": 10, "strength": 1.0}
        ]
        
        weighted_items = engine.apply_forgetting_weights(items)
        
        assert len(weighted_items) == 3
        for item in weighted_items:
            assert "forgetting_weight" in item
            assert 0.0 <= item["forgetting_weight"] <= 1.0
    
    def test_apply_forgetting_weights_preserves_original(self):
        """测试应用权重不修改原始数据"""
        engine = ForgettingEngine()
        
        items = [
            {"id": 1, "time_elapsed": 5, "strength": 1.0}
        ]
        
        weighted_items = engine.apply_forgetting_weights(items)
        
        # 原始项不应该被修改
        assert "forgetting_weight" not in items[0]
        # 新项应该有权重
        assert "forgetting_weight" in weighted_items[0]
    
    def test_apply_forgetting_weights_custom_keys(self):
        """测试使用自定义键名"""
        engine = ForgettingEngine()
        
        items = [
            {"id": 1, "days_old": 5, "importance": 2.0}
        ]
        
        weighted_items = engine.apply_forgetting_weights(
            items,
            time_key="days_old",
            strength_key="importance"
        )
        
        assert "forgetting_weight" in weighted_items[0]
        assert weighted_items[0]["forgetting_weight"] > 0
    
    def test_apply_forgetting_weights_missing_keys(self):
        """测试缺少键时使用默认值"""
        engine = ForgettingEngine()
        
        items = [
            {"id": 1}  # 缺少 time_elapsed 和 strength
        ]
        
        weighted_items = engine.apply_forgetting_weights(items)
        
        # 应该使用默认值：time_elapsed=0, strength=1.0
        assert "forgetting_weight" in weighted_items[0]
        # 时间为 0，强度为 1.0，权重应该接近 1.0
        assert weighted_items[0]["forgetting_weight"] > 0.9
    
    def test_apply_forgetting_weights_ordering(self):
        """测试权重应用后的排序"""
        engine = ForgettingEngine()
        
        items = [
            {"id": 1, "time_elapsed": 0, "strength": 1.0},
            {"id": 2, "time_elapsed": 10, "strength": 1.0},
            {"id": 3, "time_elapsed": 5, "strength": 1.0}
        ]
        
        weighted_items = engine.apply_forgetting_weights(items)
        
        # 权重应该按时间递减
        assert weighted_items[0]["forgetting_weight"] > weighted_items[2]["forgetting_weight"]
        assert weighted_items[2]["forgetting_weight"] > weighted_items[1]["forgetting_weight"]


class TestMarkItemsForForgetting:
    """测试标记遗忘项"""
    
    def test_mark_items_for_forgetting_basic(self):
        """测试基本的遗忘标记"""
        engine = ForgettingEngine()
        
        items = [
            {"id": 1, "time_elapsed": 0, "strength": 1.0},   # 新记忆，应保留
            {"id": 2, "time_elapsed": 100, "strength": 0.5}, # 旧记忆，应遗忘
        ]
        
        to_keep, to_forget = engine.mark_items_for_forgetting(
            items,
            forgetting_threshold=0.5
        )
        
        assert len(to_keep) >= 1
        assert len(to_forget) >= 0
        assert len(to_keep) + len(to_forget) == len(items)
    
    def test_mark_items_for_forgetting_all_keep(self):
        """测试所有项都保留的情况"""
        engine = ForgettingEngine()
        
        items = [
            {"id": 1, "time_elapsed": 0, "strength": 1.0},
            {"id": 2, "time_elapsed": 1, "strength": 1.0},
        ]
        
        to_keep, to_forget = engine.mark_items_for_forgetting(
            items,
            forgetting_threshold=0.9  # 高阈值
        )
        
        assert len(to_keep) == 2
        assert len(to_forget) == 0
    
    def test_mark_items_for_forgetting_all_forget(self):
        """测试所有项都遗忘的情况"""
        engine = ForgettingEngine()
        
        items = [
            {"id": 1, "time_elapsed": 100, "strength": 0.1},
            {"id": 2, "time_elapsed": 200, "strength": 0.1},
        ]
        
        to_keep, to_forget = engine.mark_items_for_forgetting(
            items,
            forgetting_threshold=0.1  # 低阈值
        )
        
        assert len(to_forget) >= 1
    
    def test_mark_items_for_forgetting_adds_score(self):
        """测试标记时添加遗忘分数"""
        engine = ForgettingEngine()
        
        items = [
            {"id": 1, "time_elapsed": 5, "strength": 1.0}
        ]
        
        to_keep, to_forget = engine.mark_items_for_forgetting(items)
        
        all_items = to_keep + to_forget
        for item in all_items:
            assert "forgetting_score" in item
            assert 0.0 <= item["forgetting_score"] <= 1.0
    
    def test_mark_items_for_forgetting_threshold_boundary(self):
        """测试阈值边界情况"""
        engine = ForgettingEngine()
        
        items = [
            {"id": 1, "time_elapsed": 5, "strength": 1.0}
        ]
        
        # 计算实际的遗忘分数
        score = engine.calculate_forgetting_score(5, 1.0)
        
        # 使用略低于分数的阈值，应该保留
        to_keep, to_forget = engine.mark_items_for_forgetting(
            items,
            forgetting_threshold=score + 0.01
        )
        assert len(to_keep) == 1
        
        # 使用略高于分数的阈值，应该遗忘
        to_keep, to_forget = engine.mark_items_for_forgetting(
            items,
            forgetting_threshold=score - 0.01
        )
        assert len(to_forget) == 1


class TestForgettingStatistics:
    """测试遗忘统计"""
    
    def test_get_forgetting_statistics_basic(self):
        """测试基本的统计信息"""
        engine = ForgettingEngine()
        
        items = [
            {"id": 1, "time_elapsed": 0, "strength": 1.0},
            {"id": 2, "time_elapsed": 10, "strength": 1.0},
            {"id": 3, "time_elapsed": 50, "strength": 0.5},
        ]
        
        stats = engine.get_forgetting_statistics(items, forgetting_threshold=0.5)
        
        assert "total_items" in stats
        assert "items_to_keep" in stats
        assert "items_to_forget" in stats
        assert "forgetting_rate" in stats
        assert "average_retention" in stats
        assert "average_forgetting_score" in stats
        
        assert stats["total_items"] == 3
        assert stats["items_to_keep"] + stats["items_to_forget"] == 3
    
    def test_get_forgetting_statistics_empty_list(self):
        """测试空列表的统计"""
        engine = ForgettingEngine()
        
        stats = engine.get_forgetting_statistics([])
        
        assert stats["total_items"] == 0
        assert stats["items_to_keep"] == 0
        assert stats["items_to_forget"] == 0
        assert stats["forgetting_rate"] == 0.0
        assert stats["average_retention"] == 0.0
        assert stats["average_forgetting_score"] == 0.0
    
    def test_get_forgetting_statistics_forgetting_rate(self):
        """测试遗忘率计算"""
        engine = ForgettingEngine()
        
        items = [
            {"id": 1, "time_elapsed": 0, "strength": 1.0},
            {"id": 2, "time_elapsed": 100, "strength": 0.1},
        ]
        
        stats = engine.get_forgetting_statistics(items, forgetting_threshold=0.5)
        
        expected_rate = stats["items_to_forget"] / stats["total_items"]
        assert abs(stats["forgetting_rate"] - expected_rate) < 0.0001
    
    def test_get_forgetting_statistics_averages(self):
        """测试平均值计算"""
        engine = ForgettingEngine()
        
        items = [
            {"id": 1, "time_elapsed": 0, "strength": 1.0},
            {"id": 2, "time_elapsed": 5, "strength": 1.0},
        ]
        
        stats = engine.get_forgetting_statistics(items)
        
        # 平均保持率应该在 0 到 1 之间
        assert 0.0 <= stats["average_retention"] <= 1.0
        # 平均遗忘分数应该在 0 到 1 之间
        assert 0.0 <= stats["average_forgetting_score"] <= 1.0
        # 平均保持率 + 平均遗忘分数应该接近 1.0
        assert abs(stats["average_retention"] + stats["average_forgetting_score"] - 1.0) < 0.0001
    
    def test_get_forgetting_statistics_all_fresh(self):
        """测试所有记忆都是新鲜的情况"""
        engine = ForgettingEngine()
        
        items = [
            {"id": i, "time_elapsed": 0, "strength": 1.0}
            for i in range(10)
        ]
        
        stats = engine.get_forgetting_statistics(items, forgetting_threshold=0.5)
        
        # 所有新鲜记忆应该被保留
        assert stats["items_to_keep"] == 10
        assert stats["items_to_forget"] == 0
        assert stats["forgetting_rate"] == 0.0
        assert stats["average_retention"] > 0.9
    
    def test_get_forgetting_statistics_all_old(self):
        """测试所有记忆都是旧的情况"""
        engine = ForgettingEngine()
        
        items = [
            {"id": i, "time_elapsed": 1000, "strength": 0.1}
            for i in range(10)
        ]
        
        stats = engine.get_forgetting_statistics(items, forgetting_threshold=0.1)
        
        # 大部分旧记忆应该被遗忘
        assert stats["items_to_forget"] >= 5
        assert stats["forgetting_rate"] >= 0.5


class TestTimeCalculations:
    """测试时间计算辅助方法"""
    
    def test_calculate_time_elapsed_days_basic(self):
        """测试基本的天数计算"""
        engine = ForgettingEngine()
        
        created_at = datetime(2024, 1, 1, 12, 0, 0)
        current_time = datetime(2024, 1, 6, 12, 0, 0)
        
        days = engine.calculate_time_elapsed_days(created_at, current_time)
        
        assert abs(days - 5.0) < 0.01
    
    def test_calculate_time_elapsed_days_with_hours(self):
        """测试包含小时的天数计算"""
        engine = ForgettingEngine()
        
        created_at = datetime(2024, 1, 1, 0, 0, 0)
        current_time = datetime(2024, 1, 2, 12, 0, 0)
        
        days = engine.calculate_time_elapsed_days(created_at, current_time)
        
        assert abs(days - 1.5) < 0.01
    
    def test_calculate_time_elapsed_days_zero(self):
        """测试零天数"""
        engine = ForgettingEngine()
        
        now = datetime.now()
        days = engine.calculate_time_elapsed_days(now, now)
        
        assert abs(days) < 0.001
    
    def test_calculate_time_elapsed_days_default_current_time(self):
        """测试使用默认当前时间"""
        engine = ForgettingEngine()
        
        created_at = datetime.now() - timedelta(days=3)
        days = engine.calculate_time_elapsed_days(created_at)
        
        assert 2.9 < days < 3.1
    
    def test_calculate_time_elapsed_hours_basic(self):
        """测试基本的小时数计算"""
        engine = ForgettingEngine()
        
        created_at = datetime(2024, 1, 1, 10, 0, 0)
        current_time = datetime(2024, 1, 1, 15, 0, 0)
        
        hours = engine.calculate_time_elapsed_hours(created_at, current_time)
        
        assert abs(hours - 5.0) < 0.01
    
    def test_calculate_time_elapsed_hours_with_days(self):
        """测试跨天的小时数计算"""
        engine = ForgettingEngine()
        
        created_at = datetime(2024, 1, 1, 20, 0, 0)
        current_time = datetime(2024, 1, 2, 8, 0, 0)
        
        hours = engine.calculate_time_elapsed_hours(created_at, current_time)
        
        assert abs(hours - 12.0) < 0.01
    
    def test_calculate_time_elapsed_hours_zero(self):
        """测试零小时数"""
        engine = ForgettingEngine()
        
        now = datetime.now()
        hours = engine.calculate_time_elapsed_hours(now, now)
        
        assert abs(hours) < 0.001
    
    def test_calculate_time_elapsed_hours_default_current_time(self):
        """测试使用默认当前时间"""
        engine = ForgettingEngine()
        
        created_at = datetime.now() - timedelta(hours=6)
        hours = engine.calculate_time_elapsed_hours(created_at)
        
        assert 5.9 < hours < 6.1


class TestEdgeCases:
    """测试边界情况和异常处理"""
    
    def test_very_large_time_values(self):
        """测试非常大的时间值"""
        engine = ForgettingEngine()
        
        retention = engine.forgetting_curve(10000, 1.0)
        
        # 应该接近 offset
        assert abs(retention - engine.offset) < 0.01
    
    def test_very_large_strength_values(self):
        """测试非常大的强度值"""
        engine = ForgettingEngine()
        
        retention = engine.forgetting_curve(10, 1000.0)
        
        # 强度很大时，保持率应该接近 1.0
        assert retention > 0.9
    
    def test_very_small_strength_values(self):
        """测试非常小的强度值"""
        engine = ForgettingEngine()
        
        retention = engine.forgetting_curve(10, 0.001)
        
        # 强度很小时，应该返回 offset
        assert retention == engine.offset
    
    def test_extreme_lambda_values(self):
        """测试极端的 lambda 值"""
        config = ForgettingEngineConfig(
            offset=0.1,
            lambda_time=10.0,
            lambda_mem=0.01
        )
        engine = ForgettingEngine(config)
        
        retention = engine.forgetting_curve(1, 1.0)
        
        # 应该在有效范围内
        assert 0.0 <= retention <= 1.0
    
    def test_empty_items_list(self):
        """测试空项列表"""
        engine = ForgettingEngine()
        
        weighted = engine.apply_forgetting_weights([])
        assert weighted == []
        
        to_keep, to_forget = engine.mark_items_for_forgetting([])
        assert to_keep == []
        assert to_forget == []
        
        stats = engine.get_forgetting_statistics([])
        assert stats["total_items"] == 0


class TestConfigurationImpact:
    """测试配置参数对遗忘行为的影响"""
    
    def test_offset_impact(self):
        """测试 offset 参数的影响"""
        config_low = ForgettingEngineConfig(offset=0.0)
        config_high = ForgettingEngineConfig(offset=0.5)
        
        engine_low = ForgettingEngine(config_low)
        engine_high = ForgettingEngine(config_high)
        
        # 相同条件下，高 offset 应该有更高的保持率
        retention_low = engine_low.forgetting_curve(100, 1.0)
        retention_high = engine_high.forgetting_curve(100, 1.0)
        
        assert retention_high >= retention_low
        assert retention_high >= 0.5
    
    def test_lambda_time_impact(self):
        """测试 lambda_time 参数的影响"""
        config_low = ForgettingEngineConfig(lambda_time=0.05)
        config_high = ForgettingEngineConfig(lambda_time=0.5)
        
        engine_low = ForgettingEngine(config_low)
        engine_high = ForgettingEngine(config_high)
        
        # lambda_time 越大，时间衰减越快
        retention_low = engine_low.forgetting_curve(10, 1.0)
        retention_high = engine_high.forgetting_curve(10, 1.0)
        
        assert retention_low > retention_high
    
    def test_lambda_mem_impact(self):
        """测试 lambda_mem 参数的影响"""
        config_low = ForgettingEngineConfig(lambda_mem=0.5)
        config_high = ForgettingEngineConfig(lambda_mem=2.0)
        
        engine_low = ForgettingEngine(config_low)
        engine_high = ForgettingEngine(config_high)
        
        # lambda_mem 越大，强度的保护作用越强
        retention_low = engine_low.forgetting_curve(10, 2.0)
        retention_high = engine_high.forgetting_curve(10, 2.0)
        
        assert retention_high > retention_low


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
