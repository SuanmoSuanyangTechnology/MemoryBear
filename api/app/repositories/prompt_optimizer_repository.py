import uuid

from sqlalchemy.orm import Session

from app.core.logging_config import get_db_logger
from app.models.prompt_optimizer_model import (
    PromptOptimizerSession, PromptOptimizerSessionHistory, RoleType
)

db_logger = get_db_logger()


class PromptOptimizerSessionRepository:
    """Repository for managing prompt optimization sessions and session history."""

    def __init__(self, db: Session):
        self.db = db

    def create_session(
            self,
            tenant_id: uuid.UUID,
            user_id: uuid.UUID
    ) -> PromptOptimizerSession:
        """
        Create a new prompt optimization session for a user and app.

        Args:
            tenant_id (uuid.UUID): The unique identifier of the tenant.
            user_id (uuid.UUID): The unique identifier of the user.

        Returns:
            PromptOptimizerSession: The newly created session object.
        """
        db_logger.debug(f"Create prompt optimization session: tenant_id={tenant_id}, user_id={user_id}")
        try:
            session = PromptOptimizerSession(
                tenant_id=tenant_id,
                user_id=user_id,
            )
            self.db.add(session)
            self.db.commit()
            self.db.refresh(session)
            db_logger.debug(f"Prompt optimization session created: ID:{session.id}")
            return session
        except Exception as e:
            db_logger.error(f"Error creating prompt optimization session: user_id={user_id} - {str(e)}")
            raise

    def get_session_history(
            self,
            session_id: uuid.UUID,
            user_id: uuid.UUID
    ) -> list[type[PromptOptimizerSessionHistory]]:
        """
        Retrieve all message history of a specific prompt optimization session.

        Args:
            session_id (uuid.UUID): The unique identifier of the session.
            user_id (uuid.UUID): The unique identifier of the user.

        Returns:
            list[PromptOptimizerSessionHistory]: A list of session history records
            ordered by creation time ascending.
        """
        db_logger.debug(f"Get prompt optimization session history: "
                        f"user_id={user_id}, session_id={session_id}")

        try:
            # First get the internal session ID from the session list table
            session = self.db.query(PromptOptimizerSession).filter(
                PromptOptimizerSession.id == session_id,
                PromptOptimizerSession.user_id == user_id
            ).first()
            
            if not session:
                return []
            
            history = self.db.query(PromptOptimizerSessionHistory).filter(
                PromptOptimizerSessionHistory.session_id == session.id,
                PromptOptimizerSessionHistory.user_id == user_id
            ).order_by(PromptOptimizerSessionHistory.created_at.asc()).all()
            return history
        except Exception as e:
            db_logger.error(f"Error retrieving prompt optimization session history: session_id={session_id} - {str(e)}")
            raise

    def create_message(
            self,
            tenant_id: uuid.UUID,
            session_id: uuid.UUID,
            user_id: uuid.UUID,
            role: RoleType,
            content: str,
    ) -> PromptOptimizerSessionHistory:
        """
        Create a new message in the session history.

        This method is a placeholder for future implementation.
        """
        try:
            # Get the session to ensure it exists and belongs to the user
            session = self.db.query(PromptOptimizerSession).filter(
                PromptOptimizerSession.id == session_id,
                PromptOptimizerSession.user_id == user_id,
                PromptOptimizerSession.tenant_id == tenant_id
            ).first()
            
            if not session:
                db_logger.error(f"Session {session_id} not found for user {user_id}")
                raise ValueError(f"Session {session_id} not found for user {user_id}")
            
            message = PromptOptimizerSessionHistory(
                tenant_id=tenant_id,
                session_id=session.id,
                user_id=user_id,
                role=role.value,
                content=content,
            )
            self.db.add(message)
            self.db.commit()
            return message
        except Exception as e:
            db_logger.error(f"Error creating prompt optimization session history: session_id={session_id} - {str(e)}")
            raise
