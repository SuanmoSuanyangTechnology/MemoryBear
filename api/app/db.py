import os
from contextlib import contextmanager
from typing import Generator
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.ext.declarative import declarative_base
from app.core.config import settings

SQLALCHEMY_DATABASE_URL = f"postgresql://{settings.DB_USER}:{settings.DB_PASSWORD}@{settings.DB_HOST}:{settings.DB_PORT}/{settings.DB_NAME}"

engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    pool_size=settings.DB_POOL_SIZE,
    max_overflow=settings.DB_MAX_OVERFLOW,
    pool_pre_ping=settings.DB_POOL_PRE_PING,
    pool_recycle=settings.DB_POOL_RECYCLE,
    pool_timeout=settings.DB_POOL_TIMEOUT,
    connect_args={
        "options": "-c timezone=Asia/Shanghai -c statement_timeout=60000"
    },
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

# Dependency to get a DB session
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        try:
            if db.in_transaction():
                db.rollback()
        finally:
            db.close()

@contextmanager
def get_db_context() -> Generator[Session, None, None]:
    """
    线程安全、池友好的 Session 上下文。
    不会自动 commit/rollback，调用方自己决定事务边界。
    用法：
        with get_db_context() as db:
            db.add(obj)
            db.commit()          # 或 db.rollback()
    """
    db: Session = SessionLocal()
    try:
        yield db
    finally:
        # 如果还有未提交的事务，直接 rollback 防止 idle in transaction
        if db.in_transaction():
            db.rollback()
        db.close()

@contextmanager
def get_db_read() -> Generator[Session, None, None]:
    """只读场景专用，出上下文自动 rollback，绝不留下 idle in transaction"""
    with get_db_context() as db:
        yield db
        db.rollback()   # 只读任务无需 commit

def get_pool_status():
    """获取连接池状态（用于监控）"""
    pool = engine.pool
    return {
        "pool_size": pool.size(),
        "checked_in": pool.checkedin(),
        "checked_out": pool.checkedout(),
        "overflow": pool.overflow(),
        "total": pool.size() + pool.overflow(),
        "usage_percent": round(pool.checkedout() / (pool.size() + pool.overflow()) * 100, 2) if (pool.size() + pool.overflow()) > 0 else 0
    }