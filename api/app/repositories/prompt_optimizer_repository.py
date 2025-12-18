import uuid
from typing import Optional

from sqlalchemy.orm import Session

from app.core.logging_config import get_db_logger
from app.models.prompt_optimizer_model import (
    PromptOptimizerModelConfig,
    PromptOptimizerSession, PromptOptimizerSessionHistory, RoleType
)

db_logger = get_db_logger()


class PromptOptimizerModelConfigRepository:
    """Repository for managing prompt optimizer model configurations."""

    def __init__(self, db: Session):
        self.db = db

    def get_by_tenant_id(self, tenant_id: uuid.UUID) -> Optional[PromptOptimizerModelConfig]:
        """
        Retrieve the prompt optimizer model configuration for a specific tenant.

        Args:
           tenant_id (uuid.UUID): The unique identifier of the tenant.

        Returns:
           Optional[PromptOptimizerModelConfig]: The model configuration if found, else None.
        """
        db_logger.debug(f"Get prompt optimization model configuration: tenant_id={tenant_id}")

        try:
            config = self.db.query(PromptOptimizerModelConfig).filter(
                PromptOptimizerModelConfig.tenant_id == tenant_id,
                # PromptOptimizerModelConfig.model_id == model_id
            ).first()
            if config:
                db_logger.debug(f"Prompt optimization model configuration found: (ID: {config.id})")
            else:
                db_logger.debug(f"Prompt optimization model configuration not found: tenant_id={tenant_id}")
            return config
        except Exception as e:
            db_logger.error(
                f"Error retrieving prompt optimization model configuration: tenant_id={tenant_id} - {str(e)}")
            raise

    def get_by_config_id(self, tenant_id: uuid.UUID, config_id: uuid.UUID) -> Optional[PromptOptimizerModelConfig]:
        """
        Retrieve a specific prompt optimizer model configuration by config ID and tenant ID.

        Args:
            tenant_id (uuid.UUID): The unique identifier of the tenant.
            config_id (uuid.UUID): The unique identifier of the model configuration.

        Returns:
            Optional[PromptOptimizerModelConfig]: The model configuration if found, else None.
        """
        db_logger.debug(f"Get prompt optimization model configuration: config_id={config_id}, tenant_id={tenant_id}")
        try:
            model = self.db.query(PromptOptimizerModelConfig).filter(
                PromptOptimizerModelConfig.tenant_id == tenant_id,
                PromptOptimizerModelConfig.id == config_id
            ).first()
            if model:
                db_logger.debug(f"Prompt optimization model configuration found: (ID: {model.id})")
            else:
                db_logger.debug(f"Prompt optimization model configuration not found: config_id={config_id}")
            return model
        except Exception as e:
            db_logger.error(
                f"Error retrieving prompt optimization model configuration: model_id={config_id} - {str(e)}")
            raise

    def create_or_update(
            self,
            config_id: uuid.UUID,
            tenant_id: uuid.UUID,
            system_prompt: str,
    ) -> Optional[PromptOptimizerModelConfig]:
        """
        Create a new or update an existing prompt optimizer model configuration.

        If a configuration with the given config_id exists, it updates its system_prompt.
        Otherwise, it creates a new configuration record.

        Args:
            config_id (uuid.UUID): The unique identifier for the configuration.
            tenant_id (uuid.UUID): The tenant's unique identifier.
            system_prompt (str): The system prompt content for prompt optimization.

        Returns:
            Optional[PromptOptimizerModelConfig]: The created or updated model configuration.
        """
        db_logger.debug(f"Create/Update prompt optimization model configuration: tenant_id={tenant_id}")
        existing_config = self.get_by_config_id(tenant_id, config_id)

        if existing_config:
            existing_config.system_prompt = system_prompt
            self.db.commit()
            self.db.refresh(existing_config)
            db_logger.debug(f"Prompt optimization model configuration update: ID:{config_id}")
            return existing_config
        else:
            config = PromptOptimizerModelConfig(
                id=config_id,
                # model_id=model_id,
                tenant_id=tenant_id,
                system_prompt=system_prompt
            )
            self.db.add(config)
            self.db.commit()
            self.db.refresh(config)
            db_logger.debug(f"Prompt optimization model configuration created: ID:{config.id}")
            return config


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
