#!/bin/bash
# Celery Worker 启动脚本

# 设置工作目录
cd "$(dirname "$0")"

# 加载环境变量
if [ -f .env ]; then
    echo "加载 .env 文件..."
    export $(cat .env | grep -v '^#' | xargs)
else
    echo "警告: .env 文件不存在"
fi

# 显示关键配置
echo "Neo4j URI: $NEO4J_URI"
echo "Redis Host: $REDIS_HOST"
echo "DB Host: $DB_HOST"

# 启动 Celery worker
echo "启动 Celery worker..."
celery -A app.celery_app worker --loglevel=info --concurrency=4
