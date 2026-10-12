"""他域表的只读映射（apps / app_releases / agent_configs / multi_agent_configs /
workspaces / tenant_subscriptions / package_plans / package_plan_versions /
resource_pack_versions / tenant_resource_packs）。

表结构归其归属方管理，本服务不生成迁移、不建 relationship、不声明 FK：
仅取影响面分析与配额读取所需的列子集，对侧改列名/删列时须同步本目录。
"""
