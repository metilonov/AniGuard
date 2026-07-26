from __future__ import annotations

import re
from dataclasses import dataclass

from app.durations import clean_reason, parse_duration_prefix


@dataclass(slots=True)
class ParsedModerationCommand:
    action: str
    target_token: str | None
    duration_seconds: int | None
    duration_was_explicit: bool
    reason: str
    trigger: str


# Longest phrases must come first.
ACTION_ALIASES: tuple[tuple[str, str], ...] = (
    ("снять ограничение команд", "unrestrict_commands"),
    ("разрешить команды", "unrestrict_commands"),
    ("снять запрет ссылок", "unrestrict_links"),
    ("разрешить ссылки", "unrestrict_links"),
    ("снять запрет медиа", "unrestrict_media"),
    ("разрешить медиа", "unrestrict_media"),
    ("заблокировать команды", "restrict_commands"),
    ("запретить команды", "restrict_commands"),
    ("запретить ссылки", "restrict_links"),
    ("запретить медиа", "restrict_media"),
    ("снять предупреждение", "unwarn"),
    ("снять карантин", "unquarantine"),
    ("снять пред", "unwarn"),
    ("снять мут", "unmute"),
    ("снять бан", "unban"),
    ("размут", "unmute"),
    ("анмут", "unmute"),
    ("разбан", "unban"),
    ("анбан", "unban"),
    ("анварн", "unwarn"),
    ("предупреждение", "warn"),
    ("карантин", "quarantine"),
    ("исключить", "kick"),
    ("выгнать", "kick"),
    ("кик", "kick"),
    ("ограничить медиа", "restrict_media"),
    ("ограничить ссылки", "restrict_links"),
    ("ограничить команды", "restrict_commands"),
    ("мут", "mute"),
    ("бан", "ban"),
    ("пред", "warn"),
    ("варн", "warn"),
)

TARGET_RE = re.compile(r"^\s*(@[A-Za-z0-9_]{5,}|-?\d{4,})\b")

TIMED_ACTIONS = {
    "mute",
    "ban",
    "quarantine",
    "restrict_media",
    "restrict_links",
    "restrict_commands",
}

UNTIMED_ACTIONS = {
    "warn",
    "unwarn",
    "unmute",
    "unban",
    "unrestrict_media",
    "unrestrict_links",
    "unrestrict_commands",
    "unquarantine",
    "kick",
    "case",
}


def detect_action(text: str) -> tuple[str, str, str] | None:
    stripped = text.strip()
    lowered = stripped.casefold()
    if stripped.startswith("/"):
        first, _, remainder = stripped.partition(" ")
        command = first[1:].split("@", 1)[0].casefold()
        slash_map = {
            "mute": "mute",
            "unmute": "unmute",
            "ban": "ban",
            "unban": "unban",
            "warn": "warn",
            "unwarn": "unwarn",
            "quarantine": "quarantine",
            "unquarantine": "unquarantine",
            "kick": "kick",
            "media": "restrict_media",
            "unmedia": "unrestrict_media",
            "linksban": "restrict_links",
            "unlinksban": "unrestrict_links",
            "commandsban": "restrict_commands",
            "uncommandsban": "unrestrict_commands",
        }
        action = slash_map.get(command)
        if action:
            return action, command, remainder

    for alias, action in ACTION_ALIASES:
        alias_lower = alias.casefold()
        if lowered == alias_lower:
            return action, alias, ""
        if lowered.startswith(alias_lower + " ") or lowered.startswith(alias_lower + "\n"):
            return action, alias, stripped[len(alias):]
    return None


def parse_moderation_command(
    text: str,
    *,
    default_duration_seconds: int,
    forced_action: str | None = None,
    forced_trigger: str | None = None,
) -> ParsedModerationCommand | None:
    if forced_action:
        action = forced_action
        trigger = forced_trigger or forced_action
        remainder = text or ""
    else:
        detected = detect_action(text)
        if not detected:
            return None
        action, trigger, remainder = detected

    if action not in TIMED_ACTIONS | UNTIMED_ACTIONS:
        return None

    remainder = remainder.strip()
    target_token: str | None = None
    target_match = TARGET_RE.match(remainder)
    if target_match:
        target_token = target_match.group(1)
        remainder = remainder[target_match.end():].lstrip()

    duration_seconds: int | None = None
    explicit = False
    if action in TIMED_ACTIONS:
        parsed_duration = parse_duration_prefix(remainder)
        if parsed_duration.seconds is not None:
            duration_seconds = parsed_duration.seconds
            explicit = True
            remainder = remainder[parsed_duration.consumed:].lstrip()
        else:
            duration_seconds = default_duration_seconds

    reason = clean_reason(remainder)
    return ParsedModerationCommand(
        action=action,
        target_token=target_token,
        duration_seconds=duration_seconds,
        duration_was_explicit=explicit,
        reason=reason,
        trigger=trigger,
    )
