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

    # BotHost limits. Runtime usage is collected from this container's cgroup
    # and served to the browser through AniGuard's own domain/API.
    bothost_ram_limit_mb: int = Field(default=2048, alias="BOTHOST_RAM_LIMIT_MB")
    bothost_cpu_limit: float = Field(default=4.0, alias="BOTHOST_CPU_LIMIT")
    bothost_disk_limit_gb: float = Field(default=15.0, alias="BOTHOST_DISK_LIMIT_GB")
    bothost_project_dir: Path = Field(default=Path("/app"), alias="BOTHOST_PROJECT_DIR")
    bothost_disk_scan_interval_seconds: float = Field(default=60.0, alias="BOTHOST_DISK_SCAN_INTERVAL_SECONDS")
    resource_poll_interval_seconds: float = Field(default=1.0, alias="RESOURCE_POLL_INTERVAL_SECONDS")
    resource_persist_interval_seconds: int = Field(default=10, alias="RESOURCE_PERSIST_INTERVAL_SECONDS")
    resource_history_days: int = Field(default=7, alias="RESOURCE_HISTORY_DAYS")

    # Currency conversion for the owner dashboard. USD/BYN is refreshed from
    # the National Bank of Belarus; the Star-to-USD coefficient stays
    # configurable because Telegram settlement values can change.
    telegram_star_usd_rate: float = Field(default=0.013, alias="TELEGRAM_STAR_USD_RATE")
    nbrb_usd_rate_url: str = Field(
        default="https://api.nbrb.by/exrates/rates/431",
        alias="NBRB_USD_RATE_URL",
    )
    usd_byn_fallback: float = Field(default=3.3, alias="USD_BYN_FALLBACK")
    fx_refresh_seconds: int = Field(default=3600, alias="FX_REFRESH_SECONDS")

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

    @field_validator("bothost_disk_scan_interval_seconds")
    @classmethod
    def minimum_disk_scan_interval(cls, value: float) -> float:
        return max(10.0, float(value))

    @field_validator("resource_poll_interval_seconds")
    @classmethod
    def minimum_resource_interval(cls, value: float) -> float:
        # One second is the highest supported refresh rate for this project.
        return max(1.0, float(value))

    @field_validator("fx_refresh_seconds")
    @classmethod
    def minimum_fx_refresh(cls, value: int) -> int:
        return max(300, int(value))

    @field_validator("resource_persist_interval_seconds")
    @classmethod
    def minimum_persist_interval(cls, value: int) -> int:
        return max(5, int(value))

    @field_validator("webapp_url", "admin_url")
    @classmethod
    def strip_public_url(cls, value: str) -> str:
        return value.rstrip("/")


@lru_cache

def get_settings() -> Settings:
    return Settings()
