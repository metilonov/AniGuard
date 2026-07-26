from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    bot_token: str = Field(alias="BOT_TOKEN")
    webapp_url: str = Field(default="https://example.com", alias="WEBAPP_URL")
    database_url: str = Field(
        default=f"sqlite+aiosqlite:///{BASE_DIR / 'aniguard.db'}",
        alias="DATABASE_URL",
    )
    admin_ids: set[int] = Field(default_factory=set, alias="ADMIN_IDS")
    dev_mode: bool = Field(default=False, alias="DEV_MODE")
    init_data_max_age: int = Field(default=3600, alias="INIT_DATA_MAX_AGE")
    host: str = Field(default="0.0.0.0", alias="HOST")
    port: int = Field(default=8000, alias="PORT")
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
        if isinstance(value, str):
            return {int(item.strip()) for item in value.split(",") if item.strip()}
        return set(value)

    @field_validator("webapp_url")
    @classmethod
    def strip_webapp_url(cls, value: str) -> str:
        return value.rstrip("/")


@lru_cache

def get_settings() -> Settings:
    return Settings()
