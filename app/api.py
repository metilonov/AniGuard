from __future__ import annotations

import asyncio
import json
import os
import secrets
from datetime import timedelta
from io import BytesIO
from pathlib import Path
from typing import Any

from aiogram.enums import ChatMemberStatus
from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse, Response
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import bot, moderation_response, render_custom_template
from app.defaults import BASIC_MODERATION_COMMANDS, PREMIUM_SETTING_KEYS, default_basic_commands
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
    Payment,
    PromoCode,
    PromoRedemption,
    BroadcastJob,
    SystemSetting,
    UserWallet,
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
    BasicCommandUpdate,
    CaptchaSettingsUpdate,
    GroupRulesUpdate,
    WelcomeSettingsUpdate,
    AdminCoinRequest,
    AdminChatStateRequest,
    AdminChatSettingsRequest,
    AdminBroadcastRequest,
    AdminPromoCreateRequest,
    AdminPromoToggleRequest,
    AdminSystemSettingsRequest,
    AdminReportCloseRequest,
    AdminDirectMessageRequest,
    AdminBulkPremiumRequest,
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
    active_restrictions,
    ensure_membership,
    premium_access_details,
    upsert_user,
    upsert_chat,
    sync_chat_from_telegram,
    chat_avatar_url,
)


router = APIRouter(prefix="/api")
settings = get_settings()
UPLOAD_DIR = settings.data_dir / "uploads"
WELCOME_DIR = UPLOAD_DIR / "welcome"
ADMIN_AVATAR_DIR = UPLOAD_DIR / "admin"
WELCOME_DIR.mkdir(parents=True, exist_ok=True)
ADMIN_AVATAR_DIR.mkdir(parents=True, exist_ok=True)


def _admin_avatar_file() -> Path | None:
    for suffix in (".webp", ".png", ".jpg", ".jpeg"):
        candidate = ADMIN_AVATAR_DIR / f"avatar{suffix}"
        if candidate.is_file():
            return candidate
    return None


def _admin_avatar_url() -> str | None:
    file = _admin_avatar_file()
    if file is None:
        return None
    return f"/api/admin/avatar?v={int(file.stat().st_mtime)}"


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
        result: list[dict[str, Any]] = []
        for row in rows:
            try:
                telegram_chat = await bot.get_chat(row.id)
                row = await upsert_chat(session, telegram_chat)
            except Exception:
                pass
            details = await premium_access_details(session, chat_id=row.id, user_id=user.id)
            result.append({
                "id": row.id,
                "title": row.title,
                "username": row.username,
                "premium": details["active"],
                "premium_until": details["until"].isoformat() if details["until"] else None,
                "premium_plan": details["plan"],
                "premium_source": details["source"],
                "owner_id": details["owner_id"],
                "avatar_url": chat_avatar_url(row),
            })
        await session.commit()
        return result
    await ensure_entity_available(session, user_id=user.id)
    result = await list_admin_chats(session, bot, user.id)
    await session.commit()
    return result


@router.get("/chats/{chat_id}/dashboard")
async def get_dashboard(
    chat_id: int,
    user: TelegramUser = Depends(current_telegram_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    await ensure_admin(chat_id, user)
    try:
        data = await dashboard_data(session, chat_id)
        premium = await premium_access_details(session, chat_id=chat_id, user_id=user.id)
        data["chat"].update({
            "premium": premium["active"],
            "chat_premium": premium["group"]["active"],
            "owner_premium": premium["owner"]["active"],
            "user_premium": await entity_has_premium(session, "user", user.id),
            "premium_source": premium["source"],
            "premium_until": premium["until"].isoformat() if premium["until"] else None,
            "premium_plan": premium["plan"],
            "owner_id": premium["owner_id"],
        })
        return data
    except Exception as exc:
        raise http_error(exc) from exc


@router.get("/chats/{chat_id}/profile")
async def get_chat_profile(
    chat_id: int,
    user: TelegramUser = Depends(current_telegram_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Return current Telegram group data and synchronise its creator."""
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
        "owner": None,
        "administrators": [],
        "avatar_url": chat_avatar_url(chat),
    }
    try:
        telegram_chat = await bot.get_chat(chat_id)
        result["title"] = telegram_chat.title or chat.title
        result["username"] = telegram_chat.username or chat.username
        result["description"] = telegram_chat.description or ""
        result["invite_link"] = telegram_chat.invite_link
        chat.title = result["title"]
        chat.username = result["username"]
        await upsert_chat(session, telegram_chat)
        result["avatar_url"] = chat_avatar_url(chat)
    except Exception:
        pass
    try:
        result["members"] = await bot.get_chat_member_count(chat_id)
    except Exception:
        pass
    try:
        admins = await bot.get_chat_administrators(chat_id)
        admin_rows: list[dict[str, Any]] = []
        merged_settings = await get_merged_settings(session, chat_id)
        for admin in admins:
            admin_user = admin.user
            await upsert_user(session, admin_user)
            status_value = getattr(admin.status, "value", str(admin.status))
            role = "owner" if admin.status == ChatMemberStatus.CREATOR else "admin"
            await ensure_membership(session, chat_id, admin_user.id, role)
            row = {
                "id": admin_user.id,
                "first_name": admin_user.first_name,
                "last_name": admin_user.last_name,
                "full_name": " ".join(filter(None, [admin_user.first_name, admin_user.last_name])),
                "username": admin_user.username,
                "status": status_value,
                "custom_title": getattr(admin, "custom_title", None),
                "avatar_url": f"/api/avatars/{admin_user.id}",
            }
            admin_rows.append(row)
            if admin.status == ChatMemberStatus.CREATOR:
                result["owner"] = row
                merged_settings["owner_user_id"] = admin_user.id
        chat.settings = merged_settings
        result["administrators"] = admin_rows
    except Exception:
        pass

    premium = await premium_access_details(session, chat_id=chat_id, user_id=user.id)
    result.update({
        "premium": premium["active"],
        "premium_until": premium["until"].isoformat() if premium["until"] else None,
        "premium_plan": premium["plan"],
        "premium_source": premium["source"],
        "premium_lifetime": premium["lifetime"],
        "owner_premium": premium["owner"]["active"],
        "owner_premium_until": premium["owner"]["until"].isoformat() if premium["owner"]["until"] else None,
        "group_premium": premium["group"]["active"],
        "group_premium_until": premium["group"]["until"].isoformat() if premium["group"]["until"] else None,
    })
    await session.commit()
    return result


@router.get("/chats/{chat_id}/settings")
async def get_settings_endpoint(
    chat_id: int,
    user: TelegramUser = Depends(current_telegram_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    await ensure_admin(chat_id, user)
    try:
        await get_chat_or_raise(session, chat_id)
        premium = await premium_access_details(session, chat_id=chat_id, user_id=user.id)
        return {
            "settings": await get_merged_settings(session, chat_id),
            "premium": premium["active"],
            "chat_premium": premium["group"]["active"],
            "owner_premium": premium["owner"]["active"],
            "user_premium": await entity_has_premium(session, "user", user.id),
            "premium_source": premium["source"],
            "premium_until": premium["until"].isoformat() if premium["until"] else None,
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
        premium_keys = PREMIUM_SETTING_KEYS
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
    limit: int = Query(default=200, ge=1, le=500),
    user: TelegramUser = Depends(current_telegram_user),
    session: AsyncSession = Depends(get_session),
) -> list[dict[str, Any]]:
    await ensure_admin(chat_id, user)
    stmt = (
        select(Membership, User)
        .join(User, User.id == Membership.user_id)
        .where(Membership.chat_id == chat_id)
        .order_by(Membership.role.desc(), Membership.last_seen_at.desc())
        .limit(limit)
    )
    if q:
        pattern = f"%{q.lower()}%"
        stmt = stmt.where(
            func.lower(User.first_name).like(pattern)
            | func.lower(func.coalesce(User.last_name, "")).like(pattern)
            | func.lower(func.coalesce(User.username, "")).like(pattern)
        )
    rows = (await session.execute(stmt)).all()
    result: list[dict[str, Any]] = []
    for membership, db_user in rows:
        telegram_status: str | None = None
        try:
            tg_member = await bot.get_chat_member(chat_id, db_user.id)
            telegram_status = getattr(tg_member.status, "value", str(tg_member.status))
            if tg_member.status == ChatMemberStatus.CREATOR:
                membership.role = "owner"
            elif tg_member.status == ChatMemberStatus.ADMINISTRATOR:
                membership.role = "admin"
        except Exception:
            pass
        result.append({
            "id": db_user.id,
            "username": db_user.username,
            "first_name": db_user.first_name,
            "last_name": db_user.last_name,
            "full_name": " ".join(filter(None, [db_user.first_name, db_user.last_name])),
            "avatar_url": f"/api/avatars/{db_user.id}",
            "role": membership.role,
            "telegram_status": telegram_status,
            "warnings": membership.warnings,
            "penalty_points": membership.penalty_points,
            "messages": membership.message_count,
            "xp": membership.xp,
            "coins": membership.coins,
            "joined_at": membership.joined_at.isoformat(),
            "last_seen_at": membership.last_seen_at.isoformat(),
            "muted_until": membership.muted_until.isoformat() if membership.muted_until else None,
            "quarantined_until": membership.quarantined_until.isoformat() if membership.quarantined_until else None,
        })
    await session.flush()
    return result


@router.get("/chats/{chat_id}/members/{member_id}")
async def get_member_details(
    chat_id: int,
    member_id: int,
    user: TelegramUser = Depends(current_telegram_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    await ensure_admin(chat_id, user)
    row = await session.execute(
        select(Membership, User)
        .join(User, User.id == Membership.user_id)
        .where(Membership.chat_id == chat_id, Membership.user_id == member_id)
    )
    found = row.first()
    if not found:
        raise HTTPException(status_code=404, detail="Участник не найден")
    membership, db_user = found
    restrictions = sorted(await active_restrictions(session, chat_id=chat_id, user_id=member_id))
    telegram: dict[str, Any] = {}
    try:
        tg_member = await bot.get_chat_member(chat_id, member_id)
        telegram = {
            "status": getattr(tg_member.status, "value", str(tg_member.status)),
            "custom_title": getattr(tg_member, "custom_title", None),
            "is_member": getattr(tg_member, "is_member", None),
            "until_date": getattr(tg_member, "until_date", None).isoformat() if getattr(tg_member, "until_date", None) else None,
            "can_send_messages": getattr(tg_member, "can_send_messages", None),
            "can_send_photos": getattr(tg_member, "can_send_photos", None),
            "can_send_videos": getattr(tg_member, "can_send_videos", None),
            "can_send_other_messages": getattr(tg_member, "can_send_other_messages", None),
        }
    except Exception:
        telegram = {"status": None}
    return {
        "id": db_user.id,
        "username": db_user.username,
        "first_name": db_user.first_name,
        "last_name": db_user.last_name,
        "full_name": " ".join(filter(None, [db_user.first_name, db_user.last_name])),
        "avatar_url": f"/api/avatars/{db_user.id}",
        "role": membership.role,
        "warnings": membership.warnings,
        "penalty_points": membership.penalty_points,
        "messages": membership.message_count,
        "xp": membership.xp,
        "coins": membership.coins,
        "joined_at": membership.joined_at.isoformat(),
        "last_seen_at": membership.last_seen_at.isoformat(),
        "muted_until": membership.muted_until.isoformat() if membership.muted_until else None,
        "quarantined_until": membership.quarantined_until.isoformat() if membership.quarantined_until else None,
        "active_restrictions": restrictions,
        "telegram": telegram,
    }


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
                command_settings = group_settings.get("basic_moderation_commands") or {}
                selected = next(
                    (value for value in command_settings.values() if isinstance(value, dict) and value.get("action") == payload.action),
                    None,
                )
                if selected and selected.get("response"):
                    chat = await get_chat_or_raise(session, chat_id)
                    response_text = render_custom_template(
                        str(selected["response"]),
                        actor_id=user.id,
                        target_id=payload.target_id,
                        command_name=str(selected.get("name") or payload.action),
                        duration_seconds=result.get("duration_seconds"),
                        reason=str(result.get("reason") or payload.reason or "Причина не указана"),
                        chat_title=chat.title,
                    )
                else:
                    response_text = moderation_response(
                        actor_id=user.id,
                        target_id=payload.target_id,
                        action=payload.action,
                        duration_seconds=result.get("duration_seconds"),
                        reason=str(result.get("reason") or payload.reason or "Причина не указана"),
                        show_duration=bool(group_settings.get("show_moderation_duration", True)),
                        show_reason=bool(group_settings.get("show_moderation_reason", True)),
                    )
                await bot.send_message(chat_id, response_text)
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


@router.get("/admin/avatar", include_in_schema=False)
async def admin_avatar() -> FileResponse:
    file = _admin_avatar_file()
    if file is None:
        raise HTTPException(status_code=404, detail="Аватар администратора не настроен")
    return FileResponse(file, headers={"Cache-Control": "public, max-age=3600"})


@router.post("/admin/profile/avatar")
async def upload_admin_avatar(
    avatar: UploadFile = File(...),
    user: TelegramUser = Depends(current_telegram_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    ensure_bot_admin(user)
    allowed = {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
    }
    suffix = allowed.get(avatar.content_type or "")
    if suffix is None:
        raise HTTPException(status_code=400, detail="Поддерживаются JPG, PNG и WEBP")
    content = await avatar.read()
    if not content or len(content) > 5 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Размер изображения должен быть от 1 байта до 5 МБ")
    for existing in ADMIN_AVATAR_DIR.glob("avatar.*"):
        existing.unlink(missing_ok=True)
    target = ADMIN_AVATAR_DIR / f"avatar{suffix}"
    target.write_bytes(content)
    await _write_admin_log(
        session,
        admin_id=user.id,
        action="admin_avatar_updated",
        details={"filename": Path(avatar.filename or target.name).name},
    )
    await session.commit()
    return {"avatar_url": _admin_avatar_url()}


@router.delete("/admin/profile/avatar", status_code=204)
async def delete_admin_avatar(
    user: TelegramUser = Depends(current_telegram_user),
    session: AsyncSession = Depends(get_session),
) -> Response:
    ensure_bot_admin(user)
    for existing in ADMIN_AVATAR_DIR.glob("avatar.*"):
        existing.unlink(missing_ok=True)
    await _write_admin_log(session, admin_id=user.id, action="admin_avatar_deleted")
    await session.commit()
    return Response(status_code=204)

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


# ---------------------------------------------------------------------------
# Extended Mini App integrations: avatars, members, group content, CAPTCHA,
# editable built-in moderation commands, and effective Premium information.
# ---------------------------------------------------------------------------


@router.get("/avatars/{user_id}", include_in_schema=False)
async def get_avatar(user_id: int) -> Response:
    """Proxy a Telegram profile photo without exposing the bot token."""
    try:
        photos = await bot.get_user_profile_photos(user_id, limit=1)
        if not photos.photos:
            raise HTTPException(status_code=404, detail="У пользователя нет аватара")
        file_id = photos.photos[0][-1].file_id
        telegram_file = await bot.get_file(file_id)
        if not telegram_file.file_path:
            raise HTTPException(status_code=404, detail="Аватар недоступен")
        buffer = BytesIO()
        await bot.download_file(telegram_file.file_path, destination=buffer)
        return Response(
            content=buffer.getvalue(),
            media_type="image/jpeg",
            headers={"Cache-Control": "public, max-age=900"},
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=404, detail="Аватар недоступен") from exc


@router.get("/chat-avatars/{chat_id}", include_in_schema=False)
async def get_chat_avatar(chat_id: int) -> Response:
    """Proxy the current Telegram group photo without exposing the bot token."""
    try:
        async with SessionFactory() as session:
            chat = await session.get(Chat, chat_id)
            if chat is None or not (chat.photo_big_file_id or chat.photo_small_file_id):
                chat = await sync_chat_from_telegram(
                    session, bot, chat_id, sync_administrators=False
                )
                await session.commit()
            file_id = chat.photo_big_file_id or chat.photo_small_file_id
        if not file_id:
            raise HTTPException(status_code=404, detail="У группы нет аватара")
        telegram_file = await bot.get_file(file_id)
        if not telegram_file.file_path:
            raise HTTPException(status_code=404, detail="Аватар группы недоступен")
        buffer = BytesIO()
        await bot.download_file(telegram_file.file_path, destination=buffer)
        return Response(
            content=buffer.getvalue(),
            media_type="image/jpeg",
            headers={"Cache-Control": "public, max-age=3600"},
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=404, detail="Аватар группы недоступен") from exc


@router.get("/media/welcome/{chat_id}", include_in_schema=False)
async def get_public_welcome_photo(chat_id: int) -> FileResponse:
    async with SessionFactory() as session:
        try:
            merged = await get_merged_settings(session, chat_id)
        except Exception as exc:
            raise HTTPException(status_code=404, detail="Фотография не найдена") from exc
    raw_path = str(merged.get("welcome_photo_path") or "")
    if not raw_path:
        raise HTTPException(status_code=404, detail="Фотография не добавлена")
    path = Path(raw_path).resolve()
    try:
        path.relative_to(WELCOME_DIR.resolve())
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Некорректный путь фотографии") from exc
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Фотография не найдена")
    return FileResponse(path, headers={"Cache-Control": "public, max-age=300"})


@router.get("/chats/{chat_id}/group-rules")
async def get_group_rules(
    chat_id: int,
    user: TelegramUser = Depends(current_telegram_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    await ensure_admin(chat_id, user)
    merged = await get_merged_settings(session, chat_id)
    return {"rules": [str(item) for item in merged.get("group_rules", []) if str(item).strip()]}


@router.put("/chats/{chat_id}/group-rules")
async def put_group_rules(
    chat_id: int,
    payload: GroupRulesUpdate,
    user: TelegramUser = Depends(current_telegram_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    await ensure_admin(chat_id, user)
    rules = [item.strip() for item in payload.rules if item.strip()]
    if len(rules) > 100:
        raise HTTPException(status_code=400, detail="Можно добавить не более 100 правил")
    if any(len(item) > 500 for item in rules):
        raise HTTPException(status_code=400, detail="Одно правило не может быть длиннее 500 символов")
    updated = await update_chat_settings(session, chat_id, {"group_rules": rules})
    await session.commit()
    return {"rules": updated["group_rules"]}


@router.get("/chats/{chat_id}/welcome")
async def get_welcome_settings(
    chat_id: int,
    user: TelegramUser = Depends(current_telegram_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    await ensure_admin(chat_id, user)
    merged = await get_merged_settings(session, chat_id)
    photo_name = str(merged.get("welcome_photo_name") or "")
    return {
        "enabled": bool(merged.get("welcome_enabled", True)),
        "text": str(merged.get("welcome_text") or ""),
        "after_captcha": bool(merged.get("welcome_after_captcha", True)),
        "photo_name": photo_name,
        "photo_url": f"/api/media/welcome/{chat_id}?v={int(utcnow().timestamp())}" if photo_name else None,
    }


@router.put("/chats/{chat_id}/welcome")
async def put_welcome_settings(
    chat_id: int,
    payload: WelcomeSettingsUpdate,
    user: TelegramUser = Depends(current_telegram_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    await ensure_admin(chat_id, user)
    text_value = payload.text.strip()
    if payload.enabled and not text_value:
        raise HTTPException(status_code=400, detail="Введите текст приветственного сообщения")
    updated = await update_chat_settings(
        session,
        chat_id,
        {
            "welcome_enabled": payload.enabled,
            "welcome_text": text_value,
            "welcome_after_captcha": payload.after_captcha,
        },
    )
    await session.commit()
    return {
        "enabled": updated["welcome_enabled"],
        "text": updated["welcome_text"],
        "after_captcha": updated["welcome_after_captcha"],
        "photo_name": updated.get("welcome_photo_name") or "",
        "photo_url": f"/api/media/welcome/{chat_id}?v={int(utcnow().timestamp())}" if updated.get("welcome_photo_name") else None,
    }


@router.post("/chats/{chat_id}/welcome/photo")
async def upload_welcome_photo(
    chat_id: int,
    photo: UploadFile = File(...),
    user: TelegramUser = Depends(current_telegram_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    await ensure_admin(chat_id, user)
    allowed = {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
    }
    suffix = allowed.get(photo.content_type or "")
    if suffix is None:
        raise HTTPException(status_code=400, detail="Поддерживаются JPG, PNG и WEBP")
    content = await photo.read()
    if not content:
        raise HTTPException(status_code=400, detail="Файл пуст")
    if len(content) > 8 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Размер фотографии не должен превышать 8 МБ")
    for existing in WELCOME_DIR.glob(f"{chat_id}.*"):
        try:
            existing.unlink()
        except OSError:
            pass
    target = WELCOME_DIR / f"{chat_id}{suffix}"
    target.write_bytes(content)
    updated = await update_chat_settings(
        session,
        chat_id,
        {
            "welcome_photo_path": str(target),
            "welcome_photo_name": Path(photo.filename or target.name).name,
        },
    )
    await session.commit()
    return {
        "photo_name": updated["welcome_photo_name"],
        "photo_url": f"/api/media/welcome/{chat_id}?v={int(utcnow().timestamp())}",
    }


@router.delete("/chats/{chat_id}/welcome/photo", status_code=204)
async def delete_welcome_photo(
    chat_id: int,
    user: TelegramUser = Depends(current_telegram_user),
    session: AsyncSession = Depends(get_session),
) -> Response:
    await ensure_admin(chat_id, user)
    merged = await get_merged_settings(session, chat_id)
    raw_path = str(merged.get("welcome_photo_path") or "")
    if raw_path:
        path = Path(raw_path)
        try:
            if path.resolve().is_relative_to(WELCOME_DIR.resolve()) and path.exists():
                path.unlink()
        except (OSError, ValueError):
            pass
    await update_chat_settings(session, chat_id, {"welcome_photo_path": "", "welcome_photo_name": ""})
    await session.commit()
    return Response(status_code=204)


@router.get("/chats/{chat_id}/captcha")
async def get_captcha_settings(
    chat_id: int,
    user: TelegramUser = Depends(current_telegram_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    await ensure_admin(chat_id, user)
    merged = await get_merged_settings(session, chat_id)
    return {
        "enabled": bool(merged.get("captcha_enabled", True)),
        "timeout_seconds": int(merged.get("captcha_timeout_seconds", 60)),
        "attempts": int(merged.get("captcha_attempts", 3)),
        "failure_action": str(merged.get("captcha_failure_action", "kick")),
        "image_set": str(merged.get("captcha_image_set", "random")),
        "message": str(merged.get("captcha_message") or ""),
    }


@router.put("/chats/{chat_id}/captcha")
async def put_captcha_settings(
    chat_id: int,
    payload: CaptchaSettingsUpdate,
    user: TelegramUser = Depends(current_telegram_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    await ensure_admin(chat_id, user)
    message_value = payload.message.strip()
    if payload.enabled and not message_value:
        raise HTTPException(status_code=400, detail="Введите сообщение CAPTCHA")
    updated = await update_chat_settings(
        session,
        chat_id,
        {
            "captcha_enabled": payload.enabled,
            "captcha_timeout_seconds": payload.timeout_seconds,
            "captcha_attempts": payload.attempts,
            "captcha_failure_action": payload.failure_action,
            "captcha_image_set": payload.image_set,
            "captcha_message": message_value,
        },
    )
    await session.commit()
    return {
        "enabled": updated["captcha_enabled"],
        "timeout_seconds": updated["captcha_timeout_seconds"],
        "attempts": updated["captcha_attempts"],
        "failure_action": updated["captcha_failure_action"],
        "image_set": updated["captcha_image_set"],
        "message": updated["captcha_message"],
    }


@router.get("/chats/{chat_id}/captcha/preview")
async def get_captcha_preview(
    chat_id: int,
    image_set: str | None = Query(default=None, max_length=20),
    user: TelegramUser = Depends(current_telegram_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    from app.captcha import select_captcha

    await ensure_admin(chat_id, user)
    merged = await get_merged_settings(session, chat_id)
    selected = select_captcha(str(image_set or merged.get("captcha_image_set", "random")))
    return {
        "key": selected["key"],
        "image_url": f"/static/captcha/{selected['key']}.png",
        "options": selected["options"],
        "answer": selected["answer"],
        "label": selected["label"],
    }


@router.get("/chats/{chat_id}/basic-commands")
async def get_basic_commands(
    chat_id: int,
    user: TelegramUser = Depends(current_telegram_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    await ensure_admin(chat_id, user)
    merged = await get_merged_settings(session, chat_id)
    commands = default_basic_commands()
    stored = merged.get("basic_moderation_commands")
    if isinstance(stored, dict):
        for key, value in stored.items():
            if key in commands and isinstance(value, dict):
                commands[key].update({item_key: item_value for item_key, item_value in value.items() if item_key in {"name", "trigger", "action", "response"}})
    premium = await premium_access_details(session, chat_id=chat_id, user_id=user.id)
    return {"commands": [{"key": key, **value} for key, value in commands.items()], "premium": premium["active"]}


@router.put("/chats/{chat_id}/basic-commands/{command_key}")
async def update_basic_command(
    chat_id: int,
    command_key: str,
    payload: BasicCommandUpdate,
    user: TelegramUser = Depends(current_telegram_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    await ensure_admin(chat_id, user)
    premium = await premium_access_details(session, chat_id=chat_id, user_id=user.id)
    if not premium["active"]:
        raise HTTPException(status_code=403, detail="Редактор основных команд доступен только с AniGuard Premium")
    if command_key not in BASIC_MODERATION_COMMANDS:
        raise HTTPException(status_code=404, detail="Команда не найдена")
    trigger = payload.trigger.strip().lstrip("/").strip()
    if not trigger:
        raise HTTPException(status_code=400, detail="Вызов команды не может быть пустым")
    merged = await get_merged_settings(session, chat_id)
    commands = default_basic_commands()
    stored = merged.get("basic_moderation_commands")
    if isinstance(stored, dict):
        for key, value in stored.items():
            if key in commands and isinstance(value, dict):
                commands[key].update(value)
    duplicate = next(
        (item for key, item in commands.items() if key != command_key and str(item.get("trigger", "")).casefold() == trigger.casefold()),
        None,
    )
    if duplicate:
        raise HTTPException(status_code=400, detail=f"Вызов «{trigger}» уже используется другой командой")
    response = payload.response.strip()
    if not response:
        raise HTTPException(status_code=400, detail="Ответ бота не может быть пустым")
    commands[command_key]["trigger"] = trigger
    commands[command_key]["response"] = response
    await update_chat_settings(session, chat_id, {"basic_moderation_commands": commands})
    await session.commit()
    return commands[command_key]


@router.get("/chats/{chat_id}/premium/status")
async def get_premium_status(
    chat_id: int,
    user: TelegramUser = Depends(current_telegram_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    await ensure_admin(chat_id, user)
    details = await premium_access_details(session, chat_id=chat_id, user_id=user.id)
    owner: dict[str, Any] | None = None
    if details["owner_id"]:
        db_owner = await session.get(User, details["owner_id"])
        if db_owner:
            owner = {
                "id": db_owner.id,
                "first_name": db_owner.first_name,
                "last_name": db_owner.last_name,
                "full_name": " ".join(filter(None, [db_owner.first_name, db_owner.last_name])),
                "username": db_owner.username,
                "avatar_url": f"/api/avatars/{db_owner.id}",
            }
    until = details["until"]
    remaining_seconds = max(0, int((until - utcnow()).total_seconds())) if until else None
    return {
        "active": details["active"],
        "source": details["source"],
        "until": until.isoformat() if until else None,
        "remaining_seconds": remaining_seconds,
        "lifetime": details["lifetime"],
        "plan": details["plan"],
        "owner": owner,
        "group_subscription": {
            "active": details["group"]["active"],
            "until": details["group"]["until"].isoformat() if details["group"]["until"] else None,
            "lifetime": details["group"]["lifetime"],
            "plan": details["group"]["plan"],
        },
        "owner_subscription": {
            "active": details["owner"]["active"],
            "until": details["owner"]["until"].isoformat() if details["owner"]["until"] else None,
            "lifetime": details["owner"]["lifetime"],
            "plan": details["owner"]["plan"],
        },
        "features": [
            "Полный набор автоматических фильтров",
            "Умный антиспам и антифишинг",
            "Координированный спам и защита от рейдов",
            "Автокарантин и лестница наказаний",
            "Адаптивная и ночная защита",
            "Редактор вызовов основных команд",
            "Редактор ответов бота",
            "Кастомные команды и расширенные правила",
            "Расширенная статистика и журналирование",
        ],
    }


# ---------------------------------------------------------------------------
# Full owner dashboard API used by /admin
# ---------------------------------------------------------------------------

_ADMIN_SETTING_DEFAULTS: dict[str, bool] = {
    "maintenance": False,
    "registration": True,
    "premiumInheritance": True,
    "paymentNotify": True,
    "autoReports": True,
    "themeAuto": False,
    "backupAlerts": True,
}


def _iso(value: Any) -> str | None:
    return value.isoformat() if value else None


async def _admin_setting_values(session: AsyncSession) -> dict[str, bool]:
    row = await session.get(SystemSetting, "admin_panel")
    values = dict(_ADMIN_SETTING_DEFAULTS)
    if row and isinstance(row.value, dict):
        values.update({key: bool(value) for key, value in row.value.items() if key in values})
    return values


async def _write_admin_log(
    session: AsyncSession,
    *,
    admin_id: int,
    action: str,
    entity_type: str | None = None,
    entity_id: int | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    session.add(
        AdminActionLog(
            admin_id=admin_id,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            details=details or {},
        )
    )


@router.get("/admin/dashboard")
async def admin_dashboard(
    user: TelegramUser = Depends(current_telegram_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    ensure_bot_admin(user)
    now = utcnow()
    day_ago = now - timedelta(days=1)
    week_ago = now - timedelta(days=7)

    user_count = int(await session.scalar(select(func.count()).select_from(User)) or 0)
    chat_count = int(await session.scalar(select(func.count()).select_from(Chat)) or 0)
    active_24 = int(
        await session.scalar(
            select(func.count(func.distinct(Membership.user_id))).where(Membership.last_seen_at >= day_ago)
        )
        or 0
    )
    active_7 = int(
        await session.scalar(
            select(func.count(func.distinct(Membership.user_id))).where(Membership.last_seen_at >= week_ago)
        )
        or 0
    )
    revenue = int(await session.scalar(select(func.coalesce(func.sum(Payment.stars), 0))) or 0)
    ani_coin = int(await session.scalar(select(func.coalesce(func.sum(UserWallet.balance), 0))) or 0)
    if not ani_coin:
        ani_coin = int(await session.scalar(select(func.coalesce(func.sum(Membership.coins), 0))) or 0)

    grants = (await session.scalars(select(EntityAccessGrant))).all()
    premium_users = 0
    premium_chats = 0
    expiring_premium = 0
    for grant in grants:
        active = bool(grant.is_lifetime or (as_utc(grant.premium_until) and as_utc(grant.premium_until) > now))
        if not active:
            continue
        if grant.entity_type == "user":
            premium_users += 1
        elif grant.entity_type == "chat":
            premium_chats += 1
        until = as_utc(grant.premium_until)
        if until and now < until <= now + timedelta(days=7):
            expiring_premium += 1
    paid_chats = (await session.scalars(select(Chat).where(Chat.premium_until.is_not(None)))).all()
    premium_chat_ids = {
        row.entity_id
        for row in grants
        if row.entity_type == "chat"
        and (row.is_lifetime or (as_utc(row.premium_until) and as_utc(row.premium_until) > now))
    }
    premium_chat_ids.update(
        row.id for row in paid_chats if as_utc(row.premium_until) and as_utc(row.premium_until) > now
    )
    premium_chats = len(premium_chat_ids)

    users_rows = (await session.scalars(select(User).order_by(User.updated_at.desc()).limit(200))).all()
    users_payload: list[dict[str, Any]] = []
    for row in users_rows:
        grant = await session.scalar(
            select(EntityAccessGrant).where(
                EntityAccessGrant.entity_type == "user", EntityAccessGrant.entity_id == row.id
            )
        )
        premium = bool(
            grant
            and (grant.is_lifetime or (as_utc(grant.premium_until) and as_utc(grant.premium_until) > now))
        )
        wallet = await session.get(UserWallet, row.id)
        last_seen = await session.scalar(
            select(func.max(Membership.last_seen_at)).where(Membership.user_id == row.id)
        )
        chats_for_user = int(
            await session.scalar(select(func.count()).select_from(Membership).where(Membership.user_id == row.id))
            or 0
        )
        spent = int(
            await session.scalar(
                select(func.coalesce(func.sum(Payment.stars), 0)).where(Payment.user_id == row.id)
            )
            or 0
        )
        block = await get_block_record(session, "user", row.id)
        full_name = " ".join(filter(None, [row.first_name, row.last_name]))
        users_payload.append(
            {
                "id": row.id,
                "name": full_name or row.first_name or "User",
                "username": row.username or "",
                "joined": _iso(row.created_at),
                "last": _iso(last_seen or row.updated_at),
                "premium": premium,
                "premiumUntil": "Навсегда" if grant and grant.is_lifetime else _iso(grant.premium_until) if grant else None,
                "active24": bool(last_seen and as_utc(last_seen) and as_utc(last_seen) >= day_ago),
                "active7": bool(last_seen and as_utc(last_seen) and as_utc(last_seen) >= week_ago),
                "aniCoin": int(wallet.balance if wallet else 0),
                "spent": spent,
                "referrals": 0,
                "blocked": block is not None,
                "status": "Заблокирован" if block else "Активен" if last_seen and as_utc(last_seen) and as_utc(last_seen) >= week_ago else "Неактивен",
                "chats": chats_for_user,
            }
        )

    chat_rows = (await session.scalars(select(Chat).order_by(Chat.updated_at.desc()).limit(200))).all()
    chats_payload: list[dict[str, Any]] = []
    for row in chat_rows:
        member_count = int(
            await session.scalar(select(func.count()).select_from(Membership).where(Membership.chat_id == row.id))
            or 0
        )
        active_members = int(
            await session.scalar(
                select(func.count()).select_from(Membership).where(
                    Membership.chat_id == row.id, Membership.last_seen_at >= week_ago
                )
            )
            or 0
        )
        actions = int(
            await session.scalar(
                select(func.count()).select_from(ModerationLog).where(ModerationLog.chat_id == row.id)
            )
            or 0
        )
        details = await premium_access_details(session, chat_id=row.id, user_id=user.id)
        settings_data = await get_merged_settings(session, row.id)
        owner_id = int(settings_data.get("owner_user_id") or 0)
        automod_keys = [key for key, value in settings_data.items() if key.endswith("_enabled") and value]
        chats_payload.append(
            {
                "id": row.id,
                "name": row.title,
                "username": row.username or "",
                "ownerId": owner_id,
                "members": member_count,
                "activeMembers": active_members,
                "premium": bool(details["active"]),
                "premiumType": details.get("source") or "Нет",
                "premiumUntil": _iso(details.get("until")),
                "botStatus": "Активен" if row.is_active else "Приостановлен",
                "automod": len(automod_keys),
                "captcha": bool(settings_data.get("captcha_enabled", False)),
                "actions": actions,
                "avatar_url": chat_avatar_url(row),
            }
        )

    payment_rows = (
        await session.scalars(select(Payment).order_by(Payment.id.desc()).limit(200))
    ).all()
    payments_payload = [
        {
            "id": f"PAY-{row.id}",
            "userId": row.user_id,
            "chatId": row.chat_id,
            "item": f"Premium {row.plan_code}",
            "amount": row.stars,
            "date": _iso(row.created_at),
            "status": "Оплачено",
        }
        for row in payment_rows
    ]

    report_rows = (await session.scalars(select(Report).order_by(Report.id.desc()).limit(200))).all()
    reports_payload = [
        {
            "id": f"REP-{row.id}",
            "rawId": row.id,
            "type": "Жалоба",
            "text": row.reason,
            "status": {"new": "Новая", "in_progress": "В работе", "closed": "Решено"}.get(row.status, row.status),
            "chatId": row.chat_id,
            "userId": row.reporter_id,
            "targetId": row.target_id,
            "date": _iso(row.created_at),
        }
        for row in report_rows
    ]
    tickets_payload = [
        {
            "id": f"AG-{row.id}",
            "rawId": row.id,
            "userId": row.reporter_id,
            "title": f"Жалоба в беседе {row.chat_id}",
            "status": {"new": "Открыт", "in_progress": "В работе", "closed": "Решён"}.get(row.status, row.status),
            "date": _iso(row.created_at),
            "text": row.reason,
        }
        for row in report_rows
    ]

    promo_rows = (await session.scalars(select(PromoCode).order_by(PromoCode.created_at.desc()))).all()
    promos_payload = [
        {
            "code": row.code,
            "reward": f"Premium {row.reward_value} дней" if row.reward_type == "premium" else f"{row.reward_value} AniCoin",
            "rewardType": row.reward_type,
            "rewardValue": row.reward_value,
            "uses": row.uses,
            "limit": row.max_uses,
            "active": row.active,
        }
        for row in promo_rows
    ]

    log_rows = (
        await session.scalars(select(AdminActionLog).order_by(AdminActionLog.id.desc()).limit(200))
    ).all()
    logs_payload = [
        {
            "id": row.id,
            "entity_id": row.entity_id,
            "entity_type": row.entity_type,
            "time": _iso(row.created_at),
            "text": f"{row.action}" + (f" · {row.entity_type} {row.entity_id}" if row.entity_id is not None else ""),
            "tag": "premium" if "premium" in row.action else "chat" if row.entity_type == "chat" else "admin",
            "details": row.details,
        }
        for row in log_rows
    ]

    open_reports = sum(1 for row in report_rows if row.status != "closed")
    return {
        "admin": {
            "id": user.id,
            "name": " ".join(filter(None, [user.first_name, user.last_name])) or "AniGuard Admin",
            "username": user.username,
            "avatar_url": _admin_avatar_url(),
        },
        "stats": {
            "users": user_count,
            "active24": active_24,
            "active7": active_7,
            "chats": chat_count,
            "premiumUsers": premium_users,
            "premiumChats": premium_chats,
            "expiringPremium": expiring_premium,
            "revenue": revenue,
            "aniCoin": ani_coin,
            "reports": open_reports,
        },
        "users": users_payload,
        "chats": chats_payload,
        "payments": payments_payload,
        "reports": reports_payload,
        "tickets": tickets_payload,
        "promos": promos_payload,
        "logs": logs_payload,
        "settings": await _admin_setting_values(session),
    }


@router.post("/admin/coins")
async def admin_update_coins(
    payload: AdminCoinRequest,
    user: TelegramUser = Depends(current_telegram_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    ensure_bot_admin(user)
    target = await session.get(User, payload.user_id)
    if not target:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    wallet = await session.get(UserWallet, payload.user_id)
    if not wallet:
        wallet = UserWallet(user_id=payload.user_id, balance=0)
        session.add(wallet)
    if payload.operation == "add":
        wallet.balance += payload.amount
    elif payload.operation == "subtract":
        wallet.balance = max(0, wallet.balance - payload.amount)
    else:
        wallet.balance = payload.amount
    await _write_admin_log(
        session,
        admin_id=user.id,
        action="coins_update",
        entity_type="user",
        entity_id=payload.user_id,
        details={"operation": payload.operation, "amount": payload.amount, "balance": wallet.balance, "note": payload.note},
    )
    await session.commit()
    return {"user_id": payload.user_id, "balance": wallet.balance}


@router.post("/admin/chats/{chat_id}/state")
async def admin_chat_state(
    chat_id: int,
    payload: AdminChatStateRequest,
    user: TelegramUser = Depends(current_telegram_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    ensure_bot_admin(user)
    chat = await session.get(Chat, chat_id)
    if not chat:
        raise HTTPException(status_code=404, detail="Беседа не найдена")
    chat.is_active = payload.active
    await _write_admin_log(
        session,
        admin_id=user.id,
        action="chat_started" if payload.active else "chat_paused",
        entity_type="chat",
        entity_id=chat_id,
    )
    await session.commit()
    return {"chat_id": chat_id, "active": chat.is_active}


@router.post("/admin/chats/{chat_id}/settings")
async def admin_chat_quick_settings(
    chat_id: int,
    payload: AdminChatSettingsRequest,
    user: TelegramUser = Depends(current_telegram_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    ensure_bot_admin(user)
    chat = await session.get(Chat, chat_id)
    if not chat:
        raise HTTPException(status_code=404, detail="Беседа не найдена")
    current = await get_merged_settings(session, chat_id)
    if payload.captcha is not None:
        current["captcha_enabled"] = payload.captcha
    if payload.automod is not None:
        for key in list(current):
            if key.endswith("_enabled") and key != "captcha_enabled":
                current[key] = payload.automod
    chat.settings = current
    await _write_admin_log(
        session,
        admin_id=user.id,
        action="chat_settings_update",
        entity_type="chat",
        entity_id=chat_id,
        details=payload.model_dump(exclude_none=True),
    )
    await session.commit()
    return {"chat_id": chat_id, "settings": current}


async def _run_broadcast(job_id: int) -> None:
    async with SessionFactory() as session:
        job = await session.get(BroadcastJob, job_id)
        if not job:
            return
        job.status = "running"
        await session.commit()

        user_ids: set[int] = set()
        if job.audience in {"users", "all"}:
            user_ids.update(int(value) for value in (await session.scalars(select(User.id))).all())
        if job.audience == "active_users":
            cutoff = utcnow() - timedelta(days=7)
            user_ids.update(
                int(value)
                for value in (
                    await session.scalars(
                        select(Membership.user_id).where(Membership.last_seen_at >= cutoff).distinct()
                    )
                ).all()
            )
        if job.audience in {"premium_users", "all"}:
            grants = (
                await session.scalars(
                    select(EntityAccessGrant).where(EntityAccessGrant.entity_type == "user")
                )
            ).all()
            now = utcnow()
            user_ids.update(
                row.entity_id
                for row in grants
                if row.is_lifetime or (as_utc(row.premium_until) and as_utc(row.premium_until) > now)
            )
        if job.audience in {"chat_owners", "all"}:
            chats = (await session.scalars(select(Chat))).all()
            for chat in chats:
                owner_id = (chat.settings or {}).get("owner_user_id")
                if owner_id:
                    user_ids.add(int(owner_id))

        markup = None
        if job.button_text and job.button_url:
            from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

            markup = InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text=job.button_text, url=job.button_url)]]
            )
        sent = 0
        failed = 0
        for recipient_id in user_ids:
            try:
                await bot.send_message(recipient_id, job.text, reply_markup=markup)
                sent += 1
            except Exception:
                failed += 1
            if (sent + failed) % 25 == 0:
                job.sent_count = sent
                job.failed_count = failed
                await session.commit()
            await asyncio.sleep(0.04)
        job.status = "finished"
        job.sent_count = sent
        job.failed_count = failed
        job.finished_at = utcnow()
        await session.commit()


@router.post("/admin/broadcast")
async def admin_broadcast(
    payload: AdminBroadcastRequest,
    user: TelegramUser = Depends(current_telegram_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    ensure_bot_admin(user)
    if bool(payload.button_text) != bool(payload.button_url):
        raise HTTPException(status_code=400, detail="Для кнопки укажите и текст, и ссылку")
    job = BroadcastJob(created_by=user.id, **payload.model_dump())
    session.add(job)
    await session.flush()
    await _write_admin_log(
        session,
        admin_id=user.id,
        action="broadcast_created",
        details={"job_id": job.id, "audience": payload.audience},
    )
    await session.commit()
    asyncio.create_task(_run_broadcast(job.id), name=f"broadcast-{job.id}")
    return {"job_id": job.id, "status": job.status}


@router.get("/admin/payments")
async def admin_payments(
    limit: int = Query(default=200, ge=1, le=1000),
    user: TelegramUser = Depends(current_telegram_user),
    session: AsyncSession = Depends(get_session),
) -> list[dict[str, Any]]:
    ensure_bot_admin(user)
    rows = (await session.scalars(select(Payment).order_by(Payment.id.desc()).limit(limit))).all()
    return [
        {
            "id": row.id,
            "user_id": row.user_id,
            "chat_id": row.chat_id,
            "plan_code": row.plan_code,
            "stars": row.stars,
            "created_at": _iso(row.created_at),
        }
        for row in rows
    ]


@router.post("/admin/promos")
async def admin_create_promo(
    payload: AdminPromoCreateRequest,
    user: TelegramUser = Depends(current_telegram_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    ensure_bot_admin(user)
    code = (payload.code or f"AG{secrets.token_hex(4)}").upper().replace(" ", "")
    if not code.isalnum():
        raise HTTPException(status_code=400, detail="Промокод может содержать только буквы и цифры")
    if await session.get(PromoCode, code):
        raise HTTPException(status_code=409, detail="Такой промокод уже существует")
    row = PromoCode(
        code=code,
        reward_type=payload.reward_type,
        reward_value=payload.reward_value,
        max_uses=payload.max_uses,
        created_by=user.id,
    )
    session.add(row)
    await _write_admin_log(session, admin_id=user.id, action="promo_created", details={"code": code})
    await session.commit()
    return {"code": row.code, "active": row.active}


@router.post("/admin/promos/{code}/toggle")
async def admin_toggle_promo(
    code: str,
    payload: AdminPromoToggleRequest,
    user: TelegramUser = Depends(current_telegram_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    ensure_bot_admin(user)
    row = await session.get(PromoCode, code.upper())
    if not row:
        raise HTTPException(status_code=404, detail="Промокод не найден")
    row.active = payload.active
    await _write_admin_log(
        session, admin_id=user.id, action="promo_toggled", details={"code": row.code, "active": row.active}
    )
    await session.commit()
    return {"code": row.code, "active": row.active}


@router.post("/admin/reports/{report_id}/status")
async def admin_report_status(
    report_id: int,
    payload: AdminReportCloseRequest,
    user: TelegramUser = Depends(current_telegram_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    ensure_bot_admin(user)
    row = await session.get(Report, report_id)
    if not row:
        raise HTTPException(status_code=404, detail="Жалоба не найдена")
    row.status = payload.status
    row.assigned_to = user.id
    row.closed_at = utcnow() if payload.status == "closed" else None
    await _write_admin_log(
        session,
        admin_id=user.id,
        action="report_status",
        entity_type="chat",
        entity_id=row.chat_id,
        details={"report_id": row.id, "status": payload.status},
    )
    await session.commit()
    return {"report_id": row.id, "status": row.status}


@router.get("/admin/export")
async def admin_export(
    user: TelegramUser = Depends(current_telegram_user),
    session: AsyncSession = Depends(get_session),
) -> Response:
    ensure_bot_admin(user)
    dashboard = await admin_dashboard(user=user, session=session)
    payload = json.dumps(dashboard, ensure_ascii=False, indent=2, default=str).encode("utf-8")
    await _write_admin_log(session, admin_id=user.id, action="data_export")
    await session.commit()
    return Response(
        content=payload,
        media_type="application/json; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="aniguard_export.json"'},
    )


@router.post("/admin/system/settings")
async def admin_system_settings(
    payload: AdminSystemSettingsRequest,
    user: TelegramUser = Depends(current_telegram_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    ensure_bot_admin(user)
    values = await _admin_setting_values(session)
    values.update({key: bool(value) for key, value in payload.settings.items() if key in values})
    row = await session.get(SystemSetting, "admin_panel")
    if not row:
        row = SystemSetting(key="admin_panel", value=values, updated_by=user.id)
        session.add(row)
    else:
        row.value = values
        row.updated_by = user.id
    await _write_admin_log(session, admin_id=user.id, action="system_settings", details=payload.settings)
    await session.commit()
    return {"settings": values}


@router.post("/admin/system/action/{action}")
async def admin_system_action(
    action: str,
    user: TelegramUser = Depends(current_telegram_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    ensure_bot_admin(user)
    if action not in {"clear_cache", "restart", "maintenance", "backup"}:
        raise HTTPException(status_code=404, detail="Неизвестное системное действие")
    if action == "maintenance":
        values = await _admin_setting_values(session)
        values["maintenance"] = True
        row = await session.get(SystemSetting, "admin_panel")
        if not row:
            session.add(SystemSetting(key="admin_panel", value=values, updated_by=user.id))
        else:
            row.value = values
            row.updated_by = user.id
    await _write_admin_log(session, admin_id=user.id, action=f"system_{action}")
    await session.commit()
    if action == "restart":
        asyncio.get_running_loop().call_later(1.0, os._exit, 0)
    return {"action": action, "ok": True}


@router.post("/admin/users/{user_id}/message")
async def admin_direct_message(
    user_id: int,
    payload: AdminDirectMessageRequest,
    user: TelegramUser = Depends(current_telegram_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    ensure_bot_admin(user)
    if not await session.get(User, user_id):
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    try:
        await bot.send_message(user_id, payload.text)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Не удалось отправить сообщение: {exc}") from exc
    await _write_admin_log(
        session, admin_id=user.id, action="direct_message", entity_type="user", entity_id=user_id
    )
    await session.commit()
    return {"user_id": user_id, "sent": True}


@router.post("/admin/premium/bulk")
async def admin_bulk_premium(
    payload: AdminBulkPremiumRequest,
    user: TelegramUser = Depends(current_telegram_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    ensure_bot_admin(user)
    now = utcnow()
    entity_type = "chat" if payload.category == "premium_chats" else "user"
    grants = (
        await session.scalars(
            select(EntityAccessGrant).where(EntityAccessGrant.entity_type == entity_type)
        )
    ).all()
    target_ids: list[int] = []
    for grant in grants:
        if grant.is_lifetime:
            continue
        until = as_utc(grant.premium_until)
        if not until or until <= now:
            continue
        if payload.category == "expiring_users" and until > now + timedelta(days=7):
            continue
        target_ids.append(grant.entity_id)
    if entity_type == "chat":
        paid = (await session.scalars(select(Chat).where(Chat.premium_until.is_not(None)))).all()
        target_ids.extend(
            row.id for row in paid if as_utc(row.premium_until) and as_utc(row.premium_until) > now
        )
    target_ids = sorted(set(target_ids))
    for entity_id in target_ids:
        await set_entity_premium(
            session, entity_type=entity_type, entity_id=entity_id, days=payload.days,
            admin_id=user.id, permanent=False, plan="admin_bulk", note="Массовое продление"
        )
    await _write_admin_log(
        session, admin_id=user.id, action="premium_bulk", entity_type=entity_type,
        details={"category": payload.category, "days": payload.days, "count": len(target_ids)}
    )
    await session.commit()
    return {"count": len(target_ids), "days": payload.days, "category": payload.category}
