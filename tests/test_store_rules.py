from app.store_rules import (
    PREMIUM_CASE_WEIGHTS,
    advertising_price,
    coin_stars,
    premium_probability_ppm,
)


def test_coin_packages_convert_to_integer_stars() -> None:
    assert coin_stars(100) == 1
    assert coin_stars(1_000) == 10
    assert coin_stars(100_000) == 1_000


def test_custom_coin_purchase_has_100_star_minimum() -> None:
    try:
        coin_stars(9_900, custom=True)
    except ValueError as exc:
        assert "10 000" in str(exc)
    else:
        raise AssertionError("custom amount below 10 000 must fail")
    assert coin_stars(10_000, custom=True) == 100


def test_advertising_prices_match_specification() -> None:
    assert advertising_price("channel", 10) == 20
    assert advertising_price("bot", 10) == 50
    assert advertising_price("rewarded", 10) == 150


def test_premium_case_weights_are_exact_and_below_one_percent() -> None:
    assert sum(weight for _kind, weight, _value, _label in PREMIUM_CASE_WEIGHTS) == 1_000_000
    assert premium_probability_ppm() == 7_510
    year = next(item for item in PREMIUM_CASE_WEIGHTS if item[3] == "Premium на 1 год")
    assert year[1] == 10  # 0.001%
