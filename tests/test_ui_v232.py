from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
ADMIN = (ROOT / 'app/static/admin.html').read_text(encoding='utf-8')
INDEX = (ROOT / 'app/static/index.html').read_text(encoding='utf-8')


def test_admin_overview_uses_two_column_block_grid():
    assert '.metrics,.dashboard-grid{grid-template-columns:repeat(2,minmax(0,1fr))' in ADMIN
    assert '<div class="dashboard-grid">' in ADMIN
    assert "metric('AniCoin'" in ADMIN


def test_admin_overview_icons_are_unique():
    metric_icons = re.findall(r"metric\([^\n]+?'(metric-[a-z0-9-]+)'(?:,\s*(?:true|false))?\)", ADMIN)
    action_icons = re.findall(r"dashboardCard\([^\n]+?'(action-[a-z0-9-]+)'", ADMIN)
    icons = metric_icons + action_icons
    assert len(metric_icons) == 17
    assert len(action_icons) == 14
    assert len(icons) == len(set(icons))
    for icon_id in icons:
        assert f'<symbol id="{icon_id}"' in ADMIN


def test_account_menu_has_wiki_before_faq():
    wiki = INDEX.index("['wiki', 'i-menu-wiki'")
    faq = INDEX.index("['faq', 'i-menu-faq'")
    assert wiki < faq
    assert '<symbol id="i-menu-wiki"' in INDEX


def test_wiki_contains_commands_automod_and_search():
    assert 'function wikiContent()' in INDEX
    assert 'Команды модерации' in INDEX
    assert 'Игровые техники' in INDEX
    assert 'Автоматическая модерация' in INDEX
    assert 'id="wiki-search"' in INDEX
    assert "filterWiki(event.target.value)" in INDEX
