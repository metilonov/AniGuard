from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Final


@dataclass(frozen=True, slots=True)
class RoleDefinition:
    key: str
    level: int
    ordinary_name: str
    naruto_name: str
    max_mute_seconds: int | None
    max_ban_seconds: int | None
    can_assign_max_level: int | None = None


ROLE_DEFINITIONS: Final[dict[str, RoleDefinition]] = {
    "creator": RoleDefinition("creator", 8, "Создатель беседы", "Хокаге", None, None, 7),
    "senior_admin": RoleDefinition("senior_admin", 7, "Старший админ", "Советник Хокаге", None, None, 6),
    "admin": RoleDefinition("admin", 6, "Админ", "Командир АНБУ", None, None, 5),
    "junior_admin": RoleDefinition("junior_admin", 5, "Младший админ", "Капитан АНБУ", None, None, 4),
    "senior_moderator": RoleDefinition("senior_moderator", 4, "Старший модератор", "Джонин", None, 30 * 86400),
    "moderator": RoleDefinition("moderator", 3, "Модератор", "Чунин", 24 * 3600, 7 * 86400),
    "junior_moderator": RoleDefinition("junior_moderator", 2, "Младший модератор", "Генин", 3600, -1),
    "member": RoleDefinition("member", 1, "Участник", "Житель деревни", -1, -1),
}

ROLE_ORDER: Final[tuple[str, ...]] = tuple(
    item.key for item in sorted(ROLE_DEFINITIONS.values(), key=lambda role: role.level, reverse=True)
)

LEGACY_ROLE_ALIASES: Final[dict[str, str]] = {
    "owner": "creator",
    "chat_owner": "creator",
    "administrator": "admin",
    "telegram_admin": "admin",
    "mod": "moderator",
    "user": "member",
}

ROLE_INPUT_ALIASES: Final[dict[str, str]] = {
    "создатель": "creator",
    "создатель беседы": "creator",
    "хокаге": "creator",
    "старший админ": "senior_admin",
    "старший администратор": "senior_admin",
    "советник хокаге": "senior_admin",
    "админ": "admin",
    "администратор": "admin",
    "командир анбу": "admin",
    "младший админ": "junior_admin",
    "младший администратор": "junior_admin",
    "капитан анбу": "junior_admin",
    "старший модератор": "senior_moderator",
    "джонин": "senior_moderator",
    "модератор": "moderator",
    "чунин": "moderator",
    "младший модератор": "junior_moderator",
    "генин": "junior_moderator",
    "участник": "member",
    "житель деревни": "member",
}


@dataclass(frozen=True, slots=True)
class PenaltyDefinition:
    key: str
    level: int
    ordinary_name: str
    naruto_name: str


PENALTY_DEFINITIONS: Final[dict[str, PenaltyDefinition]] = {
    "none": PenaltyDefinition("none", 1, "Нет", "Нет"),
    "violator": PenaltyDefinition("violator", 0, "Нарушитель", "Нукенин"),
    "severe_violator": PenaltyDefinition("severe_violator", -1, "Злостный нарушитель", "Преступник S-ранга"),
}

PENALTY_INPUT_ALIASES: Final[dict[str, str]] = {
    "нет": "none",
    "снять": "none",
    "обычный": "none",
    "нарушитель": "violator",
    "нукенин": "violator",
    "злостный нарушитель": "severe_violator",
    "злостный": "severe_violator",
    "преступник s ранга": "severe_violator",
    "преступник s-ранга": "severe_violator",
}

# Minimum internal power level required for each moderation action.
ACTION_MIN_LEVEL: Final[dict[str, int]] = {
    "warn": 2,
    "unwarn": 2,
    "mute": 2,
    "unmute": 2,
    "restrict_media": 2,
    "unrestrict_media": 2,
    "restrict_links": 2,
    "unrestrict_links": 2,
    "restrict_commands": 2,
    "unrestrict_commands": 2,
    "kick": 3,
    "ban": 3,
    "unban": 4,
    "quarantine": 4,
    "unquarantine": 4,
    "purge": 3,
    "slow": 5,
    "lock": 5,
    "unlock": 5,
    "susanoo": 5,
    "case": 5,
}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def normalize_role(value: str | None) -> str:
    raw = (value or "member").strip().casefold().replace("_", " ").replace("ё", "е")
    canonical = LEGACY_ROLE_ALIASES.get(raw.replace(" ", "_"))
    if canonical:
        return canonical
    compact = " ".join(raw.split())
    alias = ROLE_INPUT_ALIASES.get(compact)
    if alias:
        return alias
    underscored = compact.replace(" ", "_")
    return underscored if underscored in ROLE_DEFINITIONS else "member"


def normalize_penalty_status(value: str | None) -> str:
    raw = (value or "none").strip().casefold().replace("_", " ").replace("ё", "е")
    compact = " ".join(raw.split())
    alias = PENALTY_INPUT_ALIASES.get(compact)
    if alias:
        return alias
    underscored = compact.replace(" ", "_")
    return underscored if underscored in PENALTY_DEFINITIONS else "none"


def role_definition(value: str | None) -> RoleDefinition:
    return ROLE_DEFINITIONS[normalize_role(value)]


def role_level(value: str | None) -> int:
    return role_definition(value).level


def role_name(value: str | None, *, naruto: bool = False) -> str:
    role = role_definition(value)
    return role.naruto_name if naruto else role.ordinary_name


def penalty_name(value: str | None, *, naruto: bool = False) -> str:
    penalty = PENALTY_DEFINITIONS[normalize_penalty_status(value)]
    return penalty.naruto_name if naruto else penalty.ordinary_name


def is_staff_role(value: str | None) -> bool:
    return role_level(value) >= 2


def is_admin_role(value: str | None) -> bool:
    return role_level(value) >= 5


def effective_role(value: str | None, penalty_status: str | None = None) -> str:
    """Return the role used for authorization while preserving the stored role.

    Any active penalty status suspends staff powers. The creator is protected
    from penalty assignment by the service layer and remains authoritative.
    """
    role = normalize_role(value)
    if role != "creator" and normalize_penalty_status(penalty_status) != "none":
        return "member"
    return role


def can_manage_target(actor_role: str | None, target_role: str | None) -> bool:
    return role_level(actor_role) > role_level(target_role)


def validate_role_assignment(actor_role: str | None, target_role: str | None, new_role: str | None) -> str:
    actor = role_definition(actor_role)
    target = role_definition(target_role)
    new = role_definition(new_role)
    if actor.key == "member" or actor.level < 5:
        raise PermissionError("Назначать роли может только младший администратор или выше")
    if target.key == "creator":
        raise PermissionError("Роль создателя беседы управляется только в Telegram")
    if actor.level <= target.level:
        raise PermissionError("Нельзя изменить роль пользователя равного или более высокого уровня")
    if new.key == "creator":
        raise PermissionError("Назначить создателя через бота нельзя")
    if actor.can_assign_max_level is None or new.level > actor.can_assign_max_level:
        raise PermissionError("Эта роль выше доступного вам уровня назначения")
    return new.key


def validate_action(
    actor_role: str | None,
    target_role: str | None,
    action: str,
    duration_seconds: int | None,
) -> None:
    actor = role_definition(actor_role)
    target = role_definition(target_role)
    required = ACTION_MIN_LEVEL.get(action, 2)
    if actor.level < required:
        raise PermissionError(f"Для действия «{action}» нужен уровень власти {required} или выше")
    if target.key == "creator":
        raise PermissionError("Создателя беседы нельзя наказать командами бота")
    if actor.level <= target.level:
        raise PermissionError("Нельзя применять наказание к равной или более высокой роли")

    if action == "mute":
        _validate_duration_limit(actor, duration_seconds, actor.max_mute_seconds, "мута")
    elif action == "ban":
        _validate_duration_limit(actor, duration_seconds, actor.max_ban_seconds, "бана")


def _validate_duration_limit(
    actor: RoleDefinition,
    duration_seconds: int | None,
    maximum: int | None,
    label: str,
) -> None:
    if maximum is None:
        return
    if maximum < 0:
        raise PermissionError(f"Роль «{actor.ordinary_name}» не может выдавать {label}")
    requested = 0 if duration_seconds is None else int(duration_seconds)
    if requested == 0:
        raise PermissionError(f"Роль «{actor.ordinary_name}» не может выдавать бессрочный {label}")
    if requested > maximum:
        raise PermissionError(
            f"Максимальный срок {label} для роли «{actor.ordinary_name}» — {format_seconds(maximum)}"
        )


def temporary_until(duration_seconds: int | None) -> datetime | None:
    if duration_seconds in (None, 0):
        return None
    return utcnow() + timedelta(seconds=max(1, int(duration_seconds)))


def format_seconds(seconds: int) -> str:
    if seconds % 86400 == 0:
        return f"{seconds // 86400} дн."
    if seconds % 3600 == 0:
        return f"{seconds // 3600} ч."
    if seconds % 60 == 0:
        return f"{seconds // 60} мин."
    return f"{seconds} сек."
