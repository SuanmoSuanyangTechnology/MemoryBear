"""memory-profile 数据库模型基类（命名与分工对齐 identity-service/src/models/base.py）。

两个独立 registry，用途互斥，**名字本身即表明是否进迁移链**：

- ``ServiceBase``：本服务**自有表**，作为 alembic ``target_metadata``（migrations/env.py），
  由本服务迁移链建表/改表，版本记录在 ``alembic_version_memory_profile``。
- ``ReadOnlyBase``：**核心/老单体已有表**的只读映射（如 ``end_users``、``end_user_merge``），
  表结构归 core（老单体链 ``alembic_version``）管理，不生成迁移。

因两者 metadata 相互隔离，挂在 ``ReadOnlyBase`` 上的表不会进入 autogenerate 的对比范围，
既不会被本链误判为「待建」，也不会被判为「待删除」；core 改列名/删列时须同步
ReadOnlyBase 侧的模型定义。

注意 ``ReadOnly`` 是**约定而非机制**：基类不拦截写入（无 flush 守卫、无只读事务、
无库角色隔离），保障仅来自命名与 code review——勿依赖它在运行时拒绝写 core 表。
"""

from sqlalchemy.orm import declarative_base

ServiceBase = declarative_base()

ReadOnlyBase = declarative_base()