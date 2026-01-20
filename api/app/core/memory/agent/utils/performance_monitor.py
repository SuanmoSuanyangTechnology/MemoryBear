
import time
import json
from collections import defaultdict
from typing import Dict, List
from app.core.logging_config import get_agent_logger

logger = get_agent_logger(__name__)

class ProblemExtensionMonitor:
    """Problem_Extension性能监控器"""
    
    def __init__(self):
        self.metrics = defaultdict(list)
        self.slow_queries = []
        self.error_count = 0
        
    def record_execution(self, duration: float, question_count: int, success: bool):
        """记录执行指标"""
        self.metrics['durations'].append(duration)
        self.metrics['question_counts'].append(question_count)
        
        if not success:
            self.error_count += 1
            
        # 记录慢查询（超过10秒）
        if duration > 10.0:
            self.slow_queries.append({
                'duration': duration,
                'question_count': question_count,
                'timestamp': time.time()
            })
            
    def get_stats(self) -> Dict:
        """获取统计信息"""
        durations = self.metrics['durations']
        if not durations:
            return {"message": "暂无数据"}
            
        return {
            "total_executions": len(durations),
            "avg_duration": sum(durations) / len(durations),
            "max_duration": max(durations),
            "min_duration": min(durations),
            "slow_queries_count": len(self.slow_queries),
            "error_rate": self.error_count / len(durations) if durations else 0,
            "recent_slow_queries": self.slow_queries[-5:]  # 最近5个慢查询
        }
        
    def log_stats(self):
        """记录统计信息到日志"""
        stats = self.get_stats()
        logger.info(f"Problem_Extension性能统计: {json.dumps(stats, indent=2)}")

# 全局监控器实例
performance_monitor = ProblemExtensionMonitor()
