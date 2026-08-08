import pytest

from app.durations import format_duration_ru, parse_duration_prefix


@pytest.mark.parametrize(
    ("text", "seconds"),
    [
        ("1 секунда", 1),
        ("2 сек", 2),
        ("30 с", 30),
        ("1 минута", 60),
        ("2 мин", 120),
        ("5 м", 300),
        ("1 час", 3600),
        ("2 ч", 7200),
        ("1 день", 86400),
        ("7 д", 604800),
        ("1 неделя", 604800),
        ("1 месяц", 2592000),
        ("навсегда", 0),
        ("бессрочно", 0),
    ],
)
def test_russian_duration_aliases(text: str, seconds: int) -> None:
    parsed = parse_duration_prefix(text)
    assert parsed.seconds == seconds
    assert parsed.consumed == len(text)


def test_composite_duration_stops_before_reason() -> None:
    text = "1 день 2 часа 30 минут флуд"
    parsed = parse_duration_prefix(text)
    assert parsed.seconds == 95400
    assert text[parsed.consumed :].strip() == "флуд"


def test_text_without_duration_is_not_consumed() -> None:
    parsed = parse_duration_prefix("флуд и реклама")
    assert parsed.seconds is None
    assert parsed.consumed == 0


def test_duration_over_365_days_is_rejected() -> None:
    with pytest.raises(ValueError):
        parse_duration_prefix("13 месяцев")


def test_duration_formatting() -> None:
    assert format_duration_ru(0) == "навсегда"
    assert format_duration_ru(604800) == "1 неделя"
    assert format_duration_ru(95400) == "1 день 2 часа 30 минут"


def test_dotted_abbreviation_is_fully_consumed() -> None:
    parsed = parse_duration_prefix("30 мин. флуд")
    assert parsed.seconds == 1800
    assert "30 мин. флуд"[parsed.consumed :].strip() == "флуд"


def test_permanent_with_period_does_not_become_reason() -> None:
    parsed = parse_duration_prefix("навсегда. реклама")
    assert parsed.seconds == 0
    assert "навсегда. реклама"[parsed.consumed :].strip() == "реклама"
