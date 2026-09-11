import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from src.repositories.readonly.end_user_repo import EndUserRepository


class EndUserService:
    def __init__(self):
        self.repo: EndUserRepository = EndUserRepository()

    async def verify_end_user_in_workspace(
            self,
            db: AsyncSession,
            end_user_id: uuid.UUID,
            workspace_id: uuid.UUID
    ) -> bool:
        """校验该终端用户属于指定工作空间且有效。

        调用方拿到 False 即应拒绝（不存在 / 已软删 / 不属于本工作空间三种情况
        不区分——避免向调用方泄露「该 ID 存在于别的工作空间」）。

        Args:
            db: 请求级会话（本服务对 core 表只读，不提交）。
            end_user_id: 待校验的终端用户 ID。
            workspace_id: 调用方工作空间 ID，取自 principal.workspace_id。

        Returns:
            属于该工作空间且 ``is_active`` 为真时 True，否则 False。
        """
        return await self.repo.enduser_in_workspace(db, end_user_id, workspace_id)

