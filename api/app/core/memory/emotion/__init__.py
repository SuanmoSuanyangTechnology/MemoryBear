"""情绪回复域（回复链路生产情绪 + 快写链路消费缓存）。

- ``emotion_cache``：Redis 情绪缓存（回复链路写、快写链路读后即删）。
- ``emotion_resolver``：回复侧情绪识别 + policy 映射 + 提示词注入。
"""
