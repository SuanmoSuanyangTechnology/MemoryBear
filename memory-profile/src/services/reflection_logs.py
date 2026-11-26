"""Read use cases for reflection logs."""

from uuid import UUID

from src.common.errors import BizCode, ProfileError
from src.common.normalization import parse_uuid
from src.infrastructure.postgres import PostgresManager
from src.repositories.end_user import EndUserRepository
from src.repositories.reflection_logs import ReflectionLogRepository
from src.schemas.reflection_logs import (
    LogStatus,
    ReflectionLogDetail,
    ReflectionLogListItem,
    ReflectionLogPage,
    ReflectionLogPageInfo,
    ReflectionLogStats,
    SubProblem,
    TriggerType,
)


class ReflectionLogService:
    def __init__(self, postgres: PostgresManager) -> None:
        self.end_users = EndUserRepository(postgres)
        self.logs = ReflectionLogRepository(postgres)

    async def _require_end_user(self, end_user_id: str, workspace_id: UUID) -> UUID:
        user_id = parse_uuid(end_user_id)
        if user_id is None:
            raise ProfileError(
                BizCode.INVALID_PARAMETER, "请求参数无效", detail="无效的终端用户ID格式"
            )
        if await self.end_users.resolve_user_in_workspace(user_id, workspace_id) is None:
            raise ProfileError(
                BizCode.USER_NOT_FOUND,
                f"终端用户不存在: {end_user_id}",
                detail="end_user not found",
            )
        # A merge establishes existence, while the logs still belong to the requested ID.
        return user_id

    async def stats(self, end_user_id: str, workspace_id: UUID) -> ReflectionLogStats:
        user_id = await self._require_end_user(end_user_id, workspace_id)
        sub_problem_counts = dict.fromkeys((item.value for item in SubProblem), 0)
        status_counts = dict.fromkeys((item.value for item in LogStatus), 0)
        total = 0
        for sub_problem, status, count in await self.logs.grouped_counts(user_id):
            total += count
            if sub_problem in sub_problem_counts:
                sub_problem_counts[sub_problem] += count
            if status in status_counts:
                status_counts[status] += count

        return ReflectionLogStats(
            total=total,
            sub_problem=sub_problem_counts,
            status=status_counts,
            resolve_rate=round(status_counts[LogStatus.RESOLVED] / total, 2) if total else 0.0,
        )

    async def detail(self, log_id: str, workspace_id: UUID) -> ReflectionLogDetail:
        reflection_id = parse_uuid(log_id)
        if reflection_id is None:
            raise ProfileError(BizCode.INVALID_PARAMETER, "请求参数无效", detail="无效的日志ID格式")
        log = await self.logs.get_detail(reflection_id, workspace_id)
        if log is None:
            raise ProfileError(BizCode.NOT_FOUND, "日志不存在")
        return ReflectionLogDetail.model_validate(log)

    async def list_page(
        self,
        end_user_id: str,
        workspace_id: UUID,
        *,
        sub_problem: SubProblem | None,
        status: LogStatus | None,
        trigger_type: TriggerType | None,
        page: int,
        pagesize: int,
    ) -> ReflectionLogPage:
        user_id = await self._require_end_user(end_user_id, workspace_id)
        total, items = await self.logs.list_page(
            user_id,
            page=page,
            pagesize=pagesize,
            sub_problem=sub_problem,
            status=status,
            trigger_type=trigger_type,
        )
        return ReflectionLogPage(
            items=[ReflectionLogListItem.model_validate(item) for item in items],
            page=ReflectionLogPageInfo(
                page=page, pagesize=pagesize, total=total, hasnext=page * pagesize < total
            ),
        )
