"""Message feedback service (frozen port of the monolith
``FeedbackService``/``FavoriteService`` submit/read subset).

Like/dislike and favorite share one ``message_feedbacks`` row per
(message, user): the row carries a nullable, mutually exclusive feedback type
plus the favorite flag, so both interactions coexist without extra tables.
"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy.orm import Session

from ..errors import BizCode, BusinessException
from ..models.conversation_model import Message
from ..models.message_feedback_model import MessageFeedback
from ..utils.datetime_utils import to_timestamp_ms

logger = logging.getLogger(__name__)


class FeedbackService:
    """Message feedback service."""

    def __init__(self, db: Session):
        self.db = db

    def submit_feedback(
        self,
        message_id: uuid.UUID,
        conversation_id: uuid.UUID,
        workspace_id: uuid.UUID,
        user_id: str,
        feedback_type: str,
        feedback_content: str | None = None,
    ) -> dict:
        """Submit like/dislike feedback (idempotent).

        Re-clicking the same type cancels it; the opposite type switches it;
        both keep the denormalized counters on the message in sync.
        """

        existing = self.db.query(MessageFeedback).filter(
            MessageFeedback.message_id == message_id,
            MessageFeedback.user_id == user_id,
        ).first()

        message = self.db.get(Message, message_id)
        if not message:
            raise BusinessException("消息不存在", BizCode.NOT_FOUND)

        if existing:
            # Re-click of the same type: cancel.
            if existing.feedback_type == feedback_type:
                if feedback_type == "like":
                    message.like_count = max(0, message.like_count - 1)
                else:
                    message.dislike_count = max(0, message.dislike_count - 1)

                # The favorite may live on the same row: keep the row (and the
                # favorite) when set, otherwise delete it.
                if existing.is_favorite:
                    existing.feedback_type = None
                    existing.feedback_content = None
                else:
                    self.db.delete(existing)
                self.db.commit()
                logger.info(
                    "Feedback cancelled",
                    extra={
                        "message_id": str(message_id),
                        "user_id": user_id,
                        "feedback_type": feedback_type,
                    },
                )
                return {"action": "cancelled", "feedback_type": None}

            # Switch/set the type: existing.feedback_type may be None (a
            # favorite-only row receiving its first like/dislike).
            if existing.feedback_type == "like":
                message.like_count = max(0, message.like_count - 1)
            elif existing.feedback_type == "dislike":
                message.dislike_count = max(0, message.dislike_count - 1)
            if feedback_type == "like":
                message.like_count += 1
            else:
                message.dislike_count += 1

            existing.feedback_type = feedback_type
            existing.feedback_content = feedback_content
            self.db.commit()
            logger.info(
                "Feedback updated",
                extra={
                    "message_id": str(message_id),
                    "user_id": user_id,
                    "feedback_type": feedback_type,
                },
            )
            return {"action": "updated", "feedback_type": feedback_type}

        feedback = MessageFeedback(
            message_id=message_id,
            conversation_id=conversation_id,
            workspace_id=workspace_id,
            user_id=user_id,
            feedback_type=feedback_type,
            feedback_content=feedback_content,
            is_favorite=False,
        )
        self.db.add(feedback)

        if feedback_type == "like":
            message.like_count += 1
        else:
            message.dislike_count += 1

        self.db.commit()
        logger.info(
            "Feedback created",
            extra={
                "message_id": str(message_id),
                "user_id": user_id,
                "feedback_type": feedback_type,
            },
        )
        return {"action": "created", "feedback_type": feedback_type}

    def get_user_feedback(
        self,
        message_id: uuid.UUID,
        user_id: str,
    ) -> dict | None:
        """Current user's feedback of one message; None when absent."""

        feedback = self.db.query(MessageFeedback).filter(
            MessageFeedback.message_id == message_id,
            MessageFeedback.user_id == user_id,
        ).first()

        if not feedback:
            return None

        return {
            "feedback_type": feedback.feedback_type,
            "feedback_content": feedback.feedback_content,
            "created_at": to_timestamp_ms(feedback.created_at),
        }


class FavoriteService:
    """Message favorite service.

    The favorite flag reuses ``message_feedbacks.is_favorite`` on the same
    (message, user) interaction row as like/dislike.
    """

    def __init__(self, db: Session):
        self.db = db

    def toggle_favorite(
        self,
        message_id: uuid.UUID,
        conversation_id: uuid.UUID,
        workspace_id: uuid.UUID,
        user_id: str,
    ) -> dict:
        """Toggle the favorite flag (idempotent).

        An existing interaction row just flips the flag (an empty row left with
        no feedback type is deleted for cleanliness); without a row, one is
        inserted carrying the favorite only.

        Callers must ensure the message exists and pass its conversation id.
        """

        existing = self.db.query(MessageFeedback).filter(
            MessageFeedback.message_id == message_id,
            MessageFeedback.user_id == user_id,
        ).first()

        if existing:
            if existing.is_favorite:
                existing.is_favorite = False
                if existing.feedback_type is None:
                    # No like/dislike and no favorite left: drop the empty row.
                    self.db.delete(existing)
                self.db.commit()
                logger.info(
                    "Favorite cancelled",
                    extra={"message_id": str(message_id), "user_id": user_id},
                )
                return {"action": "cancelled", "is_favorited": False}

            existing.is_favorite = True
            self.db.commit()
            logger.info(
                "Message favorited",
                extra={"message_id": str(message_id), "user_id": user_id},
            )
            return {"action": "created", "is_favorited": True}

        favorite = MessageFeedback(
            message_id=message_id,
            conversation_id=conversation_id,
            workspace_id=workspace_id,
            user_id=user_id,
            feedback_type=None,
            feedback_content=None,
            is_favorite=True,
        )
        self.db.add(favorite)
        self.db.commit()
        logger.info(
            "Message favorited",
            extra={"message_id": str(message_id), "user_id": user_id},
        )
        return {"action": "created", "is_favorited": True}


__all__ = ["FavoriteService", "FeedbackService"]
