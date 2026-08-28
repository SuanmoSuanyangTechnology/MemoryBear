"""记忆价值排行与遗忘候选共用的权重策略常量。"""

G_WEIGHT = 0.75
T_WEIGHT = 0.25

# 自动遗忘核心池当前只按时间新近度排序，但仍显式保留 G/T 公式，
# 便于后续直接调整权重而无需重写候选查询。
FORGET_CORE_G_WEIGHT = 0.0
FORGET_CORE_T_WEIGHT = 1.0
T_HALF_LIFE_MS = 2_592_000_000.0

# 调用方必须提供 created_epoch 和 $evaluated_at_ms；使用前需过滤非法时间。
T_CYPHER_EXPR = f"""
CASE
  WHEN created_epoch >= $evaluated_at_ms THEN 1.0
  ELSE 2.0 ^ (- ($evaluated_at_ms - created_epoch) / {T_HALF_LIFE_MS})
END
""".strip()
