#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
测试 Neo4j 连接
"""
import asyncio
import sys
from app.core.config import settings

async def test_neo4j_basic():
    """测试基本的 Neo4j 连接"""
    print("=" * 60)
    print("测试 Neo4j 基本连接")
    print("=" * 60)
    print(f"URI: {settings.NEO4J_URI}")
    print(f"Username: {settings.NEO4J_USERNAME}")
    print(f"Password: {'*' * len(settings.NEO4J_PASSWORD) if settings.NEO4J_PASSWORD else 'NOT SET'}")
    print()
    
    try:
        from neo4j import AsyncGraphDatabase, basic_auth
        
        driver = AsyncGraphDatabase.driver(
            settings.NEO4J_URI,
            auth=basic_auth(settings.NEO4J_USERNAME, settings.NEO4J_PASSWORD)
        )
        
        print("✓ 驱动初始化成功")
        
        # 测试连接
        async with driver.session(database="neo4j") as session:
            result = await session.run("RETURN 1 as test")
            record = await result.single()
            print(f"✓ 查询测试成功: {record['test']}")
        
        await driver.close()
        print("✓ 连接关闭成功")
        return True
        
    except Exception as e:
        print(f"✗ Neo4j 连接失败: {type(e).__name__}: {str(e)}")
        import traceback
        traceback.print_exc()
        return False

async def test_neo4j_connector():
    """测试 Neo4jConnector 类"""
    print("\n" + "=" * 60)
    print("测试 Neo4jConnector 类")
    print("=" * 60)
    
    try:
        from app.repositories.neo4j.neo4j_connector import Neo4jConnector
        
        connector = Neo4jConnector()
        print("✓ Neo4jConnector 初始化成功")
        
        # 测试查询
        result = await connector.execute_query("RETURN 1 as test")
        print(f"✓ execute_query 测试成功: {result}")
        
        # 测试查询节点数量
        result = await connector.execute_query("MATCH (n) RETURN count(n) as count")
        print(f"✓ 节点数量查询成功: {result[0]['count']} 个节点")
        
        await connector.close()
        print("✓ 连接关闭成功")
        return True
        
    except Exception as e:
        print(f"✗ Neo4jConnector 测试失败: {type(e).__name__}: {str(e)}")
        import traceback
        traceback.print_exc()
        return False

async def test_write_graph():
    """测试写入图的初始化"""
    print("\n" + "=" * 60)
    print("测试写入图初始化")
    print("=" * 60)
    
    try:
        from app.core.memory.agent.langgraph_graph.write_graph import make_write_graph
        
        print("正在初始化写入图...")
        async with make_write_graph() as graph:
            print("✓ 写入图初始化成功")
            print(f"  图类型: {type(graph)}")
        
        print("✓ 写入图关闭成功")
        return True
        
    except Exception as e:
        print(f"✗ 写入图初始化失败: {type(e).__name__}: {str(e)}")
        import traceback
        traceback.print_exc()
        return False

async def test_write_operation():
    """测试完整的写入操作"""
    print("\n" + "=" * 60)
    print("测试完整写入操作")
    print("=" * 60)
    
    try:
        from app.services.memory_agent_service import MemoryAgentService
        from app.db import get_db
        
        service = MemoryAgentService()
        db = next(get_db())
        
        # 使用你的实际参数
        test_group_id = "be586acf-6b90-4c24-9e1e-d31e06cc4ad7"
        test_message = "这是一个测试消息"
        test_config_id = "80"
        
        print(f"Group ID: {test_group_id}")
        print(f"Message: {test_message}")
        print(f"Config ID: {test_config_id}")
        print()
        
        print("开始写入操作...")
        result = await service.write_memory(
            group_id=test_group_id,
            message=test_message,
            config_id=test_config_id,
            db=db,
            storage_type="neo4j",
            user_rag_memory_id=""
        )
        
        print(f"✓ 写入操作成功")
        print(f"  结果: {result}")
        
        db.close()
        return True
        
    except Exception as e:
        print(f"✗ 写入操作失败: {type(e).__name__}: {str(e)}")
        import traceback
        traceback.print_exc()
        
        # 打印更详细的错误信息
        if hasattr(e, '__cause__') and e.__cause__:
            print(f"\n原因: {type(e.__cause__).__name__}: {str(e.__cause__)}")
        
        return False

async def main():
    """主函数"""
    print("\n" + "=" * 60)
    print("Neo4j 连接诊断")
    print("=" * 60)
    
    results = {}
    
    # 1. 测试基本连接
    results["基本连接"] = await test_neo4j_basic()
    
    if not results["基本连接"]:
        print("\n" + "=" * 60)
        print("基本连接失败，停止后续测试")
        print("=" * 60)
        return 1
    
    # 2. 测试 Neo4jConnector
    results["Neo4jConnector"] = await test_neo4j_connector()
    
    # 3. 测试写入图初始化
    results["写入图初始化"] = await test_write_graph()
    
    # 4. 测试完整写入操作
    if all(results.values()):
        results["完整写入操作"] = await test_write_operation()
    
    # 打印结果汇总
    print("\n" + "=" * 60)
    print("测试结果汇总")
    print("=" * 60)
    for name, success in results.items():
        status = "✓ 成功" if success else "✗ 失败"
        print(f"{name}: {status}")
    
    print("\n" + "=" * 60)
    if all(results.values()):
        print("所有测试通过！")
        return 0
    else:
        print("部分测试失败，请检查上面的错误信息")
        return 1

if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
