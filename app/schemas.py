from typing import Any, Literal

from pydantic import BaseModel, Field


class SettingsUpdate(BaseModel):
    settings: dict[str, Any]


class ActionRequest(BaseModel):
    action: Literal[
        "warn", "unwarn", "mute", "unmute", "ban", "unban", "purge",
        "slow", "lock", "unlock", "quarantine", "susanoo", "case"
    ]
    target_id: int | None = None
    duration_seconds: int | None = Field(default=None, ge=0, le=31_536_000)
    amount: int | None = Field(default=None, ge=1, le=1000)
    reason: str = Field(default="", max_length=500)


class RPCommandCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    aliases: list[str] = Field(default_factory=list, max_length=20)
    response_template: str = Field(min_length=1, max_length=1000)
    response_variants: list[str] = Field(default_factory=list, max_length=10)
    enabled: bool = True
    is_premium: bool = False
    cooldown_seconds: int = Field(default=5, ge=0, le=86_400)
    access: Literal["all", "verified", "moderators", "admins"] = "all"
    reward_xp: int = Field(default=0, ge=0, le=10_000)
    reward_coins: int = Field(default=0, ge=0, le=10_000)


class RPCommandUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    aliases: list[str] | None = None
    response_template: str | None = Field(default=None, min_length=1, max_length=1000)
    response_variants: list[str] | None = None
    enabled: bool | None = None
    is_premium: bool | None = None
    cooldown_seconds: int | None = Field(default=None, ge=0, le=86_400)
    access: Literal["all", "verified", "moderators", "admins"] | None = None
    reward_xp: int | None = Field(default=None, ge=0, le=10_000)
    reward_coins: int | None = Field(default=None, ge=0, le=10_000)


class RuleCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    condition: dict[str, Any]
    actions: list[dict[str, Any]]
    enabled: bool = True
    is_premium: bool = False


class RuleUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    condition: dict[str, Any] | None = None
    actions: list[dict[str, Any]] | None = None
    enabled: bool | None = None


class PremiumInvoiceRequest(BaseModel):
    plan_code: str


class ReportDecision(BaseModel):
    decision: Literal["dismiss", "warn", "mute", "ban"]
    duration_seconds: int | None = Field(default=None, ge=0, le=31_536_000)
    reason: str = Field(default="", max_length=500)
