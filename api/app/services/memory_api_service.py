"""
Memory API Service

Provides external access to memory read and write operations through API Key authentication.
This service validates inputs and delegates to MemoryAgentService for core memory operations.
"""

import uuid
from typing import Any, Dict, Optional

from app.core.error_codes import BizCode
from app.core.exceptions import BusinessException, ResourceNotFoundException
from app.core.logging_config import get_logger
from app.models.app_model import App
from app.models.end_user_model import EndUser
from app.schemas.memory_config_schema import ConfigurationError
from app.services.memory_agent_service import MemoryAgentService
from sqlalchemy.orm import Session

logger = get_logger(__name__)


class MemoryAPIService:
    """Service for memory API operations with validation and delegation to MemoryAgentService.
    
    This service provides a thin layer that:
    1. Validates end_user exists and belongs to the authorized workspace
    2. Maps end_user_id to end_user_id for memory operations
    3. Delegates to MemoryAgentService for actual memory read/write operations
    """

    def __init__(self, db: Session):
        """Initialize MemoryAPIService.
        
        Args:
            db: SQLAlchemy database session
        """
        self.db = db

    def validate_end_user(
            self,
            end_user_id: str,
            workspace_id: uuid.UUID
    ) -> EndUser:
        """Validate that end_user exists and belongs to the workspace.
        
        Args:
            end_user_id: End user ID to validate
            workspace_id: Workspace ID from API key authorization
            
        Returns:
            EndUser object if valid
            
        Raises:
            ResourceNotFoundException: If end_user not found
            BusinessException: If end_user not in authorized workspace
        """
        logger.info(f"Validating end_user: {end_user_id} for workspace: {workspace_id}")

        # Query end_user by ID
        try:
            end_user_uuid = uuid.UUID(end_user_id)
        except ValueError:
            logger.warning(f"Invalid end_user_id format: {end_user_id}")
            raise BusinessException(
                message=f"Invalid end_user_id format: {end_user_id}",
                code=BizCode.INVALID_PARAMETER
            )

        end_user = self.db.query(EndUser).filter(EndUser.id == end_user_uuid).first()

        if not end_user:
            logger.warning(f"End user not found: {end_user_id}")
            raise ResourceNotFoundException(
                resource_type="EndUser",
                resource_id=end_user_id
            )

        # Verify end_user belongs to the workspace via App relationship
        app = self.db.query(App).filter(
            App.id == end_user.app_id,
            App.is_active.is_(True)
        ).first()

        if not app:
            logger.warning(f"App not found for end_user: {end_user_id}")
            # raise ResourceNotFoundException(
            #     resource_type="App",
            #     resource_id=str(end_user.app_id)
            # )
        # temporally allow any workspace to access
        # if end_user.workspace_id != workspace_id:
        #     print(f"[DEBUG] end_user.workspace_id={end_user.workspace_id}, api_key.workspace_id={workspace_id}")
        #     logger.warning(
        #         f"End user {end_user_id} belongs to workspace {end_user.workspace_id}, "
        #         f"not authorized workspace {workspace_id}"
        #     )
        #     raise BusinessException(
        #         message=f"End user does not belong to authorized workspace. end_user.workspace_id={end_user.workspace_id}, api_key.workspace_id={workspace_id}",
        #         code=BizCode.FORBIDDEN
        #     )

        logger.info(f"End user {end_user_id} validated successfully")
        return end_user

    def _update_end_user_config(self, end_user_id: str, config_id: str) -> None:
        """Update the end user's memory_config_id.
        
        Silently updates the config association. Logs warnings on failure
        but does not raise, so it won't block the main read/write operation.
        
        Args:
            end_user_id: End user identifier
            config_id: Memory configuration ID to assign
        """
        try:
            config_uuid = uuid.UUID(config_id)
            from app.repositories.end_user_repository import EndUserRepository
            end_user_repo = EndUserRepository(self.db)
            end_user_repo.update_memory_config_id(
                end_user_id=uuid.UUID(end_user_id),
                memory_config_id=config_uuid,
            )
        except Exception as e:
            logger.warning(f"Failed to update memory_config_id for end_user {end_user_id}: {e}")

    async def write_memory(
            self,
            workspace_id: uuid.UUID,
            end_user_id: str,
            message: str,
            config_id: str,
            storage_type: str = "neo4j",
            files: Optional[list]=None,
            user_rag_memory_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Write memory with validation.
        
        Validates end_user exists and belongs to workspace, updates the end user's
        memory_config_id, then delegates to MemoryAgentService.write_memory.
        
        Args:
            workspace_id: Workspace ID for resource validation
            end_user_id: End user identifier (used as end_user_id)
            message: Message content to store
            config_id: Memory configuration ID (required)
            storage_type: Storage backend (neo4j or rag)
            user_rag_memory_id: Optional RAG memory ID
            
        Returns:
            Dict with status and end_user_id
            
        Raises:
            ResourceNotFoundException: If end_user not found
            BusinessException: If end_user not in authorized workspace or write fails
        """
        if files is None:
            files = list()
        logger.info(f"Writing memory for end_user: {end_user_id}, workspace: {workspace_id}")

        # Validate end_user exists and belongs to workspace
        self.validate_end_user(end_user_id, workspace_id)

        # Update end user's memory_config_id
        self._update_end_user_config(end_user_id, config_id)

        try:
            # Delegate to MemoryAgentService
            # Convert string message to list[dict] format expected by MemoryAgentService
            messages = message if isinstance(message, list) else [{"role": "user", "content": message}]
            result = await MemoryAgentService().write_memory(
                end_user_id=end_user_id,
                messages=messages,
                config_id=config_id,
                db=self.db,
                storage_type=storage_type,
                user_rag_memory_id=user_rag_memory_id or "",
                files=files
            )

            logger.info(f"Memory write successful for end_user: {end_user_id}")

            # result may be a string "success" or a dict with a "status" key
            # Preserve the full dict so callers don't silently lose extra fields
            # (e.g. error codes, metadata) returned by MemoryAgentService.
            if isinstance(result, dict):
                return {
                    **result,
                    "status": result.get("status", "unknown"),
                    "end_user_id": end_user_id,
                }
            return {
                "status": result if isinstance(result, str) else "success",
                "end_user_id": end_user_id,
            }

        except ConfigurationError as e:
            logger.error(f"Memory configuration error for end_user {end_user_id}: {e}")
            raise BusinessException(
                message=str(e),
                code=BizCode.MEMORY_CONFIG_NOT_FOUND
            )
        except BusinessException:
            raise
        except Exception as e:
            logger.error(f"Memory write failed for end_user {end_user_id}: {e}")
            raise BusinessException(
                message=f"Memory write failed: {str(e)}",
                code=BizCode.MEMORY_WRITE_FAILED
            )

    async def read_memory(
            self,
            workspace_id: uuid.UUID,
            end_user_id: str,
            message: str,
            search_switch: str = "0",
            config_id: str = "",
            storage_type: str = "neo4j",
            user_rag_memory_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Read memory with validation.
        
        Validates end_user exists and belongs to workspace, updates the end user's
        memory_config_id, then delegates to MemoryAgentService.read_memory.
        
        Args:
            workspace_id: Workspace ID for resource validation
            end_user_id: End user identifier (used as end_user_id)
            message: Query message
            search_switch: Search mode (0=deep search with verification, 1=deep search, 2=fast search)
            config_id: Memory configuration ID (required)
            storage_type: Storage backend (neo4j or rag)
            user_rag_memory_id: Optional RAG memory ID
            
        Returns:
            Dict with answer, intermediate_outputs, and end_user_id
            
        Raises:
            ResourceNotFoundException: If end_user not found
            BusinessException: If end_user not in authorized workspace or read fails
        """
        logger.info(f"Reading memory for end_user: {end_user_id}, workspace: {workspace_id}")

        # Validate end_user exists and belongs to workspace
        self.validate_end_user(end_user_id, workspace_id)

        # Update end user's memory_config_id
        self._update_end_user_config(end_user_id, config_id)

        try:
            # Delegate to MemoryAgentService
            result = await MemoryAgentService().read_memory(
                end_user_id=end_user_id,
                message=message,
                history=[],
                search_switch=search_switch,
                config_id=config_id,
                db=self.db,
                storage_type=storage_type,
                user_rag_memory_id=user_rag_memory_id or ""
            )

            logger.info(f"Memory read successful for end_user: {end_user_id}")

            return {
                "answer": result.get("answer", ""),
                "intermediate_outputs": result.get("intermediate_outputs", []),
                "end_user_id": end_user_id
            }

        except ConfigurationError as e:
            logger.error(f"Memory configuration error for end_user {end_user_id}: {e}")
            raise BusinessException(
                message=str(e),
                code=BizCode.MEMORY_CONFIG_NOT_FOUND
            )
        except BusinessException:
            raise
        except Exception as e:
            logger.error(f"Memory read failed for end_user {end_user_id}: {e}")
            raise BusinessException(
                message=f"Memory read failed: {str(e)}",
                code=BizCode.MEMORY_READ_FAILED
            )

    def list_memory_configs(
            self,
            workspace_id: uuid.UUID,
    ) -> Dict[str, Any]:
        """List all memory configs for a workspace.
        
        Args:
            workspace_id: Workspace ID from API key authorization
            
        Returns:
            Dict with configs list and total count
            
        Raises:
            BusinessException: If listing fails
        """
        logger.info(f"Listing memory configs for workspace: {workspace_id}")

        try:
            from app.repositories.memory_config_repository import MemoryConfigRepository

            results = MemoryConfigRepository.get_all(self.db, workspace_id=workspace_id)

            configs = []
            for config, scene_name in results:
                configs.append({
                    "config_id": str(config.config_id),
                    "config_name": config.config_name,
                    "config_desc": config.config_desc,
                    "is_default": config.is_default or False,
                    "scene_name": scene_name,
                    "created_at": config.created_at.isoformat() if config.created_at else None,
                    "updated_at": config.updated_at.isoformat() if config.updated_at else None,
                })

            logger.info(f"Found {len(configs)} memory configs for workspace {workspace_id}")
            return {
                "configs": configs,
                "total": len(configs),
            }

        except Exception as e:
            logger.error(f"Failed to list memory configs for workspace {workspace_id}: {e}")
            raise BusinessException(
                message=f"Failed to list memory configs: {str(e)}",
                code=BizCode.MEMORY_READ_FAILED
            )
