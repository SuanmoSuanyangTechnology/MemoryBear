import uuid

from sqlalchemy.orm import Session

from app.core.logging_config import get_db_logger
from app.models.prompt_optimizer_model import (
    PromptOptimizerSession,
    PromptOptimizerSessionHistory,
    RoleType,
    PromptHistory
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
            return session
        except Exception as e:
            db_logger.error(f"Error creating prompt optimization session: - {str(e)}")
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

            return message
        except Exception as e:
            db_logger.error(f"Error creating prompt optimization session history: session_id={session_id} - {str(e)}")
            raise

    def get_first_user_message(self, session_id: uuid.UUID) -> str | None:
        """
        Get the first user message from a session.

        Args:
            session_id (uuid.UUID): The session ID.

        Returns:
            str | None: The content of the first user message, or None if not found.
        """
        try:
            message = self.db.query(PromptOptimizerSessionHistory).filter(
                PromptOptimizerSessionHistory.session_id == session_id,
                PromptOptimizerSessionHistory.role == RoleType.USER.value
            ).order_by(
                PromptOptimizerSessionHistory.created_at.asc()
            ).first()
            
            return message.content if message else None
        except Exception as e:
            db_logger.error(f"Error getting first user message: session_id={session_id} - {str(e)}")
            raise


class PromptReleaseRepository:
    def __init__(self, db: Session):
        self.db = db

    def get_prompt_by_session_id(self, session_id: uuid.UUID) -> PromptHistory | None:
        prompt_obj = self.db.query(PromptHistory).filter(
            PromptHistory.session_id == session_id,
            PromptHistory.is_delete.is_(False)
        ).first()
        return prompt_obj

    def create_prompt_release(
            self,
            tanant_id: uuid.UUID,
            title: str,
            session_id: uuid.UUID,
            prompt: str,
    ) -> PromptHistory:
        try:
            prompt_obj = PromptHistory(
                tenant_id=tanant_id,
                title=title,
                session_id=session_id,
                prompt=prompt,
            )
            self.db.add(prompt_obj)
            return prompt_obj
        except Exception as e:
            db_logger.error(f"Error creating prompt release: session_id={session_id} - {str(e)}")
            raise

    def soft_delete_prompt(self, prompt_obj: PromptHistory) -> None:
        """
        Soft delete a prompt release by setting is_delete flag to True.

        Args:
            prompt_obj (PromptHistory): The prompt release object to delete.
        """
        try:
            prompt_obj.is_delete = True
            db_logger.debug(f"Soft deleted prompt release: id={prompt_obj.id}, session_id={prompt_obj.session_id}")
        except Exception as e:
            db_logger.error(f"Error soft deleting prompt release: id={prompt_obj.id} - {str(e)}")
            raise

    def get_prompt_by_id(self, prompt_id: uuid.UUID) -> PromptHistory | None:
        """
        Get a prompt release by its ID.

        Args:
            prompt_id (uuid.UUID): The prompt release ID.

        Returns:
            PromptHistory | None: The prompt release object or None if not found.
        """
        try:
            prompt_obj = self.db.query(PromptHistory).filter(
                PromptHistory.id == prompt_id
            ).first()
            return prompt_obj
        except Exception as e:
            db_logger.error(f"Error getting prompt release by id: id={prompt_id} - {str(e)}")
            raise

    def count_prompts(self, tenant_id: uuid.UUID) -> int:
        """
        Count total number of non-deleted prompts for a tenant.

        Args:
            tenant_id (uuid.UUID): The tenant ID.

        Returns:
            int: Total count of prompts.
        """
        try:
            count = self.db.query(PromptHistory).filter(
                PromptHistory.tenant_id == tenant_id,
                PromptHistory.is_delete.is_(False)
            ).count()
            return count
        except Exception as e:
            db_logger.error(f"Error counting prompts: tenant_id={tenant_id} - {str(e)}")
            raise

    def get_prompts_paginated(
            self,
            tenant_id: uuid.UUID,
            offset: int,
            limit: int
    ) -> list[PromptHistory]:
        """
        Get paginated list of prompt releases for a tenant.

        Args:
            tenant_id (uuid.UUID): The tenant ID.
            offset (int): Number of records to skip.
            limit (int): Maximum number of records to return.

        Returns:
            list[PromptHistory]: List of prompt releases.
        """
        try:
            prompts = self.db.query(PromptHistory).filter(
                PromptHistory.tenant_id == tenant_id,
                PromptHistory.is_delete.is_(False)
            ).order_by(
                PromptHistory.created_at.desc()
            ).offset(offset).limit(limit).all()
            return prompts
        except Exception as e:
            db_logger.error(f"Error getting paginated prompts: tenant_id={tenant_id} - {str(e)}")
            raise

    def count_prompts_by_keyword(self, tenant_id: uuid.UUID, keyword: str) -> int:
        """
        Count total number of non-deleted prompts matching keyword for a tenant.

        Args:
            tenant_id (uuid.UUID): The tenant ID.
            keyword (str): Search keyword for title.

        Returns:
            int: Total count of matching prompts.
        """
        try:
            count = self.db.query(PromptHistory).filter(
                PromptHistory.tenant_id == tenant_id,
                PromptHistory.is_delete.is_(False),
                PromptHistory.title.ilike(f"%{keyword}%")
            ).count()
            return count
        except Exception as e:
            db_logger.error(f"Error counting prompts by keyword: tenant_id={tenant_id}, keyword={keyword} - {str(e)}")
            raise

    def search_prompts_paginated(
            self,
            tenant_id: uuid.UUID,
            keyword: str,
            offset: int,
            limit: int
    ) -> list[PromptHistory]:
        """
        Search prompt releases by keyword in title with pagination.

        Args:
            tenant_id (uuid.UUID): The tenant ID.
            keyword (str): Search keyword for title.
            offset (int): Number of records to skip.
            limit (int): Maximum number of records to return.

        Returns:
            list[PromptHistory]: List of matching prompt releases.
        """
        try:
            prompts = self.db.query(PromptHistory).filter(
                PromptHistory.tenant_id == tenant_id,
                PromptHistory.is_delete.is_(False),
                PromptHistory.title.ilike(f"%{keyword}%")
            ).order_by(
                PromptHistory.created_at.desc()
            ).offset(offset).limit(limit).all()
            return prompts
        except Exception as e:
            db_logger.error(f"Error searching prompts: tenant_id={tenant_id}, keyword={keyword} - {str(e)}")
            raise
