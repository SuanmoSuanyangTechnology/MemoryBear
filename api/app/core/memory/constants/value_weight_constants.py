"""记忆价值排行与遗忘候选共用的权重策略常量。"""

G_WEIGHT = 0.75
T_WEIGHT = 0.25
T_HALF_LIFE_MS = 2_592_000_000.0

# 调用方必须提供 created_epoch 和 $evaluated_at_ms；使用前需过滤非法时间。
T_CYPHER_EXPR = f"""
CASE
  WHEN created_epoch >= $evaluated_at_ms THEN 1.0
  ELSE 2.0 ^ (- ($evaluated_at_ms - created_epoch) / {T_HALF_LIFE_MS})
END
""".strip()
