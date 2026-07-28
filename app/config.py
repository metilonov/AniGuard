from functools import lru_cache
import os
from pathlib import Path
from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    bot_token: str = Field(alias="BOT_TOKEN")
    webapp_url: str = Field(default="https://aniguard.bothost.tech/panel", alias="WEBAPP_URL")
    admin_url: str = Field(default="https://aniguard.bothost.tech/admin", alias="ADMIN_URL")
    data_dir: Path = Field(default=Path("/app/data"), alias="DATA_DIR")
    database_url: str = Field(
        default="sqlite+aiosqlite:////app/data/aniguard.db",
        alias="DATABASE_URL",
    )
    admin_ids: Annotated[set[int], NoDecode] = Field(default_factory=set, alias="ADMIN_IDS")
    recovery_chat_ids: Annotated[set[int], NoDecode] = Field(default_factory=set, alias="RECOVERY_CHAT_IDS")
    dev_mode: bool = Field(default=False, alias="DEV_MODE")
    init_data_max_age: int = Field(default=3600, alias="INIT_DATA_MAX_AGE")
    host: str = Field(default="0.0.0.0", alias="HOST")
    port: int = Field(default=3000, alias="PORT")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    @field_validator("database_url", mode="before")
    @classmethod
    def persistent_sqlite_path(cls, value):
        """Keep SQLite in Bothost's persistent /app/data directory.

        Older AniGuard releases used ./aniguard.db in the application root.
        Bothost rebuilds that directory on every deploy, so transparently map
        the old default to the persistent data directory. Explicit external
        PostgreSQL/MySQL URLs and custom absolute SQLite paths are untouched.
        """
        raw = str(value or "").strip()
        legacy_values = {
            "",
            "sqlite+aiosqlite:///./aniguard.db",
            f"sqlite+aiosqlite:///{BASE_DIR / 'aniguard.db'}",
        }
        if raw in legacy_values:
            data_dir = Path(os.getenv("DATA_DIR", "/app/data"))
            return f"sqlite+aiosqlite:///{data_dir / 'aniguard.db'}"
        return raw

    @field_validator("admin_ids", "recovery_chat_ids", mode="before")
    @classmethod
    def parse_admin_ids(cls, value):
        if value in (None, ""):
            return set()
        if isinstance(value, int):
            return {value}
        if isinstance(value, str):
            cleaned = value.strip()
            if cleaned.startswith("[") and cleaned.endswith("]"):
                cleaned = cleaned[1:-1]
            return {int(item.strip()) for item in cleaned.split(",") if item.strip()}
        if isinstance(value, (list, tuple, set)):
            return {int(item) for item in value}
        raise ValueError("Значение должно содержать один или несколько числовых Telegram ID")

    @field_validator("webapp_url", "admin_url")
    @classmethod
    def strip_public_url(cls, value: str) -> str:
        return value.rstrip("/")


@lru_cache

def get_settings() -> Settings:
    return Settings()
