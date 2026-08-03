from __future__ import annotations

from pathlib import Path
import os

os.environ.setdefault("BOT_TOKEN", "123456:TEST_TOKEN_FOR_UNIT_TESTS")

from app.defaults import default_chat_settings
from app.feature_services import CASE_ACTIONS
from app.models import (
    Appeal,
    BackupSnapshot,
    CaseEvidence,
    ModerationCase,
    ModeratorPerformance,
    ModeratorShift,
    PermissionOverride,
    ResourceSample,
    SecurityIncident,
    StaffProbation,
    WeeklyReportSnapshot,
)


def test_v20_defaults_cover_all_18_systems() -> None:
    settings = default_chat_settings()
    required = {
        "cases_enabled",
        "appeals_enabled",
        "punishment_ladder",
        "staff_probation_enabled",
        "moderator_rating_enabled",
        "moderator_abuse_detection_enabled",
        "anti_raid_auto_enabled",
        "emergency_mode_enabled",
        "evidence_capture_enabled",
        "report_merge_window_seconds",
        "moderator_shifts_enabled",
        "weekly_reports_enabled",
        "backups_enabled",
        "test_mode_enabled",
        "response_style",
    }
    assert required.issubset(settings)
    assert len(settings["punishment_ladder"]) == 6
    assert settings["report_categories"]
    assert "case" in settings["case_actions"]
    assert "case" in CASE_ACTIONS


def test_v20_tables_are_registered() -> None:
    names = {
        ModerationCase.__tablename__,
        CaseEvidence.__tablename__,
        Appeal.__tablename__,
        StaffProbation.__tablename__,
        ModeratorPerformance.__tablename__,
        PermissionOverride.__tablename__,
        SecurityIncident.__tablename__,
        ModeratorShift.__tablename__,
        WeeklyReportSnapshot.__tablename__,
        BackupSnapshot.__tablename__,
        ResourceSample.__tablename__,
    }
    assert names == {
        "moderation_cases",
        "case_evidence",
        "appeals",
        "staff_probations",
        "moderator_performance",
        "permission_overrides",
        "security_incidents",
        "moderator_shifts",
        "weekly_report_snapshots",
        "backup_snapshots",
        "resource_samples",
    }


def test_monitor_source_uses_local_container_and_domain_api() -> None:
    source = Path("app/monitoring.py").read_text(encoding="utf-8")
    config = Path("app/config.py").read_text(encoding="utf-8")
    assert "_collect_local" in source
    assert "resource-monitor-1s" in source
    assert "/api/admin/live" in source
    assert "http://agent:8000" not in source
    assert "BOTHOST_AGENT_URL" not in config


def test_admin_panel_has_one_second_live_updates_and_18_cards() -> None:
    html = Path("app/static/admin.html").read_text(encoding="utf-8")
    assert "setInterval(loadLiveData, 1000)" in html
    assert "/api/admin/live" in html
    assert "обновление 1 сек." in html
    for number in range(1, 19):
        assert f"[{number}," in html
    for action in (
        "ops-cases", "ops-appeals", "ops-ladder", "ops-probations",
        "ops-performance", "ops-permissions", "ops-antiraid",
        "ops-emergency", "ops-evidence", "ops-shifts", "ops-weekly",
        "ops-backups", "ops-testmode", "ops-rules", "ops-abuse",
        "ops-chatinfo", "ops-style", "ops-resources-history",
    ):
        assert action in html


def test_admin_routes_for_operations_are_present() -> None:
    source = Path("app/api.py").read_text(encoding="utf-8")
    paths = (
        "/admin/live",
        "/admin/system/resources",
        "/admin/cases",
        "/admin/appeals",
        "/admin/security/incidents",
        "/admin/chats/{chat_id}/anti-raid",
        "/admin/chats/{chat_id}/emergency",
        "/admin/chats/{chat_id}/test-mode",
        "/admin/chats/{chat_id}/punishment-ladder",
        "/admin/chats/{chat_id}/permission-overrides",
        "/admin/staff/probations",
        "/admin/staff/performance",
        "/admin/staff/shifts",
        "/admin/backups",
        "/admin/weekly-reports",
    )
    for path in paths:
        assert path in source
