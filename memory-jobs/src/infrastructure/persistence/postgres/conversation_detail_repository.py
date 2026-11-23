"""Scoped conversation snapshot and short, guarded summary writes."""

from __future__ import annotations

import hashlib
import json
from uuid import UUID, uuid4

from sqlalchemy import select

from src.domain.working_memory.errors import DetailFailure
from src.models import (
    Conversation,
    ConversationDetail,
    EndUser,
    Message,
    ModelConfig,
    Workspace,
    WorkspaceDefaultModelPreset,
)


class SqlAlchemyConversationDetailRepository:
    def __init__(self, session) -> None:
        self.session = session

    @staticmethod
    def scoped_statement(payload):
        return (
            select(Conversation)
            .join(Workspace, Workspace.id == Conversation.workspace_id)
            .join(EndUser, EndUser.id == UUID(payload["end_user_id"]))
            .where(
                Conversation.id == UUID(payload["conversation_id"]),
                Conversation.user_id == payload["end_user_id"],
                Conversation.is_active.is_(True),
                Workspace.id == UUID(payload["workspace_id"]),
                Workspace.tenant_id == UUID(payload["tenant_id"]),
                Workspace.is_active.is_(True),
                EndUser.workspace_id == Workspace.id,
                EndUser.is_active.is_(True),
            )
        )

    @staticmethod
    def messages_statement(conversation_id):
        # Legacy takes the FIRST 20 chronological current, non-deleted messages.
        return (
            select(Message)
            .where(
                Message.conversation_id == conversation_id,
                Message.is_deleted.is_not(True),
                Message.is_current.is_not(False),
            )
            .order_by(Message.created_at.asc())
            .limit(20)
        )

    def _conversation(self, session, payload, *, lock=False):
        stmt = self.scoped_statement(payload)
        if lock:
            stmt = stmt.with_for_update(of=Conversation)
        row = session.scalar(stmt)
        if row is None:
            raise DetailFailure("invalid_conversation", "Conversation not found")
        return row

    @staticmethod
    def _detail(session, cid):
        return session.scalars(
            select(ConversationDetail).where(ConversationDetail.conversation_id == cid)
        ).first()

    def read(self, payload):
        session = self.session
        conversation = self._conversation(session, payload)
        detail = self._detail(session, conversation.id)
        return {
            "updated_at": conversation.updated_at,
            "detail": None
            if detail is None
            else {
                "theme": detail.theme,
                "question": detail.question or [],
                "summary": detail.summary,
                "takeaways": detail.takeaways,
                "info_score": detail.info_score,
            },
        }

    def _snapshot(self, session, payload, conversation):
        workspace = session.get(Workspace, UUID(payload["workspace_id"]))
        model_id_value = workspace.llm
        if workspace.is_default_config and not model_id_value:
            model_id_value = session.scalar(
                select(WorkspaceDefaultModelPreset.llm_model_config_id).where(
                    WorkspaceDefaultModelPreset.singleton_key == "default"
                )
            )
        try:
            model_id = UUID(str(model_id_value))
        except (TypeError, ValueError) as exc:
            raise DetailFailure(
                "model_not_found", "Workspace model configuration not found"
            ) from exc
        model = session.scalar(
            select(ModelConfig).where(
                ModelConfig.id == model_id,
                ModelConfig.is_active.is_(True),
                (ModelConfig.tenant_id == UUID(payload["tenant_id"]))
                | ModelConfig.is_public.is_(True),
            )
        )
        if model is None:
            raise DetailFailure("model_not_found", "Workspace model configuration not found")
        rows = session.scalars(self.messages_statement(conversation.id)).all()
        messages = []
        for row in rows:
            files = (row.meta_data or {}).get("history_files", {})
            if files and model.provider:
                if files.get("provider") != model.provider:
                    continue
                content = [{"type": "text", "text": row.content}, *files.get("content", [])]
            else:
                content = row.content
            messages.append({"role": row.role, "content": content})
        version = hashlib.sha256(
            json.dumps(
                {
                    "updated_at": str(conversation.updated_at),
                    "model_id": str(model_id),
                    "provider": model.provider,
                    "messages": messages,
                },
                sort_keys=True,
                default=str,
            ).encode()
        ).hexdigest()
        return {
            **payload,
            "updated_at": conversation.updated_at,
            "messages": messages,
            "model_id": str(model_id),
            "version": version,
        }

    def generation_context(self, payload):
        session = self.session
        conversation = self._conversation(session, payload)
        return self._snapshot(session, payload, conversation)

    def save(self, payload, context, data):
        session = self.session
        # Serialize insert/update even though the legacy table has no unique
        # constraint on conversation_id. Never hold this lock during LLM I/O.
        conversation = self._conversation(session, payload, lock=True)
        current = self._snapshot(session, payload, conversation)
        if current["version"] != context["version"]:
            raise DetailFailure("source_changed", "会话或模型配置已变更，请重新请求")
        detail = self._detail(session, conversation.id)
        if detail is None:
            detail = ConversationDetail(id=uuid4(), conversation_id=conversation.id)
            session.add(detail)
        for name, value in data.items():
            setattr(detail, name, value)
