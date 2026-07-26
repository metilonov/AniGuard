from __future__ import annotations

import re
from dataclasses import dataclass

MAX_DURATION_SECONDS = 31_536_000  # 365 days; longer restrictions should use "навсегда".


@dataclass(slots=True, frozen=True)
class ParsedDuration:
    seconds: int | None
    consumed: int
    text: str | None


_PERMANENT_RE = re.compile(
    r"^\s*(навсегда|бессрочно|постоянно|перманентно)[.,;:]?(?=\s|$|[!?])",
    re.IGNORECASE,
)

_UNIT_ALIASES: tuple[tuple[str, int], ...] = (
    (r"секунд(?:а|ы)?|сек(?:унда|унды|унд)?|сек|с", 1),
    (r"минут(?:а|ы)?|мин(?:ута|уты|ут)?|мин|м", 60),
    (r"час(?:а|ов)?|ч", 3_600),
    (r"д(?:ень|ня|ней)|дн(?:я|ей)?|д", 86_400),
    (r"недел(?:я|и|ь)|нед", 604_800),
    (r"месяц(?:а|ев)?|мес", 2_592_000),
    (r"год(?:а|ов)?|лет", 31_536_000),
)

_PART_RE = re.compile(
    r"^\s*(\d{1,6})\s*(" + "|".join(unit for unit, _ in _UNIT_ALIASES) + r")[.]?(?=\s|$|[,;:!?])",
    re.IGNORECASE,
)


def _unit_multiplier(unit: str) -> int:
    normalized = unit.casefold().rstrip(".")
    for pattern, multiplier in _UNIT_ALIASES:
        if re.fullmatch(pattern, normalized, re.IGNORECASE):
            return multiplier
    raise ValueError(f"Неизвестная единица времени: {unit}")


def parse_duration_prefix(value: str | None) -> ParsedDuration:
    """Parse one or more Russian duration parts from the beginning of *value*.

    Examples: ``30 секунд``, ``1 день 2 часа``, ``навсегда``.
    If the text does not start with a duration, ``seconds`` is ``None`` and
    ``consumed`` is zero.
    """
    if not value:
        return ParsedDuration(None, 0, None)

    permanent = _PERMANENT_RE.match(value)
    if permanent:
        return ParsedDuration(0, permanent.end(), "навсегда")

    offset = 0
    total = 0
    matched_any = False
    while offset < len(value):
        match = _PART_RE.match(value[offset:])
        if not match:
            break
        amount = int(match.group(1))
        if amount <= 0:
            raise ValueError("Срок должен быть больше нуля")
        multiplier = _unit_multiplier(match.group(2))
        total += amount * multiplier
        offset += match.end()
        matched_any = True

    if not matched_any:
        return ParsedDuration(None, 0, None)
    if total > MAX_DURATION_SECONDS:
        raise ValueError("Максимальный временный срок — 365 дней. Для бессрочного действия напишите «навсегда».")
    return ParsedDuration(total, offset, format_duration_ru(total))


def parse_duration(value: str | None, default: int) -> int:
    parsed = parse_duration_prefix(value)
    if parsed.seconds is None:
        return default
    trailing = (value or "")[parsed.consumed:].strip()
    if trailing:
        raise ValueError("После срока найден лишний текст")
    return parsed.seconds


def _plural(number: int, one: str, few: str, many: str) -> str:
    n = abs(number) % 100
    if 11 <= n <= 14:
        return many
    n %= 10
    if n == 1:
        return one
    if 2 <= n <= 4:
        return few
    return many


def format_duration_ru(seconds: int | None) -> str:
    if seconds is None:
        return "не указан"
    if seconds == 0:
        return "навсегда"

    remaining = int(seconds)
    units = (
        (31_536_000, ("год", "года", "лет")),
        (2_592_000, ("месяц", "месяца", "месяцев")),
        (604_800, ("неделя", "недели", "недель")),
        (86_400, ("день", "дня", "дней")),
        (3_600, ("час", "часа", "часов")),
        (60, ("минута", "минуты", "минут")),
        (1, ("секунда", "секунды", "секунд")),
    )
    parts: list[str] = []
    for size, forms in units:
        if remaining < size:
            continue
        amount, remaining = divmod(remaining, size)
        parts.append(f"{amount} {_plural(amount, *forms)}")
        if len(parts) == 3:
            break
    return " ".join(parts) or "0 секунд"


def clean_reason(value: str | None) -> str:
    reason = (value or "").strip()
    reason = reason.lstrip(" ,.;:—-")
    reason = re.sub(r"^(?:за\s+|причина\s*[:—-]\s*)", "", reason, flags=re.IGNORECASE)
    return reason.strip()
