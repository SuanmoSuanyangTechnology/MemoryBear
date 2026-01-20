"""
Session Service for managing user sessions and conversation history.

This service provides clean Redis interactions with error handling and
session management utilities.
"""
from typing import List, Optional

from app.core.logging_config import get_agent_logger
from app.core.memory.agent.utils.redis_tool import RedisSessionStore


logger = get_agent_logger(__name__)


class SessionService:
    """Service for managing user sessions and conversation history."""
    
    def __init__(self, store: RedisSessionStore):
        """
        Initialize the session service.
        
        Args:
            store: Redis session store instance
        """
        self.store = store
        logger.info("SessionService initialized")
    
    def resolve_user_id(self, session_string: str) -> str:
        """
        Extract user ID from session string.
        
        Handles formats like:
        - 'call_id_user123' -> 'user123'
        - 'prefix_id_user456_suffix' -> 'user456_suffix'
        
        Args:
            session_string: Session identifier string
            
        Returns:
            Extracted user ID
        """
        try:
            # Split by '_id_' and take everything after it
            parts = session_string.split('_id_')
            if len(parts) > 1:
                return parts[1]
            
            # Fallback: return original string
            return session_string
            
        except Exception as e:
            logger.warning(
                f"Failed to parse user ID from session string '{session_string}': {e}"
            )
            return session_string
    
    async def get_history(
        self,
        user_id: str,
        apply_id: str,
        group_id: str
    ) -> List[dict]:
        """
        Retrieve conversation history from Redis.
        
        Args:
            user_id: User identifier
            apply_id: Application identifier
            group_id: Group identifier
            
        Returns:
            List of conversation history items with Query and Answer keys
            Returns empty list if no history found or on error
        """
        try:
            history = self.store.find_user_apply_group(user_id, apply_id, group_id)
            
            # Validate history structure
            if not isinstance(history, list):
                logger.warning(
                    f"Invalid history format for user {user_id}, "
                    f"apply {apply_id}, group {group_id}: expected list, got {type(history)}"
                )
                return []
            
            return history
            
        except Exception as e:
            logger.error(
                f"Failed to retrieve history for user {user_id}, "
                f"apply {apply_id}, group {group_id}: {e}",
                exc_info=True
            )
            # Return empty list on error to allow execution to continue
            return []
    
    async def save_session(
        self,
        user_id: str,
        query: str,
        apply_id: str,
        group_id: str,
        ai_response: str
    ) -> Optional[str]:
        """
        Save conversation turn to Redis.
        
        Args:
            user_id: User identifier
            query: User query/message
            apply_id: Application identifier
            group_id: Group identifier
            ai_response: AI response/answer
            
        Returns:
            Session ID if successful, None on error
        """
        try:
            # Validate required fields
            if not user_id:
                logger.warning("Cannot save session: user_id is empty")
                return None
            
            if not query:
                logger.warning("Cannot save session: query is empty")
                return None
            
            # Save session
            session_id = self.store.save_session(
                userid=user_id,
                messages=query,
                apply_id=apply_id,
                group_id=group_id,
                aimessages=ai_response
            )
            
            logger.info(f"Session saved successfully: {session_id}")
            return session_id
            
        except Exception as e:
            logger.error(
                f"Failed to save session for user {user_id}: {e}",
                exc_info=True
            )
            return None
    
    async def cleanup_duplicates(self) -> int:
        """
        Remove duplicate session entries.
        
        Duplicates are identified by matching:
        - sessionid
        - user_id (id field)
        - group_id
        - messages
        - aimessages
        
        Returns:
            Number of duplicate sessions deleted
        """
        try:
            deleted_count = self.store.delete_duplicate_sessions()
            logger.info(f"Cleaned up {deleted_count} duplicate sessions")
            return deleted_count
            
        except Exception as e:
            logger.error(f"Failed to cleanup duplicate sessions: {e}", exc_info=True)
            return 0
