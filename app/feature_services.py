from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.defaults import default_chat_settings
from app.models import (
    Appeal,
    BackupSnapshot,
    CaseEvidence,
    Chat,
    CustomCommand,
    GameCommand,
    Membership,
    ModerationCase,
    ModerationLog,
    ModerationRule,
    ModeratorPerformance,
    ModeratorShift,
    PermissionOverride,
    Report,
    RoleAssignmentHistory,
    RPCommand,
    SecurityIncident,
    StaffProbation,
    WeeklyReportSnapshot,
)
from app.roles import normalize_role, role_level


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


CASE_ACTIONS = {
    "warn", "mute", "ban", "kick", "quarantine",
    "restrict_media", "restrict_links", "restrict_commands",
    "penalty_status", "role_change", "global_block", "case",
}


async def create_moderation_case(
    session: AsyncSession,
    *,
    chat_id: int,
    actor_id: int | None,
    target_id: int | None,
    action: str,
    reason: str,
    duration_seconds: int | None = None,
    source_message_id: int | None = None,
    details: dict[str, Any] | None = None,
    evidence: dict[str, Any] | None = None,
) -> ModerationCase:
    severity = "critical" if action in {"ban", "global_block"} else "high" if action in {"mute", "kick", "quarantine"} else "normal"
    row = ModerationCase(
        code="pending",
        chat_id=chat_id,
        actor_id=actor_id,
        target_id=target_id,
        action=action,
        reason=reason or "Причина не указана",
        duration_seconds=duration_seconds,
        source_message_id=source_message_id,
        severity=severity,
        metadata_json=details or {},
    )
    session.add(row)
    await session.flush()
    row.code = f"AG-{row.id:06d}"
    if evidence:
        await add_case_evidence(session, row, evidence)
    await session.flush()
    return row


async def add_case_evidence(
    session: AsyncSession,
    case: ModerationCase,
    evidence: dict[str, Any],
) -> CaseEvidence:
    item = CaseEvidence(
        case_id=case.id,
        chat_id=case.chat_id,
        message_id=evidence.get("message_id"),
        author_id=evidence.get("author_id"),
        text=(evidence.get("text") or evidence.get("caption") or None),
        media=list(evidence.get("media") or []),
        snapshot=dict(evidence),
    )
    session.add(item)
    case.evidence_count = int(case.evidence_count or 0) + 1
    await session.flush()
    return item


async def close_case(
    session: AsyncSession,
    *,
    case_id: int,
    actor_id: int,
    status: str = "closed",
) -> ModerationCase:
    row = await session.get(ModerationCase, case_id)
    if row is None:
        raise ValueError("Дело не найдено")
    row.status = status
    row.closed_at = utcnow()
    row.closed_by = actor_id
    await session.flush()
    return row


async def create_appeal(
    session: AsyncSession,
    *,
    chat_id: int,
    user_id: int,
    text: str,
    case_id: int | None = None,
) -> Appeal:
    case: ModerationCase | None = None
    if case_id:
        case = await session.get(ModerationCase, case_id)
        if case is None or case.chat_id != chat_id:
            raise ValueError("Дело для апелляции не найдено")
    elif user_id:
        case = await session.scalar(
            select(ModerationCase)
            .where(
                ModerationCase.chat_id == chat_id,
                ModerationCase.target_id == user_id,
                ModerationCase.status.in_(["open", "appealed"]),
            )
            .order_by(ModerationCase.id.desc())
            .limit(1)
        )
    row = Appeal(
        case_id=case.id if case else None,
        chat_id=chat_id,
        user_id=user_id,
        text=text.strip() or "Наказание выдано ошибочно",
    )
    session.add(row)
    if case:
        case.status = "appealed"
    await session.flush()
    return row


async def decide_appeal(
    session: AsyncSession,
    *,
    appeal_id: int,
    reviewer_id: int,
    decision: str,
    note: str = "",
) -> Appeal:
    row = await session.get(Appeal, appeal_id)
    if row is None:
        raise ValueError("Апелляция не найдена")
    case = await session.get(ModerationCase, row.case_id) if row.case_id else None
    if case and case.actor_id == reviewer_id:
        raise PermissionError("Апелляцию должен рассматривать другой сотрудник")
    row.status = "accepted" if decision == "accept" else "rejected" if decision == "reject" else "changed"
    row.decision = decision
    row.decision_note = note.strip() or None
    row.reviewer_id = reviewer_id
    row.reviewed_at = utcnow()
    if case:
        case.status = "reversed" if decision == "accept" else "changed" if decision == "change" else "closed"
        case.closed_at = utcnow()
        case.closed_by = reviewer_id
        if case.actor_id:
            performance = await get_or_create_performance(session, case.chat_id, case.actor_id)
            if decision == "accept":
                performance.reversed_actions += 1
                performance.accepted_appeals += 1
                performance.rating = max(0, performance.rating - 8)
            elif decision == "reject":
                performance.confirmed_actions += 1
                performance.rating = min(100, performance.rating + 1)
    await session.flush()
    return row


async def get_or_create_performance(
    session: AsyncSession,
    chat_id: int,
    user_id: int,
) -> ModeratorPerformance:
    row = await session.scalar(
        select(ModeratorPerformance).where(
            ModeratorPerformance.chat_id == chat_id,
            ModeratorPerformance.user_id == user_id,
        )
    )
    if row is None:
        row = ModeratorPerformance(chat_id=chat_id, user_id=user_id)
        session.add(row)
        await session.flush()
    return row


async def record_moderator_action(
    session: AsyncSession,
    *,
    chat_id: int,
    actor_id: int | None,
    action: str,
    settings: dict[str, Any],
) -> None:
    if not actor_id or not settings.get("moderator_rating_enabled", True):
        return
    membership = await session.scalar(
        select(Membership).where(Membership.chat_id == chat_id, Membership.user_id == actor_id)
    )
    if membership is None or role_level(membership.role) < 2:
        return
    performance = await get_or_create_performance(session, chat_id, actor_id)
    performance.actions_count += 1
    probation = await session.scalar(
        select(StaffProbation).where(
            StaffProbation.chat_id == chat_id,
            StaffProbation.user_id == actor_id,
            StaffProbation.status == "active",
        ).order_by(StaffProbation.id.desc()).limit(1)
    )
    if probation:
        probation.actions_count += 1
    shift = await session.scalar(
        select(ModeratorShift).where(
            ModeratorShift.chat_id == chat_id,
            ModeratorShift.user_id == actor_id,
            ModeratorShift.starts_at <= utcnow(),
            ModeratorShift.ends_at >= utcnow(),
            ModeratorShift.status.in_(["scheduled", "active"]),
        ).order_by(ModeratorShift.id.desc()).limit(1)
    )
    if shift:
        shift.status = "active"
        if action == "warn":
            shift.warnings_issued += 1
        elif action == "mute":
            shift.mutes_issued += 1
        elif action == "purge":
            shift.messages_deleted += 1

    if not settings.get("moderator_abuse_detection_enabled", True):
        return
    window = max(60, int(settings.get("abuse_window_seconds", 300)))
    limit = max(3, int(settings.get("abuse_action_limit", 10)))
    since = utcnow() - timedelta(seconds=window)
    count = int(await session.scalar(
        select(func.count()).select_from(ModerationLog).where(
            ModerationLog.chat_id == chat_id,
            ModerationLog.actor_id == actor_id,
            ModerationLog.created_at >= since,
            ModerationLog.action.in_(["ban", "kick", "mute", "role_change", "penalty_status"]),
        )
    ) or 0)
    if count >= limit:
        existing = await session.scalar(
            select(SecurityIncident).where(
                SecurityIncident.chat_id == chat_id,
                SecurityIncident.kind == "admin_abuse",
                SecurityIncident.actor_id == actor_id,
                SecurityIncident.status == "open",
                SecurityIncident.created_at >= since,
            ).limit(1)
        )
        if existing is None:
            session.add(SecurityIncident(
                chat_id=chat_id,
                kind="admin_abuse",
                severity="critical",
                actor_id=actor_id,
                details={"actions": count, "window_seconds": window, "limit": limit},
            ))
            if settings.get("abuse_auto_suspend", True):
                performance.suspended_until = utcnow() + timedelta(hours=1)
                performance.rating = max(0, performance.rating - 15)


async def permission_override(
    session: AsyncSession,
    *,
    chat_id: int,
    user_id: int,
    permission: str,
) -> PermissionOverride | None:
    row = await session.scalar(
        select(PermissionOverride).where(
            PermissionOverride.chat_id == chat_id,
            PermissionOverride.user_id == user_id,
            PermissionOverride.permission == permission,
        )
    )
    if row and row.expires_at and _as_utc(row.expires_at) <= utcnow():
        await session.delete(row)
        await session.flush()
        return None
    return row


async def ensure_action_permission(
    session: AsyncSession,
    *,
    chat_id: int,
    user_id: int,
    action: str,
    duration_seconds: int | None,
) -> None:
    performance = await session.scalar(
        select(ModeratorPerformance).where(
            ModeratorPerformance.chat_id == chat_id,
            ModeratorPerformance.user_id == user_id,
        )
    )
    if performance and performance.suspended_until and _as_utc(performance.suspended_until) > utcnow():
        raise PermissionError("Опасные полномочия временно приостановлены системой контроля")
    row = await permission_override(session, chat_id=chat_id, user_id=user_id, permission=action)
    if row is None:
        return
    if not row.allowed:
        raise PermissionError(f"Индивидуальное право «{action}» отключено")
    if row.limit_value is not None and duration_seconds not in (None, 0) and duration_seconds > row.limit_value:
        raise PermissionError("Запрошенный срок превышает индивидуальный лимит")


async def start_staff_probation(
    session: AsyncSession,
    *,
    chat_id: int,
    user_id: int,
    role: str,
    actor_id: int,
    days: int,
) -> StaffProbation:
    active = await session.scalar(
        select(StaffProbation).where(
            StaffProbation.chat_id == chat_id,
            StaffProbation.user_id == user_id,
            StaffProbation.status == "active",
        ).order_by(StaffProbation.id.desc()).limit(1)
    )
    if active:
        active.ends_at = utcnow() + timedelta(days=max(1, days))
        active.role = normalize_role(role)
        return active
    row = StaffProbation(
        chat_id=chat_id,
        user_id=user_id,
        role=normalize_role(role),
        started_by=actor_id,
        ends_at=utcnow() + timedelta(days=max(1, days)),
    )
    session.add(row)
    await session.flush()
    return row


async def create_shift(
    session: AsyncSession,
    *,
    chat_id: int,
    user_id: int,
    actor_id: int,
    starts_at: datetime,
    ends_at: datetime,
    temporary_role: str | None = None,
) -> ModeratorShift:
    if ends_at <= starts_at:
        raise ValueError("Конец смены должен быть позже начала")
    membership = await session.scalar(select(Membership).where(Membership.chat_id == chat_id, Membership.user_id == user_id))
    row = ModeratorShift(
        chat_id=chat_id,
        user_id=user_id,
        assigned_by=actor_id,
        starts_at=starts_at,
        ends_at=ends_at,
        temporary_role=normalize_role(temporary_role) if temporary_role else None,
        previous_role=membership.role if membership else "member",
    )
    session.add(row)
    await session.flush()
    return row


async def set_security_mode(
    session: AsyncSession,
    *,
    chat_id: int,
    actor_id: int,
    kind: str,
    enabled: bool,
    reason: str = "",
    duration_seconds: int | None = None,
) -> SecurityIncident:
    chat = await session.get(Chat, chat_id)
    if chat is None:
        raise ValueError("Беседа не найдена")
    settings = default_chat_settings()
    settings.update(chat.settings or {})
    if kind == "anti_raid":
        settings["anti_raid_enabled"] = enabled
        settings["raid_lockdown_enabled"] = enabled
        if enabled:
            settings["captcha_enabled"] = True
    elif kind == "emergency":
        settings["emergency_mode_enabled"] = enabled
        settings["emergency_reason"] = reason.strip()
        settings["emergency_mode_until"] = (
            (utcnow() + timedelta(seconds=duration_seconds)).isoformat()
            if enabled and duration_seconds else None
        )
        settings["chat_locked"] = enabled
    else:
        raise ValueError("Неизвестный режим безопасности")
    chat.settings = settings
    row = SecurityIncident(
        chat_id=chat_id,
        kind=kind,
        severity="critical" if enabled else "info",
        actor_id=actor_id,
        status="open" if enabled else "resolved",
        details={"enabled": enabled, "reason": reason, "duration_seconds": duration_seconds},
        resolved_at=None if enabled else utcnow(),
        resolved_by=None if enabled else actor_id,
    )
    session.add(row)
    await session.flush()
    return row


async def generate_weekly_report(
    session: AsyncSession,
    *,
    chat_id: int,
    generated_by: int | None = None,
) -> WeeklyReportSnapshot:
    end = utcnow()
    start = end - timedelta(days=7)
    actions = int(await session.scalar(select(func.count()).select_from(ModerationLog).where(ModerationLog.chat_id == chat_id, ModerationLog.created_at >= start)) or 0)
    reports = int(await session.scalar(select(func.count()).select_from(Report).where(Report.chat_id == chat_id, Report.created_at >= start)) or 0)
    messages = int(await session.scalar(select(func.coalesce(func.sum(Membership.message_count), 0)).where(Membership.chat_id == chat_id)) or 0)
    new_members = int(await session.scalar(select(func.count()).select_from(Membership).where(Membership.chat_id == chat_id, Membership.joined_at >= start)) or 0)
    warnings = int(await session.scalar(select(func.count()).select_from(ModerationLog).where(ModerationLog.chat_id == chat_id, ModerationLog.created_at >= start, ModerationLog.action == "warn")) or 0)
    mutes = int(await session.scalar(select(func.count()).select_from(ModerationLog).where(ModerationLog.chat_id == chat_id, ModerationLog.created_at >= start, ModerationLog.action == "mute")) or 0)
    bans = int(await session.scalar(select(func.count()).select_from(ModerationLog).where(ModerationLog.chat_id == chat_id, ModerationLog.created_at >= start, ModerationLog.action == "ban")) or 0)
    appeals = int(await session.scalar(select(func.count()).select_from(Appeal).where(Appeal.chat_id == chat_id, Appeal.created_at >= start)) or 0)
    accepted_appeals = int(await session.scalar(select(func.count()).select_from(Appeal).where(Appeal.chat_id == chat_id, Appeal.created_at >= start, Appeal.status == "accepted")) or 0)
    top_moderator = await session.execute(
        select(ModerationLog.actor_id, func.count(ModerationLog.id).label("count"))
        .where(ModerationLog.chat_id == chat_id, ModerationLog.created_at >= start, ModerationLog.actor_id.is_not(None))
        .group_by(ModerationLog.actor_id).order_by(func.count(ModerationLog.id).desc()).limit(1)
    )
    top_row = top_moderator.first()
    payload = {
        "messages_total_observed": messages,
        "new_members": new_members,
        "moderation_actions": actions,
        "reports": reports,
        "warnings": warnings,
        "mutes": mutes,
        "bans": bans,
        "appeals": appeals,
        "accepted_appeals": accepted_appeals,
        "top_moderator_id": top_row[0] if top_row else None,
        "top_moderator_actions": top_row[1] if top_row else 0,
    }
    row = WeeklyReportSnapshot(chat_id=chat_id, period_start=start, period_end=end, payload=payload, generated_by=generated_by)
    session.add(row)
    await session.flush()
    return row


async def run_operations_maintenance(session: AsyncSession) -> list[dict[str, Any]]:
    """Advance scheduled v20 systems and return one-shot events for the bot.

    The worker is intentionally idempotent: every transition changes a status or
    a chat setting, so the same event is not emitted again on the next pass.
    """
    now = utcnow()
    events: list[dict[str, Any]] = []

    # Start scheduled moderator shifts and apply their temporary role.
    starting = (await session.scalars(
        select(ModeratorShift).where(
            ModeratorShift.status == "scheduled",
            ModeratorShift.starts_at <= now,
            ModeratorShift.ends_at > now,
        )
    )).all()
    for shift in starting:
        shift.status = "active"
        membership = await session.scalar(
            select(Membership).where(
                Membership.chat_id == shift.chat_id,
                Membership.user_id == shift.user_id,
            )
        )
        if membership and shift.temporary_role:
            old_role = normalize_role(membership.role)
            new_role = normalize_role(shift.temporary_role)
            if old_role != new_role:
                if not shift.previous_role:
                    shift.previous_role = old_role
                membership.role = new_role
                session.add(RoleAssignmentHistory(
                    chat_id=shift.chat_id,
                    user_id=shift.user_id,
                    actor_id=shift.assigned_by,
                    old_role=old_role,
                    new_role=new_role,
                    temporary_until=shift.ends_at,
                    reason="Временная роль на смену",
                    source="shift",
                ))
        events.append({"kind": "shift_started", "chat_id": shift.chat_id, "user_id": shift.user_id, "shift_id": shift.id})

    # Finish shifts and restore the role that existed before the shift.
    ending = (await session.scalars(
        select(ModeratorShift).where(
            ModeratorShift.status.in_(["scheduled", "active"]),
            ModeratorShift.ends_at <= now,
        )
    )).all()
    for shift in ending:
        shift.status = "completed"
        membership = await session.scalar(
            select(Membership).where(
                Membership.chat_id == shift.chat_id,
                Membership.user_id == shift.user_id,
            )
        )
        if membership and shift.temporary_role and shift.previous_role:
            old_role = normalize_role(membership.role)
            temporary = normalize_role(shift.temporary_role)
            restored = normalize_role(shift.previous_role)
            if old_role == temporary and old_role != restored:
                membership.role = restored
                session.add(RoleAssignmentHistory(
                    chat_id=shift.chat_id,
                    user_id=shift.user_id,
                    actor_id=shift.assigned_by,
                    old_role=old_role,
                    new_role=restored,
                    temporary_until=None,
                    reason="Смена завершена",
                    source="shift_auto_restore",
                ))
        events.append({
            "kind": "shift_completed", "chat_id": shift.chat_id, "user_id": shift.user_id,
            "shift_id": shift.id, "reports_handled": shift.reports_handled,
            "warnings_issued": shift.warnings_issued, "mutes_issued": shift.mutes_issued,
            "messages_deleted": shift.messages_deleted,
        })

    # Expired probation does not silently confirm a staff member: it awaits a
    # decision from a senior administrator in the admin panel.
    expired_probations = (await session.scalars(
        select(StaffProbation).where(
            StaffProbation.status == "active",
            StaffProbation.ends_at <= now,
        )
    )).all()
    for probation in expired_probations:
        probation.status = "awaiting_review"
        events.append({
            "kind": "probation_review", "chat_id": probation.chat_id,
            "user_id": probation.user_id, "probation_id": probation.id,
        })

    # Automatically end emergency mode when its deadline expires.
    chats = (await session.scalars(select(Chat).where(Chat.is_active.is_(True)))).all()
    for chat in chats:
        values = default_chat_settings()
        values.update(chat.settings or {})
        until_raw = values.get("emergency_mode_until")
        if values.get("emergency_mode_enabled") and until_raw:
            try:
                until = datetime.fromisoformat(str(until_raw).replace("Z", "+00:00"))
                until = _as_utc(until)
            except (TypeError, ValueError):
                until = None
            if until and until <= now:
                values["emergency_mode_enabled"] = False
                values["emergency_mode_until"] = None
                values["chat_locked"] = False
                chat.settings = values
                open_incidents = (await session.scalars(
                    select(SecurityIncident).where(
                        SecurityIncident.chat_id == chat.id,
                        SecurityIncident.kind == "emergency",
                        SecurityIncident.status == "open",
                    )
                )).all()
                for incident in open_incidents:
                    incident.status = "resolved"
                    incident.resolved_at = now
                events.append({"kind": "emergency_expired", "chat_id": chat.id})

        # Generate one weekly snapshot when enabled and no report was created in
        # the previous seven days. It can still be generated manually at any time.
        if values.get("weekly_reports_enabled", True):
            latest = await session.scalar(
                select(WeeklyReportSnapshot).where(
                    WeeklyReportSnapshot.chat_id == chat.id,
                ).order_by(WeeklyReportSnapshot.created_at.desc()).limit(1)
            )
            if latest is None or (_as_utc(latest.created_at) or now) <= now - timedelta(days=7):
                report = await generate_weekly_report(session, chat_id=chat.id, generated_by=None)
                if latest is not None:
                    events.append({"kind": "weekly_report", "chat_id": chat.id, "report_id": report.id, "payload": report.payload})

        # Keep a rolling automatic backup. Manual backups remain available in the
        # panel and are not removed by this routine.
        if values.get("backups_enabled", True):
            latest_auto = await session.scalar(
                select(BackupSnapshot).where(
                    BackupSnapshot.chat_id == chat.id,
                    BackupSnapshot.kind == "automatic",
                ).order_by(BackupSnapshot.created_at.desc()).limit(1)
            )
            if latest_auto is None or (_as_utc(latest_auto.created_at) or now) <= now - timedelta(days=1):
                snapshot = await create_backup_snapshot(session, chat_id=chat.id, created_by=0, kind="automatic")
                events.append({"kind": "backup_created", "chat_id": chat.id, "snapshot_id": snapshot.id})

            retention = max(1, int(values.get("backup_retention_count", 20)))
            auto_rows = (await session.scalars(
                select(BackupSnapshot).where(
                    BackupSnapshot.chat_id == chat.id,
                    BackupSnapshot.kind == "automatic",
                ).order_by(BackupSnapshot.created_at.desc())
            )).all()
            for old in auto_rows[retention:]:
                await session.delete(old)

    await session.flush()
    return events


async def build_chat_backup_payload(session: AsyncSession, chat_id: int) -> dict[str, Any]:
    chat = await session.get(Chat, chat_id)
    if chat is None:
        raise ValueError("Беседа не найдена")
    memberships = (await session.scalars(select(Membership).where(Membership.chat_id == chat_id))).all()
    rules = (await session.scalars(select(ModerationRule).where(ModerationRule.chat_id == chat_id))).all()
    custom = (await session.scalars(select(CustomCommand).where(CustomCommand.chat_id == chat_id))).all()
    games = (await session.scalars(select(GameCommand).where(GameCommand.chat_id == chat_id))).all()
    rp = (await session.scalars(select(RPCommand).where(RPCommand.chat_id == chat_id))).all()
    overrides = (await session.scalars(select(PermissionOverride).where(PermissionOverride.chat_id == chat_id))).all()
    return {
        "version": 20,
        "chat": {"id": chat.id, "title": chat.title, "settings": chat.settings or {}},
        "memberships": [
            {"user_id": r.user_id, "role": r.role, "penalty_status": r.penalty_status, "warnings": r.warnings}
            for r in memberships
        ],
        "rules": [{"name": r.name, "condition": r.condition, "actions": r.actions, "enabled": r.enabled, "is_premium": r.is_premium, "created_by": r.created_by} for r in rules],
        "custom_commands": [{"name": r.name, "trigger": r.trigger, "action_type": r.action_type, "duration_seconds": r.duration_seconds, "response_template": r.response_template, "required_role": r.required_role, "target_mode": r.target_mode, "cooldown_seconds": r.cooldown_seconds, "delete_trigger": r.delete_trigger, "require_reason": r.require_reason, "enabled": r.enabled, "creator_id": r.creator_id} for r in custom],
        "game_commands": [{"name": r.name, "trigger": r.trigger, "command_type": r.command_type, "response_template": r.response_template, "response_variants": r.response_variants, "reward_xp": r.reward_xp, "reward_coins": r.reward_coins, "cooldown_seconds": r.cooldown_seconds, "access": r.access, "enabled": r.enabled, "creator_id": r.creator_id} for r in games],
        "rp_commands": [{"name": r.name, "aliases": r.aliases, "response_template": r.response_template, "response_variants": r.response_variants, "enabled": r.enabled, "is_premium": r.is_premium, "cooldown_seconds": r.cooldown_seconds, "access": r.access, "reward_xp": r.reward_xp, "reward_coins": r.reward_coins, "created_by": r.created_by} for r in rp],
        "permission_overrides": [{"user_id": r.user_id, "permission": r.permission, "allowed": r.allowed, "limit_value": r.limit_value, "assigned_by": r.assigned_by} for r in overrides],
        "created_at": utcnow().isoformat(),
    }


async def create_backup_snapshot(
    session: AsyncSession,
    *,
    chat_id: int,
    created_by: int,
    kind: str = "manual",
) -> BackupSnapshot:
    payload = await build_chat_backup_payload(session, chat_id)
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    row = BackupSnapshot(
        chat_id=chat_id,
        kind=kind,
        payload=payload,
        checksum=hashlib.sha256(encoded).hexdigest(),
        created_by=created_by,
    )
    session.add(row)
    await session.flush()
    return row


async def restore_backup_snapshot(
    session: AsyncSession,
    *,
    snapshot_id: int,
    actor_id: int,
) -> BackupSnapshot:
    row = await session.get(BackupSnapshot, snapshot_id)
    if row is None or row.chat_id is None:
        raise ValueError("Резервная копия не найдена")
    payload = row.payload or {}
    chat = await session.get(Chat, row.chat_id)
    if chat is None:
        raise ValueError("Беседа не найдена")
    chat.settings = dict((payload.get("chat") or {}).get("settings") or {})

    membership_map = {int(x["user_id"]): x for x in payload.get("memberships", []) if x.get("user_id") is not None}
    for membership in (await session.scalars(select(Membership).where(Membership.chat_id == row.chat_id))).all():
        saved = membership_map.get(membership.user_id)
        if saved:
            membership.role = normalize_role(saved.get("role"))
            membership.penalty_status = str(saved.get("penalty_status") or "none")
            membership.warnings = max(0, int(saved.get("warnings") or 0))

    await session.execute(delete(ModerationRule).where(ModerationRule.chat_id == row.chat_id))
    for item in payload.get("rules", []):
        session.add(ModerationRule(chat_id=row.chat_id, **item))

    await session.execute(delete(PermissionOverride).where(PermissionOverride.chat_id == row.chat_id))
    for item in payload.get("permission_overrides", []):
        session.add(PermissionOverride(chat_id=row.chat_id, **item))

    row.restored_at = utcnow()
    row.restored_by = actor_id
    await session.flush()
    return row
