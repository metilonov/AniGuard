from __future__ import annotations

import shutil
from collections.abc import AsyncIterator
from pathlib import Path
from urllib.parse import unquote

from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import BASE_DIR, get_settings
from app.models import Base


settings = get_settings()


def _sqlite_path(database_url: str) -> Path | None:
    prefixes = ("sqlite+aiosqlite:///", "sqlite:///")
    for prefix in prefixes:
        if database_url.startswith(prefix):
            raw = unquote(database_url.removeprefix(prefix))
            if raw == ":memory:":
                return None
            path = Path(raw)
            return path if path.is_absolute() else (BASE_DIR / path).resolve()
    return None


def _prepare_sqlite_storage() -> None:
    """Create the persistent directory and migrate the legacy root database.

    Bothost preserves /app/data between deploys. Older AniGuard builds kept
    aniguard.db in /app, which is rebuilt from Git and therefore disappeared.
    The first fixed start copies that legacy database when it is still present.
    """
    target = _sqlite_path(settings.database_url)
    if target is None:
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    legacy_candidates = [
        BASE_DIR / "aniguard.db",
        Path("/app/aniguard.db"),
    ]
    if target.exists():
        return
    for legacy in legacy_candidates:
        try:
            if legacy.resolve() == target.resolve():
                continue
            if legacy.is_file():
                shutil.copy2(legacy, target)
                break
        except OSError:
            continue


_prepare_sqlite_storage()
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

_CHAT_COLUMNS: dict[str, str] = {
    "photo_small_file_id": "VARCHAR(255)",
    "photo_big_file_id": "VARCHAR(255)",
    "photo_unique_id": "VARCHAR(255)",
}


def _missing_columns(sync_connection, table_name: str, expected: dict[str, str]) -> list[tuple[str, str]]:
    inspector = inspect(sync_connection)
    if table_name not in inspector.get_table_names():
        return []
    existing = {column["name"] for column in inspector.get_columns(table_name)}
    return [(name, sql_type) for name, sql_type in expected.items() if name not in existing]


async def init_db() -> None:
    """Create tables and apply small additive runtime migrations."""
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
        captcha_missing = await connection.run_sync(
            lambda sync_connection: _missing_columns(sync_connection, "captcha_challenges", _CAPTCHA_COLUMNS)
        )
        chat_missing = await connection.run_sync(
            lambda sync_connection: _missing_columns(sync_connection, "chats", _CHAT_COLUMNS)
        )
        for name, sql_type in [*captcha_missing, *chat_missing]:
            table = "captcha_challenges" if name in _CAPTCHA_COLUMNS else "chats"
            await connection.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {sql_type}"))


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionFactory() as session:
        yield session
