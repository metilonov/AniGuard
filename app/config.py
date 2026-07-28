from functools import lru_cache
from pathlib import Path
from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    bot_token: str = Field(alias="BOT_TOKEN")
    webapp_url: str = Field(default="https://aniguard.bothost.tech/panel", alias="WEBAPP_URL")
    admin_url: str = Field(default="https://aniguard.bothost.tech/admin", alias="ADMIN_URL")
    database_url: str = Field(
        default=f"sqlite+aiosqlite:///{BASE_DIR / 'aniguard.db'}",
        alias="DATABASE_URL",
    )
    admin_ids: Annotated[set[int], NoDecode] = Field(default_factory=set, alias="ADMIN_IDS")
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

    @field_validator("admin_ids", mode="before")
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
        raise ValueError("ADMIN_IDS должен содержать один или несколько Telegram ID")

    @field_validator("webapp_url", "admin_url")
    @classmethod
    def strip_public_url(cls, value: str) -> str:
        return value.rstrip("/")


@lru_cache

def get_settings() -> Settings:
    return Settings()
