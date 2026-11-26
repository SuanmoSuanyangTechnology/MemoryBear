# 知识库 PostgreSQL 迁移

从 `mem-knowledge` 目录执行 `python -m src.database_migrations`。发布任务和开启 `DB_AUTO_UPGRADE` 的知识库 API 复用同一入口，先接管六张存量表，再升级到当前知识库 head。迁移在同一 PostgreSQL 事务和锁内完成，更新 `alembic_version_knowledge`。

存量库由开源和企业的旧 API 链先完成历史结构升级。接管基线核对六张表的列、约束、索引和文档统计触发器，结构吻合时保留物理表和业务数据。碰到部分缺表、结构不一致或未知知识库版本时，核对现场结构和版本记录后再重试。

基线同时兼容已由核心链添加 `knowledges.source`、`knowledges.index_strategy` 的库：两列须同时存在、类型为 `varchar(16)`、允许 NULL 且无数据库默认值。对应的两个 CHECK 归核心链管理，是否已 VALIDATE 不影响旧表接管；原基线的列、约束、索引和触发器仍严格核对。此兼容不创建或删除这两列，也不将其他未知结构视为合法。

手工执行过配置表 DDL 的环境，须先核对 `alembic_version_knowledge` 与实际结构；接管基线兼容来源列不等于配置表迁移已登记，不能重复执行建表，也不能仅凭表存在直接 stamp 到 head。

独立空库由平台初始化任务先提供 `users`、`workspaces`、`model_configs`，然后由知识库创建六张表及后续 Wiki 表。部署还包含旧 API 时，先完成该安装方式的历史数据库初始化，再启动知识库迁移。旧历史脚本仍包含六张表的建表和变更，按这个顺序运行可避免两个初始化过程争用同一批表。

升级完成后用 `alembic heads` 和 `alembic current` 核对版本。应用回退保留已接管的表和版本记录，回退前先确认旧镜像的启动配置兼容当前数据库 revision。
