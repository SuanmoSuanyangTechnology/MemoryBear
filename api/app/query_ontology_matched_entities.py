# -*- coding: utf-8 -*-
"""
api\scripts\query_ontology_matched_entities.py

根据 end_user_id 查询 Neo4j 中的 ExtractedEntity 节点，
并筛选出 entity_type 与以下类型匹配的实体：
1. 场景本体类型（ontology_class 表）
2. 通用本体类型（General_purpose_entity.ttl 等文件）

用法: python scripts/query_ontology_matched_entities.py <end_user_id> [config_id]
示例: python scripts/query_ontology_matched_entities.py 075660cf-08e6-40a6-a76e-308b6f52fbf1
      python scripts/query_ontology_matched_entities.py 075660cf-08e6-40a6-a76e-308b6f52fbf1 fd547bb9-7b9e-47ea-ae53-242d208a31a2
"""

import sys
import os
import asyncio
from uuid import UUID
from typing import List, Dict, Any, Set, Optional
from collections import defaultdict

# 添加项目根目录到路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from app.db import SessionLocal
from app.repositories.neo4j.neo4j_connector import Neo4jConnector
from app.repositories.ontology_class_repository import OntologyClassRepository
from app.repositories.ontology_scene_repository import OntologySceneRepository
from app.repositories.memory_config_repository import MemoryConfigRepository
from app.core.memory.ontology_services.ontology_type_loader import (
    get_general_ontology_registry,
    is_general_ontology_enabled,
)


async def get_entities_by_end_user_id(connector: Neo4jConnector, end_user_id: str) -> List[Dict[str, Any]]:
    """从 Neo4j 查询指定 end_user_id 的所有实体"""
    
    query = """
    MATCH (n:ExtractedEntity)
    WHERE n.end_user_id = $end_user_id
    RETURN 
        n.id AS id,
        n.name AS name,
        n.entity_type AS entity_type,
        n.description AS description,
        n.end_user_id AS end_user_id,
        n.created_at AS created_at
    ORDER BY n.created_at DESC
    """
    
    results = await connector.execute_query(query, end_user_id=end_user_id)
    return results


def get_ontology_types_from_scene(db, scene_id: UUID) -> Set[str]:
    """获取场景下所有本体类型名称"""
    class_repo = OntologyClassRepository(db)
    ontology_classes = class_repo.get_by_scene(scene_id)
    return {oc.class_name for oc in ontology_classes}


def get_ontology_types_from_config(db, config_id: UUID) -> Optional[Set[str]]:
    """从记忆配置获取关联的本体类型"""
    memory_config = MemoryConfigRepository.get_by_id(db, config_id)
    if not memory_config or not memory_config.scene_id:
        return None
    return get_ontology_types_from_scene(db, memory_config.scene_id)


def get_all_ontology_types(db) -> Dict[str, Set[str]]:
    """获取所有工作空间的本体类型"""
    from app.models.ontology_scene import OntologyScene
    
    scenes = db.query(OntologyScene).all()
    all_types = {}
    
    for scene in scenes:
        class_repo = OntologyClassRepository(db)
        ontology_classes = class_repo.get_by_scene(scene.scene_id)
        for oc in ontology_classes:
            if oc.class_name not in all_types:
                all_types[oc.class_name] = set()
            all_types[oc.class_name].add(scene.scene_name)
    
    return all_types


def get_general_ontology_types() -> Set[str]:
    """获取通用本体类型名称集合"""
    if not is_general_ontology_enabled():
        return set()
    
    try:
        registry = get_general_ontology_registry()
        return set(registry.types.keys())
    except Exception as e:
        print(f"⚠️  加载通用本体类型失败: {e}")
        return set()


async def query_ontology_matched_entities(end_user_id: str, config_id: Optional[str] = None):
    """查询与本体类型匹配的实体"""
    
    print(f"\n{'='*70}")
    print(f"查询 Neo4j 中与本体类型匹配的实体")
    print(f"{'='*70}")
    print(f"end_user_id: {end_user_id}")
    
    db = SessionLocal()
    connector = Neo4jConnector()
    
    try:
        # 1. 获取场景本体类型集合
        scene_ontology_types: Set[str] = set()
        scene_name = "所有场景"
        
        if config_id:
            try:
                config_uuid = UUID(config_id)
                types = get_ontology_types_from_config(db, config_uuid)
                if types:
                    scene_ontology_types = types
                    memory_config = MemoryConfigRepository.get_by_id(db, config_uuid)
                    if memory_config and memory_config.scene_id:
                        scene_repo = OntologySceneRepository(db)
                        scene = scene_repo.get_by_id(memory_config.scene_id)
                        if scene:
                            scene_name = scene.scene_name
                    print(f"config_id: {config_id}")
                    print(f"关联场景: {scene_name}")
            except ValueError:
                print(f"⚠️  无效的 config_id 格式: {config_id}")
        
        # 如果没有指定 config_id 或获取失败，获取所有场景本体类型
        if not scene_ontology_types:
            all_types = get_all_ontology_types(db)
            scene_ontology_types = set(all_types.keys())
            print(f"使用所有场景本体类型进行匹配")
        
        # 2. 获取通用本体类型
        general_ontology_types = get_general_ontology_types()
        
        print(f"\n📋 场景本体类型 (共 {len(scene_ontology_types)} 个):")
        print(f"   {'-'*50}")
        for i, type_name in enumerate(sorted(scene_ontology_types)[:20], 1):
            print(f"   {i:2}. {type_name}")
        if len(scene_ontology_types) > 20:
            print(f"   ... 还有 {len(scene_ontology_types) - 20} 个")
        
        print(f"\n📋 通用本体类型 (共 {len(general_ontology_types)} 个):")
        print(f"   {'-'*50}")
        sample_general_types = sorted(general_ontology_types)[:20]
        for i, type_name in enumerate(sample_general_types, 1):
            print(f"   {i:2}. {type_name}")
        if len(general_ontology_types) > 20:
            print(f"   ... 还有 {len(general_ontology_types) - 20} 个")
        
        # 3. 从 Neo4j 查询实体
        print(f"\n🔍 正在查询 Neo4j...")
        entities = await get_entities_by_end_user_id(connector, end_user_id)
        
        if not entities:
            print(f"\n⚠️  未找到 end_user_id={end_user_id} 的任何实体")
            return
        
        print(f"   找到 {len(entities)} 个实体")
        
        # 4. 分类实体（场景类型、通用类型、未匹配）
        scene_matched_entities = []
        general_matched_entities = []
        both_matched_entities = []  # 同时匹配场景和通用类型
        unmatched_entities = []
        
        scene_type_distribution = defaultdict(list)
        general_type_distribution = defaultdict(list)
        
        for entity in entities:
            entity_type = entity.get('entity_type', '')
            in_scene = entity_type in scene_ontology_types
            in_general = entity_type in general_ontology_types
            
            if in_scene and in_general:
                both_matched_entities.append(entity)
                scene_type_distribution[entity_type].append(entity)
                general_type_distribution[entity_type].append(entity)
            elif in_scene:
                scene_matched_entities.append(entity)
                scene_type_distribution[entity_type].append(entity)
            elif in_general:
                general_matched_entities.append(entity)
                general_type_distribution[entity_type].append(entity)
            else:
                unmatched_entities.append(entity)
        
        # 5. 输出匹配场景类型的实体
        total_scene_matched = len(scene_matched_entities) + len(both_matched_entities)
        print(f"\n{'='*70}")
        print(f"✅ 匹配场景本体类型的实体 (共 {total_scene_matched} 个)")
        print(f"{'='*70}")
        
        if scene_type_distribution:
            for type_name in sorted(scene_type_distribution.keys()):
                entities_of_type = scene_type_distribution[type_name]
                print(f"\n📌 类型: {type_name} ({len(entities_of_type)} 个)")
                print(f"   {'-'*50}")
                for entity in entities_of_type[:3]:
                    name = entity.get('name', 'N/A')
                    desc = entity.get('description', '')
                    desc_preview = (desc[:50] + "...") if desc and len(desc) > 50 else (desc or "无描述")
                    print(f"   • {name}")
                    print(f"     描述: {desc_preview}")
                if len(entities_of_type) > 3:
                    print(f"   ... 还有 {len(entities_of_type) - 3} 个")
        else:
            print(f"\n   (无匹配场景类型的实体)")
        
        # 6. 输出匹配通用类型的实体
        total_general_matched = len(general_matched_entities) + len(both_matched_entities)
        print(f"\n{'='*70}")
        print(f"✅ 匹配通用本体类型的实体 (共 {total_general_matched} 个)")
        print(f"{'='*70}")
        
        if general_type_distribution:
            for type_name in sorted(general_type_distribution.keys()):
                entities_of_type = general_type_distribution[type_name]
                print(f"\n📌 类型: {type_name} ({len(entities_of_type)} 个)")
                print(f"   {'-'*50}")
                for entity in entities_of_type[:3]:
                    name = entity.get('name', 'N/A')
                    desc = entity.get('description', '')
                    desc_preview = (desc[:50] + "...") if desc and len(desc) > 50 else (desc or "无描述")
                    print(f"   • {name}")
                    print(f"     描述: {desc_preview}")
                if len(entities_of_type) > 3:
                    print(f"   ... 还有 {len(entities_of_type) - 3} 个")
        else:
            print(f"\n   (无匹配通用类型的实体)")
        
        # 7. 输出未匹配的实体
        print(f"\n{'='*70}")
        print(f"❌ 未匹配任何本体类型的实体 (共 {len(unmatched_entities)} 个)")
        print(f"{'='*70}")
        
        if unmatched_entities:
            unmatched_by_type = defaultdict(list)
            for entity in unmatched_entities:
                entity_type = entity.get('entity_type', 'Unknown')
                unmatched_by_type[entity_type].append(entity)
            
            for type_name in sorted(unmatched_by_type.keys()):
                entities_of_type = unmatched_by_type[type_name]
                print(f"\n📌 类型: {type_name} ({len(entities_of_type)} 个)")
                print(f"   {'-'*50}")
                for entity in entities_of_type[:3]:
                    name = entity.get('name', 'N/A')
                    print(f"   • {name}")
                if len(entities_of_type) > 3:
                    print(f"   ... 还有 {len(entities_of_type) - 3} 个")
        else:
            print(f"\n   (所有实体都匹配本体类型)")
        
        # 8. 统计摘要
        total_entities = len(entities)
        any_matched = total_entities - len(unmatched_entities)
        
        print(f"\n{'='*70}")
        print(f"📊 统计摘要")
        print(f"{'='*70}")
        print(f"\n   基础统计:")
        print(f"   {'-'*50}")
        print(f"   总实体数: {total_entities}")
        print(f"   场景本体类型数: {len(scene_ontology_types)}")
        print(f"   通用本体类型数: {len(general_ontology_types)}")
        
        print(f"\n   匹配率统计:")
        print(f"   {'-'*50}")
        scene_rate = total_scene_matched / total_entities * 100 if total_entities > 0 else 0
        general_rate = total_general_matched / total_entities * 100 if total_entities > 0 else 0
        any_rate = any_matched / total_entities * 100 if total_entities > 0 else 0
        unmatched_rate = len(unmatched_entities) / total_entities * 100 if total_entities > 0 else 0
        
        print(f"   匹配场景类型: {total_scene_matched} 个 ({scene_rate:.1f}%)")
        print(f"   匹配通用类型: {total_general_matched} 个 ({general_rate:.1f}%)")
        print(f"   同时匹配两者: {len(both_matched_entities)} 个 ({len(both_matched_entities)/total_entities*100:.1f}%)")
        print(f"   仅匹配场景类型: {len(scene_matched_entities)} 个 ({len(scene_matched_entities)/total_entities*100:.1f}%)")
        print(f"   仅匹配通用类型: {len(general_matched_entities)} 个 ({len(general_matched_entities)/total_entities*100:.1f}%)")
        print(f"   匹配任一类型: {any_matched} 个 ({any_rate:.1f}%)")
        print(f"   未匹配任何类型: {len(unmatched_entities)} 个 ({unmatched_rate:.1f}%)")
        
        # 9. 类型分布详情
        if scene_type_distribution:
            print(f"\n   场景类型分布 (Top 10):")
            print(f"   {'-'*50}")
            sorted_scene_types = sorted(scene_type_distribution.items(), key=lambda x: len(x[1]), reverse=True)
            for type_name, entities_list in sorted_scene_types[:10]:
                print(f"   - {type_name}: {len(entities_list)} 个")
        
        if general_type_distribution:
            print(f"\n   通用类型分布 (Top 10):")
            print(f"   {'-'*50}")
            sorted_general_types = sorted(general_type_distribution.items(), key=lambda x: len(x[1]), reverse=True)
            for type_name, entities_list in sorted_general_types[:10]:
                print(f"   - {type_name}: {len(entities_list)} 个")
        
    except Exception as e:
        print(f"\n❌ 查询出错: {str(e)}")
        import traceback
        traceback.print_exc()
    finally:
        db.close()
        await connector.close()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python scripts/query_ontology_matched_entities.py <end_user_id> [config_id]")
        print("示例: python scripts/query_ontology_matched_entities.py 075660cf-08e6-40a6-a76e-308b6f52fbf1")
        print("      python scripts/query_ontology_matched_entities.py 075660cf-08e6-40a6-a76e-308b6f52fbf1 fd547bb9-7b9e-47ea-ae53-242d208a31a2")
        sys.exit(1)
    
    end_user_id = sys.argv[1]
    config_id = sys.argv[2] if len(sys.argv) > 2 else None
    
    asyncio.run(query_ontology_matched_entities(end_user_id, config_id))
