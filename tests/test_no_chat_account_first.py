from pathlib import Path


STATIC = Path(__file__).resolve().parents[1] / "app" / "static" / "index.html"


def test_no_chat_mode_opens_account_and_keeps_two_global_nav_items():
    html = STATIC.read_text(encoding="utf-8")
    assert "setView(allowedWithoutChat.has(initialView) ? initialView : 'profile')" in html
    assert 'data-view="shop" data-global-nav="true"' in html
    assert 'data-view="profile" data-global-nav="true"' in html
    assert '.bottom-nav.no-chat-mode .nav[data-requires-chat="true"] { display:none; }' in html
    assert "renderNoChatAccountHeader" in html


def test_no_chat_mode_does_not_show_old_empty_action_buttons():
    html = STATIC.read_text(encoding="utf-8")
    assert 'data-empty-view="shop"' not in html
    assert 'data-empty-view="profile"' not in html
