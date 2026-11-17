from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from functools import lru_cache

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.core.config import get_settings


class Base(DeclarativeBase):
    """Declarative base for every ORM model; Alembic reads its metadata."""


@lru_cache
def get_engine() -> AsyncEngine:
    """Singleton: one engine means one connection pool for the whole process."""
    return create_async_engine(get_settings().database_url, pool_pre_ping=True)


class Database:
    """The one way services open a transaction; repositories receive the session."""

    def __init__(self, engine: AsyncEngine) -> None:
        self._sessions = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[AsyncSession]:
        """Commits when the block exits normally, rolls back if it raises."""
        async with self._sessions() as session, session.begin():
            yield session
