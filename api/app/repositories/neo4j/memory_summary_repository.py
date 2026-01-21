# -*- coding: utf-8 -*-
"""Memory Summary Repository Module

This module provides data access functionality for MemorySummary nodes.

Classes:
    MemorySummaryRepository: Repository for managing MemorySummary CRUD operations
"""

from datetime import datetime
from typing import Any, Dict, List, Optional

from app.repositories.neo4j.base_neo4j_repository import BaseNeo4jRepository
from app.repositories.neo4j.neo4j_connector import Neo4jConnector


class MemorySummaryRepository(BaseNeo4jRepository):
    """Memory Summary Repository
    
    Manages CRUD operations for MemorySummary nodes.
    Provides methods to query summaries by end_user_id, user_id, and time ranges.
    
    Attributes:
        connector: Neo4j connector instance
        node_label: Node label, fixed as "MemorySummary"
    """
    
    def __init__(self, connector: Neo4jConnector):
        """Initialize memory summary repository
        
        Args:
            connector: Neo4j connector instance
        """
        super().__init__(connector, "MemorySummary")
    
    def _map_to_dict(self, node_data: Dict) -> Dict[str, Any]:
        """Map node data to dictionary format
        
        Args:
            node_data: Node data returned from Neo4j query
            
        Returns:
            Dict[str, Any]: Memory summary data dictionary
        """
        # Extract node data from query result
        n = node_data.get('n', node_data)
        
        # Handle datetime fields
        if isinstance(n.get('created_at'), str):
            n['created_at'] = datetime.fromisoformat(n['created_at'])
        
        return dict(n)
    
    async def find_by_end_user_id(
        self, 
        end_user_id: str,
        limit: int = 1000,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None
    ) -> List[Dict[str, Any]]:
        """Query memory summaries by end_user_id
        
        Args:
            end_user_id: Group ID to filter by
            limit: Maximum number of results to return
            start_date: Optional start date filter
            end_date: Optional end date filter
            
        Returns:
            List[Dict[str, Any]]: List of memory summary dictionaries
        """
        query = f"""
        MATCH (n:{self.node_label})
        WHERE n.end_user_id = $end_user_id
        """
        
        params = {"end_user_id": end_user_id, "limit": limit}
        
        # Add date range filters if provided
        if start_date:
            query += " AND n.created_at >= $start_date"
            params["start_date"] = start_date
        
        if end_date:
            query += " AND n.created_at <= $end_date"
            params["end_date"] = end_date
        
        query += """
        RETURN n
        ORDER BY n.created_at DESC
        LIMIT $limit
        """
        
        results = await self.connector.execute_query(query, **params)
        return [self._map_to_dict(r) for r in results]
    
    async def find_by_user_id(
        self, 
        user_id: str, 
        limit: int = 1000,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None
    ) -> List[Dict[str, Any]]:
        """Query memory summaries by user_id
        
        Args:
            user_id: User ID to filter by
            limit: Maximum number of results to return
            start_date: Optional start date filter
            end_date: Optional end date filter
            
        Returns:
            List[Dict[str, Any]]: List of memory summary dictionaries
        """
        query = f"""
        MATCH (n:{self.node_label})
        WHERE n.user_id = $user_id
        """
        
        params = {"user_id": user_id, "limit": limit}
        
        # Add date range filters if provided
        if start_date:
            query += " AND n.created_at >= $start_date"
            params["start_date"] = start_date
        
        if end_date:
            query += " AND n.created_at <= $end_date"
            params["end_date"] = end_date
        
        query += """
        RETURN n
        ORDER BY n.created_at DESC
        LIMIT $limit
        """
        
        results = await self.connector.execute_query(query, **params)
        return [self._map_to_dict(r) for r in results]
    
    async def find_by_group_and_user(
        self,
        end_user_id: str,
        user_id: str,
        limit: int = 1000,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None
    ) -> List[Dict[str, Any]]:
        """Query memory summaries by both end_user_id and user_id
        
        Args:
            end_user_id: Group ID to filter by
            user_id: User ID to filter by
            limit: Maximum number of results to return
            start_date: Optional start date filter
            end_date: Optional end date filter
            
        Returns:
            List[Dict[str, Any]]: List of memory summary dictionaries
        """
        query = f"""
        MATCH (n:{self.node_label})
        WHERE n.end_user_id = $end_user_id AND n.user_id = $user_id
        """
        
        params = {"end_user_id": end_user_id, "user_id": user_id, "limit": limit}
        
        # Add date range filters if provided
        if start_date:
            query += " AND n.created_at >= $start_date"
            params["start_date"] = start_date
        
        if end_date:
            query += " AND n.created_at <= $end_date"
            params["end_date"] = end_date
        
        query += """
        RETURN n
        ORDER BY n.created_at DESC
        LIMIT $limit
        """
        
        results = await self.connector.execute_query(query, **params)
        return [self._map_to_dict(r) for r in results]
    
    async def find_recent_summaries(
        self,
        end_user_id: str,
        days: int = 7,
        limit: int = 1000
    ) -> List[Dict[str, Any]]:
        """Query recent memory summaries
        
        Args:
            end_user_id: Group ID to filter by
            days: Number of recent days to query
            limit: Maximum number of results to return
            
        Returns:
            List[Dict[str, Any]]: List of memory summary dictionaries
        """
        query = f"""
        MATCH (n:{self.node_label})
        WHERE n.end_user_id = $end_user_id
        AND n.created_at >= datetime() - duration({{days: $days}})
        RETURN n
        ORDER BY n.created_at DESC
        LIMIT $limit
        """
        
        results = await self.connector.execute_query(
            query,
            group_id=group_id,
            days=days,
            limit=limit
        )
        return [self._map_to_dict(r) for r in results]
    
    async def find_by_content_keywords(
        self,
        group_id: str,
        keywords: List[str],
        limit: int = 100
    ) -> List[Dict[str, Any]]:
        """Query memory summaries by content keywords
        
        Args:
            group_id: Group ID to filter by
            keywords: List of keywords to search for in content
            limit: Maximum number of results to return
            
        Returns:
            List[Dict[str, Any]]: List of memory summary dictionaries
        """
        # Build keyword search conditions
        keyword_conditions = []
        params = {"group_id": group_id, "limit": limit}
        
        for i, keyword in enumerate(keywords):
            keyword_conditions.append(f"toLower(n.content) CONTAINS toLower($keyword_{i})")
            params[f"keyword_{i}"] = keyword
        
        keyword_filter = " OR ".join(keyword_conditions)
        
        query = f"""
        MATCH (n:{self.node_label})
        WHERE n.group_id = $group_id
        AND ({keyword_filter})
        RETURN n
        ORDER BY n.created_at DESC
        LIMIT $limit
        """
        
        results = await self.connector.execute_query(query, **params)
        return [self._map_to_dict(r) for r in results]
    
    async def get_summary_count_by_group(self, group_id: str) -> int:
        """Get count of memory summaries for a group
        
        Args:
            group_id: Group ID to count summaries for
            
        Returns:
            int: Number of memory summaries
        """
        query = f"""
        MATCH (n:{self.node_label})
        WHERE n.group_id = $group_id
        RETURN count(n) as count
        """
        
        results = await self.connector.execute_query(query, group_id=group_id)
        return results[0]['count'] if results else 0
    