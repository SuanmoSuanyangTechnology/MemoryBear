"""
交互式 Neo4j End User 数据检查工具

用于查询指定 end_user_id 在 Neo4j 中是否存在数据，以及数据的详细统计信息。

使用方法:
    python check_group_data.py
    python check_group_data.py --group-id locomo_benchmark
    python check_group_data.py --group-id memsciqa_benchmark --detailed
"""

import asyncio
import argparse
import os
from pathlib import Path
from typing import Dict, Any
from dotenv import load_dotenv

# Load evaluation config
eval_config_path = Path(__file__).resolve().parent / ".env.evaluation"
if eval_config_path.exists():
    load_dotenv(eval_config_path, override=True)
    print(f"✅ 加载评估配置: {eval_config_path}\n")

from app.repositories.neo4j.neo4j_connector import Neo4jConnector


async def check_group_exists(end_user_id: str) -> Dict[str, Any]:
    """
    检查指定 end_user_id 是否存在数据
    
    Args:
        end_user_id: 要检查的 end_user ID
        
    Returns:
        包含统计信息的字典
    """
    connector = Neo4jConnector()
    
    try:
        # 查询该 end_user 的节点总数
        query_total = """
        MATCH (n {end_user_id: $end_user_id})
        RETURN count(n) as total_nodes
        """
        result_total = await connector.execute_query(query_total, end_user_id=end_user_id)
        total_nodes = result_total[0]["total_nodes"] if result_total else 0
        
        # 查询各类型节点的数量
        query_by_type = """
        MATCH (n {end_user_id: $end_user_id})
        RETURN labels(n) as labels, count(n) as count
        ORDER BY count DESC
        """
        result_by_type = await connector.execute_query(query_by_type, end_user_id=end_user_id)
        
        # 查询关系数量
        query_relationships = """
        MATCH (n {end_user_id: $end_user_id})-[r]-()
        RETURN count(DISTINCT r) as total_relationships
        """
        result_rel = await connector.execute_query(query_relationships, end_user_id=end_user_id)
        total_relationships = result_rel[0]["total_relationships"] if result_rel else 0
        
        return {
            "exists": total_nodes > 0,
            "total_nodes": total_nodes,
            "total_relationships": total_relationships,
            "nodes_by_type": result_by_type
        }
    
    finally:
        await connector.close()


async def get_detailed_stats(end_user_id: str) -> Dict[str, Any]:
    """
    获取详细的统计信息
    
    Args:
        end_user_id: 要检查的 end_user ID
        
    Returns:
        详细统计信息字典
    """
    connector = Neo4jConnector()
    
    try:
        stats = {}
        
        # Chunk 节点统计
        query_chunks = """
        MATCH (c:Chunk {end_user_id: $end_user_id})
        RETURN count(c) as count, 
               avg(size(c.content)) as avg_content_length
        """
        result_chunks = await connector.execute_query(query_chunks, end_user_id=end_user_id)
        if result_chunks and result_chunks[0]["count"] > 0:
            stats["chunks"] = {
                "count": result_chunks[0]["count"],
                "avg_content_length": int(result_chunks[0]["avg_content_length"]) if result_chunks[0]["avg_content_length"] else 0
            }
        
        # Statement 节点统计
        query_statements = """
        MATCH (s:Statement {end_user_id: $end_user_id})
        RETURN count(s) as count
        """
        result_statements = await connector.execute_query(query_statements, end_user_id=end_user_id)
        if result_statements and result_statements[0]["count"] > 0:
            stats["statements"] = {
                "count": result_statements[0]["count"]
            }
        
        # Entity 节点统计
        query_entities = """
        MATCH (e:Entity {end_user_id: $end_user_id})
        RETURN count(e) as count, 
               count(DISTINCT e.entity_type) as unique_types
        """
        result_entities = await connector.execute_query(query_entities, end_user_id=end_user_id)
        if result_entities and result_entities[0]["count"] > 0:
            stats["entities"] = {
                "count": result_entities[0]["count"],
                "unique_types": result_entities[0]["unique_types"]
            }
        
        # Dialogue 节点统计
        query_dialogues = """
        MATCH (d:Dialogue {end_user_id: $end_user_id})
        RETURN count(d) as count
        """
        result_dialogues = await connector.execute_query(query_dialogues, end_user_id=end_user_id)
        if result_dialogues and result_dialogues[0]["count"] > 0:
            stats["dialogues"] = {
                "count": result_dialogues[0]["count"]
            }
        
        # Summary 节点统计
        query_summaries = """
        MATCH (s:Summary {end_user_id: $end_user_id})
        RETURN count(s) as count
        """
        result_summaries = await connector.execute_query(query_summaries, end_user_id=end_user_id)
        if result_summaries and result_summaries[0]["count"] > 0:
            stats["summaries"] = {
                "count": result_summaries[0]["count"]
            }
        
        return stats
    
    finally:
        await connector.close()


async def list_all_end_users() -> list:
    """
    列出数据库中所有的 end_user_id
    
    Returns:
        end_user_id 列表及其节点数量
    """
    connector = Neo4jConnector()
    
    try:
        query = """
        MATCH (n)
        WHERE n.end_user_id IS NOT NULL
        RETURN DISTINCT n.end_user_id as end_user_id, count(n) as node_count
        ORDER BY node_count DESC
        """
        results = await connector.execute_query(query)
        return results
    
    finally:
        await connector.close()


def print_results(end_user_id: str, stats: Dict[str, Any], detailed_stats: Dict[str, Any] = None):
    """
    打印查询结果
    
    Args:
        end_user_id: End User ID
        stats: 基本统计信息
        detailed_stats: 详细统计信息（可选）
    """
    print(f"\n{'='*60}")
    print(f"📊 End User ID: {end_user_id}")
    print(f"{'='*60}\n")
    
    if not stats["exists"]:
        print("❌ 该 end_user_id 不存在数据")
        print("\n💡 提示: 请先运行基准测试以摄入数据")
        return
    
    print(f"✅ 该 end_user_id 存在数据\n")
    print(f"📈 基本统计:")
    print(f"   总节点数: {stats['total_nodes']}")
    print(f"   总关系数: {stats['total_relationships']}")
    
    if stats["nodes_by_type"]:
        print(f"\n📋 节点类型分布:")
        for item in stats["nodes_by_type"]:
            labels = ", ".join(item["labels"])
            count = item["count"]
            print(f"   {labels}: {count}")
    
    if detailed_stats:
        print(f"\n🔍 详细统计:")
        
        if "chunks" in detailed_stats:
            print(f"   Chunks: {detailed_stats['chunks']['count']} 个")
            print(f"     平均内容长度: {detailed_stats['chunks']['avg_content_length']} 字符")
        
        if "statements" in detailed_stats:
            print(f"   Statements: {detailed_stats['statements']['count']} 个")
        
        if "entities" in detailed_stats:
            print(f"   Entities: {detailed_stats['entities']['count']} 个")
            print(f"     唯一类型数: {detailed_stats['entities']['unique_types']}")
        
        if "dialogues" in detailed_stats:
            print(f"   Dialogues: {detailed_stats['dialogues']['count']} 个")
        
        if "summaries" in detailed_stats:
            print(f"   Summaries: {detailed_stats['summaries']['count']} 个")
    
    print(f"\n{'='*60}\n")


async def interactive_mode():
    """
    交互式模式
    """
    print("\n" + "="*60)
    print("🔍 Neo4j End User 数据检查工具 - 交互模式")
    print("="*60 + "\n")
    
    while True:
        print("\n请选择操作:")
        print("  1. 检查指定 end_user_id")
        print("  2. 列出所有 end_user_id")
        print("  3. 退出")
        
        choice = input("\n请输入选项 (1-3): ").strip()
        
        if choice == "1":
            end_user_id = input("\n请输入 end_user_id: ").strip()
            if not end_user_id:
                print("❌ end_user_id 不能为空")
                continue
            
            detailed = input("是否显示详细统计? (y/n, 默认 n): ").strip().lower() == 'y'
            
            print("\n🔄 正在查询...")
            stats = await check_group_exists(end_user_id)
            
            detailed_stats = None
            if detailed and stats["exists"]:
                detailed_stats = await get_detailed_stats(end_user_id)
            
            print_results(end_user_id, stats, detailed_stats)
        
        elif choice == "2":
            print("\n🔄 正在查询所有 end_user_id...")
            end_users = await list_all_end_users()
            
            if not end_users:
                print("\n❌ 数据库中没有任何 end_user 数据")
            else:
                print(f"\n{'='*60}")
                print(f"📋 数据库中的所有 End User ID")
                print(f"{'='*60}\n")
                
                for idx, end_user in enumerate(end_users, 1):
                    print(f"  {idx}. {end_user['end_user_id']}")
                    print(f"     节点数: {end_user['node_count']}")
                
                print(f"\n{'='*60}\n")
        
        elif choice == "3":
            print("\n👋 再见！")
            break
        
        else:
            print("\n❌ 无效的选项，请重新选择")


async def main():
    """
    主函数
    """
    parser = argparse.ArgumentParser(
        description="检查 Neo4j 中指定 end_user_id 的数据情况",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 交互模式
  python check_group_data.py
  
  # 检查指定 end_user
  python check_group_data.py --end-user-id locomo_benchmark
  
  # 检查并显示详细统计
  python check_group_data.py --end-user-id memsciqa_benchmark --detailed
  
  # 列出所有 end_user
  python check_group_data.py --list-all
        """
    )
    
    parser.add_argument(
        "--end-user-id",
        type=str,
        help="要检查的 end_user ID"
    )
    
    parser.add_argument(
        "--detailed",
        action="store_true",
        help="显示详细统计信息"
    )
    
    parser.add_argument(
        "--list-all",
        action="store_true",
        help="列出所有 end_user_id"
    )
    
    args = parser.parse_args()
    
    # 如果没有提供任何参数，进入交互模式
    if not args.end_user_id and not args.list_all:
        await interactive_mode()
        return
    
    # 列出所有 end_user
    if args.list_all:
        print("\n🔄 正在查询所有 end_user_id...")
        end_users = await list_all_end_users()
        
        if not end_users:
            print("\n❌ 数据库中没有任何 end_user 数据")
        else:
            print(f"\n{'='*60}")
            print(f"📋 数据库中的所有 End User ID")
            print(f"{'='*60}\n")
            
            for idx, end_user in enumerate(end_users, 1):
                print(f"  {idx}. {end_user['end_user_id']}")
                print(f"     节点数: {end_user['node_count']}")
            
            print(f"\n{'='*60}\n")
        return
    
    # 检查指定 end_user
    if args.end_user_id:
        print(f"\n🔄 正在查询 end_user_id: {args.end_user_id}...")
        stats = await check_group_exists(args.end_user_id)
        
        detailed_stats = None
        if args.detailed and stats["exists"]:
            print("🔄 正在获取详细统计...")
            detailed_stats = await get_detailed_stats(args.end_user_id)
        
        print_results(args.end_user_id, stats, detailed_stats)


if __name__ == "__main__":
    asyncio.run(main())
