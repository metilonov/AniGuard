from __future__ import annotations

from datetime import timedelta
from typing import Any

from aiogram.enums import ChatMemberStatus
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import bot, moderation_response
from app.config import get_settings
from app.db import SessionFactory, get_session
from app.models import (
    AdminActionLog,
    BlockedEntity,
    Chat,
    CustomCommand,
    EntityAccessGrant,
    GameCommand,
    Membership,
    ModerationLog,
    ModerationRule,
    Report,
    RPCommand,
    User,
)
from app.pricing import PREMIUM_PLANS
from app.schemas import (
    ActionRequest,
    AdminBlockRequest,
    AdminPremiumRequest,
    CustomCommandCreate,
    CustomCommandUpdate,
    GameCommandCreate,
    GameCommandUpdate,
    MemberRoleUpdate,
    PremiumInvoiceRequest,
    ReportDecision,
    RPCommandCreate,
    RPCommandUpdate,
    RuleCreate,
    RuleUpdate,
    SettingsUpdate,
)
from app.security import TelegramUser, current_telegram_user
from app.services import (
    create_invoice_link,
    dashboard_data,
    get_chat_or_raise,
    ensure_entity_available,
    entity_has_premium,
    get_block_record,
    get_merged_settings,
    has_premium_access,
    is_premium,
    list_admin_chats,
    perform_action,
    require_chat_admin,
    set_entity_block,
    set_entity_premium,
    update_chat_settings,
    as_utc,
    utcnow,
)


router = APIRouter(prefix="/api")
settings = get_settings()


def http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, PermissionError):
        return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    if isinstance(exc, ValueError):
        return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))


async def ensure_admin(chat_id: int, user: TelegramUser) -> None:
    """Allow a Telegram chat admin or a global AniGuard owner."""
    if user.id in settings.admin_ids:
        return
    try:
        async with SessionFactory() as session:
            await ensure_entity_available(session, user_id=user.id, chat_id=chat_id)
            await session.commit()
        await require_chat_admin(bot, chat_id, user.id)
    except Exception as exc:
        raise http_error(exc) from exc


def ensure_bot_admin(user: TelegramUser) -> None:
    if user.id not in settings.admin_ids:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Раздел доступен только владельцу AniGuard")


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/me")
async def get_me(
    user: TelegramUser = Depends(current_telegram_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    blocked = await get_block_record(session, "user", user.id)
    return {
        "id": user.id,
        "first_name": user.first_name,
        "last_name": user.last_name,
        "username": user.username,
        "is_bot_admin": user.id in settings.admin_ids,
        "premium": await entity_has_premium(session, "user", user.id),
        "blocked": blocked is not None,
        "block_reason": blocked.reason if blocked else None,
    }


@router.get("/chats")
async def get_chats(
    user: TelegramUser = Depends(current_telegram_user),
    session: AsyncSession = Depends(get_session),
) -> list[dict[str, Any]]:
    if user.id in settings.admin_ids:
        rows = (
            await session.scalars(
                select(Chat).where(Chat.is_active.is_(True)).order_by(Chat.updated_at.desc()).limit(300)
            )
        ).all()
        return [
            {
                "id": row.id,
                "title": row.title,
                "username": row.username,
                "premium": await entity_has_premium(session, "chat", row.id),
                "premium_until": row.premium_until.isoformat() if row.premium_until else None,
                "premium_plan": row.premium_plan,
            }
            for row in rows
        ]
    await ensure_entity_available(session, user_id=user.id)
    return await list_admin_chats(session, bot, user.id)


@router.get("/chats/{chat_id}/dashboard")
async def get_dashboard(
    chat_id: int,
    user: TelegramUser = Depends(current_telegram_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    await ensure_admin(chat_id, user)
    try:
        data = await dashboard_data(session, chat_id)
        data["chat"]["chat_premium"] = data["chat"]["premium"]
        data["chat"]["user_premium"] = await entity_has_premium(session, "user", user.id)
        data["chat"]["premium"] = await has_premium_access(session, chat_id=chat_id, user_id=user.id)
        return data
    except Exception as exc:
        raise http_error(exc) from exc


@router.get("/chats/{chat_id}/profile")
async def get_chat_profile(
    chat_id: int,
    user: TelegramUser = Depends(current_telegram_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Return the group profile used by the Mini App.

    Telegram may reject some optional lookups when the bot lacks a permission;
    in that case the endpoint still returns the data stored by AniGuard.
    """
    await ensure_admin(chat_id, user)
    chat = await get_chat_or_raise(session, chat_id)
    db_members = await session.scalar(
        select(func.count()).select_from(Membership).where(Membership.chat_id == chat_id)
    ) or 0
    result: dict[str, Any] = {
        "id": chat.id,
        "title": chat.title,
        "username": chat.username,
        "description": "",
        "invite_link": None,
        "members": db_members,
        "premium": await has_premium_access(session, chat_id=chat_id, user_id=user.id),
        "premium_until": chat.premium_until.isoformat() if chat.premium_until else None,
        "owner": None,
        "administrators": [],
    }
    try:
        telegram_chat = await bot.get_chat(chat_id)
        result["title"] = telegram_chat.title or chat.title
        result["username"] = telegram_chat.username or chat.username
        result["description"] = telegram_chat.description or ""
        result["invite_link"] = telegram_chat.invite_link
    except Exception:
        pass
    try:
        result["members"] = await bot.get_chat_member_count(chat_id)
    except Exception:
        pass
    try:
        admins = await bot.get_chat_administrators(chat_id)
        admin_rows: list[dict[str, Any]] = []
        for admin in admins:
            admin_user = admin.user
            status_value = getattr(admin.status, "value", str(admin.status))
            row = {
                "id": admin_user.id,
                "first_name": admin_user.first_name,
                "last_name": admin_user.last_name,
                "username": admin_user.username,
                "status": status_value,
                "custom_title": getattr(admin, "custom_title", None),
            }
            admin_rows.append(row)
            if admin.status == ChatMemberStatus.CREATOR:
                result["owner"] = row
        result["administrators"] = admin_rows
    except Exception:
        pass
    return result


@router.get("/chats/{chat_id}/settings")
async def get_settings_endpoint(
    chat_id: int,
    user: TelegramUser = Depends(current_telegram_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    await ensure_admin(chat_id, user)
    try:
        chat = await get_chat_or_raise(session, chat_id)
        return {
            "settings": await get_merged_settings(session, chat_id),
            "premium": await has_premium_access(session, chat_id=chat_id, user_id=user.id),
            "chat_premium": is_premium(chat),
            "user_premium": await entity_has_premium(session, "user", user.id),
        }
    except Exception as exc:
        raise http_error(exc) from exc


@router.put("/chats/{chat_id}/settings")
async def put_settings(
    chat_id: int,
    payload: SettingsUpdate,
    user: TelegramUser = Depends(current_telegram_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    await ensure_admin(chat_id, user)
    try:
        premium_keys = {
            "premium_quarantine", "premium_cases", "premium_schedule", "premium_stats",
            "premium_smart_spam_enabled", "premium_hidden_links_enabled",
            "premium_suspicious_newcomers_enabled", "premium_auto_quarantine_enabled",
            "premium_punishment_ladder_enabled", "premium_adaptive_protection_enabled",
            "premium_raid_lockdown_enabled", "premium_newcomer_quarantine_seconds",
            "premium_ladder_mute_seconds", "premium_adaptive_trigger_count",
            "premium_adaptive_window_seconds",
        }
        premium_access = await has_premium_access(session, chat_id=chat_id, user_id=user.id)
        current_settings = await get_merged_settings(session, chat_id)
        if not premium_access and any(
            key in premium_keys and value != current_settings.get(key)
            for key, value in payload.settings.items()
        ):
            raise PermissionError("Эти настройки доступны только с AniGuard Premium")
        updated = await update_chat_settings(session, chat_id, payload.settings)
        await session.commit()
        return {"settings": updated, "premium": premium_access}
    except Exception as exc:
        await session.rollback()
        raise http_error(exc) from exc


@router.get("/chats/{chat_id}/members")
async def get_members(
    chat_id: int,
    q: str = Query(default="", max_length=64),
    limit: int = Query(default=50, ge=1, le=200),
    user: TelegramUser = Depends(current_telegram_user),
    session: AsyncSession = Depends(get_session),
) -> list[dict[str, Any]]:
    await ensure_admin(chat_id, user)
    stmt = (
        select(Membership, User)
        .join(User, User.id == Membership.user_id)
        .where(Membership.chat_id == chat_id)
        .order_by(Membership.last_seen_at.desc())
        .limit(limit)
    )
    if q:
        pattern = f"%{q.lower()}%"
        stmt = stmt.where(
            func.lower(User.first_name).like(pattern)
            | func.lower(func.coalesce(User.username, "")).like(pattern)
        )
    rows = (await session.execute(stmt)).all()
    return [
        {
            "id": db_user.id,
            "username": db_user.username,
            "first_name": db_user.first_name,
            "role": membership.role,
            "warnings": membership.warnings,
            "penalty_points": membership.penalty_points,
            "messages": membership.message_count,
            "xp": membership.xp,
            "coins": membership.coins,
            "joined_at": membership.joined_at.isoformat(),
            "muted_until": membership.muted_until.isoformat() if membership.muted_until else None,
            "quarantined_until": membership.quarantined_until.isoformat() if membership.quarantined_until else None,
        }
        for membership, db_user in rows
    ]


@router.patch("/chats/{chat_id}/members/{member_id}/role")
async def update_member_role(
    chat_id: int,
    member_id: int,
    payload: MemberRoleUpdate,
    user: TelegramUser = Depends(current_telegram_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    await ensure_admin(chat_id, user)
    try:
        tg_member = await bot.get_chat_member(chat_id, member_id)
        if tg_member.status in {ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR}:
            raise PermissionError("Роль администратора Telegram управляется настройками самой группы")
    except PermissionError:
        raise
    except Exception:
        pass
    membership = await session.scalar(
        select(Membership).where(Membership.chat_id == chat_id, Membership.user_id == member_id)
    )
    if membership is None:
        raise HTTPException(status_code=404, detail="Участник не найден")
    membership.role = payload.role
    await session.commit()
    return {"user_id": member_id, "role": membership.role}


@router.post("/chats/{chat_id}/actions")
async def run_action(
    chat_id: int,
    payload: ActionRequest,
    user: TelegramUser = Depends(current_telegram_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    await ensure_admin(chat_id, user)
    try:
        if payload.action == "purge":
            chat = await get_chat_or_raise(session, chat_id)
            last_message_id = int((chat.settings or {}).get("last_message_id", 0))
            count = int(payload.amount or 25)
            if last_message_id <= 0:
                raise ValueError("The bot has not recorded any messages for deletion yet")
            ids = list(range(max(1, last_message_id - count + 1), last_message_id + 1))
            deleted_count = 0
            try:
                await bot.delete_messages(chat_id, ids)
                deleted_count = len(ids)
            except Exception:
                for message_id in ids:
                    try:
                        await bot.delete_message(chat_id, message_id)
                        deleted_count += 1
                    except Exception:
                        continue
            result = await perform_action(
                session,
                bot,
                chat_id=chat_id,
                actor_id=user.id,
                action="purge",
                amount=deleted_count,
                reason=payload.reason,
            )
        else:
            result = await perform_action(
                session,
                bot,
                chat_id=chat_id,
                actor_id=user.id,
                action=payload.action,
                target_id=payload.target_id,
                duration_seconds=payload.duration_seconds,
                amount=payload.amount,
                reason=payload.reason,
            )
        await session.commit()
        if payload.target_id is not None and payload.action not in {"purge", "slow", "lock", "unlock", "susanoo"}:
            try:
                group_settings = await get_merged_settings(session, chat_id)
                await bot.send_message(
                    chat_id,
                    moderation_response(
                        actor_id=user.id,
                        target_id=payload.target_id,
                        action=payload.action,
                        duration_seconds=result.get("duration_seconds"),
                        reason=str(result.get("reason") or payload.reason or "Причина не указана"),
                        show_duration=bool(group_settings.get("show_moderation_duration", True)),
                        show_reason=bool(group_settings.get("show_moderation_reason", True)),
                    ),
                )
            except Exception:
                pass
        return result
    except Exception as exc:
        await session.rollback()
        raise http_error(exc) from exc


@router.get("/chats/{chat_id}/logs")
async def get_logs(
    chat_id: int,
    limit: int = Query(default=100, ge=1, le=500),
    user: TelegramUser = Depends(current_telegram_user),
    session: AsyncSession = Depends(get_session),
) -> list[dict[str, Any]]:
    await ensure_admin(chat_id, user)
    rows = (
        await session.scalars(
            select(ModerationLog)
            .where(ModerationLog.chat_id == chat_id)
            .order_by(ModerationLog.created_at.desc())
            .limit(limit)
        )
    ).all()
    return [
        {
            "id": row.id,
            "action": row.action,
            "actor_id": row.actor_id,
            "target_id": row.target_id,
            "reason": row.reason,
            "duration_seconds": row.duration_seconds,
            "details": row.details,
            "created_at": row.created_at.isoformat(),
        }
        for row in rows
    ]


@router.get("/chats/{chat_id}/reports")
async def get_reports(
    chat_id: int,
    status_filter: str = Query(default="open"),
    user: TelegramUser = Depends(current_telegram_user),
    session: AsyncSession = Depends(get_session),
) -> list[dict[str, Any]]:
    await ensure_admin(chat_id, user)
    stmt = select(Report).where(Report.chat_id == chat_id)
    if status_filter == "open":
        stmt = stmt.where(Report.status.in_(["new", "in_progress"]))
    rows = (await session.scalars(stmt.order_by(Report.created_at.desc()).limit(200))).all()
    return [
        {
            "id": row.id,
            "reporter_id": row.reporter_id,
            "target_id": row.target_id,
            "message_id": row.message_id,
            "reason": row.reason,
            "status": row.status,
            "assigned_to": row.assigned_to,
            "created_at": row.created_at.isoformat(),
        }
        for row in rows
    ]


@router.post("/chats/{chat_id}/reports/{report_id}/decision")
async def decide_report(
    chat_id: int,
    report_id: int,
    payload: ReportDecision,
    user: TelegramUser = Depends(current_telegram_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    await ensure_admin(chat_id, user)
    report = await session.get(Report, report_id)
    if not report or report.chat_id != chat_id:
        raise HTTPException(status_code=404, detail="Report not found")
    try:
        result: dict[str, Any] = {"decision": payload.decision}
        if payload.decision != "dismiss":
            result = await perform_action(
                session,
                bot,
                chat_id=chat_id,
                actor_id=user.id,
                action=payload.decision,
                target_id=report.target_id,
                duration_seconds=payload.duration_seconds,
                reason=payload.reason or f"Report AG-{report.id}",
            )
        report.status = "closed"
        report.assigned_to = user.id
        from app.services import utcnow
        report.closed_at = utcnow()
        await session.commit()
        return result
    except Exception as exc:
        await session.rollback()
        raise http_error(exc) from exc


@router.get("/chats/{chat_id}/rp")
async def list_rp(
    chat_id: int,
    user: TelegramUser = Depends(current_telegram_user),
    session: AsyncSession = Depends(get_session),
) -> list[dict[str, Any]]:
    await ensure_admin(chat_id, user)
    rows = (
        await session.scalars(
            select(RPCommand).where(RPCommand.chat_id == chat_id).order_by(RPCommand.name)
        )
    ).all()
    return [
        {
            "id": row.id,
            "name": row.name,
            "aliases": row.aliases,
            "response_template": row.response_template,
            "response_variants": row.response_variants,
            "enabled": row.enabled,
            "is_premium": row.is_premium,
            "cooldown_seconds": row.cooldown_seconds,
            "access": row.access,
            "reward_xp": row.reward_xp,
            "reward_coins": row.reward_coins,
        }
        for row in rows
    ]


@router.post("/chats/{chat_id}/rp", status_code=201)
async def create_rp(
    chat_id: int,
    payload: RPCommandCreate,
    user: TelegramUser = Depends(current_telegram_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    await ensure_admin(chat_id, user)
    chat = await get_chat_or_raise(session, chat_id)
    count = await session.scalar(select(func.count()).select_from(RPCommand).where(RPCommand.chat_id == chat_id))
    premium_access = await has_premium_access(session, chat_id=chat_id, user_id=user.id)
    if not premium_access and (count or 0) >= 25:
        raise HTTPException(status_code=403, detail="The free plan allows up to 25 RP commands")
    if payload.is_premium and not premium_access:
        raise HTTPException(status_code=403, detail="Premium RP commands require an active subscription")
    existing = await session.scalar(
        select(RPCommand).where(RPCommand.chat_id == chat_id, func.lower(RPCommand.name) == payload.name.lower())
    )
    if existing:
        raise HTTPException(status_code=409, detail="An RP command with this name already exists")
    row = RPCommand(chat_id=chat_id, created_by=user.id, **payload.model_dump())
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return {"id": row.id, **payload.model_dump()}


@router.patch("/chats/{chat_id}/rp/{command_id}")
async def update_rp(
    chat_id: int,
    command_id: int,
    payload: RPCommandUpdate,
    user: TelegramUser = Depends(current_telegram_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    await ensure_admin(chat_id, user)
    row = await session.get(RPCommand, command_id)
    if not row or row.chat_id != chat_id:
        raise HTTPException(status_code=404, detail="RP command not found")
    chat = await get_chat_or_raise(session, chat_id)
    update = payload.model_dump(exclude_unset=True)
    if update.get("is_premium") and not await has_premium_access(session, chat_id=chat_id, user_id=user.id):
        raise HTTPException(status_code=403, detail="Premium RP commands require an active subscription")
    for key, value in update.items():
        setattr(row, key, value)
    await session.commit()
    return {"id": row.id, **update}


@router.delete("/chats/{chat_id}/rp/{command_id}", status_code=204)
async def delete_rp(
    chat_id: int,
    command_id: int,
    user: TelegramUser = Depends(current_telegram_user),
    session: AsyncSession = Depends(get_session),
) -> None:
    await ensure_admin(chat_id, user)
    row = await session.get(RPCommand, command_id)
    if not row or row.chat_id != chat_id:
        raise HTTPException(status_code=404, detail="RP command not found")
    await session.delete(row)
    await session.commit()


@router.get("/chats/{chat_id}/rules")
async def list_rules(
    chat_id: int,
    user: TelegramUser = Depends(current_telegram_user),
    session: AsyncSession = Depends(get_session),
) -> list[dict[str, Any]]:
    await ensure_admin(chat_id, user)
    rows = (
        await session.scalars(
            select(ModerationRule).where(ModerationRule.chat_id == chat_id).order_by(ModerationRule.id.desc())
        )
    ).all()
    return [
        {
            "id": row.id,
            "name": row.name,
            "condition": row.condition,
            "actions": row.actions,
            "enabled": row.enabled,
            "is_premium": row.is_premium,
        }
        for row in rows
    ]


@router.post("/chats/{chat_id}/rules", status_code=201)
async def create_rule(
    chat_id: int,
    payload: RuleCreate,
    user: TelegramUser = Depends(current_telegram_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    await ensure_admin(chat_id, user)
    chat = await get_chat_or_raise(session, chat_id)
    count = await session.scalar(select(func.count()).select_from(ModerationRule).where(ModerationRule.chat_id == chat_id))
    premium_access = await has_premium_access(session, chat_id=chat_id, user_id=user.id)
    if not premium_access and (count or 0) >= 5:
        raise HTTPException(status_code=403, detail="The free plan allows up to 5 moderation rules")
    if premium_access and (count or 0) >= 100:
        raise HTTPException(status_code=403, detail="The Premium plan allows up to 100 moderation rules")
    if payload.is_premium and not premium_access:
        raise HTTPException(status_code=403, detail="Premium rules require an active subscription")
    row = ModerationRule(chat_id=chat_id, created_by=user.id, **payload.model_dump())
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return {"id": row.id, **payload.model_dump()}


@router.patch("/chats/{chat_id}/rules/{rule_id}")
async def update_rule(
    chat_id: int,
    rule_id: int,
    payload: RuleUpdate,
    user: TelegramUser = Depends(current_telegram_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    await ensure_admin(chat_id, user)
    row = await session.get(ModerationRule, rule_id)
    if not row or row.chat_id != chat_id:
        raise HTTPException(status_code=404, detail="Rule not found")
    update = payload.model_dump(exclude_unset=True)
    for key, value in update.items():
        setattr(row, key, value)
    await session.commit()
    return {"id": row.id, **update}


@router.delete("/chats/{chat_id}/rules/{rule_id}", status_code=204)
async def delete_rule(
    chat_id: int,
    rule_id: int,
    user: TelegramUser = Depends(current_telegram_user),
    session: AsyncSession = Depends(get_session),
) -> None:
    await ensure_admin(chat_id, user)
    row = await session.get(ModerationRule, rule_id)
    if not row or row.chat_id != chat_id:
        raise HTTPException(status_code=404, detail="Rule not found")
    await session.delete(row)
    await session.commit()


@router.get("/top-chats")
async def top_chats(
    limit: int = Query(default=20, ge=1, le=100),
    user: TelegramUser = Depends(current_telegram_user),
    session: AsyncSession = Depends(get_session),
) -> list[dict[str, Any]]:
    rows = (
        await session.execute(
            select(Chat, func.count(Membership.id).label("members"))
            .outerjoin(Membership, Membership.chat_id == Chat.id)
            .where(Chat.is_active.is_(True))
            .group_by(Chat.id)
            .order_by(func.count(Membership.id).desc())
            .limit(limit)
        )
    ).all()
    return [
        {
            "place": index,
            "id": chat.id,
            "title": chat.title,
            "members": members,
            "premium": is_premium(chat),
        }
        for index, (chat, members) in enumerate(rows, 1)
    ]


@router.get("/premium/plans")
async def premium_plans(
    user: TelegramUser = Depends(current_telegram_user),
) -> list[dict[str, Any]]:
    return [
        {
            "code": plan.code,
            "title": plan.title,
            "days": plan.days,
            "stars": plan.stars,
            "badge": plan.badge,
            "description": plan.description,
        }
        for plan in PREMIUM_PLANS.values()
    ]


@router.post("/chats/{chat_id}/premium/invoice")
async def premium_invoice(
    chat_id: int,
    payload: PremiumInvoiceRequest,
    user: TelegramUser = Depends(current_telegram_user),
) -> dict[str, str]:
    await ensure_admin(chat_id, user)
    try:
        link, invoice_payload = await create_invoice_link(
            bot,
            user_id=user.id,
            chat_id=chat_id,
            plan_code=payload.plan_code,
        )
        return {"invoice_url": link, "payload": invoice_payload}
    except Exception as exc:
        raise http_error(exc) from exc


# ---------------------------------------------------------------------------
# Premium custom moderation commands
# ---------------------------------------------------------------------------

@router.get("/chats/{chat_id}/custom-commands")
async def list_custom_commands(
    chat_id: int,
    user: TelegramUser = Depends(current_telegram_user),
    session: AsyncSession = Depends(get_session),
) -> list[dict[str, Any]]:
    await ensure_admin(chat_id, user)
    rows = (
        await session.scalars(
            select(CustomCommand).where(CustomCommand.chat_id == chat_id).order_by(CustomCommand.id.desc())
        )
    ).all()
    result: list[dict[str, Any]] = []
    for row in rows:
        command_premium = await has_premium_access(
            session, chat_id=chat_id, user_id=row.creator_id
        )
        result.append({
            "id": row.id,
            "name": row.name,
            "trigger": row.trigger,
            "action_type": row.action_type,
            "duration_seconds": row.duration_seconds,
            "response_template": row.response_template,
            "required_role": row.required_role,
            "target_mode": row.target_mode,
            "cooldown_seconds": row.cooldown_seconds,
            "delete_trigger": row.delete_trigger,
            "require_reason": row.require_reason,
            "enabled": row.enabled,
            "frozen": not command_premium,
            "creator_id": row.creator_id,
        })
    return result


@router.post("/chats/{chat_id}/custom-commands", status_code=201)
async def create_custom_command(
    chat_id: int,
    payload: CustomCommandCreate,
    user: TelegramUser = Depends(current_telegram_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    await ensure_admin(chat_id, user)
    if not await has_premium_access(session, chat_id=chat_id, user_id=user.id):
        raise HTTPException(status_code=403, detail="Конструктор команд модерации доступен с Premium")
    trigger = payload.trigger.strip().casefold().lstrip("/")
    if not trigger or "\n" in trigger:
        raise HTTPException(status_code=400, detail="Некорректный триггер команды")
    existing = await session.scalar(
        select(CustomCommand).where(CustomCommand.chat_id == chat_id, func.lower(CustomCommand.trigger) == trigger)
    )
    if existing:
        raise HTTPException(status_code=409, detail="Команда с таким триггером уже существует")
    data = payload.model_dump()
    data["trigger"] = trigger
    row = CustomCommand(chat_id=chat_id, creator_id=user.id, **data)
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return {"id": row.id, **data}


@router.patch("/chats/{chat_id}/custom-commands/{command_id}")
async def update_custom_command(
    chat_id: int,
    command_id: int,
    payload: CustomCommandUpdate,
    user: TelegramUser = Depends(current_telegram_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    await ensure_admin(chat_id, user)
    if not await has_premium_access(session, chat_id=chat_id, user_id=user.id):
        raise HTTPException(status_code=403, detail="Команды заморожены до продления Premium")
    row = await session.get(CustomCommand, command_id)
    if not row or row.chat_id != chat_id:
        raise HTTPException(status_code=404, detail="Кастомная команда не найдена")
    update = payload.model_dump(exclude_unset=True)
    if "trigger" in update and update["trigger"]:
        update["trigger"] = update["trigger"].strip().casefold().lstrip("/")
        duplicate = await session.scalar(
            select(CustomCommand).where(
                CustomCommand.chat_id == chat_id,
                func.lower(CustomCommand.trigger) == update["trigger"],
                CustomCommand.id != command_id,
            )
        )
        if duplicate:
            raise HTTPException(status_code=409, detail="Команда с таким триггером уже существует")
    for key, value in update.items():
        setattr(row, key, value)
    row.creator_id = user.id
    await session.commit()
    return {"id": row.id, "creator_id": row.creator_id, **update}


@router.delete("/chats/{chat_id}/custom-commands/{command_id}", status_code=204)
async def delete_custom_command(
    chat_id: int,
    command_id: int,
    user: TelegramUser = Depends(current_telegram_user),
    session: AsyncSession = Depends(get_session),
) -> None:
    await ensure_admin(chat_id, user)
    row = await session.get(CustomCommand, command_id)
    if not row or row.chat_id != chat_id:
        raise HTTPException(status_code=404, detail="Кастомная команда не найдена")
    await session.delete(row)
    await session.commit()


# ---------------------------------------------------------------------------
# Game commands
# ---------------------------------------------------------------------------

@router.get("/chats/{chat_id}/game-commands")
async def list_game_commands(
    chat_id: int,
    user: TelegramUser = Depends(current_telegram_user),
    session: AsyncSession = Depends(get_session),
) -> list[dict[str, Any]]:
    await ensure_admin(chat_id, user)
    rows = (
        await session.scalars(
            select(GameCommand).where(GameCommand.chat_id == chat_id).order_by(GameCommand.id.desc())
        )
    ).all()
    return [
        {
            "id": row.id,
            "name": row.name,
            "trigger": row.trigger,
            "command_type": row.command_type,
            "response_template": row.response_template,
            "response_variants": row.response_variants,
            "reward_xp": row.reward_xp,
            "reward_coins": row.reward_coins,
            "cooldown_seconds": row.cooldown_seconds,
            "access": row.access,
            "enabled": row.enabled,
        }
        for row in rows
    ]


@router.post("/chats/{chat_id}/game-commands", status_code=201)
async def create_game_command(
    chat_id: int,
    payload: GameCommandCreate,
    user: TelegramUser = Depends(current_telegram_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    await ensure_admin(chat_id, user)
    trigger = payload.trigger.strip().casefold().lstrip("/")
    if not trigger or "\n" in trigger:
        raise HTTPException(status_code=400, detail="Некорректный триггер игровой команды")
    existing = await session.scalar(
        select(GameCommand).where(GameCommand.chat_id == chat_id, func.lower(GameCommand.trigger) == trigger)
    )
    if existing:
        raise HTTPException(status_code=409, detail="Игровая команда с таким триггером уже существует")
    data = payload.model_dump()
    data["trigger"] = trigger
    row = GameCommand(chat_id=chat_id, creator_id=user.id, **data)
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return {"id": row.id, **data}


@router.patch("/chats/{chat_id}/game-commands/{command_id}")
async def update_game_command(
    chat_id: int,
    command_id: int,
    payload: GameCommandUpdate,
    user: TelegramUser = Depends(current_telegram_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    await ensure_admin(chat_id, user)
    row = await session.get(GameCommand, command_id)
    if not row or row.chat_id != chat_id:
        raise HTTPException(status_code=404, detail="Игровая команда не найдена")
    update = payload.model_dump(exclude_unset=True)
    if "trigger" in update and update["trigger"]:
        update["trigger"] = update["trigger"].strip().casefold().lstrip("/")
        if "\n" in update["trigger"]:
            raise HTTPException(status_code=400, detail="Некорректный триггер игровой команды")
        duplicate = await session.scalar(
            select(GameCommand).where(
                GameCommand.chat_id == chat_id,
                func.lower(GameCommand.trigger) == update["trigger"],
                GameCommand.id != command_id,
            )
        )
        if duplicate:
            raise HTTPException(status_code=409, detail="Игровая команда с таким триггером уже существует")
    for key, value in update.items():
        setattr(row, key, value)
    await session.commit()
    return {"id": row.id, **update}


@router.delete("/chats/{chat_id}/game-commands/{command_id}", status_code=204)
async def delete_game_command(
    chat_id: int,
    command_id: int,
    user: TelegramUser = Depends(current_telegram_user),
    session: AsyncSession = Depends(get_session),
) -> None:
    await ensure_admin(chat_id, user)
    row = await session.get(GameCommand, command_id)
    if not row or row.chat_id != chat_id:
        raise HTTPException(status_code=404, detail="Игровая команда не найдена")
    await session.delete(row)
    await session.commit()


# ---------------------------------------------------------------------------
# Global AniGuard owner panel
# ---------------------------------------------------------------------------

@router.get("/admin/overview")
async def admin_overview(
    user: TelegramUser = Depends(current_telegram_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    ensure_bot_admin(user)
    users = await session.scalar(select(func.count()).select_from(User)) or 0
    chats = await session.scalar(select(func.count()).select_from(Chat)) or 0
    block_rows = (
        await session.scalars(select(BlockedEntity).where(BlockedEntity.is_active.is_(True)))
    ).all()
    blocked = sum(
        1
        for row in block_rows
        if row.blocked_until is None or (as_utc(row.blocked_until) and as_utc(row.blocked_until) > utcnow())
    )
    grants = (await session.scalars(select(EntityAccessGrant))).all()
    premium_entities = {
        (row.entity_type, row.entity_id)
        for row in grants
        if row.is_lifetime or (as_utc(row.premium_until) and as_utc(row.premium_until) > utcnow())
    }
    paid_chats = (await session.scalars(select(Chat).where(Chat.premium_until.is_not(None)))).all()
    premium_entities.update(
        ("chat", row.id)
        for row in paid_chats
        if as_utc(row.premium_until) and as_utc(row.premium_until) > utcnow()
    )
    active_grants = len(premium_entities)
    custom = await session.scalar(select(func.count()).select_from(CustomCommand)) or 0
    return {
        "users": users,
        "chats": chats,
        "blocked": blocked,
        "active_premium": active_grants,
        "custom_commands": custom,
    }


@router.get("/admin/entities")
async def admin_entities(
    entity_type: str = Query(default="user", pattern="^(user|chat)$"),
    q: str = Query(default="", max_length=100),
    limit: int = Query(default=100, ge=1, le=300),
    user: TelegramUser = Depends(current_telegram_user),
    session: AsyncSession = Depends(get_session),
) -> list[dict[str, Any]]:
    ensure_bot_admin(user)
    if entity_type == "user":
        stmt = select(User).order_by(User.updated_at.desc()).limit(limit)
        if q:
            pattern = f"%{q.casefold()}%"
            if q.lstrip("-").isdigit():
                stmt = stmt.where(User.id == int(q))
            else:
                stmt = stmt.where(
                    func.lower(User.first_name).like(pattern)
                    | func.lower(func.coalesce(User.username, "")).like(pattern)
                )
        rows = (await session.scalars(stmt)).all()
        result = []
        for row in rows:
            grant = await session.scalar(select(EntityAccessGrant).where(EntityAccessGrant.entity_type == "user", EntityAccessGrant.entity_id == row.id))
            block = await get_block_record(session, "user", row.id)
            result.append({
                "id": row.id,
                "title": row.first_name,
                "username": row.username,
                "premium_until": grant.premium_until.isoformat() if grant and grant.premium_until else None,
                "premium_permanent": bool(grant and grant.is_lifetime),
                "blocked": block is not None,
                "block_reason": block.reason if block else None,
            })
        return result

    stmt = select(Chat).order_by(Chat.updated_at.desc()).limit(limit)
    if q:
        pattern = f"%{q.casefold()}%"
        if q.lstrip("-").isdigit():
            stmt = stmt.where(Chat.id == int(q))
        else:
            stmt = stmt.where(
                func.lower(Chat.title).like(pattern)
                | func.lower(func.coalesce(Chat.username, "")).like(pattern)
            )
    rows = (await session.scalars(stmt)).all()
    result = []
    for row in rows:
        grant = await session.scalar(select(EntityAccessGrant).where(EntityAccessGrant.entity_type == "chat", EntityAccessGrant.entity_id == row.id))
        block = await get_block_record(session, "chat", row.id)
        until = row.premium_until or (grant.premium_until if grant else None)
        result.append({
            "id": row.id,
            "title": row.title,
            "username": row.username,
            "premium_until": until.isoformat() if until else None,
            "premium_permanent": bool(grant and grant.is_lifetime),
            "blocked": block is not None,
            "block_reason": block.reason if block else None,
            "active": row.is_active,
        })
    return result


@router.post("/admin/premium")
async def admin_set_premium(
    payload: AdminPremiumRequest,
    user: TelegramUser = Depends(current_telegram_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    ensure_bot_admin(user)
    row = await set_entity_premium(
        session,
        entity_type=payload.entity_type,
        entity_id=payload.entity_id,
        days=payload.days,
        admin_id=user.id,
        permanent=payload.permanent,
        plan=payload.plan,
        note=payload.note,
    )
    await session.commit()
    return {
        "entity_type": row.entity_type,
        "entity_id": row.entity_id,
        "premium_until": row.premium_until.isoformat() if row.premium_until else None,
        "permanent": row.is_lifetime,
    }


@router.post("/admin/block")
async def admin_set_block(
    payload: AdminBlockRequest,
    user: TelegramUser = Depends(current_telegram_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    ensure_bot_admin(user)
    if payload.entity_type == "user" and payload.entity_id in settings.admin_ids and payload.blocked:
        raise HTTPException(status_code=400, detail="Нельзя заблокировать владельца AniGuard")
    row = await set_entity_block(
        session,
        entity_type=payload.entity_type,
        entity_id=payload.entity_id,
        blocked=payload.blocked,
        admin_id=user.id,
        reason=payload.reason,
        duration_seconds=payload.duration_seconds,
    )
    await session.commit()
    return {
        "entity_type": row.entity_type,
        "entity_id": row.entity_id,
        "blocked": row.is_active,
        "blocked_until": row.blocked_until.isoformat() if row.blocked_until else None,
    }


@router.get("/admin/logs")
async def admin_logs(
    limit: int = Query(default=100, ge=1, le=300),
    user: TelegramUser = Depends(current_telegram_user),
    session: AsyncSession = Depends(get_session),
) -> list[dict[str, Any]]:
    ensure_bot_admin(user)
    rows = (
        await session.scalars(select(AdminActionLog).order_by(AdminActionLog.id.desc()).limit(limit))
    ).all()
    return [
        {
            "id": row.id,
            "admin_id": row.admin_id,
            "action": row.action,
            "entity_type": row.entity_type,
            "entity_id": row.entity_id,
            "details": row.details,
            "created_at": row.created_at.isoformat(),
        }
        for row in rows
    ]
