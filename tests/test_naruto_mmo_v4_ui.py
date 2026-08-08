from __future__ import annotations

import re
from pathlib import Path

UI = Path(__file__).resolve().parents[1] / "app" / "naruto_game" / "ui_v4.py"
ROUTER = Path(__file__).resolve().parents[1] / "app" / "naruto_game" / "router.py"
UI_TEXT = UI.read_text(encoding="utf-8")
ROUTER_TEXT = ROUTER.read_text(encoding="utf-8")


def _callbacks(text: str) -> list[str]:
    return re.findall(r'"((?:ng|ng4):[^"\\]+)"', text)


def test_root_menu_is_compact_and_category_based():
    block = UI_TEXT.split("def main_menu()", 1)[1].split("def hub_menu", 1)[0]
    data = _callbacks(block)
    for item in (
        "ng:hub:shinobi", "ng:hub:activities", "ng:hub:world", "ng:hub:social",
        "ng:hub:economy", "ng:hub:growth", "ng:menu:mmo4",
    ):
        assert item in data
    assert "ng4:daily:claim" not in data
    assert "ng4:mission:classic" not in data
    assert "ng4:raid:attack" not in data
    assert "ng4:arena:match" not in data


def test_leaf_screens_have_contextual_home_navigation():
    leaf_names = [
        "profile_menu", "daily_menu", "mission_menu", "story_menu", "battle_menu",
        "arena_menu", "techniques_menu", "cards_menu", "inventory_menu", "world_menu",
        "clan_menu", "raid_menu", "social_menu", "mmo_menu", "path_menu", "events_menu",
        "territory_menu", "government_menu", "newspaper_menu", "mmo4_menu", "economy_menu",
        "growth_menu", "recommend_menu", "pulse_menu", "bijuu_menu",
    ]
    for name in leaf_names:
        start = UI_TEXT.index(f"def {name}()")
        tail = UI_TEXT[start:]
        next_match = re.search(r"\ndef [a-zA-Z_]", tail[1:])
        block = tail[: next_match.start() + 1] if next_match else tail
        # Most leaf menus call leaf_back(); mmo4/craft have direct Home callbacks.
        assert "leaf_back(" in block or "ng:menu:home" in block, name


def test_all_literal_callback_payloads_fit_telegram_limit():
    for callback in _callbacks(UI_TEXT + ROUTER_TEXT):
        assert len(callback.encode("utf-8")) <= 64, callback


def test_risky_leaf_openers_do_not_execute_actions_directly():
    menu_block = ROUTER_TEXT.split('@router.callback_query(F.data.startswith("ng:menu:"))', 1)[1]
    menu_block = menu_block.split('@router.callback_query(F.data.startswith("ng4:"))', 1)[0]
    assert 'section == "daily"' in menu_block and "daily_center_text" in menu_block
    assert 'section == "mission"' in menu_block and "mission_center_text" in menu_block
    assert 'section == "arena"' in menu_block and "arena_center_text" in menu_block
    assert 'section == "raid"' in menu_block and "raid_center_text" in menu_block
    assert "claim_daily" not in menu_block
    assert "run_mission" not in menu_block
    assert "arena_match" not in menu_block
    assert "raid_attack" not in menu_block
