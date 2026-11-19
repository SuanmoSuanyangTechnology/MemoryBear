"""Scoped, read-only lookup; summary generation/writes belong to jobs."""

from uuid import UUID

from sqlalchemy import select

from src.models.references import Conversation, EndUser, Workspace
from src.models.references.conversation_detail_model import ConversationDetail


class ConversationDetailRepository:
    def __init__(self, postgres) -> None:
        self.postgres = postgres

    @staticmethod
    def statement(workspace_id: UUID, tenant_id: UUID, user_id: UUID, conversation_id: UUID):
        return (
            select(
                Conversation.updated_at,
                ConversationDetail.id.label("detail_id"),
                ConversationDetail.theme,
                ConversationDetail.question,
                ConversationDetail.summary,
                ConversationDetail.takeaways,
                ConversationDetail.info_score,
            )
            .select_from(Conversation)
            .join(Workspace, Workspace.id == Conversation.workspace_id)
            .join(EndUser, EndUser.id == user_id)
            .outerjoin(ConversationDetail, ConversationDetail.conversation_id == Conversation.id)
            .where(
                Workspace.id == workspace_id,
                Workspace.tenant_id == tenant_id,
                Workspace.is_active.is_(True),
                EndUser.workspace_id == workspace_id,
                EndUser.is_active.is_(True),
                Conversation.id == conversation_id,
                Conversation.user_id == str(user_id),
                Conversation.is_active.is_(True),
            )
            .limit(1)
        )

    async def read(self, workspace_id: UUID, tenant_id: UUID, user_id: UUID, conversation_id: UUID):
        async with self.postgres.session() as session:
            row = (await session.execute(
                self.statement(workspace_id, tenant_id, user_id, conversation_id)
            )).mappings().first()
            return dict(row) if row else None
