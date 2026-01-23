#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
测试 Celery Worker 环境
"""
from app.celery_app import celery_app

@celery_app.task(name="test.check_environment")
def check_environment():
    """检查 Celery worker 的环境配置"""
    import os
    from app.core.config import settings
    
    result = {
        "neo4j_uri": settings.NEO4J_URI,
        "neo4j_username": settings.NEO4J_USERNAME,
        "neo4j_password_set": bool(settings.NEO4J_PASSWORD),
        "redis_host": settings.REDIS_HOST,
        "redis_port": settings.REDIS_PORT,
        "db_host": settings.DB_HOST,
        "db_name": settings.DB_NAME,
        "python_path": os.environ.get("PYTHONPATH", "Not set"),
        "working_dir": os.getcwd(),
    }
    
    # 测试 Neo4j 连接
    try:
        import asyncio
        from app.repositories.neo4j.neo4j_connector import Neo4jConnector
        
        async def test_neo4j():
            connector = Neo4jConnector()
            result = await connector.execute_query("RETURN 1 as test")
            await connector.close()
            return result
        
        neo4j_result = asyncio.run(test_neo4j())
        result["neo4j_connection"] = "SUCCESS"
        result["neo4j_test_result"] = neo4j_result
    except Exception as e:
        result["neo4j_connection"] = "FAILED"
        result["neo4j_error"] = str(e)
    
    return result

if __name__ == "__main__":
    # 提交任务
    print("提交环境检查任务...")
    task = check_environment.delay()
    print(f"任务ID: {task.id}")
    print("等待结果...")
    
    # 等待结果
    result = task.get(timeout=30)
    
    print("\n" + "=" * 60)
    print("Celery Worker 环境信息")
    print("=" * 60)
    for key, value in result.items():
        print(f"{key}: {value}")
    print("=" * 60)
