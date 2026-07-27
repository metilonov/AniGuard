from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import get_settings
from app.models import Base


settings = get_settings()
engine = create_async_engine(settings.database_url, pool_pre_ping=True)
SessionFactory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


_CAPTCHA_COLUMNS: dict[str, str] = {
    "answer": "VARCHAR(32)",
    "options": "JSON",
    "image_key": "VARCHAR(32)",
    "attempts_left": "INTEGER",
    "failure_action": "VARCHAR(32)",
    "resolved_at": "TIMESTAMP",
}


def _missing_captcha_columns(sync_connection) -> list[tuple[str, str]]:
    inspector = inspect(sync_connection)
    if "captcha_challenges" not in inspector.get_table_names():
        return []
    existing = {column["name"] for column in inspector.get_columns("captcha_challenges")}
    return [(name, sql_type) for name, sql_type in _CAPTCHA_COLUMNS.items() if name not in existing]


async def init_db() -> None:
    """Create tables and apply small additive migrations.

    The project intentionally avoids a heavyweight migration dependency.  All
    runtime migrations are additive, so an existing SQLite/PostgreSQL database
    can be upgraded without deleting data.
    """
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
        missing = await connection.run_sync(_missing_captcha_columns)
        for name, sql_type in missing:
            await connection.execute(text(f"ALTER TABLE captcha_challenges ADD COLUMN {name} {sql_type}"))


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionFactory() as session:
        yield session
