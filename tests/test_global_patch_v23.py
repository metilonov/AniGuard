from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("BOT_TOKEN", "123456:TEST_TOKEN_FOR_UNIT_TESTS")

from app.models import ResponseStylePack
from app.response_styles import (
    BUILTIN_STYLE_TEMPLATES,
    STYLE_VARIABLES,
    render_action_response,
    validate_templates,
)

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "app" / "static"


def test_detailed_moderation_responses_are_not_generic_done() -> None:
    for style in ("ordinary", "naruto"):
        for action in ("warn", "mute", "ban", "kick", "purge", "lock", "unlock", "report", "appeal"):
            text = BUILTIN_STYLE_TEMPLATES[style][action]
            assert "Готово." not in text
            assert len(text) > 90
            assert "{" in text
    rendered = render_action_response(
        style="naruto",
        action="mute",
        actor_id=1,
        actor_name="Какаши",
        target_id=2,
        target_name="Наруто",
        duration_seconds=3600,
        reason="Флуд",
        case_id=10,
        chat_title="Коноха",
    )
    assert "Дзюцу" in rendered
    assert "Флуд" in rendered
    assert "1 час" in rendered


def test_constructor_variables_and_unicode_template_keys() -> None:
    names = {item["name"] for item in STYLE_VARIABLES}
    assert {"{admin}", "{user}", "{reason}", "{duration}", "{actor}", "{target}", "{case_code}"}.issubset(names)
    cleaned = validate_templates({"rp.обнять": "🤗 {actor} обнял {target}", "game.расенган": "🌀 {actor} атакует {target}"})
    assert "rp.обнять" in cleaned
    assert "game.расенган" in cleaned


def test_style_pack_table_registered() -> None:
    assert ResponseStylePack.__tablename__ == "response_style_packs"


def test_constructor_menu_order_and_custom_style_search() -> None:
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    group_html = (STATIC / "group_panel_v12_integrated.html").read_text(encoding="utf-8")
    for source in (html, group_html):
        constructor = source.index("'constructor', 'i-menu-constructor'")
        support = source.index("'support', 'i-menu-support'")
        settings = source.index("'settings', 'i-menu-settings'")
        faq = source.index("'faq', 'i-menu-faq'")
        assert constructor < support < settings < faq
        assert "/api/styles/constructor" in source
        assert "/api/styles/search?code=" in source
        assert "/response-style" in source
        assert "Кастомный код не меняет глобальный стиль AniGuard" in source
        assert "constructor-existing-command" in source
        assert "loadExistingConstructorCommand" in source


def test_case_reel_is_portrait_and_shows_phone_sized_cells() -> None:
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    assert "aspect-ratio:9/16" in html
    assert "min-width:clamp(64px,19.5vw,78px)!important" in html
    assert "width:min(430px,calc(92dvh * 9 / 16),calc(100vw - 12px))" in html
    assert "height:auto;max-height:none;aspect-ratio:9/16" in html


def test_admin_overview_and_style_moderation() -> None:
    html = (STATIC / "admin.html").read_text(encoding="utf-8")
    required = (
        "Premium пользователей", "Активные 24 часа", "Активные 7 дней",
        "Доход Stars", "Доход BYN", "Доход с рекламы", "Апелляции",
        "Модерация стилей", "styleModerationSheet", "data-style-decision",
        "avatarMarkup(item.display_name", "setInterval(loadLiveData, 1000)",
    )
    for token in required:
        assert token in html
    assert "Stars | ${Number(revenueByn).toFixed(2)} BYN" in html


def test_api_has_style_workflow_and_live_byn_fields() -> None:
    source = (ROOT / "app" / "api.py").read_text(encoding="utf-8")
    for path in (
        '/styles/constructor', '/styles/mine', '/styles/{style_id}/submit',
        '/styles/search', '/chats/{chat_id}/response-style',
        '/admin/styles', '/admin/styles/{style_id}/decision',
    ):
        assert path in source
    for field in ("revenueByn", "advertisingRevenueByn", "stylesPending", "premiumUsers", "active7"):
        assert field in source


def test_v23_completion_patch_covers_all_residual_items() -> None:
    from app.command_catalog import BUILTIN_COMMANDS
    anime = {key: value for key, value in BUILTIN_COMMANDS.items() if key.startswith("anime_")}
    assert len(anime) == 180
    assert len({row["naruto_response"] for row in anime.values()}) == 180
    assert all(len(row["naruto_response"]) > 220 for row in anime.values())
    assert all("Готово" not in str(row) for row in anime.values())

    bot_source = (ROOT / "app" / "bot.py").read_text(encoding="utf-8")
    api_source = (ROOT / "app" / "api.py").read_text(encoding="utf-8")
    panel = (STATIC / "index.html").read_text(encoding="utf-8")
    admin = (STATIC / "admin.html").read_text(encoding="utf-8")
    assert 'style != "custom" and config.get("_response_overridden")' in bot_source
    assert 'command_key=f"custom_{command.id}"' in bot_source
    assert "constructor-template-custom-key" in panel
    assert "constructorSelectedTemplateKey" in panel
    assert "templates.slice(0, 12)" not in admin
    assert '"code": row.code if row.status == "approved" else None' in api_source
    assert 'return {"ok": True, "status": row.status, "code": None}' in api_source
    assert "event_user_id = _event_user_id(row)" in api_source
    assert 'return "Системное событие"' in api_source

    env = (ROOT / ".env.example").read_text(encoding="utf-8")
    for key in ("TELEGRAM_STAR_USD_RATE", "NBRB_USD_RATE_URL", "USD_BYN_FALLBACK", "FX_REFRESH_SECONDS"):
        assert f"{key}=" in env
