from pathlib import Path

import pytest

from app.roles import (
    ROLE_ORDER,
    effective_role,
    normalize_penalty_status,
    normalize_role,
    penalty_name,
    role_level,
    role_name,
    validate_action,
    validate_role_assignment,
)


ROOT = Path(__file__).resolve().parents[1]


def test_role_hierarchy_and_names() -> None:
    assert ROLE_ORDER == (
        "creator",
        "senior_admin",
        "admin",
        "junior_admin",
        "senior_moderator",
        "moderator",
        "junior_moderator",
        "member",
    )
    assert role_level("creator") == 8
    assert role_level("member") == 1
    assert role_name("senior_admin") == "Старший админ"
    assert role_name("senior_admin", naruto=True) == "Советник Хокаге"
    assert role_name("junior_moderator", naruto=True) == "Генин"


def test_role_and_penalty_aliases() -> None:
    assert normalize_role("owner") == "creator"
    assert normalize_role("Командир АНБУ") == "admin"
    assert normalize_role("младший_админ") == "junior_admin"
    assert normalize_role("Джонин") == "senior_moderator"
    assert normalize_penalty_status("Нукенин") == "violator"
    assert normalize_penalty_status("Преступник S-ранга") == "severe_violator"
    assert penalty_name("severe_violator") == "Злостный нарушитель"
    assert penalty_name("severe_violator", naruto=True) == "Преступник S-ранга"


def test_penalty_status_suspends_staff_power_without_replacing_role() -> None:
    assert effective_role("admin", "none") == "admin"
    assert effective_role("admin", "violator") == "member"
    assert effective_role("senior_moderator", "severe_violator") == "member"
    assert effective_role("creator", "none") == "creator"


def test_role_assignment_caps_and_hierarchy_protection() -> None:
    assert validate_role_assignment("creator", "member", "senior_admin") == "senior_admin"
    assert validate_role_assignment("senior_admin", "member", "admin") == "admin"
    assert validate_role_assignment("admin", "member", "junior_admin") == "junior_admin"
    assert validate_role_assignment("junior_admin", "member", "senior_moderator") == "senior_moderator"

    with pytest.raises(PermissionError):
        validate_role_assignment("admin", "member", "admin")
    with pytest.raises(PermissionError):
        validate_role_assignment("moderator", "member", "junior_moderator")
    with pytest.raises(PermissionError):
        validate_role_assignment("admin", "senior_admin", "member")
    with pytest.raises(PermissionError):
        validate_role_assignment("creator", "member", "creator")


def test_punishment_limits_by_role() -> None:
    validate_action("junior_moderator", "member", "mute", 3600)
    with pytest.raises(PermissionError):
        validate_action("junior_moderator", "member", "mute", 3601)
    with pytest.raises(PermissionError):
        validate_action("junior_moderator", "member", "ban", 86400)

    validate_action("moderator", "member", "ban", 7 * 86400)
    with pytest.raises(PermissionError):
        validate_action("moderator", "member", "ban", 7 * 86400 + 1)
    with pytest.raises(PermissionError):
        validate_action("moderator", "member", "ban", 0)

    validate_action("senior_moderator", "member", "ban", 30 * 86400)
    validate_action("senior_moderator", "member", "mute", 0)
    with pytest.raises(PermissionError):
        validate_action("senior_moderator", "member", "ban", 31 * 86400)


def test_equal_or_higher_role_cannot_be_punished() -> None:
    with pytest.raises(PermissionError):
        validate_action("moderator", "moderator", "mute", 60)
    with pytest.raises(PermissionError):
        validate_action("admin", "senior_admin", "ban", 86400)
    with pytest.raises(PermissionError):
        validate_action("creator", "creator", "warn", None)


def test_bot_source_contains_info_admin_role_and_penalty_commands() -> None:
    source = (ROOT / "app" / "bot.py").read_text(encoding="utf-8")
    for value in (
        'Command("admin")',
        'Command("info")',
        'Command("role")',
        'Command("penalty")',
        'Command("role_history")',
        "совет[_\\s]+пяти[_\\s]+каге",
        "свиток[_\\s]+ниндзя",
        "enforce_penalty_status_message",
    ):
        assert value in source


def test_mini_app_contains_role_and_penalty_controls() -> None:
    html = (ROOT / "app" / "static" / "index.html").read_text(encoding="utf-8")
    assert "ROLE_OPTIONS" in html
    assert "member-role" in html
    assert "member-penalty-status" in html
    assert "/role-history" in html
    assert "Преступник S-ранга" in html
