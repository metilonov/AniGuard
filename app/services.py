from __future__ import annotations

import json
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from aiogram import Bot
from aiogram.enums import ChatMemberStatus
from aiogram.types import ChatPermissions, LabeledPrice, User as TgUser
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    CaptchaChallenge,
    Chat,
    Membership,
    ModerationLog,
    ModerationRule,
    Payment,
    Report,
    RPCommand,
    User,
    default_chat_settings,
)
from app.pricing import get_plan


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def is_premium(chat: Chat) -> bool:
    premium_until = as_utc(chat.premium_until)
    return bool(premium_until and premium_until > utcnow())


async def upsert_user(session: AsyncSession, user: TgUser | Any) -> User:
    db_user = await session.get(User, int(user.id))
    if db_user is None:
        db_user = User(
            id=int(user.id),
            username=getattr(user, "username", None),
            first_name=getattr(user, "first_name", "User") or "User",
            last_name=getattr(user, "last_name", None),
        )
        session.add(db_user)
    else:
        db_user.username = getattr(user, "username", None)
        db_user.first_name = getattr(user, "first_name", db_user.first_name) or db_user.first_name
        db_user.last_name = getattr(user, "last_name", None)
    await session.flush()
    return db_user


async def upsert_chat(session: AsyncSession, chat_obj: Any) -> Chat:
    chat = await session.get(Chat, int(chat_obj.id))
    title = getattr(chat_obj, "title", None) or getattr(chat_obj, "full_name", None) or "Telegram chat"
    if chat is None:
        chat = Chat(
            id=int(chat_obj.id),
            title=title,
            username=getattr(chat_obj, "username", None),
            settings=default_chat_settings(),
        )
        session.add(chat)
    else:
        chat.title = title
        chat.username = getattr(chat_obj, "username", None)
        chat.is_active = True
    await session.flush()
    return chat


async def ensure_membership(
    session: AsyncSession,
    chat_id: int,
    user_id: int,
    role: str = "member",
) -> Membership:
    membership = await session.scalar(
        select(Membership).where(Membership.chat_id == chat_id, Membership.user_id == user_id)
    )
    if membership is None:
        membership = Membership(chat_id=chat_id, user_id=user_id, role=role)
        session.add(membership)
    else:
        membership.role = role or membership.role
        membership.last_seen_at = utcnow()
    await session.flush()
    return membership


async def get_chat_or_raise(session: AsyncSession, chat_id: int) -> Chat:
    chat = await session.get(Chat, chat_id)
    if chat is None:
        raise ValueError("Chat is not registered. Add the bot to the chat and send /panel there first.")
    return chat


async def get_merged_settings(session: AsyncSession, chat_id: int) -> dict[str, Any]:
    chat = await get_chat_or_raise(session, chat_id)
    merged = default_chat_settings()
    merged.update(chat.settings or {})
    return merged


async def update_chat_settings(
    session: AsyncSession,
    chat_id: int,
    patch: dict[str, Any],
) -> dict[str, Any]:
    chat = await get_chat_or_raise(session, chat_id)
    merged = default_chat_settings()
    merged.update(chat.settings or {})
    allowed = set(merged)
    for key, value in patch.items():
        if key in allowed:
            merged[key] = value
    chat.settings = merged
    await session.flush()
    return merged


async def user_is_chat_admin(bot: Bot, chat_id: int, user_id: int) -> bool:
    member = await bot.get_chat_member(chat_id, user_id)
    return member.status in {ChatMemberStatus.CREATOR, ChatMemberStatus.ADMINISTRATOR}


async def require_chat_admin(bot: Bot, chat_id: int, user_id: int) -> None:
    if not await user_is_chat_admin(bot, chat_id, user_id):
        raise PermissionError("Only chat administrators can perform this action")


async def list_admin_chats(
    session: AsyncSession,
    bot: Bot,
    user_id: int,
    limit: int = 50,
) -> list[dict[str, Any]]:
    chats = (await session.scalars(select(Chat).where(Chat.is_active.is_(True)).limit(limit))).all()
    result: list[dict[str, Any]] = []
    for chat in chats:
        try:
            if await user_is_chat_admin(bot, chat.id, user_id):
                result.append(
                    {
                        "id": chat.id,
                        "title": chat.title,
                        "username": chat.username,
                        "premium": is_premium(chat),
                        "premium_until": chat.premium_until.isoformat() if chat.premium_until else None,
                        "premium_plan": chat.premium_plan,
                    }
                )
        except Exception:
            continue
    return result


async def add_log(
    session: AsyncSession,
    chat_id: int,
    action: str,
    actor_id: int | None = None,
    target_id: int | None = None,
    reason: str | None = None,
    duration_seconds: int | None = None,
    details: dict[str, Any] | None = None,
) -> ModerationLog:
    row = ModerationLog(
        chat_id=chat_id,
        action=action,
        actor_id=actor_id,
        target_id=target_id,
        reason=reason,
        duration_seconds=duration_seconds,
        details=details or {},
    )
    session.add(row)
    await session.flush()
    return row


def full_permissions() -> ChatPermissions:
    return ChatPermissions(
        can_send_messages=True,
        can_send_audios=True,
        can_send_documents=True,
        can_send_photos=True,
        can_send_videos=True,
        can_send_video_notes=True,
        can_send_voice_notes=True,
        can_send_polls=True,
        can_send_other_messages=True,
        can_add_web_page_previews=True,
        can_invite_users=True,
        can_pin_messages=False,
        can_change_info=False,
        can_manage_topics=False,
    )


def muted_permissions() -> ChatPermissions:
    return ChatPermissions(can_send_messages=False)


def quarantine_permissions() -> ChatPermissions:
    return ChatPermissions(
        can_send_messages=True,
        can_send_audios=False,
        can_send_documents=False,
        can_send_photos=False,
        can_send_videos=False,
        can_send_video_notes=False,
        can_send_voice_notes=False,
        can_send_polls=False,
        can_send_other_messages=False,
        can_add_web_page_previews=False,
        can_invite_users=False,
        can_pin_messages=False,
        can_change_info=False,
        can_manage_topics=False,
    )


async def perform_action(
    session: AsyncSession,
    bot: Bot,
    *,
    chat_id: int,
    actor_id: int,
    action: str,
    target_id: int | None = None,
    duration_seconds: int | None = None,
    amount: int | None = None,
    reason: str = "",
) -> dict[str, Any]:
    chat = await get_chat_or_raise(session, chat_id)
    settings = await get_merged_settings(session, chat_id)
    premium = is_premium(chat)
    reason = reason.strip() or "Причина не указана"

    target_required = {"warn", "unwarn", "mute", "unmute", "ban", "unban", "quarantine", "case"}
    premium_actions = {"quarantine", "susanoo", "case"}
    if action in target_required and target_id is None:
        raise ValueError("Target user is required")
    if action in premium_actions and not premium:
        raise PermissionError("This action requires AniGuard Premium")
    if action == "quarantine" and not settings.get("premium_quarantine", True):
        raise PermissionError("The Quarantine Pro module is disabled in settings")
    if action == "case" and not settings.get("premium_cases", True):
        raise PermissionError("The Cases and Evidence module is disabled in settings")

    duration_seconds = duration_seconds or int(settings["default_mute_seconds"])
    result: dict[str, Any] = {"action": action, "chat_id": chat_id, "target_id": target_id}

    if target_id is not None:
        membership = await ensure_membership(session, chat_id, target_id)
    else:
        membership = None

    if action == "warn":
        assert membership is not None
        membership.warnings += 1
        result["warnings"] = membership.warnings
        if settings["warn_threshold"] and membership.warnings >= int(settings["warn_threshold"]):
            until = utcnow() + timedelta(seconds=duration_seconds)
            await bot.restrict_chat_member(chat_id, target_id, muted_permissions(), until_date=until)
            membership.muted_until = until
            result["auto_muted"] = True
    elif action == "unwarn":
        assert membership is not None
        membership.warnings = max(0, membership.warnings - 1)
        result["warnings"] = membership.warnings
    elif action == "mute":
        until = utcnow() + timedelta(seconds=duration_seconds)
        await bot.restrict_chat_member(chat_id, target_id, muted_permissions(), until_date=until)
        assert membership is not None
        membership.muted_until = until
        result["until"] = until.isoformat()
    elif action == "unmute":
        await bot.restrict_chat_member(chat_id, target_id, full_permissions())
        assert membership is not None
        membership.muted_until = None
    elif action == "ban":
        until = utcnow() + timedelta(seconds=duration_seconds) if duration_seconds else None
        await bot.ban_chat_member(chat_id, target_id, until_date=until)
        result["until"] = until.isoformat() if until else None
    elif action == "unban":
        await bot.unban_chat_member(chat_id, target_id, only_if_banned=True)
    elif action == "purge":
        if not amount:
            raise ValueError("Message count is required")
        result["requested_count"] = amount
    elif action == "slow":
        delay = int(amount or duration_seconds or 15)
        settings["slow_mode_seconds"] = max(0, min(delay, 3600))
        chat.settings = settings
        result["slow_mode_seconds"] = settings["slow_mode_seconds"]
    elif action == "lock":
        chat_info = await bot.get_chat(chat_id)
        if chat_info.permissions:
            settings["permissions_before_lock"] = chat_info.permissions.model_dump(exclude_none=True)
        await bot.set_chat_permissions(chat_id, ChatPermissions(can_send_messages=False))
        settings["chat_locked"] = True
        chat.settings = settings
    elif action == "unlock":
        previous = settings.get("permissions_before_lock")
        permissions = ChatPermissions(**previous) if isinstance(previous, dict) and previous else full_permissions()
        await bot.set_chat_permissions(chat_id, permissions)
        settings["chat_locked"] = False
        settings.pop("permissions_before_lock", None)
        chat.settings = settings
    elif action == "quarantine":
        until = utcnow() + timedelta(seconds=duration_seconds)
        await bot.restrict_chat_member(chat_id, target_id, quarantine_permissions(), until_date=until)
        assert membership is not None
        membership.quarantined_until = until
        result["until"] = until.isoformat()
    elif action == "susanoo":
        chat_info = await bot.get_chat(chat_id)
        if chat_info.permissions and "permissions_before_lock" not in settings:
            settings["permissions_before_lock"] = chat_info.permissions.model_dump(exclude_none=True)
        await bot.set_chat_permissions(chat_id, ChatPermissions(can_send_messages=False))
        settings["chat_locked"] = True
        settings["slow_mode_seconds"] = 30
        chat.settings = settings
        result["duration_seconds"] = duration_seconds
    elif action == "case":
        result["case_created"] = True
    else:
        raise ValueError("Unsupported moderation action")

    await add_log(
        session,
        chat_id,
        action,
        actor_id=actor_id,
        target_id=target_id,
        reason=reason,
        duration_seconds=duration_seconds,
        details={"amount": amount, **result},
    )
    await session.flush()
    return result


async def create_report(
    session: AsyncSession,
    *,
    chat_id: int,
    reporter_id: int,
    target_id: int,
    message_id: int,
    reason: str,
) -> Report:
    report = Report(
        chat_id=chat_id,
        reporter_id=reporter_id,
        target_id=target_id,
        message_id=message_id,
        reason=reason or "Причина не указана",
    )
    session.add(report)
    await session.flush()
    return report


async def create_captcha(
    session: AsyncSession,
    chat_id: int,
    user_id: int,
    expires_in: int,
) -> CaptchaChallenge:
    existing = await session.scalar(
        select(CaptchaChallenge).where(
            CaptchaChallenge.chat_id == chat_id,
            CaptchaChallenge.user_id == user_id,
        )
    )
    if existing:
        await session.delete(existing)
        await session.flush()
    challenge = CaptchaChallenge(
        chat_id=chat_id,
        user_id=user_id,
        token=secrets.token_urlsafe(18),
        expires_at=utcnow() + timedelta(seconds=expires_in),
    )
    session.add(challenge)
    await session.flush()
    return challenge


async def grant_premium(
    session: AsyncSession,
    *,
    user_id: int,
    chat_id: int,
    plan_code: str,
    stars: int,
    payload: str,
    charge_id: str,
) -> Chat:
    existing = await session.scalar(
        select(Payment).where(Payment.telegram_payment_charge_id == charge_id)
    )
    if existing:
        return await get_chat_or_raise(session, chat_id)

    plan = get_plan(plan_code)
    if plan.stars != stars:
        raise ValueError("Payment amount does not match the selected plan")
    chat = await get_chat_or_raise(session, chat_id)
    current_until = as_utc(chat.premium_until)
    start = current_until if current_until and current_until > utcnow() else utcnow()
    chat.premium_until = start + timedelta(days=plan.days)
    chat.premium_plan = plan.code
    session.add(
        Payment(
            user_id=user_id,
            chat_id=chat_id,
            plan_code=plan.code,
            stars=stars,
            invoice_payload=payload,
            telegram_payment_charge_id=charge_id,
        )
    )
    await add_log(
        session,
        chat_id,
        "premium_purchase",
        actor_id=user_id,
        reason=f"Plan {plan.code}, {plan.days} days",
        details={"stars": stars, "charge_id": charge_id},
    )
    await session.flush()
    return chat


async def create_invoice_link(
    bot: Bot,
    *,
    user_id: int,
    chat_id: int,
    plan_code: str,
) -> tuple[str, str]:
    plan = get_plan(plan_code)
    nonce = secrets.token_hex(5)
    payload = f"agp:{chat_id}:{user_id}:{plan.code}:{nonce}"
    link = await bot.create_invoice_link(
        title=f"AniGuard {plan.title}",
        description=plan.description,
        payload=payload,
        currency="XTR",
        prices=[LabeledPrice(label=f"Premium {plan.days} дней", amount=plan.stars)],
        provider_token="",
    )
    return link, payload


def parse_payment_payload(payload: str) -> tuple[int, int, str]:
    parts = payload.split(":")
    if len(parts) != 5 or parts[0] != "agp":
        raise ValueError("Invalid invoice payload")
    return int(parts[1]), int(parts[2]), parts[3]


async def dashboard_data(session: AsyncSession, chat_id: int) -> dict[str, Any]:
    chat = await get_chat_or_raise(session, chat_id)
    members = await session.scalar(
        select(func.count()).select_from(Membership).where(Membership.chat_id == chat_id)
    )
    logs = await session.scalar(
        select(func.count()).select_from(ModerationLog).where(
            ModerationLog.chat_id == chat_id,
            ModerationLog.created_at >= utcnow() - timedelta(days=1),
        )
    )
    reports = await session.scalar(
        select(func.count()).select_from(Report).where(
            Report.chat_id == chat_id,
            Report.status.in_(["new", "in_progress"]),
        )
    )
    rp_count = await session.scalar(
        select(func.count()).select_from(RPCommand).where(RPCommand.chat_id == chat_id)
    )
    rule_count = await session.scalar(
        select(func.count()).select_from(ModerationRule).where(ModerationRule.chat_id == chat_id)
    )
    return {
        "chat": {
            "id": chat.id,
            "title": chat.title,
            "premium": is_premium(chat),
            "premium_until": chat.premium_until.isoformat() if chat.premium_until else None,
        },
        "metrics": {
            "members": members or 0,
            "actions_today": logs or 0,
            "open_reports": reports or 0,
            "rp_commands": rp_count or 0,
            "rules": rule_count or 0,
        },
        "settings": await get_merged_settings(session, chat_id),
    }
