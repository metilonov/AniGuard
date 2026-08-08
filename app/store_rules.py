from __future__ import annotations

from typing import Final

PREMIUM_CASE_COST: Final[int] = 2_500
ANICOIN_PER_STAR: Final[int] = 100
AD_RATES: Final[dict[str, int]] = {"channel": 2, "bot": 5, "rewarded": 15}
AD_LABELS: Final[dict[str, str]] = {
    "channel": "Telegram-канал",
    "bot": "Рассылка в боте",
    "rewarded": "Реклама с вознаграждением",
}

# Integer parts per million. Total = 1_000_000 exactly.
PREMIUM_CASE_WEIGHTS: Final[tuple[tuple[str, int, int, str], ...]] = (
    ("coins", 992_490, 0, "AniCoin"),
    ("premium", 4_000, 3_600, "Premium на 1 час"),
    ("premium", 2_000, 86_400, "Premium на 1 день"),
    ("premium", 1_000, 604_800, "Premium на 7 дней"),
    ("premium", 400, 2_592_000, "Premium на 1 месяц"),
    ("premium", 100, 15_552_000, "Premium на 6 месяцев"),
    ("premium", 10, 31_536_000, "Premium на 1 год"),
)


def coin_stars(amount: int, *, custom: bool = False) -> int:
    amount = int(amount)
    if amount < ANICOIN_PER_STAR:
        raise ValueError("Минимальный пакет — 100 AniCoin")
    if amount % ANICOIN_PER_STAR != 0:
        raise ValueError("Сумма AniCoin должна быть кратна 100")
    if custom and amount < 10_000:
        raise ValueError("Минимальная своя сумма — 10 000 AniCoin (100 звёзд)")
    return amount // ANICOIN_PER_STAR


def advertising_price(placement: str, audience_count: int) -> int:
    if placement not in AD_RATES:
        raise ValueError("Неизвестный формат рекламы")
    if audience_count < 1:
        raise ValueError("Аудитория должна быть больше нуля")
    return int(audience_count) * AD_RATES[placement]


def premium_probability_ppm() -> int:
    return sum(weight for reward_type, weight, _value, _label in PREMIUM_CASE_WEIGHTS if reward_type == "premium")
