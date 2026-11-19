from __future__ import annotations

from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from .config import settings

engine = create_async_engine(settings.database_url_async, pool_pre_ping=True)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def get_async_db():
    async with SessionLocal() as session:
        yield session


@asynccontextmanager
async def get_async_db_context():
    async with SessionLocal() as session:
        yield session
