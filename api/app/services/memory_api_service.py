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
        app = self.db.query(App).filter(App.id == end_user.app_id).first()
        
        if not app:
            logger.warning(f"App not found for end_user: {end_user_id}")
            raise ResourceNotFoundException(
                resource_type="App",
                resource_id=str(end_user.app_id)
            )
        
        if app.workspace_id != workspace_id:
            logger.warning(
                f"End user {end_user_id} belongs to workspace {app.workspace_id}, "
                f"not authorized workspace {workspace_id}"
            )
            raise BusinessException(
                message="End user does not belong to authorized workspace",
                code=BizCode.FORBIDDEN
            )
        
        logger.info(f"End user {end_user_id} validated successfully")
        return end_user
    
    async def write_memory(
        self,
        workspace_id: uuid.UUID,
        end_user_id: str,
        message: str,
        config_id: Optional[str] = None,
        storage_type: str = "neo4j",
        user_rag_memory_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Write memory with validation.
        
        Validates end_user exists and belongs to workspace, then delegates
        to MemoryAgentService.write_memory.
        
        Args:
            workspace_id: Workspace ID for resource validation
            end_user_id: End user identifier (used as end_user_id)
            message: Message content to store
            config_id: Optional memory configuration ID
            storage_type: Storage backend (neo4j or rag)
            user_rag_memory_id: Optional RAG memory ID
            
        Returns:
            Dict with status and end_user_id
            
        Raises:
            ResourceNotFoundException: If end_user not found
            BusinessException: If end_user not in authorized workspace or write fails
        """
        logger.info(f"Writing memory for end_user: {end_user_id}, workspace: {workspace_id}")
        
        # Validate end_user exists and belongs to workspace
        self.validate_end_user(end_user_id, workspace_id)
        
        # Use end_user_id as end_user_id for memory operations
        
        try:
            # Delegate to MemoryAgentService
            result = await MemoryAgentService().write_memory(
                end_user_id=end_user_id,
                message=message,
                config_id=config_id,
                db=self.db,
                storage_type=storage_type,
                user_rag_memory_id=user_rag_memory_id or ""
            )
            
            logger.info(f"Memory write successful for end_user: {end_user_id}")
            
            return {
                "status": "success" if result == "success" else result,
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
        config_id: Optional[str] = None,
        storage_type: str = "neo4j",
        user_rag_memory_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Read memory with validation.
        
        Validates end_user exists and belongs to workspace, then delegates
        to MemoryAgentService.read_memory.
        
        Args:
            workspace_id: Workspace ID for resource validation
            end_user_id: End user identifier (used as end_user_id)
            message: Query message
            search_switch: Search mode (0=deep search with verification, 1=deep search, 2=fast search)
            config_id: Optional memory configuration ID
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
        
        # Use end_user_id as end_user_id for memory operations

        
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
