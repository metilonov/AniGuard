from typing import Any, Literal

from pydantic import BaseModel, Field


class SettingsUpdate(BaseModel):
    settings: dict[str, Any]


class ActionRequest(BaseModel):
    action: Literal[
        "warn", "unwarn", "mute", "unmute", "ban", "unban", "purge",
        "slow", "lock", "unlock", "quarantine", "unquarantine", "kick", "susanoo", "case",
        "restrict_media", "unrestrict_media", "restrict_links", "unrestrict_links",
        "restrict_commands", "unrestrict_commands"
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


class CustomCommandCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    trigger: str = Field(min_length=1, max_length=64)
    action_type: Literal[
        "warn", "mute", "ban", "kick", "quarantine",
        "restrict_media", "restrict_links", "restrict_commands",
    ]
    duration_seconds: int | None = Field(default=604800, ge=0, le=31_536_000)
    response_template: str = Field(
        default="{admin} использовал {command} на {user}.",
        min_length=1,
        max_length=1000,
    )
    required_role: Literal["moderators", "admins"] = "admins"
    target_mode: Literal["reply", "reply_or_username"] = "reply_or_username"
    cooldown_seconds: int = Field(default=0, ge=0, le=86_400)
    delete_trigger: bool = False
    require_reason: bool = False
    enabled: bool = True


class CustomCommandUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    trigger: str | None = Field(default=None, min_length=1, max_length=64)
    action_type: Literal[
        "warn", "mute", "ban", "kick", "quarantine",
        "restrict_media", "restrict_links", "restrict_commands",
    ] | None = None
    duration_seconds: int | None = Field(default=None, ge=0, le=31_536_000)
    response_template: str | None = Field(default=None, min_length=1, max_length=1000)
    required_role: Literal["moderators", "admins"] | None = None
    target_mode: Literal["reply", "reply_or_username"] | None = None
    cooldown_seconds: int | None = Field(default=None, ge=0, le=86_400)
    delete_trigger: bool | None = None
    require_reason: bool | None = None
    enabled: bool | None = None


class GameCommandCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    trigger: str = Field(min_length=1, max_length=64)
    command_type: Literal["text", "random", "reward"] = "text"
    response_template: str = Field(min_length=1, max_length=1000)
    response_variants: list[str] = Field(default_factory=list, max_length=20)
    reward_xp: int = Field(default=0, ge=0, le=10_000)
    reward_coins: int = Field(default=0, ge=0, le=10_000)
    cooldown_seconds: int = Field(default=5, ge=0, le=86_400)
    access: Literal["all", "verified", "moderators", "admins"] = "all"
    enabled: bool = True


class GameCommandUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    trigger: str | None = Field(default=None, min_length=1, max_length=64)
    command_type: Literal["text", "random", "reward"] | None = None
    response_template: str | None = Field(default=None, min_length=1, max_length=1000)
    response_variants: list[str] | None = None
    reward_xp: int | None = Field(default=None, ge=0, le=10_000)
    reward_coins: int | None = Field(default=None, ge=0, le=10_000)
    cooldown_seconds: int | None = Field(default=None, ge=0, le=86_400)
    access: Literal["all", "verified", "moderators", "admins"] | None = None
    enabled: bool | None = None


class AdminPremiumRequest(BaseModel):
    entity_type: Literal["user", "chat"]
    entity_id: int
    days: int = Field(default=30, ge=0, le=3650)
    permanent: bool = False
    plan: str = Field(default="admin", max_length=32)
    note: str = Field(default="", max_length=500)


class AdminBlockRequest(BaseModel):
    entity_type: Literal["user", "chat"]
    entity_id: int
    blocked: bool = True
    reason: str = Field(default="", max_length=500)
    duration_seconds: int | None = Field(default=None, ge=0, le=31_536_000)


class MemberRoleUpdate(BaseModel):
    role: Literal[
        "creator", "senior_admin", "admin", "junior_admin",
        "senior_moderator", "moderator", "junior_moderator", "member",
    ]
    duration_seconds: int | None = Field(default=None, ge=0, le=31_536_000)
    reason: str = Field(default="", max_length=500)


class PenaltyStatusUpdate(BaseModel):
    status: Literal["none", "violator", "severe_violator"]
    duration_seconds: int | None = Field(default=None, ge=0, le=31_536_000)
    reason: str = Field(default="", max_length=500)


class BasicCommandUpdate(BaseModel):
    trigger: str = Field(min_length=1, max_length=64)
    response: str = Field(min_length=1, max_length=1500)


class GroupRulesUpdate(BaseModel):
    rules: list[str] = Field(default_factory=list, max_length=100)


class WelcomeSettingsUpdate(BaseModel):
    enabled: bool = True
    text: str = Field(default="", max_length=4000)
    after_captcha: bool = True


class CaptchaSettingsUpdate(BaseModel):
    enabled: bool = True
    timeout_seconds: int = Field(default=60, ge=30, le=1800)
    attempts: int = Field(default=3, ge=1, le=9)
    failure_action: Literal["kick", "ban", "quarantine", "notify"] = "kick"
    image_set: Literal["random", "nature", "food", "objects", "animals"] = "random"
    message: str = Field(default="", max_length=2000)


class AdminCoinRequest(BaseModel):
    user_id: int
    operation: Literal["add", "subtract", "set"] = "add"
    amount: int = Field(ge=0, le=1_000_000_000)
    note: str = Field(default="", max_length=500)


class AdminChatStateRequest(BaseModel):
    active: bool


class AdminChatSettingsRequest(BaseModel):
    captcha: bool | None = None
    automod: bool | None = None


class AdminBroadcastRequest(BaseModel):
    audience: Literal["users", "active_users", "premium_users", "chat_owners", "all"] = "users"
    text: str = Field(min_length=1, max_length=4096)
    button_text: str | None = Field(default=None, max_length=64)
    button_url: str | None = Field(default=None, max_length=500)


class AdminPromoCreateRequest(BaseModel):
    reward_type: Literal["coins", "premium"]
    reward_value: int = Field(ge=1, le=1_000_000_000)
    max_uses: int = Field(default=100, ge=1, le=1_000_000)
    code: str | None = Field(default=None, min_length=4, max_length=32)


class AdminPromoToggleRequest(BaseModel):
    active: bool


class AdminSystemSettingsRequest(BaseModel):
    settings: dict[str, bool]


class AdminReportCloseRequest(BaseModel):
    status: Literal["closed", "in_progress", "new"] = "closed"


class AdminDirectMessageRequest(BaseModel):
    text: str = Field(min_length=1, max_length=4096)


class AdminBulkPremiumRequest(BaseModel):
    category: Literal["premium_users", "expiring_users", "premium_chats"]
    days: int = Field(default=7, ge=1, le=3650)

# AniGuard operations suite v20
class AdminCaseCloseRequest(BaseModel):
    status: Literal["closed", "reversed", "changed"] = "closed"


class AdminAppealDecisionRequest(BaseModel):
    decision: Literal["accept", "reject", "change"]
    note: str = Field(default="", max_length=1000)


class AdminSecurityModeRequest(BaseModel):
    enabled: bool = True
    reason: str = Field(default="", max_length=500)
    duration_seconds: int | None = Field(default=None, ge=0, le=31_536_000)


class AdminPunishmentLadderRequest(BaseModel):
    enabled: bool = True
    steps: list[dict[str, Any]] = Field(default_factory=list, max_length=20)


class AdminPermissionOverrideRequest(BaseModel):
    user_id: int
    permission: str = Field(min_length=1, max_length=64)
    allowed: bool = True
    limit_value: int | None = Field(default=None, ge=0, le=31_536_000)
    expires_seconds: int | None = Field(default=None, ge=0, le=31_536_000)


class AdminShiftCreateRequest(BaseModel):
    user_id: int
    starts_at: str
    ends_at: str
    temporary_role: str | None = Field(default=None, max_length=32)


class AdminResponseStyleRequest(BaseModel):
    style: Literal["ordinary", "naruto", "minimal", "strict", "custom"] = "naruto"
    length: Literal["short", "full"] = "full"
    delete_command_message: bool = False
    reply_in_thread: bool = False


class AdminBackupRestoreRequest(BaseModel):
    confirm: bool = False


class AdminProbationDecisionRequest(BaseModel):
    decision: Literal["confirm", "reject", "extend"]
    note: str = Field(default="", max_length=1000)
    extend_days: int = Field(default=7, ge=1, le=365)


class StylePackCreate(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    description: str = Field(default="", max_length=1000)
    base_style: Literal["ordinary", "naruto", "minimal", "strict"] = "ordinary"
    templates: dict[str, str] = Field(default_factory=dict)
    preview_text: str = Field(default="", max_length=2000)


class StylePackUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=80)
    description: str | None = Field(default=None, max_length=1000)
    base_style: Literal["ordinary", "naruto", "minimal", "strict"] | None = None
    templates: dict[str, str] | None = None
    preview_text: str | None = Field(default=None, max_length=2000)


class StylePackModerationRequest(BaseModel):
    decision: Literal["approve", "reject", "return"]
    note: str = Field(default="", max_length=1500)


class ChatStyleApplyRequest(BaseModel):
    style: Literal["ordinary", "naruto", "minimal", "strict", "custom"]
    code: str | None = Field(default=None, max_length=24)
    length: Literal["short", "full"] = "full"
    delete_command_message: bool = False
    reply_in_thread: bool = False
