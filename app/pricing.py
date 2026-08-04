from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PremiumPlan:
    code: str
    title: str
    days: int
    stars: int
    badge: str
    description: str
    scope: str
    months: int
    discount_percent: int


GROUP_PREMIUM_PLANS: dict[str, PremiumPlan] = {
    "group_1m": PremiumPlan(
        code="group_1m",
        title="1 месяц",
        days=30,
        stars=99,
        badge="Без скидки",
        description="Premium только для выбранной беседы на 30 дней.",
        scope="group",
        months=1,
        discount_percent=0,
    ),
    "group_4m": PremiumPlan(
        code="group_4m",
        title="4 месяца",
        days=120,
        stars=376,
        badge="Скидка 5%",
        description="Premium только для выбранной беседы на 4 месяца.",
        scope="group",
        months=4,
        discount_percent=5,
    ),
    "group_7m": PremiumPlan(
        code="group_7m",
        title="7 месяцев",
        days=210,
        stars=624,
        badge="Скидка 10%",
        description="Premium только для выбранной беседы на 7 месяцев.",
        scope="group",
        months=7,
        discount_percent=10,
    ),
    "group_10m": PremiumPlan(
        code="group_10m",
        title="10 месяцев",
        days=300,
        stars=842,
        badge="Скидка 15%",
        description="Premium только для выбранной беседы на 10 месяцев.",
        scope="group",
        months=10,
        discount_percent=15,
    ),
    "group_12m": PremiumPlan(
        code="group_12m",
        title="12 месяцев",
        days=365,
        stars=950,
        badge="Скидка 20%",
        description="Premium только для выбранной беседы на 12 месяцев.",
        scope="group",
        months=12,
        discount_percent=20,
    ),
}


ACCOUNT_PREMIUM_PLANS: dict[str, PremiumPlan] = {
    "account_1m": PremiumPlan(
        code="account_1m",
        title="1 месяц",
        days=30,
        stars=199,
        badge="Без скидки",
        description=(
            "Premium аккаунта на 30 дней. Действует во всех беседах, "
            "где вы являетесь создателем."
        ),
        scope="account",
        months=1,
        discount_percent=0,
    ),
    "account_4m": PremiumPlan(
        code="account_4m",
        title="4 месяца",
        days=120,
        stars=756,
        badge="Скидка 5%",
        description=(
            "Premium аккаунта на 4 месяца для всех созданных вами бесед."
        ),
        scope="account",
        months=4,
        discount_percent=5,
    ),
    "account_7m": PremiumPlan(
        code="account_7m",
        title="7 месяцев",
        days=210,
        stars=1254,
        badge="Скидка 10%",
        description=(
            "Premium аккаунта на 7 месяцев для всех созданных вами бесед."
        ),
        scope="account",
        months=7,
        discount_percent=10,
    ),
    "account_10m": PremiumPlan(
        code="account_10m",
        title="10 месяцев",
        days=300,
        stars=1692,
        badge="Скидка 15%",
        description=(
            "Premium аккаунта на 10 месяцев для всех созданных вами бесед."
        ),
        scope="account",
        months=10,
        discount_percent=15,
    ),
    "account_12m": PremiumPlan(
        code="account_12m",
        title="12 месяцев",
        days=365,
        stars=1910,
        badge="Скидка 20%",
        description=(
            "Premium аккаунта на 12 месяцев для всех созданных вами бесед."
        ),
        scope="account",
        months=12,
        discount_percent=20,
    ),
}


# Сохраняем старое имя: существующий API группы продолжает отдавать
# только тарифы конкретной беседы.
PREMIUM_PLANS = GROUP_PREMIUM_PLANS


_LEGACY_GROUP_PLAN_ALIASES = {
    "start": "group_1m",
    "season": "group_4m",
    "halfyear": "group_7m",
    "year": "group_12m",
}


def get_plan(code: str) -> PremiumPlan:
    normalized = str(code or "").strip()
    normalized = _LEGACY_GROUP_PLAN_ALIASES.get(normalized, normalized)

    plan = GROUP_PREMIUM_PLANS.get(normalized)
    if plan is None:
        plan = ACCOUNT_PREMIUM_PLANS.get(normalized)
    if plan is None:
        raise ValueError("Unknown premium plan")
    return plan
