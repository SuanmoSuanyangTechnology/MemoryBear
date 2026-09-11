# -*- coding: UTF-8 -*-
# Author: Eternity
# @Email: 1533512157@qq.com
# @Time : 2026/9/9 14:00
"""模型聚合入口：导入即完成表注册。

alembic 的 env.py ``import src.models`` 依赖本文件把各模型模块全部导入，
否则 ``ServiceBase.metadata`` 为空、``--autogenerate`` 生成空迁移。新增模型模块后
须同步在此登记。

注意：核心表（end_users 等）挂 ReadOnlyBase 只读映射，一并导入以维持映射完整，
但它们不进本服务迁移链（详见 infrastructure/database/base.py）。
"""
from src.models.end_user_model import EndUser, EndUserMerge

__all__ = [
    "EndUser",
    "EndUserMerge",
]