import uuid

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.core.exceptions import BusinessException
from app.core.logging_config import get_db_logger
from app.core.utils.datetime_utils import to_iso_z, utcnow
from app.core.memory.models.service_models import ForgetLog
from app.models.memory_forget_model import ForgetAuditModel, ForgetTrigger
from app.models.user_model import User
from app.repositories.neo4j.graph_search import forget_recover_by_element_id
from app.repositories.neo4j.neo4j_connector import Neo4jConnector

logger = get_db_logger()


class ForgetLogRepository:
    @staticmethod
    def sync_logs(db: Session, logs: list[ForgetLog]):
        """将内存审计日志批量写入数据库（存在则更新，不存在则新增）。

        对每条 ``ForgetLog``，以 ``node_id`` 为键查找已有记录：
        - 已存在：更新字段
        - 不存在：新建 ``ForgetAuditModel``

        调用方负责 commit/rollback，适合与其他写入操作共享事务。

        Args:
            db: SQLAlchemy 同步会话。
            logs: 待持久化的 ForgetLog 列表。
        """
        if not logs:
            return

        node_ids = [log.node_id for log in logs]
        existing = db.execute(
            select(ForgetAuditModel).where(ForgetAuditModel.node_id.in_(node_ids))
        ).scalars().all()
        existing_map: dict[str, ForgetAuditModel] = {row.node_id: row for row in existing}

        updated = 0
        inserted = 0

        for log in logs:
            if log.node_id in existing_map:
                record = existing_map[log.node_id]
                record.end_user_id = log.end_user_id
                record.node_type = log.node_type
                record.content = log.content
                record.trigger = log.trigger
                record.reason = log.reason
                record.recoverable = log.recoverable
                record.operator = log.operator
                record.delete_at = log.delete_at
                record.is_recovered = log.is_recovered
                updated += 1
            else:
                db.add(ForgetAuditModel(
                    node_id=log.node_id,
                    end_user_id=log.end_user_id,
                    node_type=log.node_type,
                    content=log.content,
                    trigger=log.trigger,
                    reason=log.reason,
                    recoverable=log.recoverable,
                    operator=log.operator,
                    delete_at=log.delete_at,
                    is_recovered=log.is_recovered,
                ))
                inserted += 1

        db.flush()
        logger.info(
            "Synced forget audit logs: inserted=%d updated=%d total=%d",
            inserted, updated, len(logs),
        )

    @staticmethod
    async def recover_memory(db: AsyncSession, element_id: str, operator: uuid.UUID):
        """恢复被软删除的记忆节点并更新审计日志。

        先查 DB 确认 ``recoverable=True``，通过后再恢复 Neo4j。

        Args:
            db: SQLAlchemy 异步会话。
            element_id: Neo4j 内部元素 ID，对应 DB 中 ``node_id`` 字段。
            operator: 执行恢复操作的用户 ID。

        Raises:
            ResourceNotFoundException: 未找到可恢复的审计记录或 Neo4j 节点。
        """
        result = await db.execute(
            select(ForgetAuditModel.id, ForgetAuditModel.is_recovered).where(
                ForgetAuditModel.node_id == element_id,
                ForgetAuditModel.recoverable == True,
            )
        )
        row = result.one_or_none()

        if row is None:
            raise BusinessException(
                "该记忆不可恢复，可能已恢复或不可恢复类型", 400,
            )

        audit_id, already_recovered = row

        if already_recovered:
            logger.info(
                "Memory already recovered (idempotent): element_id=%s", element_id,
            )
            return

        async with Neo4jConnector() as connector:
            recovered = await forget_recover_by_element_id(
                connector, element_id, now=to_iso_z(utcnow()),
            )

        if recovered is None:
            raise BusinessException(
                "Neo4j 节点不存在或已被删除", 400,
            )

        values = {"is_recovered": True, "operator": operator}

        await db.execute(
            update(ForgetAuditModel)
            .where(ForgetAuditModel.id == audit_id)
            .values(**values)
        )
        await db.commit()
        logger.info("Memory recovered: element_id=%s operator=%s", element_id, operator)

    @staticmethod
    async def get_forget_logs(
        db: AsyncSession,
        end_user_id: uuid.UUID,
        method: int | None = None,
        memory_type: str | None = None,
        status: str | None = None,
        page: int = 1,
        pagesize: int = 10,
    ) -> tuple[list[ForgetAuditModel], dict[uuid.UUID, str], int]:
        """分页查询遗忘审计日志。

        Args:
            db: SQLAlchemy 异步会话。
            end_user_id: 终端用户 ID（必填）。
            method: 遗忘触发方式（1=手动, 2=定时）。
            memory_type: 记忆节点类型过滤。
            status: 恢复状态（"recoverable"=可恢复, "deleted"=已删除, None=全部）。
            page: 页码，从 1 开始。
            pagesize: 每页条数。

        Returns:
            (日志列表, operator_id → username 映射, 总记录数) 元组。
        """
        conditions = [ForgetAuditModel.end_user_id == end_user_id]
        if method is not None:
            conditions.append(ForgetAuditModel.trigger == method)
        if memory_type is not None:
            conditions.append(ForgetAuditModel.node_type == memory_type)
        if status == "recoverable":
            conditions.append(
                and_(
                    ForgetAuditModel.is_recovered == False,
                    ForgetAuditModel.recoverable == True,
                )
            )
        elif status == "recovered":
            conditions.append(
                and_(
                    ForgetAuditModel.is_recovered == True,
                    ForgetAuditModel.recoverable == True,
                )
            )
        elif status == "deleted":
            conditions.append(
                and_(
                    ForgetAuditModel.is_recovered == False,
                    ForgetAuditModel.recoverable == False,
                )
            )

        base_query = select(ForgetAuditModel).where(*conditions)

        # 总数
        count_query = select(func.count()).select_from(ForgetAuditModel).where(*conditions)
        total_result = await db.execute(count_query)
        total = total_result.scalar_one()

        page = max(page, 1)
        offset = (page - 1) * pagesize
        result = await db.execute(
            base_query.order_by(ForgetAuditModel.delete_at.desc()).offset(offset).limit(pagesize)
        )
        items = list(result.scalars().all())

        # 批量查询 operator 的 username
        operator_ids = {item.operator for item in items if item.operator is not None}
        operator_names: dict[uuid.UUID, str] = {}
        if operator_ids:
            user_result = await db.execute(
                select(User.id, User.username).where(User.id.in_(operator_ids))
            )
            for row in user_result.all():
                operator_names[row.id] = row.username

        return items, operator_names, total

    @staticmethod
    async def get_forget_stats(
        db: AsyncSession,
        end_user_id: uuid.UUID,
    ) -> dict:
        """获取遗忘统计信息。

        Args:
            db: SQLAlchemy 异步会话。
            end_user_id: 终端用户 ID。

        Returns:
            dict: 包含 total, manual_count, scheduled_count, recoverable_count。
        """
        base = select(func.count()).select_from(ForgetAuditModel).where(
            ForgetAuditModel.end_user_id == end_user_id
        )

        def _where(*extra):
            return base.where(*extra)

        total_result = await db.execute(base)
        total = total_result.scalar_one()

        manual_result = await db.execute(_where(ForgetAuditModel.trigger == ForgetTrigger.Manual))
        manual = manual_result.scalar_one()

        scheduled_result = await db.execute(_where(ForgetAuditModel.trigger == ForgetTrigger.Scheduled))
        scheduled = scheduled_result.scalar_one()

        recoverable_result = await db.execute(
            _where(
                ForgetAuditModel.is_recovered == False,
                ForgetAuditModel.recoverable == True,
            )
        )
        recoverable = recoverable_result.scalar_one()

        return {
            "total": total,
            "manual_count": manual,
            "scheduled_count": scheduled,
            "recoverable_count": recoverable,
        }

    @staticmethod
    async def get_total(db: AsyncSession, end_user_id: uuid.UUID) -> int:
        """获取遗忘日志总数。

        Args:
            db: SQLAlchemy 异步会话。
            end_user_id: 终端用户 ID。

        Returns:
            int: 总记录数。
        """
        result = await db.scalar(
            select(func.count()).select_from(ForgetAuditModel).where(
                ForgetAuditModel.end_user_id == end_user_id
            )
        )
        return int(result or 0)
