"""
配置管理优化模块

提供可选的配置管理优化功能，包括：
- LRU 缓存策略
- 缓存预热
- 缓存监控指标
- 动态 TTL 策略
- 配置版本控制

这些优化是可选的，当前的基础实现已经满足大多数需求。
"""
import logging
import statistics
import threading
from collections import OrderedDict
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional, Tuple

logger = logging.getLogger(__name__)


class LRUConfigCache:
    """
    LRU（Least Recently Used）配置缓存
    
    当缓存达到最大容量时，自动淘汰最少使用的配置
    """
    
    def __init__(self, max_size: int = 100, ttl: timedelta = timedelta(minutes=5)):
        """
        初始化 LRU 缓存
        
        Args:
            max_size: 最大缓存容量
            ttl: 缓存过期时间
        """
        self.max_size = max_size
        self.ttl = ttl
        self._cache: OrderedDict[str, Dict[str, Any]] = OrderedDict()
        self._timestamps: Dict[str, datetime] = {}
        self._lock = threading.RLock()
        
        # 统计信息
        self._stats = {
            'hits': 0,
            'misses': 0,
            'evictions': 0,
            'load_times': []
        }
    
    def get(self, config_id: str) -> Optional[Dict[str, Any]]:
        """
        获取配置（如果存在且未过期）
        
        Args:
            config_id: 配置 ID
            
        Returns:
            配置字典，如果不存在或已过期则返回 None
        """
        with self._lock:
            if config_id not in self._cache:
                self._stats['misses'] += 1
                return None
            
            # 检查是否过期
            timestamp = self._timestamps.get(config_id)
            if timestamp and (datetime.now() - timestamp) >= self.ttl:
                # 过期，移除
                self._cache.pop(config_id, None)
                self._timestamps.pop(config_id, None)
                self._stats['misses'] += 1
                return None
            
            # 命中，移动到末尾（标记为最近使用）
            self._cache.move_to_end(config_id)
            self._stats['hits'] += 1
            return self._cache[config_id]
    
    def put(self, config_id: str, config: Dict[str, Any]) -> None:
        """
        添加或更新配置
        
        Args:
            config_id: 配置 ID
            config: 配置字典
        """
        with self._lock:
            if config_id in self._cache:
                # 更新现有配置
                self._cache.move_to_end(config_id)
            else:
                # 添加新配置
                if len(self._cache) >= self.max_size:
                    # 缓存已满，移除最旧的配置
                    oldest_id, _ = self._cache.popitem(last=False)
                    self._timestamps.pop(oldest_id, None)
                    self._stats['evictions'] += 1
                    logger.debug(f"[LRUCache] 淘汰配置: {oldest_id}")
            
            self._cache[config_id] = config
            self._timestamps[config_id] = datetime.now()
    
    def clear(self, config_id: Optional[str] = None) -> None:
        """
        清除缓存
        
        Args:
            config_id: 如果指定，只清除该配置；否则清除所有
        """
        with self._lock:
            if config_id:
                self._cache.pop(config_id, None)
                self._timestamps.pop(config_id, None)
            else:
                self._cache.clear()
                self._timestamps.clear()
    
    def get_stats(self) -> Dict[str, Any]:
        """
        获取缓存统计信息
        
        Returns:
            统计信息字典
        """
        with self._lock:
            total = self._stats['hits'] + self._stats['misses']
            hit_rate = (self._stats['hits'] / total * 100) if total > 0 else 0
            
            return {
                'cache_size': len(self._cache),
                'max_size': self.max_size,
                'total_requests': total,
                'cache_hits': self._stats['hits'],
                'cache_misses': self._stats['misses'],
                'evictions': self._stats['evictions'],
                'hit_rate': hit_rate,
                'avg_load_time': statistics.mean(self._stats['load_times']) if self._stats['load_times'] else 0
            }
    
    def record_load_time(self, load_time_ms: float) -> None:
        """
        记录加载时间
        
        Args:
            load_time_ms: 加载时间（毫秒）
        """
        with self._lock:
            self._stats['load_times'].append(load_time_ms)
            # 只保留最近 1000 次的记录
            if len(self._stats['load_times']) > 1000:
                self._stats['load_times'] = self._stats['load_times'][-1000:]


class ConfigCacheWarmer:
    """
    配置缓存预热器
    
    在系统启动时预加载常用配置，减少首次请求延迟
    """
    
    @staticmethod
    def warmup(config_ids: List[str], load_func) -> Dict[str, bool]:
        """
        预热缓存
        
        Args:
            config_ids: 要预加载的配置 ID 列表
            load_func: 配置加载函数
            
        Returns:
            每个配置的加载结果
        """
        results = {}
        
        logger.info(f"[CacheWarmer] 开始预热 {len(config_ids)} 个配置")
        
        for config_id in config_ids:
            try:
                result = load_func(config_id)
                results[config_id] = result
                if result:
                    logger.debug(f"[CacheWarmer] 成功预热配置: {config_id}")
                else:
                    logger.warning(f"[CacheWarmer] 预热配置失败: {config_id}")
            except Exception as e:
                logger.error(f"[CacheWarmer] 预热配置异常: {config_id}, 错误: {e}")
                results[config_id] = False
        
        success_count = sum(1 for r in results.values() if r)
        logger.info(f"[CacheWarmer] 预热完成: {success_count}/{len(config_ids)} 成功")
        
        return results


class DynamicTTLStrategy:
    """
    动态 TTL 策略
    
    根据配置类型和更新频率动态调整缓存过期时间
    """
    
    # 预定义的 TTL 策略
    TTL_STRATEGIES = {
        'production': timedelta(minutes=30),   # 生产配置较稳定
        'staging': timedelta(minutes=15),      # 预发布配置中等稳定
        'development': timedelta(minutes=5),   # 开发配置频繁变化
        'testing': timedelta(minutes=1),       # 测试配置快速过期
        'default': timedelta(minutes=5)        # 默认策略
    }
    
    @classmethod
    def get_ttl(cls, config_id: str, config_type: Optional[str] = None) -> timedelta:
        """
        获取配置的 TTL
        
        Args:
            config_id: 配置 ID
            config_type: 配置类型（production/staging/development/testing）
            
        Returns:
            TTL 时间间隔
        """
        if config_type and config_type in cls.TTL_STRATEGIES:
            return cls.TTL_STRATEGIES[config_type]
        
        # 根据 config_id 推断类型
        if 'prod' in config_id.lower():
            return cls.TTL_STRATEGIES['production']
        elif 'stag' in config_id.lower():
            return cls.TTL_STRATEGIES['staging']
        elif 'dev' in config_id.lower():
            return cls.TTL_STRATEGIES['development']
        elif 'test' in config_id.lower():
            return cls.TTL_STRATEGIES['testing']
        
        return cls.TTL_STRATEGIES['default']


class ConfigVersionManager:
    """
    配置版本管理器
    
    跟踪配置版本，当配置更新时自动失效旧版本缓存
    """
    
    def __init__(self):
        self._versions: Dict[str, str] = {}
        self._lock = threading.RLock()
    
    def get_version(self, config_id: str) -> Optional[str]:
        """
        获取配置版本
        
        Args:
            config_id: 配置 ID
            
        Returns:
            版本号，如果不存在则返回 None
        """
        with self._lock:
            return self._versions.get(config_id)
    
    def set_version(self, config_id: str, version: str) -> None:
        """
        设置配置版本
        
        Args:
            config_id: 配置 ID
            version: 版本号
        """
        with self._lock:
            old_version = self._versions.get(config_id)
            self._versions[config_id] = version
            
            if old_version and old_version != version:
                logger.info(f"[VersionManager] 配置版本更新: {config_id} {old_version} -> {version}")
    
    def check_version(self, config_id: str, cached_version: Optional[str]) -> bool:
        """
        检查缓存版本是否有效
        
        Args:
            config_id: 配置 ID
            cached_version: 缓存的版本号
            
        Returns:
            True 如果版本匹配，False 如果版本不匹配或不存在
        """
        with self._lock:
            current_version = self._versions.get(config_id)
            
            if not current_version or not cached_version:
                return False
            
            return current_version == cached_version
    
    def invalidate(self, config_id: str) -> None:
        """
        使配置版本失效
        
        Args:
            config_id: 配置 ID
        """
        with self._lock:
            if config_id in self._versions:
                # 生成新版本号
                import uuid
                new_version = str(uuid.uuid4())
                self._versions[config_id] = new_version
                logger.info(f"[VersionManager] 配置版本失效: {config_id} -> {new_version}")


class CacheMonitor:
    """
    缓存监控器
    
    提供缓存性能监控和报告功能
    """
    
    def __init__(self, cache: LRUConfigCache):
        self.cache = cache
    
    def get_report(self) -> str:
        """
        生成缓存性能报告
        
        Returns:
            格式化的报告字符串
        """
        stats = self.cache.get_stats()
        
        report = f"""
配置缓存性能报告
================
缓存容量: {stats['cache_size']}/{stats['max_size']}
总请求数: {stats['total_requests']}
缓存命中: {stats['cache_hits']}
缓存未命中: {stats['cache_misses']}
缓存命中率: {stats['hit_rate']:.2f}%
淘汰次数: {stats['evictions']}
平均加载时间: {stats['avg_load_time']:.2f}ms
"""
        return report
    
    def log_stats(self) -> None:
        """记录统计信息到日志"""
        stats = self.cache.get_stats()
        logger.info(
            f"[CacheMonitor] 缓存统计 - "
            f"容量: {stats['cache_size']}/{stats['max_size']}, "
            f"命中率: {stats['hit_rate']:.2f}%, "
            f"淘汰: {stats['evictions']}"
        )


# 使用示例
def example_usage():
    """
    优化功能使用示例
    """
    # 1. 使用 LRU 缓存
    lru_cache = LRUConfigCache(max_size=100, ttl=timedelta(minutes=5))
    
    # 获取配置
    config = lru_cache.get("config_001")
    if config is None:
        # 缓存未命中，从数据库加载
        config = {"llm_name": "openai/gpt-4"}
        lru_cache.put("config_001", config)
    
    # 2. 预热缓存
    def load_config(config_id):
        # 实际的配置加载逻辑
        return True
    
    warmer = ConfigCacheWarmer()
    results = warmer.warmup(["config_001", "config_002"], load_config)
    
    # 3. 动态 TTL
    ttl = DynamicTTLStrategy.get_ttl("prod_config_001", "production")
    print(f"TTL: {ttl}")
    
    # 4. 版本管理
    version_manager = ConfigVersionManager()
    version_manager.set_version("config_001", "v1.0.0")
    
    # 检查版本
    is_valid = version_manager.check_version("config_001", "v1.0.0")
    
    # 5. 监控
    monitor = CacheMonitor(lru_cache)
    print(monitor.get_report())


if __name__ == "__main__":
    example_usage()
