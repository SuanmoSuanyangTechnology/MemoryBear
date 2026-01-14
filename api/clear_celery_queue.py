#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
清空 Celery 队列中的所有消息

这个脚本会删除 Redis 中 Celery 队列的所有待处理任务
"""

import redis
from app.core.config import settings
from app.celery_app import celery_app


def clear_celery_queue():
    """清空 Celery 队列"""
    print("🗑️ 清空 Celery 队列")
    print("=" * 50)
    
    try:
        # 连接到 Redis
        redis_client = redis.Redis(
            host=settings.REDIS_HOST,
            port=settings.REDIS_PORT,
            password=settings.REDIS_PASSWORD,
            db=settings.CELERY_BROKER,
            decode_responses=True
        )
        
        # 测试连接
        redis_client.ping()
        print("✅ Redis 连接成功")
        
        # 队列名称
        queue_name = 'localhost_test_wyl'
        
        # 获取队列长度
        queue_length = redis_client.llen(queue_name)
        print(f"📊 队列 '{queue_name}' 当前长度: {queue_length}")
        
        if queue_length == 0:
            print("✅ 队列已经是空的，无需清理")
            return
        
        # 确认清空
        print(f"\n⚠️ 警告: 即将删除 {queue_length} 个待处理任务")
        confirm = input("确认清空队列? (yes/no): ").strip().lower()
        
        if confirm not in ['yes', 'y']:
            print("❌ 操作已取消")
            return
        
        # 删除队列
        deleted_count = redis_client.delete(queue_name)
        print(f"✅ 已删除队列，删除了 {deleted_count} 个键")
        
        # 验证队列已清空
        new_length = redis_client.llen(queue_name)
        print(f"📊 队列 '{queue_name}' 新长度: {new_length}")
        
        if new_length == 0:
            print("✅ 队列已成功清空!")
        else:
            print(f"⚠️ 队列仍有 {new_length} 个任务")
        
        # 清理结果后端（可选）
        print("\n🧹 清理结果后端...")
        result_keys = redis_client.keys("celery-task-meta-*")
        if result_keys:
            deleted_results = redis_client.delete(*result_keys)
            print(f"✅ 删除了 {deleted_results} 个任务结果")
        else:
            print("✅ 没有待清理的任务结果")
        
    except redis.ConnectionError as e:
        print(f"❌ Redis 连接失败: {e}")
    except Exception as e:
        print(f"❌ 清空队列失败: {e}")
        import traceback
        traceback.print_exc()


def clear_all_celery_data():
    """清空所有 Celery 相关数据（包括结果）"""
    print("\n🗑️ 清空所有 Celery 数据")
    print("=" * 50)
    
    try:
        # 连接到 Redis
        redis_client = redis.Redis(
            host=settings.REDIS_HOST,
            port=settings.REDIS_PORT,
            password=settings.REDIS_PASSWORD,
            db=settings.CELERY_BROKER,
            decode_responses=True
        )
        
        # 获取所有 Celery 相关的键
        all_keys = redis_client.keys("*")
        celery_keys = [k for k in all_keys if 'celery' in k.lower() or 'localhost_test_wyl' in k]
        
        print(f"📊 找到 {len(celery_keys)} 个 Celery 相关的键")
        
        if not celery_keys:
            print("✅ 没有 Celery 数据需要清理")
            return
        
        # 显示键列表
        print("\n📋 Celery 相关的键:")
        for key in celery_keys[:10]:  # 只显示前10个
            print(f"   - {key}")
        if len(celery_keys) > 10:
            print(f"   ... 还有 {len(celery_keys) - 10} 个键")
        
        # 确认清空
        print(f"\n⚠️ 警告: 即将删除 {len(celery_keys)} 个 Celery 相关的键")
        confirm = input("确认清空所有 Celery 数据? (yes/no): ").strip().lower()
        
        if confirm not in ['yes', 'y']:
            print("❌ 操作已取消")
            return
        
        # 删除所有键
        if celery_keys:
            deleted_count = redis_client.delete(*celery_keys)
            print(f"✅ 已删除 {deleted_count} 个键")
        
        print("✅ 所有 Celery 数据已清空!")
        
    except Exception as e:
        print(f"❌ 清空失败: {e}")
        import traceback
        traceback.print_exc()


def show_queue_info():
    """显示队列信息"""
    print("\n📊 队列信息")
    print("=" * 50)
    
    try:
        # 连接到 Redis
        redis_client = redis.Redis(
            host=settings.REDIS_HOST,
            port=settings.REDIS_PORT,
            password=settings.REDIS_PASSWORD,
            db=settings.CELERY_BROKER,
            decode_responses=True
        )
        
        # 队列名称
        queue_name = 'localhost_test_wyl'
        
        # 获取队列信息
        queue_length = redis_client.llen(queue_name)
        print(f"📊 队列 '{queue_name}' 长度: {queue_length}")
        
        # 获取结果数量
        result_keys = redis_client.keys("celery-task-meta-*")
        print(f"📊 任务结果数量: {len(result_keys)}")
        
        # 获取所有 Celery 键
        all_keys = redis_client.keys("*")
        celery_keys = [k for k in all_keys if 'celery' in k.lower() or 'localhost_test_wyl' in k]
        print(f"📊 Celery 相关键总数: {len(celery_keys)}")
        
    except Exception as e:
        print(f"❌ 获取队列信息失败: {e}")


def main():
    """主函数"""
    print("🚀 Celery 队列清理工具")
    print("=" * 50)
    
    while True:
        print("\n请选择操作:")
        print("1. 查看队列信息")
        print("2. 清空队列（只删除待处理任务）")
        print("3. 清空所有 Celery 数据（包括结果）")
        print("4. 退出")
        
        choice = input("\n请输入选项 (1-4): ").strip()
        
        if choice == '1':
            show_queue_info()
        elif choice == '2':
            clear_celery_queue()
        elif choice == '3':
            clear_all_celery_data()
        elif choice == '4':
            print("👋 再见!")
            break
        else:
            print("❌ 无效选项，请重新选择")


if __name__ == "__main__":
    main()