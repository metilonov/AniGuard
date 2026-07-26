from __future__ import annotations

from datetime import timedelta
from typing import Any

from aiogram import Bot
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import bot
from app.db import get_session
from app.models import Chat, Membership, ModerationLog, ModerationRule, Report, RPCommand, User
from app.pricing import PREMIUM_PLANS
from app.schemas import (
    ActionRequest,
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
    get_merged_settings,
    is_premium,
    list_admin_chats,
    perform_action,
    require_chat_admin,
    update_chat_settings,
)


router = APIRouter(prefix="/api")


def http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, PermissionError):
        return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    if isinstance(exc, ValueError):
        return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))


async def ensure_admin(chat_id: int, user: TelegramUser) -> None:
    try:
        await require_chat_admin(bot, chat_id, user.id)
    except Exception as exc:
        raise http_error(exc) from exc


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/me")
async def get_me(user: TelegramUser = Depends(current_telegram_user)) -> dict[str, Any]:
    return {
        "id": user.id,
        "first_name": user.first_name,
        "last_name": user.last_name,
        "username": user.username,
    }


@router.get("/chats")
async def get_chats(
    user: TelegramUser = Depends(current_telegram_user),
    session: AsyncSession = Depends(get_session),
) -> list[dict[str, Any]]:
    return await list_admin_chats(session, bot, user.id)


@router.get("/chats/{chat_id}/dashboard")
async def get_dashboard(
    chat_id: int,
    user: TelegramUser = Depends(current_telegram_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    await ensure_admin(chat_id, user)
    try:
        return await dashboard_data(session, chat_id)
    except Exception as exc:
        raise http_error(exc) from exc


@router.get("/chats/{chat_id}/settings")
async def get_settings_endpoint(
    chat_id: int,
    user: TelegramUser = Depends(current_telegram_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    await ensure_admin(chat_id, user)
    try:
        chat = await get_chat_or_raise(session, chat_id)
        return {"settings": await get_merged_settings(session, chat_id), "premium": is_premium(chat)}
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
        updated = await update_chat_settings(session, chat_id, payload.settings)
        await session.commit()
        return {"settings": updated}
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
    if not is_premium(chat) and (count or 0) >= 25:
        raise HTTPException(status_code=403, detail="The free plan allows up to 25 RP commands")
    if payload.is_premium and not is_premium(chat):
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
    if update.get("is_premium") and not is_premium(chat):
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
    if not is_premium(chat) and (count or 0) >= 5:
        raise HTTPException(status_code=403, detail="The free plan allows up to 5 moderation rules")
    if is_premium(chat) and (count or 0) >= 100:
        raise HTTPException(status_code=403, detail="The Premium plan allows up to 100 moderation rules")
    if payload.is_premium and not is_premium(chat):
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
