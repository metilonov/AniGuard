from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PremiumPlan:
    code: str
    title: str
    days: int
    stars: int
    badge: str
    description: str


# Собственный вариант цен: низкий порог входа и заметная выгода на длинных периодах.
PREMIUM_PLANS: dict[str, PremiumPlan] = {
    "start": PremiumPlan(
        code="start",
        title="Старт",
        days=30,
        stars=179,
        badge="На месяц",
        description="Все Premium-функции для одной беседы на 30 дней.",
    ),
    "season": PremiumPlan(
        code="season",
        title="Сезон",
        days=90,
        stars=449,
        badge="Популярный",
        description="Premium на 90 дней; дешевле помесячной оплаты.",
    ),
    "halfyear": PremiumPlan(
        code="halfyear",
        title="Полугодие",
        days=180,
        stars=799,
        badge="Выгодно",
        description="Premium на 180 дней для стабильной работы сообщества.",
    ),
    "year": PremiumPlan(
        code="year",
        title="Год",
        days=365,
        stars=1399,
        badge="Максимальная выгода",
        description="Premium на 365 дней для одной беседы.",
    ),
}


def get_plan(code: str) -> PremiumPlan:
    try:
        return PREMIUM_PLANS[code]
    except KeyError as exc:
        raise ValueError("Unknown premium plan") from exc
