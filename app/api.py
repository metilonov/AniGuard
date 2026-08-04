from __future__ import annotations

import asyncio
import json
import os
import secrets
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from typing import Any

from aiogram.enums import ChatMemberStatus
from aiogram.types import ChatPermissions
from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse, Response
from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import bot, moderation_response, render_custom_template
from app.game_action_catalog import default_game_actions
from app.defaults import BASIC_MODERATION_COMMANDS, PREMIUM_SETTING_KEYS, default_basic_commands
from app.config import get_settings
from app.db import SessionFactory, get_session
from app.models import (
    AdminActionLog,
    BlockedEntity,
    Chat,
    CaseOpening,
    AdvertisingOrder,
    SupportTicket,
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
    StorePayment,
    UserWallet,
    ModerationRule,
    Report,
    RoleAssignmentHistory,
    RPCommand,
    ResponseStylePack,
    User,
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
    PenaltyStatusUpdate,
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
    AdminCaseCloseRequest,
    AdminAppealDecisionRequest,
    AdminSecurityModeRequest,
    AdminPunishmentLadderRequest,
    AdminPermissionOverrideRequest,
    AdminShiftCreateRequest,
    AdminResponseStyleRequest,
    AdminBackupRestoreRequest,
    AdminProbationDecisionRequest,
    StylePackCreate,
    StylePackUpdate,
    StylePackModerationRequest,
    ChatStyleApplyRequest,
)
from app.security import TelegramUser, current_telegram_user
from app.monitoring import resource_monitor
from app.response_styles import (
    ACTION_TITLES as RESPONSE_ACTION_TITLES,
    BUILTIN_STYLE_TEMPLATES,
    STYLE_EXAMPLES,
    STYLE_VARIABLES,
    build_context,
    render_template,
    style_code,
    validate_templates,
)
from app.exchange_rates import exchange_rates
from app.feature_services import (
    close_case,
    create_backup_snapshot,
    create_shift,
    decide_appeal,
    generate_weekly_report,
    restore_backup_snapshot,
    set_security_mode,
    utcnow as feature_utcnow,
)
from app.roles import (
    PENALTY_DEFINITIONS,
    ROLE_DEFINITIONS,
    effective_role,
    is_admin_role,
    normalize_penalty_status,
    normalize_role,
    penalty_name,
    role_level,
    role_name,
)
from app.services import (
    create_invoice_link,
    dashboard_data,
    get_chat_or_raise,
    ensure_entity_available,
    entity_has_premium,
    entity_premium_details,
    get_block_record,
    get_merged_settings,
    get_membership,
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
    assign_member_role,
    ensure_membership,
    refresh_membership_state,
    set_member_penalty_status,
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
    """Allow only the current Telegram group creator in the regular panel."""
    try:
        async with SessionFactory() as session:
            await ensure_entity_available(session, user_id=user.id, chat_id=chat_id)
            await session.commit()
        await require_chat_admin(bot, chat_id, user.id)
    except HTTPException:
        raise
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
    db_user = await session.get(User, user.id)
    wallet = await session.get(UserWallet, user.id)
    premium = await entity_premium_details(session, "user", user.id)

    aggregate = (
        await session.execute(
            select(
                func.count(func.distinct(Membership.chat_id)),
                func.coalesce(func.sum(Membership.message_count), 0),
                func.coalesce(func.sum(Membership.xp), 0),
                func.coalesce(func.sum(Membership.coins), 0),
                func.coalesce(func.sum(Membership.warnings), 0),
                func.coalesce(func.sum(Membership.penalty_points), 0),
            ).where(Membership.user_id == user.id)
        )
    ).one()
    chats_count, messages, total_xp, membership_coins, warnings, penalty_points = map(int, aggregate)

    account_points = messages + chats_count * 100 + total_xp // 5
    account_level = max(1, 1 + account_points // 1_000)
    game_level = max(1, 1 + total_xp // 500)
    rating = max(0, total_xp + messages * 2 + chats_count * 100 - warnings * 50 - penalty_points * 10)
    reputation = max(0, min(100, 100 - warnings * 5 - penalty_points * 2))
    ani_coin = int(wallet.balance) if wallet is not None else membership_coins

    created_at = db_user.created_at if db_user else utcnow()
    first_name = db_user.first_name if db_user else user.first_name
    last_name = db_user.last_name if db_user else user.last_name
    username = db_user.username if db_user else user.username

    return {
        "id": user.id,
        "first_name": first_name,
        "last_name": last_name,
        "username": username,
        "avatar_url": f"/api/avatars/{user.id}",
        "created_at": created_at.isoformat(),
        "is_bot_admin": user.id in settings.admin_ids,
        "premium": premium["active"],
        "premium_until": premium["until"].isoformat() if premium["until"] else None,
        "premium_lifetime": premium["lifetime"],
        "ani_coin": ani_coin,
        "statistics": {
            "rating": rating,
            "account_level": account_level,
            "game_level": game_level,
            "reputation": reputation,
            "total_xp": total_xp,
            "messages": messages,
            "chats": chats_count,
            "warnings": warnings,
        },
        "blocked": blocked is not None,
        "block_reason": blocked.reason if blocked else None,
    }


@router.get("/chats")
async def get_chats(
    user: TelegramUser = Depends(current_telegram_user),
    session: AsyncSession = Depends(get_session),
) -> list[dict[str, Any]]:
    """Return only groups where the current user is the Telegram creator."""
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
            role = "creator" if admin.status == ChatMemberStatus.CREATOR else "admin"
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


@router.post("/chats/{chat_id}/settings")
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
                membership.role = "creator"
            elif tg_member.status == ChatMemberStatus.ADMINISTRATOR and role_level(membership.role) < 5:
                membership.role = "admin"
        except Exception:
            pass
        await refresh_membership_state(session, membership)
        result.append({
            "id": db_user.id,
            "username": db_user.username,
            "first_name": db_user.first_name,
            "last_name": db_user.last_name,
            "full_name": " ".join(filter(None, [db_user.first_name, db_user.last_name])),
            "avatar_url": f"/api/avatars/{db_user.id}",
            "role": normalize_role(membership.role),
            "role_level": role_level(membership.role),
            "role_name": role_name(membership.role),
            "role_name_naruto": role_name(membership.role, naruto=True),
            "role_expires_at": membership.role_expires_at.isoformat() if membership.role_expires_at else None,
            "role_assigned_by": membership.role_assigned_by,
            "role_assigned_at": membership.role_assigned_at.isoformat() if membership.role_assigned_at else None,
            "penalty_status": normalize_penalty_status(membership.penalty_status),
            "penalty_name": penalty_name(membership.penalty_status),
            "penalty_name_naruto": penalty_name(membership.penalty_status, naruto=True),
            "penalty_until": membership.penalty_until.isoformat() if membership.penalty_until else None,
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
    await refresh_membership_state(session, membership)
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
        "role": normalize_role(membership.role),
        "role_level": role_level(membership.role),
        "role_name": role_name(membership.role),
        "role_name_naruto": role_name(membership.role, naruto=True),
        "role_expires_at": membership.role_expires_at.isoformat() if membership.role_expires_at else None,
        "role_assigned_by": membership.role_assigned_by,
        "role_assigned_at": membership.role_assigned_at.isoformat() if membership.role_assigned_at else None,
        "penalty_status": normalize_penalty_status(membership.penalty_status),
        "penalty_name": penalty_name(membership.penalty_status),
        "penalty_name_naruto": penalty_name(membership.penalty_status, naruto=True),
        "penalty_until": membership.penalty_until.isoformat() if membership.penalty_until else None,
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
        if tg_member.status == ChatMemberStatus.CREATOR:
            raise PermissionError("Роль создателя беседы управляется только в Telegram")
    except PermissionError:
        raise
    except Exception:
        pass
    try:
        membership = await assign_member_role(
            session,
            chat_id=chat_id,
            actor_id=user.id,
            target_id=member_id,
            new_role=payload.role,
            duration_seconds=payload.duration_seconds,
            reason=payload.reason or "Изменение роли через Mini App",
            source="mini_app",
        )
        await session.commit()
        return {
            "user_id": member_id,
            "role": membership.role,
            "role_level": role_level(membership.role),
            "role_name": role_name(membership.role),
            "role_name_naruto": role_name(membership.role, naruto=True),
            "role_expires_at": membership.role_expires_at.isoformat() if membership.role_expires_at else None,
        }
    except Exception as exc:
        await session.rollback()
        raise http_error(exc) from exc


@router.patch("/chats/{chat_id}/members/{member_id}/penalty-status")
async def update_member_penalty_status(
    chat_id: int,
    member_id: int,
    payload: PenaltyStatusUpdate,
    user: TelegramUser = Depends(current_telegram_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    await ensure_admin(chat_id, user)
    try:
        membership = await set_member_penalty_status(
            session,
            chat_id=chat_id,
            actor_id=user.id,
            target_id=member_id,
            status=payload.status,
            duration_seconds=payload.duration_seconds,
            reason=payload.reason or "Изменение штрафного статуса через Mini App",
            bot=bot,
        )
        await session.commit()
        return {
            "user_id": member_id,
            "penalty_status": membership.penalty_status,
            "penalty_name": penalty_name(membership.penalty_status),
            "penalty_name_naruto": penalty_name(membership.penalty_status, naruto=True),
            "penalty_until": membership.penalty_until.isoformat() if membership.penalty_until else None,
        }
    except Exception as exc:
        await session.rollback()
        raise http_error(exc) from exc


@router.get("/chats/{chat_id}/members/{member_id}/role-history")
async def get_member_role_history(
    chat_id: int,
    member_id: int,
    user: TelegramUser = Depends(current_telegram_user),
    session: AsyncSession = Depends(get_session),
) -> list[dict[str, Any]]:
    await ensure_admin(chat_id, user)
    rows = (
        await session.scalars(
            select(RoleAssignmentHistory)
            .where(RoleAssignmentHistory.chat_id == chat_id, RoleAssignmentHistory.user_id == member_id)
            .order_by(RoleAssignmentHistory.id.desc())
            .limit(100)
        )
    ).all()
    return [
        {
            "id": row.id,
            "actor_id": row.actor_id,
            "old_role": row.old_role,
            "old_role_name": role_name(row.old_role),
            "new_role": row.new_role,
            "new_role_name": role_name(row.new_role),
            "temporary_until": row.temporary_until.isoformat() if row.temporary_until else None,
            "reason": row.reason,
            "source": row.source,
            "created_at": row.created_at.isoformat(),
            "reverted_at": row.reverted_at.isoformat() if row.reverted_at else None,
        }
        for row in rows
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
        if payload.target_id is not None and payload.action not in {"purge", "slow", "lock", "unlock", "susanoo"}:
            try:
                group_settings = await get_merged_settings(session, chat_id)
                command_settings = group_settings.get("basic_moderation_commands") or {}
                selected_key = next(
                    (key for key, value in command_settings.items() if isinstance(value, dict) and value.get("action") == payload.action),
                    None,
                )
                selected = command_settings.get(selected_key) if selected_key else None
                defaults = default_basic_commands()
                default_response = str((defaults.get(selected_key) or {}).get("response") or "") if selected_key else ""
                chat = await get_chat_or_raise(session, chat_id)
                actor_db = await session.get(User, user.id)
                target_db = await session.get(User, payload.target_id)
                target_membership = await session.scalar(select(Membership).where(Membership.chat_id == chat_id, Membership.user_id == payload.target_id))
                style = str(group_settings.get("response_style") or "ordinary")
                custom_templates = None
                if style == "custom":
                    style_row = await session.scalar(select(ResponseStylePack).where(
                        func.upper(ResponseStylePack.code) == str(group_settings.get("custom_style_code") or "").upper(),
                        ResponseStylePack.status == "approved",
                    ))
                    custom_templates = dict(style_row.templates or {}) if style_row else None
                    if not custom_templates:
                        style = "ordinary"
                if style != "custom" and selected and selected.get("response") and str(selected.get("response")) != default_response:
                    response_text = render_custom_template(
                        str(selected["response"]),
                        actor_id=user.id,
                        target_id=payload.target_id,
                        command_name=str(selected.get("name") or payload.action),
                        duration_seconds=result.get("duration_seconds"),
                        reason=str(result.get("reason") or payload.reason or "Причина не указана"),
                        chat_title=chat.title,
                        actor_name=actor_db.first_name if actor_db else user.first_name,
                        target_name=target_db.first_name if target_db else "User",
                        warnings=int(result.get("warnings", target_membership.warnings if target_membership else 0) or 0),
                        case_id=result.get("case_id"),
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
                        style=style,
                        custom_templates=custom_templates,
                        actor_name=actor_db.first_name if actor_db else user.first_name,
                        actor_username=actor_db.username if actor_db else user.username,
                        target_name=target_db.first_name if target_db else "User",
                        target_username=target_db.username if target_db else None,
                        chat_title=chat.title,
                        chat_id=chat_id,
                        warnings=int(result.get("warnings", target_membership.warnings if target_membership else 0) or 0),
                        warning_limit=int(group_settings.get("warn_threshold", 3)),
                        case_id=result.get("case_id"),
                        command_name=str((selected or defaults.get(selected_key) or {}).get("name") or payload.action),
                        command_key=selected_key or payload.action,
                        command_description=str((selected or defaults.get(selected_key) or {}).get("description") or "Команда модерации"),
                        command_number=str(selected_key).removeprefix("anime_") if selected_key and str(selected_key).startswith("anime_") else "—",
                        command_templates={
                            "ordinary": (defaults.get(selected_key) or {}).get("ordinary_response") if selected_key else None,
                            "naruto": (defaults.get(selected_key) or {}).get("naruto_response") if selected_key else None,
                        },
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
    await require_constructor_premium(session, chat_id=chat_id, user=user)
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
    await require_constructor_premium(session, chat_id=chat_id, user=user)
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
    await require_constructor_premium(session, chat_id=chat_id, user=user)
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

async def require_constructor_premium(
    session: AsyncSession,
    *,
    chat_id: int | None,
    user: TelegramUser,
) -> None:
    """Allow the constructor for Premium accounts or Premium chats only."""
    if chat_id is not None:
        await ensure_admin(chat_id, user)
        if await has_premium_access(session, chat_id=chat_id, user_id=user.id):
            return
    if await entity_has_premium(session, "user", user.id):
        return
    raise HTTPException(
        status_code=403,
        detail="Конструктор доступен только Premium-аккаунтам и Premium-беседам",
    )


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
    await require_constructor_premium(session, chat_id=chat_id, user=user)
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
    await require_constructor_premium(session, chat_id=chat_id, user=user)
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
    await require_constructor_premium(session, chat_id=chat_id, user=user)
    row = await session.get(CustomCommand, command_id)
    if not row or row.chat_id != chat_id:
        raise HTTPException(status_code=404, detail="Кастомная команда не найдена")
    await session.delete(row)
    await session.commit()


# ---------------------------------------------------------------------------
# Game commands
# ---------------------------------------------------------------------------


@router.get("/chats/{chat_id}/game-actions")
async def get_builtin_game_actions(
    chat_id: int,
    user: TelegramUser = Depends(current_telegram_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    await ensure_admin(chat_id, user)
    premium = await premium_access_details(session, chat_id=chat_id, user_id=user.id)
    actions = default_game_actions()
    return {
        "commands": [{"key": key, **value} for key, value in actions.items()],
        "premium": premium["active"],
        "syntax": {
            "slash_optional": True,
            "underscore_optional": True,
            "force_game_prefix": ["игра", "game"],
        },
    }

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
    await require_constructor_premium(session, chat_id=chat_id, user=user)
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
    await require_constructor_premium(session, chat_id=chat_id, user=user)
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
    await require_constructor_premium(session, chat_id=chat_id, user=user)
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


# ---------------------------------------------------------------------------
# Response-style and command constructor (v23)
# ---------------------------------------------------------------------------

def _style_payload(row: ResponseStylePack, *, include_templates: bool = True) -> dict[str, Any]:
    result: dict[str, Any] = {
        "id": row.id,
        "creator_id": row.creator_id,
        "name": row.name,
        "description": row.description,
        "code": row.code if row.status == "approved" else None,
        "base_style": row.base_style,
        "status": row.status,
        "preview_text": row.preview_text,
        "moderation_note": row.moderation_note,
        "submitted_at": _iso(row.submitted_at),
        "reviewed_at": _iso(row.reviewed_at),
        "reviewed_by": row.reviewed_by,
        "uses_count": row.uses_count,
        "created_at": _iso(row.created_at),
        "updated_at": _iso(row.updated_at),
    }
    if include_templates:
        result["templates"] = row.templates or {}
    return result


@router.get("/styles/constructor")
async def style_constructor_catalog(
    chat_id: int | None = Query(default=None),
    user: TelegramUser = Depends(current_telegram_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    await require_constructor_premium(session, chat_id=chat_id, user=user)
    return {
        "variables": STYLE_VARIABLES,
        "examples": STYLE_EXAMPLES,
        "built_in_styles": [
            {"code": code, "name": {"ordinary": "Обычный", "naruto": "Наруто", "minimal": "Минималистичный", "strict": "Строгий"}[code]}
            for code in ("ordinary", "naruto", "minimal", "strict")
        ],
        "moderation_commands": [
            {"key": f"moderation.{key}", "action": key, "title": title, "ordinary": BUILTIN_STYLE_TEMPLATES["ordinary"].get(key, ""), "naruto": BUILTIN_STYLE_TEMPLATES["naruto"].get(key, "")}
            for key, title in RESPONSE_ACTION_TITLES.items()
        ],
        "key_examples": ["moderation.mute", "moderation.ban", "rp.обнять", "game.расенган", "moderation.default"],
    }


@router.get("/styles/mine")
async def my_style_packs(
    chat_id: int | None = Query(default=None),
    user: TelegramUser = Depends(current_telegram_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    await require_constructor_premium(session, chat_id=chat_id, user=user)
    rows = (await session.scalars(
        select(ResponseStylePack).where(ResponseStylePack.creator_id == user.id).order_by(ResponseStylePack.id.desc())
    )).all()
    return {"items": [_style_payload(row) for row in rows]}


@router.post("/styles", status_code=201)
async def create_style_pack(
    payload: StylePackCreate,
    chat_id: int | None = Query(default=None),
    user: TelegramUser = Depends(current_telegram_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    await require_constructor_premium(session, chat_id=chat_id, user=user)
    await upsert_user(session, user)
    templates = validate_templates(payload.templates)
    code = style_code()
    while await session.scalar(select(ResponseStylePack.id).where(ResponseStylePack.code == code)):
        code = style_code()
    row = ResponseStylePack(
        creator_id=user.id,
        name=payload.name.strip(),
        description=payload.description.strip(),
        code=code,
        base_style=payload.base_style,
        templates=templates,
        preview_text=payload.preview_text.strip(),
        status="draft",
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return _style_payload(row)


@router.patch("/styles/{style_id}")
async def update_style_pack(
    style_id: int,
    payload: StylePackUpdate,
    chat_id: int | None = Query(default=None),
    user: TelegramUser = Depends(current_telegram_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    await require_constructor_premium(session, chat_id=chat_id, user=user)
    row = await session.get(ResponseStylePack, style_id)
    if not row or row.creator_id != user.id:
        raise HTTPException(status_code=404, detail="Стиль не найден")
    if row.status == "approved":
        raise HTTPException(status_code=409, detail="Одобренный стиль нельзя изменить. Создайте его копию.")
    update = payload.model_dump(exclude_unset=True)
    if "templates" in update and update["templates"] is not None:
        update["templates"] = validate_templates(update["templates"])
    for key, value in update.items():
        if isinstance(value, str):
            value = value.strip()
        setattr(row, key, value)
    if row.status in {"pending", "rejected", "returned"}:
        row.status = "draft"
        row.moderation_note = None
    await session.commit()
    return _style_payload(row)


@router.post("/styles/{style_id}/submit")
async def submit_style_pack(
    style_id: int,
    chat_id: int | None = Query(default=None),
    user: TelegramUser = Depends(current_telegram_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    await require_constructor_premium(session, chat_id=chat_id, user=user)
    row = await session.get(ResponseStylePack, style_id)
    if not row or row.creator_id != user.id:
        raise HTTPException(status_code=404, detail="Стиль не найден")
    if not row.templates:
        raise HTTPException(status_code=422, detail="Стиль не содержит шаблонов")
    if row.status == "approved":
        raise HTTPException(status_code=409, detail="Стиль уже одобрен")
    row.status = "pending"
    row.submitted_at = utcnow()
    row.moderation_note = None
    await session.commit()
    return {"ok": True, "status": row.status, "code": None}


@router.get("/styles/search")
async def search_style_pack(
    code: str = Query(min_length=4, max_length=24),
    user: TelegramUser = Depends(current_telegram_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    normalized = code.strip().upper()
    row = await session.scalar(select(ResponseStylePack).where(func.upper(ResponseStylePack.code) == normalized))
    if not row or row.status != "approved":
        raise HTTPException(status_code=404, detail="Одобренный стиль с таким кодом не найден")
    creator = await session.get(User, row.creator_id)
    result = _style_payload(row, include_templates=False)
    result["creator"] = {
        "id": row.creator_id,
        "name": " ".join(filter(None, [creator.first_name, creator.last_name])) if creator else "Автор стиля",
        "username": creator.username if creator else None,
        "avatar_url": f"/api/avatars/{row.creator_id}",
    }
    return result


@router.get("/chats/{chat_id}/response-style")
async def chat_response_style(
    chat_id: int,
    user: TelegramUser = Depends(current_telegram_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    await ensure_admin(chat_id, user)
    values = await get_merged_settings(session, chat_id)
    result = {
        "style": values.get("response_style", "ordinary"),
        "code": values.get("custom_style_code") or None,
        "length": values.get("response_length", "full"),
        "delete_command_message": bool(values.get("delete_command_message", False)),
        "reply_in_thread": bool(values.get("reply_in_thread", False)),
    }
    if result["code"]:
        row = await session.scalar(select(ResponseStylePack).where(ResponseStylePack.code == result["code"]))
        result["custom_style"] = _style_payload(row, include_templates=False) if row and row.status == "approved" else None
    return result


@router.put("/chats/{chat_id}/response-style")
async def apply_chat_response_style(
    chat_id: int,
    payload: ChatStyleApplyRequest,
    user: TelegramUser = Depends(current_telegram_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    await ensure_admin(chat_id, user)
    custom_code = ""
    if payload.style == "custom":
        custom_code = (payload.code or "").strip().upper()
        if not custom_code:
            raise HTTPException(status_code=422, detail="Введите код кастомного стиля")
        row = await session.scalar(select(ResponseStylePack).where(func.upper(ResponseStylePack.code) == custom_code))
        if not row or row.status != "approved":
            raise HTTPException(status_code=404, detail="Одобренный стиль с таким кодом не найден")
        row.uses_count = int(row.uses_count or 0) + 1
    patch = {
        "response_style": payload.style,
        "custom_style_code": custom_code,
        "response_length": payload.length,
        "delete_command_message": payload.delete_command_message,
        "reply_in_thread": payload.reply_in_thread,
        "anime_replies": payload.style == "naruto",
    }
    values = await update_chat_settings(session, chat_id, patch)
    await session.commit()
    return {key: values.get(key) for key in patch}


@router.get("/admin/styles")
async def admin_style_queue(
    status_filter: str = Query(default="pending", alias="status"),
    user: TelegramUser = Depends(current_telegram_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    ensure_bot_admin(user)
    query = select(ResponseStylePack).order_by(ResponseStylePack.id.desc())
    if status_filter != "all":
        query = query.where(ResponseStylePack.status == status_filter)
    rows = (await session.scalars(query.limit(200))).all()
    items: list[dict[str, Any]] = []
    for row in rows:
        creator = await session.get(User, row.creator_id)
        item = _style_payload(row)
        item["creator"] = {
            "id": row.creator_id,
            "name": " ".join(filter(None, [creator.first_name, creator.last_name])) if creator else "User",
            "username": creator.username if creator else None,
            "avatar_url": f"/api/avatars/{row.creator_id}",
        }
        items.append(item)
    return {"items": items}


@router.post("/admin/styles/{style_id}/decision")
async def admin_style_decision(
    style_id: int,
    payload: StylePackModerationRequest,
    user: TelegramUser = Depends(current_telegram_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    ensure_bot_admin(user)
    row = await session.get(ResponseStylePack, style_id)
    if not row:
        raise HTTPException(status_code=404, detail="Стиль не найден")
    row.status = {"approve": "approved", "reject": "rejected", "return": "returned"}[payload.decision]
    row.moderation_note = payload.note.strip() or None
    row.reviewed_by = user.id
    row.reviewed_at = utcnow()
    await _admin_log(
        session,
        admin_id=user.id,
        action=f"style_{row.status}",
        entity_type="style",
        entity_id=row.id,
        details={
            "code": row.code if row.status == "approved" else None,
            "name": row.name,
            "note": row.moderation_note,
            "creator_id": row.creator_id,
        },
    )
    await session.commit()
    return _style_payload(row)


ADMIN_EVENT_LABELS: dict[str, str] = {
    "grant_premium": "Выдан Premium",
    "premium_granted": "Выдан Premium",
    "premium_bulk": "Выполнено массовое продление Premium",
    "coin_add": "Начислены AniCoin",
    "coin_subtract": "Списаны AniCoin",
    "coin_set": "Изменён баланс AniCoin",
    "coins_update": "Изменён баланс AniCoin",
    "global_ban": "Выдан глобальный бан",
    "chat_block": "Беседа заблокирована",
    "chat_started": "Беседа запущена",
    "chat_paused": "Беседа приостановлена",
    "chat_settings_update": "Обновлены настройки беседы",
    "broadcast_created": "Создана рассылка",
    "promo_created": "Создан промокод",
    "promo_toggled": "Изменён статус промокода",
    "style_approved": "Одобрен стиль ответов",
    "style_rejected": "Отклонён стиль ответов",
    "style_returned": "Стиль возвращён на доработку",
    "advertising_approved": "Одобрена реклама",
    "advertising_rejected": "Отклонена реклама",
    "support_reply": "Отправлен ответ поддержки",
    "case_closed": "Закрыто дело",
    "appeal_accepted": "Принята апелляция",
    "appeal_rejected": "Отклонена апелляция",
    "appeal_decision": "Рассмотрена апелляция",
    "admin_avatar_updated": "Обновлён аватар администратора",
    "admin_avatar_deleted": "Удалён аватар администратора",
    "data_export": "Экспортирован отчёт",
    "direct_message": "Отправлено личное сообщение",
    "report_status": "Изменён статус жалобы",
    "system_settings": "Обновлены системные настройки",
    "system_clear_cache": "Очищен системный кэш",
    "system_restart": "Запрошен перезапуск сервиса",
    "system_maintenance": "Включён режим обслуживания",
    "system_backup": "Создана резервная копия",
    "test_mode": "Изменён тестовый режим",
    "anti_raid_enabled": "Включена защита от рейдов",
    "anti_raid_disabled": "Выключена защита от рейдов",
    "emergency_enabled": "Включён аварийный режим",
    "emergency_disabled": "Выключен аварийный режим",
    "warning_removed": "Снято предупреждение",
    "restrictions_removed": "Сняты ограничения",
    "penalty_removed": "Снят штрафной статус",
    "unban": "Снята блокировка",
    "purge": "Очищены сообщения",
}


def _event_label(action: str) -> str:
    normalized = str(action or "").strip().casefold()
    if normalized in ADMIN_EVENT_LABELS:
        return ADMIN_EVENT_LABELS[normalized]
    # Never expose an untranslated internal identifier in the Russian panel.
    if normalized.endswith("_enabled"):
        return "Включена системная функция"
    if normalized.endswith("_disabled"):
        return "Выключена системная функция"
    return "Системное событие"


def _event_user_id(row: AdminActionLog) -> int:
    details = row.details or {}
    # entity_id is a Telegram user ID only for user-scoped events. Chat, style,
    # case, report and advertising IDs must never be resolved as users.
    if row.entity_type == "user" and row.entity_id:
        return int(row.entity_id)
    for key in ("user_id", "target_user_id", "creator_id"):
        value = details.get(key)
        if value is not None and str(value).lstrip("-").isdigit():
            return int(value)
    return int(row.admin_id or 0)


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
    premium_revenue = int(await session.scalar(select(func.coalesce(func.sum(Payment.stars), 0))) or 0)
    store_revenue = int(
        await session.scalar(
            select(func.coalesce(func.sum(StorePayment.stars), 0)).where(StorePayment.status == "paid")
        )
        or 0
    )
    revenue = premium_revenue + store_revenue
    revenue_byn, fx_snapshot = await exchange_rates.stars_to_byn(revenue)
    ad_revenue_stars = int(
        await session.scalar(
            select(func.coalesce(func.sum(StorePayment.stars), 0)).where(
                StorePayment.status == "paid", StorePayment.kind == "advertising"
            )
        ) or 0
    )
    ad_revenue_byn, _ = await exchange_rates.stars_to_byn(ad_revenue_stars)
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
        premium_spent = int(
            await session.scalar(
                select(func.coalesce(func.sum(Payment.stars), 0)).where(Payment.user_id == row.id)
            )
            or 0
        )
        store_spent = int(
            await session.scalar(
                select(func.coalesce(func.sum(StorePayment.stars), 0)).where(
                    StorePayment.user_id == row.id, StorePayment.status == "paid"
                )
            )
            or 0
        )
        spent = premium_spent + store_spent
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
                "avatar_url": f"/api/avatars/{row.id}",
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
    store_payment_rows = (
        await session.scalars(
            select(StorePayment).where(StorePayment.status == "paid").order_by(StorePayment.id.desc()).limit(200)
        )
    ).all()
    payments_payload.extend(
        {
            "id": f"STORE-{row.id}",
            "userId": row.user_id,
            "chatId": None,
            "item": "Покупка AniCoin" if row.kind == "coins" else f"Реклама #{row.reference_id}",
            "amount": row.stars,
            "date": _iso(row.paid_at or row.created_at),
            "status": "Оплачено",
        }
        for row in store_payment_rows
    )
    payments_payload.sort(key=lambda item: item.get("date") or "", reverse=True)

    report_rows = (await session.scalars(select(Report).order_by(Report.id.desc()).limit(200))).all()
    reports_payload = [
        {
            "id": f"REP-{row.id}",
            "rawId": row.id,
            "type": f"Жалоба · {row.category or 'другое'}",
            "text": row.reason,
            "category": row.category or "другое",
            "duplicates": int(row.duplicate_count or 1),
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
            "title": f"Жалоба в беседе {row.chat_id} · {row.category or 'другое'} · {int(row.duplicate_count or 1)} сигнал(а)",
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

    appeals_open = int(await session.scalar(select(func.count()).select_from(Appeal).where(Appeal.status == "new")) or 0)
    cases_open = int(await session.scalar(select(func.count()).select_from(ModerationCase).where(ModerationCase.status.in_(["open", "appealed", "changed"]))) or 0)
    threats_open = int(await session.scalar(select(func.count()).select_from(SecurityIncident).where(SecurityIncident.status == "open")) or 0)
    advertising_pending = int(await session.scalar(select(func.count()).select_from(AdvertisingOrder).where(AdvertisingOrder.status == "pending")) or 0)
    cases_opened = int(await session.scalar(select(func.count()).select_from(CaseOpening)) or 0)
    support_open = int(await session.scalar(select(func.count()).select_from(SupportTicket).where(SupportTicket.status == "open")) or 0)
    styles_pending = int(await session.scalar(select(func.count()).select_from(ResponseStylePack).where(ResponseStylePack.status == "pending")) or 0)

    log_rows = (
        await session.scalars(select(AdminActionLog).order_by(AdminActionLog.id.desc()).limit(200))
    ).all()
    logs_payload: list[dict[str, Any]] = []
    for row in log_rows:
        event_user_id = _event_user_id(row)
        event_user = await session.get(User, event_user_id) if event_user_id else None
        username = f"@{event_user.username}" if event_user and event_user.username else "без username"
        display_name = " ".join(filter(None, [event_user.first_name, event_user.last_name])) if event_user else "Пользователь"
        label = _event_label(row.action)
        logs_payload.append({
            "id": row.id,
            "entity_id": row.entity_id,
            "entity_type": row.entity_type,
            "user_id": event_user_id or None,
            "username": username,
            "display_name": display_name,
            "avatar_url": f"/api/avatars/{event_user_id}" if event_user_id else None,
            "time": _iso(row.created_at),
            "event": label,
            "text": f"{label} · {username} {event_user_id}".strip(),
            "tag": "premium" if "premium" in row.action else "chat" if row.entity_type == "chat" else "admin",
            "details": row.details,
        })


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
            "premiumUsers": premium_users,
            "active24": active_24,
            "active7": active_7,
            "chats": chat_count,
            "premiumChats": premium_chats,
            "expiringPremium": expiring_premium,
            "revenue": revenue,
            "revenueStars": revenue,
            "revenueByn": revenue_byn,
            "starBynRate": round(fx_snapshot.star_byn, 6),
            "usdBynRate": round(fx_snapshot.usd_byn, 4),
            "fxSource": fx_snapshot.source,
            "aniCoin": ani_coin,
            "reports": open_reports,
            "appeals": appeals_open,
            "cases": cases_open,
            "threats": threats_open,
            "advertising": advertising_pending,
            "advertisingRevenueStars": ad_revenue_stars,
            "advertisingRevenueByn": ad_revenue_byn,
            "caseOpenings": cases_opened,
            "support": support_open,
            "stylesPending": styles_pending,
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


# ---------------------------------------------------------------------------
# Live owner dashboard and the 18-part operations suite (v20)
# ---------------------------------------------------------------------------

@router.get("/admin/live")
async def admin_live(
    user: TelegramUser = Depends(current_telegram_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Lightweight one-second payload for the owner dashboard."""
    ensure_bot_admin(user)
    now = utcnow()
    day_ago = now - timedelta(days=1)
    week_ago = now - timedelta(days=7)
    resources = await resource_monitor.snapshot()

    active_grant = or_(
        EntityAccessGrant.is_lifetime.is_(True),
        EntityAccessGrant.premium_until > now,
    )
    premium_users = int(await session.scalar(
        select(func.count()).select_from(EntityAccessGrant).where(
            EntityAccessGrant.entity_type == "user", active_grant
        )
    ) or 0)
    premium_chat_grants = int(await session.scalar(
        select(func.count()).select_from(EntityAccessGrant).where(
            EntityAccessGrant.entity_type == "chat", active_grant
        )
    ) or 0)
    legacy_premium_chats = int(await session.scalar(
        select(func.count()).select_from(Chat).where(Chat.premium_until > now)
    ) or 0)
    premium_chats = max(premium_chat_grants, legacy_premium_chats)

    premium_revenue = int(await session.scalar(
        select(func.coalesce(func.sum(Payment.stars), 0))
    ) or 0)
    store_revenue = int(await session.scalar(
        select(func.coalesce(func.sum(StorePayment.stars), 0)).where(StorePayment.status == "paid")
    ) or 0)
    revenue_stars = premium_revenue + store_revenue
    revenue_byn, fx_snapshot = await exchange_rates.stars_to_byn(revenue_stars)
    ad_revenue_stars = int(await session.scalar(
        select(func.coalesce(func.sum(StorePayment.stars), 0)).where(
            StorePayment.status == "paid", StorePayment.kind == "advertising"
        )
    ) or 0)
    ad_revenue_byn, _ = await exchange_rates.stars_to_byn(ad_revenue_stars)
    ani_coin = int(await session.scalar(
        select(func.coalesce(func.sum(UserWallet.balance), 0))
    ) or 0)
    if not ani_coin:
        ani_coin = int(await session.scalar(
            select(func.coalesce(func.sum(Membership.coins), 0))
        ) or 0)

    stats = {
        "users": int(await session.scalar(select(func.count()).select_from(User)) or 0),
        "premiumUsers": premium_users,
        "active24": int(await session.scalar(select(func.count(func.distinct(Membership.user_id))).where(Membership.last_seen_at >= day_ago)) or 0),
        "active7": int(await session.scalar(select(func.count(func.distinct(Membership.user_id))).where(Membership.last_seen_at >= week_ago)) or 0),
        "chats": int(await session.scalar(select(func.count()).select_from(Chat).where(Chat.is_active.is_(True))) or 0),
        "premiumChats": premium_chats,
        "revenue": revenue_stars,
        "revenueStars": revenue_stars,
        "revenueByn": revenue_byn,
        "starBynRate": round(fx_snapshot.star_byn, 6),
        "usdBynRate": round(fx_snapshot.usd_byn, 4),
        "fxSource": fx_snapshot.source,
        "aniCoin": ani_coin,
        "reports": int(await session.scalar(select(func.count()).select_from(Report).where(Report.status.in_(["new", "in_progress"]))) or 0),
        "cases": int(await session.scalar(select(func.count()).select_from(ModerationCase).where(ModerationCase.status.in_(["open", "appealed", "changed"]))) or 0),
        "appeals": int(await session.scalar(select(func.count()).select_from(Appeal).where(Appeal.status == "new")) or 0),
        "securityAlerts": int(await session.scalar(select(func.count()).select_from(SecurityIncident).where(SecurityIncident.status == "open")) or 0),
        "threats": int(await session.scalar(select(func.count()).select_from(SecurityIncident).where(SecurityIncident.status == "open")) or 0),
        "advertising": int(await session.scalar(select(func.count()).select_from(AdvertisingOrder).where(AdvertisingOrder.status == "pending")) or 0),
        "advertisingRevenueStars": ad_revenue_stars,
        "advertisingRevenueByn": ad_revenue_byn,
        "caseOpenings": int(await session.scalar(select(func.count()).select_from(CaseOpening)) or 0),
        "support": int(await session.scalar(select(func.count()).select_from(SupportTicket).where(SupportTicket.status == "open")) or 0),
        "stylesPending": int(await session.scalar(select(func.count()).select_from(ResponseStylePack).where(ResponseStylePack.status == "pending")) or 0),
        "activeShifts": int(await session.scalar(select(func.count()).select_from(ModeratorShift).where(ModeratorShift.starts_at <= now, ModeratorShift.ends_at >= now, ModeratorShift.status.in_(["scheduled", "active"]))) or 0),
        "probations": int(await session.scalar(select(func.count()).select_from(StaffProbation).where(StaffProbation.status == "active")) or 0),
    }
    recent_incidents = (await session.scalars(
        select(SecurityIncident).where(SecurityIncident.status == "open").order_by(SecurityIncident.id.desc()).limit(5)
    )).all()
    return {
        "ok": True,
        "server_time": now.isoformat(),
        "refresh_interval_ms": 1000,
        "stats": stats,
        "resources": resources,
        "incidents": [
            {"id": row.id, "chat_id": row.chat_id, "kind": row.kind, "severity": row.severity, "actor_id": row.actor_id, "details": row.details, "created_at": _iso(row.created_at)}
            for row in recent_incidents
        ],
    }


@router.get("/admin/system/resources")
async def admin_system_resources(
    user: TelegramUser = Depends(current_telegram_user),
) -> dict[str, Any]:
    ensure_bot_admin(user)
    return {"ok": True, "resources": await resource_monitor.snapshot(), "refresh_interval_ms": 1000}


@router.get("/admin/system/resources/history")
async def admin_resource_history(
    minutes: int = Query(default=60, ge=1, le=10080),
    user: TelegramUser = Depends(current_telegram_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    ensure_bot_admin(user)
    since = utcnow() - timedelta(minutes=minutes)
    rows = (await session.scalars(
        select(ResourceSample).where(ResourceSample.collected_at >= since).order_by(ResourceSample.collected_at.asc()).limit(10000)
    )).all()
    return {
        "period_minutes": minutes,
        "samples": [
            {
                "time": _iso(row.collected_at), "provider": row.provider, "status": row.status,
                "cpu": row.cpu_percent, "memory_mb": row.memory_usage_mb,
                "memory_percent": row.memory_percent, "disk_mb": row.disk_usage_mb,
                "disk_percent": row.disk_percent, "uptime_seconds": row.uptime_seconds,
                "error": row.error,
            }
            for row in rows
        ],
    }


@router.get("/admin/cases")
async def admin_cases(
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=100, ge=1, le=500),
    user: TelegramUser = Depends(current_telegram_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    ensure_bot_admin(user)
    query = select(ModerationCase).order_by(ModerationCase.id.desc()).limit(limit)
    if status_filter:
        query = query.where(ModerationCase.status == status_filter)
    rows = (await session.scalars(query)).all()
    return {"items": [
        {"id": r.id, "code": r.code, "chat_id": r.chat_id, "target_id": r.target_id, "actor_id": r.actor_id,
         "action": r.action, "reason": r.reason, "duration_seconds": r.duration_seconds, "status": r.status,
         "severity": r.severity, "evidence_count": r.evidence_count, "created_at": _iso(r.created_at), "closed_at": _iso(r.closed_at)}
        for r in rows
    ]}


@router.get("/admin/cases/{case_id}")
async def admin_case_details(
    case_id: int,
    user: TelegramUser = Depends(current_telegram_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    ensure_bot_admin(user)
    row = await session.get(ModerationCase, case_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Дело не найдено")
    evidence = (await session.scalars(select(CaseEvidence).where(CaseEvidence.case_id == case_id).order_by(CaseEvidence.id.asc()))).all()
    appeals = (await session.scalars(select(Appeal).where(Appeal.case_id == case_id).order_by(Appeal.id.desc()))).all()
    return {
        "case": {"id": row.id, "code": row.code, "chat_id": row.chat_id, "target_id": row.target_id, "actor_id": row.actor_id, "action": row.action, "reason": row.reason, "status": row.status, "severity": row.severity, "duration_seconds": row.duration_seconds, "metadata": row.metadata_json, "created_at": _iso(row.created_at)},
        "evidence": [{"id": e.id, "message_id": e.message_id, "author_id": e.author_id, "text": e.text, "media": e.media, "created_at": _iso(e.created_at)} for e in evidence],
        "appeals": [{"id": a.id, "user_id": a.user_id, "text": a.text, "status": a.status, "decision": a.decision, "reviewer_id": a.reviewer_id, "created_at": _iso(a.created_at)} for a in appeals],
    }


@router.post("/admin/cases/{case_id}/close")
async def admin_close_case(
    case_id: int,
    payload: AdminCaseCloseRequest,
    user: TelegramUser = Depends(current_telegram_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    ensure_bot_admin(user)
    try:
        row = await close_case(session, case_id=case_id, actor_id=user.id, status=payload.status)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await _write_admin_log(session, admin_id=user.id, action="case_closed", entity_type="case", entity_id=case_id, details={"status": payload.status})
    await session.commit()
    return {"id": row.id, "status": row.status}


@router.get("/admin/appeals")
async def admin_appeals(
    status_filter: str | None = Query(default=None, alias="status"),
    user: TelegramUser = Depends(current_telegram_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    ensure_bot_admin(user)
    query = select(Appeal).order_by(Appeal.id.desc()).limit(300)
    if status_filter:
        query = query.where(Appeal.status == status_filter)
    rows = (await session.scalars(query)).all()
    return {"items": [{"id": r.id, "case_id": r.case_id, "chat_id": r.chat_id, "user_id": r.user_id, "text": r.text, "status": r.status, "reviewer_id": r.reviewer_id, "decision": r.decision, "decision_note": r.decision_note, "created_at": _iso(r.created_at), "reviewed_at": _iso(r.reviewed_at)} for r in rows]}


@router.post("/admin/appeals/{appeal_id}/decision")
async def admin_decide_appeal(
    appeal_id: int,
    payload: AdminAppealDecisionRequest,
    user: TelegramUser = Depends(current_telegram_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    ensure_bot_admin(user)
    try:
        row = await decide_appeal(session, appeal_id=appeal_id, reviewer_id=user.id, decision=payload.decision, note=payload.note)
    except (ValueError, PermissionError) as exc:
        raise http_error(exc) from exc

    # An accepted appeal reverses the practical Telegram restriction whenever the
    # original action is reversible. The database decision remains saved even if
    # Telegram temporarily refuses the network operation.
    reversed_action = None
    if payload.decision == "accept" and row.case_id:
        case = await session.get(ModerationCase, row.case_id)
        if case and case.target_id:
            membership = await session.scalar(select(Membership).where(Membership.chat_id == case.chat_id, Membership.user_id == case.target_id))
            action = str(case.action or "")
            try:
                if action in {"ban", "global_block", "ladder_ban"}:
                    await bot.unban_chat_member(case.chat_id, case.target_id, only_if_banned=True)
                    reversed_action = "unban"
                elif action in {"mute", "quarantine", "restrict_media", "restrict_links", "restrict_commands", "ladder_mute"}:
                    await bot.restrict_chat_member(case.chat_id, case.target_id, ChatPermissions(
                        can_send_messages=True, can_send_audios=True, can_send_documents=True,
                        can_send_photos=True, can_send_videos=True, can_send_video_notes=True,
                        can_send_voice_notes=True, can_send_polls=True, can_send_other_messages=True,
                        can_add_web_page_previews=True, can_invite_users=True,
                    ))
                    if membership:
                        membership.muted_until = None
                        membership.quarantined_until = None
                    reversed_action = "restrictions_removed"
                elif action in {"warn", "ladder_warn"} and membership:
                    membership.warnings = max(0, int(membership.warnings or 0) - 1)
                    reversed_action = "warning_removed"
                elif action in {"penalty_status", "ladder_penalty_violator", "ladder_penalty_severe"} and membership:
                    membership.penalty_status = "none"
                    membership.penalty_until = None
                    reversed_action = "penalty_removed"
            except Exception as telegram_error:
                reversed_action = f"telegram_error:{type(telegram_error).__name__}"

    await _write_admin_log(session, admin_id=user.id, action="appeal_decision", entity_type="appeal", entity_id=appeal_id, details={"decision": payload.decision, "reversed_action": reversed_action})
    await session.commit()
    return {"id": row.id, "status": row.status, "reversed_action": reversed_action}


@router.get("/admin/security/incidents")
async def admin_security_incidents(
    user: TelegramUser = Depends(current_telegram_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    ensure_bot_admin(user)
    rows = (await session.scalars(select(SecurityIncident).order_by(SecurityIncident.id.desc()).limit(300))).all()
    return {"items": [{"id": r.id, "chat_id": r.chat_id, "kind": r.kind, "severity": r.severity, "status": r.status, "actor_id": r.actor_id, "details": r.details, "created_at": _iso(r.created_at), "resolved_at": _iso(r.resolved_at)} for r in rows]}


async def _admin_security_mode(
    *, chat_id: int, kind: str, payload: AdminSecurityModeRequest,
    user: TelegramUser, session: AsyncSession,
) -> dict[str, Any]:
    try:
        row = await set_security_mode(session, chat_id=chat_id, actor_id=user.id, kind=kind, enabled=payload.enabled, reason=payload.reason, duration_seconds=payload.duration_seconds)
        if kind == "emergency":
            permissions = ChatPermissions(can_send_messages=False) if payload.enabled else ChatPermissions(can_send_messages=True, can_send_audios=True, can_send_documents=True, can_send_photos=True, can_send_videos=True, can_send_video_notes=True, can_send_voice_notes=True, can_send_polls=True, can_send_other_messages=True, can_add_web_page_previews=True, can_invite_users=True)
            try:
                await bot.set_chat_permissions(chat_id, permissions)
            except Exception:
                pass
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await _write_admin_log(session, admin_id=user.id, action=f"{kind}_{'enabled' if payload.enabled else 'disabled'}", entity_type="chat", entity_id=chat_id, details={"reason": payload.reason, "duration_seconds": payload.duration_seconds})
    await session.commit()
    return {"id": row.id, "enabled": payload.enabled, "kind": kind}


@router.post("/admin/chats/{chat_id}/anti-raid")
async def admin_anti_raid(
    chat_id: int, payload: AdminSecurityModeRequest,
    user: TelegramUser = Depends(current_telegram_user), session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    ensure_bot_admin(user)
    return await _admin_security_mode(chat_id=chat_id, kind="anti_raid", payload=payload, user=user, session=session)


@router.post("/admin/chats/{chat_id}/emergency")
async def admin_emergency(
    chat_id: int, payload: AdminSecurityModeRequest,
    user: TelegramUser = Depends(current_telegram_user), session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    ensure_bot_admin(user)
    return await _admin_security_mode(chat_id=chat_id, kind="emergency", payload=payload, user=user, session=session)


@router.post("/admin/chats/{chat_id}/test-mode")
async def admin_test_mode(
    chat_id: int, payload: AdminSecurityModeRequest,
    user: TelegramUser = Depends(current_telegram_user), session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    ensure_bot_admin(user)
    values = await update_chat_settings(session, chat_id, {"test_mode_enabled": payload.enabled})
    await _write_admin_log(session, admin_id=user.id, action="test_mode", entity_type="chat", entity_id=chat_id, details={"enabled": payload.enabled})
    await session.commit()
    return {"enabled": values["test_mode_enabled"]}


@router.put("/admin/chats/{chat_id}/punishment-ladder")
async def admin_punishment_ladder(
    chat_id: int, payload: AdminPunishmentLadderRequest,
    user: TelegramUser = Depends(current_telegram_user), session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    ensure_bot_admin(user)
    allowed_actions = {"warn", "mute", "penalty_violator", "penalty_severe", "ban"}
    cleaned: list[dict[str, Any]] = []
    for raw in payload.steps:
        action = str(raw.get("action") or "")
        warnings = int(raw.get("warnings") or 0)
        if action not in allowed_actions or warnings < 1:
            raise HTTPException(status_code=422, detail="Некорректный шаг лестницы наказаний")
        cleaned.append({"warnings": warnings, "action": action, "duration_seconds": max(0, int(raw.get("duration_seconds") or 0)), "label": str(raw.get("label") or action)[:100]})
    cleaned.sort(key=lambda item: item["warnings"])
    values = await update_chat_settings(session, chat_id, {"punishment_ladder_enabled": payload.enabled, "punishment_ladder": cleaned})
    await session.commit()
    return {"enabled": values["punishment_ladder_enabled"], "steps": values["punishment_ladder"]}


@router.put("/admin/chats/{chat_id}/response-style")
async def admin_response_style(
    chat_id: int, payload: AdminResponseStyleRequest,
    user: TelegramUser = Depends(current_telegram_user), session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    ensure_bot_admin(user)
    if payload.style == "custom":
        raise HTTPException(
            status_code=422,
            detail="Для кастомного стиля используйте поиск по коду в панели выбранной беседы",
        )
    patch = {"response_style": payload.style, "custom_style_code": "", "response_length": payload.length, "delete_command_message": payload.delete_command_message, "reply_in_thread": payload.reply_in_thread, "anime_replies": payload.style == "naruto"}
    values = await update_chat_settings(session, chat_id, patch)
    await session.commit()
    return {key: values[key] for key in patch}


@router.get("/admin/chats/{chat_id}/permission-overrides")
async def admin_permission_overrides(
    chat_id: int,
    user: TelegramUser = Depends(current_telegram_user), session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    ensure_bot_admin(user)
    rows = (await session.scalars(select(PermissionOverride).where(PermissionOverride.chat_id == chat_id).order_by(PermissionOverride.user_id, PermissionOverride.permission))).all()
    return {"items": [{"id": r.id, "user_id": r.user_id, "permission": r.permission, "allowed": r.allowed, "limit_value": r.limit_value, "expires_at": _iso(r.expires_at), "assigned_by": r.assigned_by} for r in rows]}


@router.put("/admin/chats/{chat_id}/permission-overrides")
async def admin_set_permission_override(
    chat_id: int, payload: AdminPermissionOverrideRequest,
    user: TelegramUser = Depends(current_telegram_user), session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    ensure_bot_admin(user)
    row = await session.scalar(select(PermissionOverride).where(PermissionOverride.chat_id == chat_id, PermissionOverride.user_id == payload.user_id, PermissionOverride.permission == payload.permission))
    if row is None:
        row = PermissionOverride(chat_id=chat_id, user_id=payload.user_id, permission=payload.permission, assigned_by=user.id)
        session.add(row)
    row.allowed = payload.allowed
    row.limit_value = payload.limit_value
    row.assigned_by = user.id
    row.expires_at = utcnow() + timedelta(seconds=payload.expires_seconds) if payload.expires_seconds else None
    await session.commit()
    return {"id": row.id, "allowed": row.allowed}


@router.get("/admin/staff/probations")
async def admin_probations(
    user: TelegramUser = Depends(current_telegram_user), session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    ensure_bot_admin(user)
    rows = (await session.scalars(select(StaffProbation).order_by(StaffProbation.id.desc()).limit(300))).all()
    return {"items": [{"id": r.id, "chat_id": r.chat_id, "user_id": r.user_id, "role": r.role, "status": r.status, "starts_at": _iso(r.starts_at), "ends_at": _iso(r.ends_at), "actions_count": r.actions_count, "reversed_actions": r.reversed_actions, "complaints_count": r.complaints_count} for r in rows]}


@router.post("/admin/staff/probations/{probation_id}/decision")
async def admin_probation_decision(
    probation_id: int, payload: AdminProbationDecisionRequest,
    user: TelegramUser = Depends(current_telegram_user), session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    ensure_bot_admin(user)
    row = await session.get(StaffProbation, probation_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Испытательный срок не найден")
    row.decision_by = user.id
    row.decision_note = payload.note or None
    if payload.decision == "extend":
        row.ends_at = utcnow() + timedelta(days=payload.extend_days)
        row.status = "active"
    elif payload.decision == "confirm":
        row.status = "passed"
    else:
        row.status = "failed"
        membership = await session.scalar(select(Membership).where(Membership.chat_id == row.chat_id, Membership.user_id == row.user_id))
        if membership and normalize_role(membership.role) != "creator":
            membership.role = "member"
    await session.commit()
    return {"id": row.id, "status": row.status}


@router.get("/admin/staff/performance")
async def admin_staff_performance(
    user: TelegramUser = Depends(current_telegram_user), session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    ensure_bot_admin(user)
    rows = (await session.scalars(select(ModeratorPerformance).order_by(ModeratorPerformance.rating.asc(), ModeratorPerformance.actions_count.desc()).limit(500))).all()
    return {"items": [{"id": r.id, "chat_id": r.chat_id, "user_id": r.user_id, "rating": r.rating, "actions_count": r.actions_count, "confirmed_actions": r.confirmed_actions, "reversed_actions": r.reversed_actions, "accepted_appeals": r.accepted_appeals, "complaints_count": r.complaints_count, "suspended_until": _iso(r.suspended_until)} for r in rows]}


@router.get("/admin/staff/shifts")
async def admin_shifts(
    user: TelegramUser = Depends(current_telegram_user), session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    ensure_bot_admin(user)
    rows = (await session.scalars(select(ModeratorShift).order_by(ModeratorShift.id.desc()).limit(300))).all()
    return {"items": [{"id": r.id, "chat_id": r.chat_id, "user_id": r.user_id, "assigned_by": r.assigned_by, "starts_at": _iso(r.starts_at), "ends_at": _iso(r.ends_at), "temporary_role": r.temporary_role, "status": r.status, "reports_handled": r.reports_handled, "warnings_issued": r.warnings_issued, "mutes_issued": r.mutes_issued} for r in rows]}


@router.post("/admin/chats/{chat_id}/shifts")
async def admin_create_shift(
    chat_id: int, payload: AdminShiftCreateRequest,
    user: TelegramUser = Depends(current_telegram_user), session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    ensure_bot_admin(user)
    try:
        starts = datetime.fromisoformat(payload.starts_at.replace("Z", "+00:00"))
        ends = datetime.fromisoformat(payload.ends_at.replace("Z", "+00:00"))
        if starts.tzinfo is None: starts = starts.replace(tzinfo=timezone.utc)
        if ends.tzinfo is None: ends = ends.replace(tzinfo=timezone.utc)
        row = await create_shift(session, chat_id=chat_id, user_id=payload.user_id, actor_id=user.id, starts_at=starts, ends_at=ends, temporary_role=payload.temporary_role)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await session.commit()
    return {"id": row.id, "status": row.status}


@router.get("/admin/backups")
async def admin_backups(
    user: TelegramUser = Depends(current_telegram_user), session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    ensure_bot_admin(user)
    rows = (await session.scalars(select(BackupSnapshot).order_by(BackupSnapshot.id.desc()).limit(100))).all()
    return {"items": [{"id": r.id, "chat_id": r.chat_id, "kind": r.kind, "checksum": r.checksum, "created_by": r.created_by, "created_at": _iso(r.created_at), "restored_at": _iso(r.restored_at)} for r in rows]}


@router.post("/admin/chats/{chat_id}/backups")
async def admin_create_backup(
    chat_id: int,
    user: TelegramUser = Depends(current_telegram_user), session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    ensure_bot_admin(user)
    try:
        row = await create_backup_snapshot(session, chat_id=chat_id, created_by=user.id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await session.commit()
    return {"id": row.id, "checksum": row.checksum}


@router.post("/admin/backups/{snapshot_id}/restore")
async def admin_restore_backup(
    snapshot_id: int, payload: AdminBackupRestoreRequest,
    user: TelegramUser = Depends(current_telegram_user), session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    ensure_bot_admin(user)
    if not payload.confirm:
        raise HTTPException(status_code=409, detail="Для восстановления передайте confirm=true")
    try:
        row = await restore_backup_snapshot(session, snapshot_id=snapshot_id, actor_id=user.id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await session.commit()
    return {"id": row.id, "restored_at": _iso(row.restored_at)}


@router.get("/admin/weekly-reports")
async def admin_weekly_reports(
    chat_id: int | None = None,
    user: TelegramUser = Depends(current_telegram_user), session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    ensure_bot_admin(user)
    query = select(WeeklyReportSnapshot).order_by(WeeklyReportSnapshot.id.desc()).limit(100)
    if chat_id is not None:
        query = query.where(WeeklyReportSnapshot.chat_id == chat_id)
    rows = (await session.scalars(query)).all()
    return {"items": [{"id": r.id, "chat_id": r.chat_id, "period_start": _iso(r.period_start), "period_end": _iso(r.period_end), "payload": r.payload, "created_at": _iso(r.created_at)} for r in rows]}


@router.post("/admin/chats/{chat_id}/weekly-report")
async def admin_generate_weekly_report(
    chat_id: int,
    user: TelegramUser = Depends(current_telegram_user), session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    ensure_bot_admin(user)
    row = await generate_weekly_report(session, chat_id=chat_id, generated_by=user.id)
    await session.commit()
    return {"id": row.id, "payload": row.payload}


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
