from pathlib import Path


def test_v22_no_agent_network_calls() -> None:
    source = Path("app/monitoring.py").read_text(encoding="utf-8")
    assert "aiohttp" not in source
    assert "agent:8000" not in source
    assert "agent.bothost" not in source
    assert "msk1.bothost" not in source


def test_v22_admin_uses_same_origin_domain_api_every_second() -> None:
    html = Path("app/static/admin.html").read_text(encoding="utf-8")
    assert "api('/api/admin/live')" in html
    assert "setInterval(loadLiveData, 1000)" in html
    assert "API AniGuard через" in html
    assert "BotHost API недоступен" not in html


def test_v22_environment_has_only_required_monitor_limits() -> None:
    env = Path(".env.example").read_text(encoding="utf-8")
    assert "WEBAPP_URL=https://aniguard.bothost.tech" in env
    assert "BOTHOST_RAM_LIMIT_MB=2048" in env
    assert "BOTHOST_CPU_LIMIT=4" in env
    assert "BOTHOST_DISK_LIMIT_GB=15" in env
