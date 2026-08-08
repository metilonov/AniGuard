from pathlib import Path


STATIC_DIR = Path(__file__).resolve().parents[1] / "app" / "static"


def test_profile_icons_are_inline_and_complete():
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    combined = html + js

    assert "/static/icons/profile/" not in combined
    for symbol in (
        "i-menu-profile",
        "i-menu-settings",
        "i-menu-advertising",
        "i-menu-faq",
        "i-profile-rating",
        "i-profile-level",
        "i-profile-game",
        "i-profile-reputation",
        "i-profile-anicoin",
        "i-profile-wallet",
    ):
        assert f'id="{symbol}"' in html


def test_profile_account_grid_and_advertising_view_exist():
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")

    assert 'id="account-info-list" class="account-info-grid"' in html
    assert 'data-panel="advertising"' in html
    assert "function renderAdvertising()" in js
    assert "function submitAdvertisingOrder()" in js
