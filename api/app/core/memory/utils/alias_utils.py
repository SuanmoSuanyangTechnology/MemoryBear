"""
Utility functions for entity alias management.

This module provides functions for validating, adding, merging, and normalizing
entity aliases in the knowledge graph system.
"""

import logging
from typing import List, Any, Dict, Set

logger = logging.getLogger(__name__)


def validate_aliases(v: Any) -> List[str]:
    """Validate and clean aliases field.
    
    Filters out invalid values (None, empty strings, non-strings), removes duplicates,
    and ensures the field is always a list.
    
    Args:
        v: The aliases value to validate
        
    Returns:
        A cleaned list of unique string aliases
    """
    if v is None:
        return []
    if not isinstance(v, list):
        return []
    
    # Filter and clean: keep only valid strings, strip whitespace, remove duplicates
    seen = set()
    result = []
    for a in v:
        if a and isinstance(a, (str, int, float)):
            cleaned = str(a).strip()
            if cleaned and cleaned not in seen:
                seen.add(cleaned)
                result.append(cleaned)
    return result


def add_alias(entity_name: str, current_aliases: List[str], new_alias: str) -> List[str]:
    """Add a single alias to an entity's alias list.
    
    Automatically handles deduplication and normalization. Ignores empty strings
    and aliases that match the entity's primary name.
    
    Args:
        entity_name: The primary name of the entity
        current_aliases: Current list of aliases
        new_alias: The alias to add
        
    Returns:
        Updated list of aliases
    """
    if not new_alias or new_alias == entity_name:
        return current_aliases
    
    normalized = new_alias.strip()
    if normalized and normalized not in current_aliases:
        return [*current_aliases, normalized]
    
    return current_aliases


def merge_aliases(entity_name: str, aliases1: List[str], aliases2: List[str]) -> List[str]:
    """Merge two alias lists.
    
    Automatically handles deduplication by adding each alias from the second list
    to the first list.
    
    Args:
        entity_name: The primary name of the entity
        aliases1: First list of aliases
        aliases2: Second list of aliases to merge
        
    Returns:
        Merged list of aliases without duplicates
    """
    result = list(aliases1)
    for alias in aliases2:
        result = add_alias(entity_name, result, alias)
    return result


def normalize_aliases(entity_name: str, aliases: List[str]) -> List[str]:
    """Normalize an alias list.
    
    Performs the following operations:
    - Removes duplicates (case-insensitive comparison)
    - Sorts alphabetically
    - Removes any aliases that match the primary name
    - Strips whitespace from all entries
    - Preserves the original case of the first occurrence
    
    Args:
        entity_name: The primary name of the entity
        aliases: List of aliases to normalize
        
    Returns:
        Normalized and sorted list of aliases
    """
    # 使用字典来去重，key是小写形式，value是原始形式
    seen_normalized = {}
    entity_name_lower = entity_name.strip().lower()
    
    for alias in aliases:
        if not alias:
            continue
        
        alias_stripped = str(alias).strip()
        if not alias_stripped:
            continue
        
        alias_lower = alias_stripped.lower()
        
        # 跳过与主名称相同的别名（不区分大小写）
        if alias_lower == entity_name_lower:
            continue
        
        # 如果这个别名（小写形式）还没见过，保存它
        if alias_lower not in seen_normalized:
            seen_normalized[alias_lower] = alias_stripped
    
    # 返回排序后的唯一别名列表
    return sorted(seen_normalized.values())



# 错误处理相关常量
MAX_ALIASES = 50  # 别名列表的最大数量限制


def merge_aliases_with_limit(
    entity_name: str,
    aliases1: List[str],
    aliases2: List[str],
    max_aliases: int = MAX_ALIASES
) -> List[str]:
    """合并别名列表并限制数量。
    
    当合并后的别名数量超过限制时，保留最相关的别名（基于长度，通常更短的别名更常用）。
    
    Args:
        entity_name: 实体的主名称
        aliases1: 第一个别名列表
        aliases2: 第二个别名列表
        max_aliases: 最大别名数量限制（默认50）
        
    Returns:
        合并后的别名列表，不超过max_aliases个
    """
    # 合并所有别名
    all_aliases = list(set(aliases1 + aliases2))
    
    # 移除与主名称相同的别名
    all_aliases = [a for a in all_aliases if a != entity_name]
    
    # 如果超过限制，保留最短的别名（通常更常用）
    if len(all_aliases) > max_aliases:
        logger.warning(
            f"Aliases exceed limit ({len(all_aliases)} > {max_aliases}) for entity '{entity_name}', "
            f"truncating to {max_aliases} shortest aliases"
        )
        # 按长度排序，然后按字母顺序排序（确保稳定排序），保留最短的
        all_aliases = sorted(all_aliases, key=lambda x: (len(x), x))[:max_aliases]
    
    # 最后按字母顺序排序返回
    return sorted(all_aliases)


def detect_alias_cycles(entities: List[Any]) -> Dict[str, Set[str]]:
    """检测实体别名中的循环引用。
    
    构建别名图并检测循环：如果实体A的别名指向实体B，实体B的别名又指向实体A。
    
    Args:
        entities: 实体列表，每个实体应有id、name和aliases属性
        
    Returns:
        Dict[str, Set[str]]: 循环组的映射，key为组ID，value为该组中的实体ID集合
    """
    # 构建名称到实体ID的映射（只映射主名称，不包括别名）
    name_to_entity: Dict[str, str] = {}
    entity_by_id: Dict[str, Any] = {}
    
    for entity in entities:
        entity_id = getattr(entity, 'id', None)
        entity_name = getattr(entity, 'name', None)
        
        if not entity_id or not entity_name:
            continue
        
        entity_by_id[entity_id] = entity
        name_to_entity[entity_name.lower().strip()] = entity_id
    
    # 构建实体间的连接图：如果实体A的别名匹配实体B的名称，则A指向B
    connections: Dict[str, Set[str]] = {}
    for entity in entities:
        entity_id = getattr(entity, 'id', None)
        entity_aliases = getattr(entity, 'aliases', []) or []
        
        if not entity_id:
            continue
        
        connections[entity_id] = set()
        
        # 检查别名是否匹配其他实体的名称
        for alias in entity_aliases:
            if not alias:
                continue
            
            normalized_alias = alias.lower().strip()
            if normalized_alias in name_to_entity:
                target_id = name_to_entity[normalized_alias]
                if target_id != entity_id:
                    connections[entity_id].add(target_id)
    
    # 使用DFS检测循环
    visited: Set[str] = set()
    rec_stack: Set[str] = set()
    cycles: Dict[str, Set[str]] = {}
    cycle_id = 0
    
    def dfs(node: str, current_path: List[str]) -> None:
        """深度优先搜索检测循环"""
        nonlocal cycle_id
        
        visited.add(node)
        rec_stack.add(node)
        current_path.append(node)
        
        for neighbor in connections.get(node, set()):
            if neighbor not in visited:
                dfs(neighbor, current_path)
            elif neighbor in rec_stack:
                # 发现循环
                cycle_start_idx = current_path.index(neighbor)
                cycle_nodes = {*current_path[cycle_start_idx:], neighbor}
                
                # 记录循环
                cycle_key = f"cycle_{cycle_id}"
                cycles[cycle_key] = cycle_nodes
                cycle_id += 1
                
                logger.warning(
                    f"Detected alias cycle: {' -> '.join(current_path[cycle_start_idx:])} -> {neighbor}"
                )
        
        current_path.pop()
        rec_stack.remove(node)
    
    # 对所有节点执行DFS
    for entity_id in connections:
        if entity_id not in visited:
            dfs(entity_id, [])
    
    return cycles


def resolve_alias_cycles(entities: List[Any], cycles: Dict[str, Set[str]]) -> List[str]:
    """解决别名循环引用。
    
    对于检测到的循环，选择最强连接的实体作为规范实体，
    将循环中的其他实体合并到规范实体。
    
    Args:
        entities: 实体列表
        cycles: 循环组的映射（由detect_alias_cycles返回）
        
    Returns:
        List[str]: 需要合并的实体ID列表（losing entity IDs）
    """
    entity_by_id: Dict[str, Any] = {
        getattr(e, 'id', None): e for e in entities if getattr(e, 'id', None)
    }
    
    merge_suggestions: List[str] = []
    
    for cycle_key, cycle_entity_ids in cycles.items():
        if len(cycle_entity_ids) < 2:
            continue
        
        # 选择规范实体：优先选择连接强度最高的
        def _strength_rank(entity_id: str) -> int:
            entity = entity_by_id.get(entity_id)
            if not entity:
                return 0
            strength = (getattr(entity, 'connect_strength', '') or '').lower()
            return {'strong': 3, 'both': 2, 'weak': 1}.get(strength, 0)
        
        # 按连接强度排序
        sorted_entities = sorted(
            cycle_entity_ids,
            key=lambda eid: (
                _strength_rank(eid),
                len(getattr(entity_by_id.get(eid), 'description', '') or ''),
                len(getattr(entity_by_id.get(eid), 'fact_summary', '') or '')
            ),
            reverse=True
        )
        
        canonical_id = sorted_entities[0]
        losing_ids = sorted_entities[1:]
        
        logger.info(
            f"Resolving cycle {cycle_key}: canonical={canonical_id}, "
            f"merging={losing_ids}"
        )
        
        merge_suggestions.extend(losing_ids)
    
    return merge_suggestions
