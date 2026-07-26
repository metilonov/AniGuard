from app.pricing import PREMIUM_PLANS, get_plan


def test_prices_are_increasing_with_duration():
    plans = list(PREMIUM_PLANS.values())
    assert [plan.days for plan in plans] == sorted(plan.days for plan in plans)
    assert [plan.stars for plan in plans] == sorted(plan.stars for plan in plans)


def test_year_is_cheaper_per_day_than_month():
    start = get_plan("start")
    year = get_plan("year")
    assert year.stars / year.days < start.stars / start.days
