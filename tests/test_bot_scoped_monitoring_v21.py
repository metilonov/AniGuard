from __future__ import annotations

from pathlib import Path


def test_v21_monitoring_uses_container_sources() -> None:
    source = Path("app/monitoring.py").read_text(encoding="utf-8")
    assert "memory.current" in source
    assert "memory.usage_in_bytes" in source
    assert "cpu.stat" in source
    assert "cpuacct.usage" in source
    assert "python-process" in source


def test_v21_disk_is_project_scoped() -> None:
    source = Path("app/monitoring.py").read_text(encoding="utf-8")
    assert "_directory_size_bytes" in source
    assert "bothost_project_dir" in source
    assert "shutil.disk_usage" not in source
    assert 'disk_scope="project-directory"' in source


def test_v22_uses_project_domain_without_agent_configuration() -> None:
    config = Path("app/config.py").read_text(encoding="utf-8")
    env_example = Path(".env.example").read_text(encoding="utf-8")
    monitoring = Path("app/monitoring.py").read_text(encoding="utf-8")
    for name in (
        "BOTHOST_AGENT_URL",
        "BOTHOST_AGENT_FALLBACK_URLS",
        "BOTHOST_AGENT_TIMEOUT_SECONDS",
        "BOTHOST_AGENT_RETRY_SECONDS",
    ):
        assert name not in config
        assert name not in env_example
        assert name not in monitoring
    for name in ("BOTHOST_PROJECT_DIR", "BOTHOST_DISK_SCAN_INTERVAL_SECONDS"):
        assert name in config
        assert name in env_example
    assert "/api/admin/live" in monitoring


def test_v21_admin_labels_bot_scoped_metrics() -> None:
    html = Path("app/static/admin.html").read_text(encoding="utf-8")
    assert "Память AniGuard" in html
    assert "Процессор AniGuard" in html
    assert "Файлы AniGuard" in html
    assert "только файлы AniGuard" in html
    assert "setInterval(loadLiveData, 1000)" in html
