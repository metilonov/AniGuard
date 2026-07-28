from __future__ import annotations

import json
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from aiogram import Bot
from aiogram.enums import ChatMemberStatus
from aiogram.types import ChatPermissions, LabeledPrice, User as TgUser
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    ActiveRestriction,
    AdminActionLog,
    BlockedEntity,
    CaptchaChallenge,
    Chat,
    CustomCommand,
    EntityAccessGrant,
    GameCommand,
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


async def get_entity_grant(
    session: AsyncSession,
    entity_type: str,
    entity_id: int,
) -> EntityAccessGrant | None:
    return await session.scalar(
        select(EntityAccessGrant).where(
            EntityAccessGrant.entity_type == entity_type,
            EntityAccessGrant.entity_id == entity_id,
        )
    )


async def entity_has_premium(
    session: AsyncSession,
    entity_type: str,
    entity_id: int,
) -> bool:
    if entity_type == "chat":
        chat = await session.get(Chat, entity_id)
        if chat and is_premium(chat):
            return True
    grant = await get_entity_grant(session, entity_type, entity_id)
    if grant and grant.is_lifetime:
        return True
    premium_until = as_utc(grant.premium_until) if grant else None
    return bool(premium_until and premium_until > utcnow())


async def entity_premium_details(
    session: AsyncSession,
    entity_type: str,
    entity_id: int,
) -> dict[str, Any]:
    """Return the effective Premium state for a user or a chat."""
    direct_until: datetime | None = None
    direct_plan: str | None = None
    if entity_type == "chat":
        chat = await session.get(Chat, entity_id)
        if chat and is_premium(chat):
            direct_until = as_utc(chat.premium_until)
            direct_plan = chat.premium_plan

    grant = await get_entity_grant(session, entity_type, entity_id)
    if grant and grant.is_lifetime:
        return {
            "active": True,
            "until": None,
            "plan": grant.premium_plan or direct_plan,
            "lifetime": True,
        }
    grant_until = as_utc(grant.premium_until) if grant else None
    candidates = [value for value in (direct_until, grant_until) if value and value > utcnow()]
    until = max(candidates) if candidates else None
    return {
        "active": until is not None,
        "until": until,
        "plan": (grant.premium_plan if grant and grant_until == until else direct_plan),
        "lifetime": False,
    }


async def chat_owner_id(session: AsyncSession, chat_id: int) -> int | None:
    chat = await session.get(Chat, chat_id)
    settings = (chat.settings or {}) if chat else {}
    configured = settings.get("owner_user_id")
    if configured:
        try:
            return int(configured)
        except (TypeError, ValueError):
            pass
    return await session.scalar(
        select(Membership.user_id).where(
            Membership.chat_id == chat_id,
            Membership.role == "owner",
        ).limit(1)
    )


async def premium_access_details(
    session: AsyncSession,
    *,
    chat_id: int,
    user_id: int | None = None,
) -> dict[str, Any]:
    """Resolve Premium for a group.

    A group has Premium when it has its own active subscription or while its
    Telegram creator has a user Premium grant.  An ordinary administrator's
    personal Premium is deliberately not inherited by somebody else's group.
    """
    chat_details = await entity_premium_details(session, "chat", chat_id)
    owner_id = await chat_owner_id(session, chat_id)
    owner_details = (
        await entity_premium_details(session, "user", owner_id)
        if owner_id is not None
        else {"active": False, "until": None, "plan": None, "lifetime": False}
    )

    if chat_details["active"]:
        source = "group"
        effective = chat_details
    elif owner_details["active"]:
        source = "owner"
        effective = owner_details
    else:
        source = None
        effective = {"active": False, "until": None, "plan": None, "lifetime": False}

    return {
        "active": bool(effective["active"]),
        "source": source,
        "until": effective["until"],
        "plan": effective["plan"],
        "lifetime": bool(effective["lifetime"]),
        "owner_id": owner_id,
        "group": chat_details,
        "owner": owner_details,
        "request_user_id": user_id,
    }


async def has_premium_access(
    session: AsyncSession,
    *,
    chat_id: int,
    user_id: int | None = None,
) -> bool:
    return bool((await premium_access_details(session, chat_id=chat_id, user_id=user_id))["active"])


async def get_block_record(
    session: AsyncSession,
    entity_type: str,
    entity_id: int,
) -> BlockedEntity | None:
    row = await session.scalar(
        select(BlockedEntity).where(
            BlockedEntity.entity_type == entity_type,
            BlockedEntity.entity_id == entity_id,
            BlockedEntity.is_active.is_(True),
        )
    )
    if row and row.blocked_until:
        blocked_until = as_utc(row.blocked_until)
        if blocked_until and blocked_until <= utcnow():
            row.is_active = False
            await session.flush()
            return None
    return row


async def entity_is_blocked(session: AsyncSession, entity_type: str, entity_id: int) -> bool:
    return await get_block_record(session, entity_type, entity_id) is not None


async def ensure_entity_available(
    session: AsyncSession,
    *,
    user_id: int | None = None,
    chat_id: int | None = None,
) -> None:
    if user_id is not None:
        row = await get_block_record(session, "user", user_id)
        if row:
            raise PermissionError(f"Доступ к AniGuard заблокирован. Причина: {row.reason}")
    if chat_id is not None:
        row = await get_block_record(session, "chat", chat_id)
        if row:
            raise PermissionError(f"AniGuard отключён для этой группы. Причина: {row.reason}")


async def set_entity_premium(
    session: AsyncSession,
    *,
    entity_type: str,
    entity_id: int,
    days: int,
    admin_id: int,
    permanent: bool = False,
    plan: str = "admin",
    note: str = "",
) -> EntityAccessGrant:
    if entity_type not in {"user", "chat"}:
        raise ValueError("entity_type должен быть user или chat")
    if days < 0 or days > 3650:
        raise ValueError("Срок Premium должен быть от 0 до 3650 дней")
    row = await get_entity_grant(session, entity_type, entity_id)
    if row is None:
        row = EntityAccessGrant(
            entity_type=entity_type,
            entity_id=entity_id,
            granted_by=admin_id,
        )
        session.add(row)
    row.granted_by = admin_id
    row.premium_plan = plan
    row.note = note.strip() or None
    row.is_lifetime = bool(permanent)
    if permanent:
        row.premium_until = None
    elif days == 0:
        row.premium_until = None
        row.premium_plan = None
    else:
        current = as_utc(row.premium_until)
        start = current if current and current > utcnow() else utcnow()
        row.premium_until = start + timedelta(days=days)
    if entity_type == "chat":
        chat = await session.get(Chat, entity_id)
        if chat:
            chat.premium_until = utcnow() + timedelta(days=3650) if permanent else row.premium_until
            chat.premium_plan = plan if (permanent or row.premium_until) else None
    session.add(AdminActionLog(
        admin_id=admin_id,
        action="premium_grant" if days else "premium_revoke",
        entity_type=entity_type,
        entity_id=entity_id,
        details={"days": days, "permanent": permanent, "plan": plan, "note": note},
    ))
    await session.flush()
    return row


async def set_entity_block(
    session: AsyncSession,
    *,
    entity_type: str,
    entity_id: int,
    blocked: bool,
    admin_id: int,
    reason: str = "",
    duration_seconds: int | None = None,
) -> BlockedEntity:
    if entity_type not in {"user", "chat"}:
        raise ValueError("entity_type должен быть user или chat")
    row = await session.scalar(
        select(BlockedEntity).where(
            BlockedEntity.entity_type == entity_type,
            BlockedEntity.entity_id == entity_id,
        )
    )
    if row is None:
        row = BlockedEntity(
            entity_type=entity_type,
            entity_id=entity_id,
            blocked_by=admin_id,
        )
        session.add(row)
    row.is_active = blocked
    row.blocked_by = admin_id
    row.reason = reason.strip() or "Причина не указана"
    row.blocked_until = (
        utcnow() + timedelta(seconds=duration_seconds)
        if blocked and duration_seconds and duration_seconds > 0
        else None
    )
    session.add(AdminActionLog(
        admin_id=admin_id,
        action="block" if blocked else "unblock",
        entity_type=entity_type,
        entity_id=entity_id,
        details={"reason": row.reason, "duration_seconds": duration_seconds},
    ))
    await session.flush()
    return row


async def set_restriction(
    session: AsyncSession,
    *,
    chat_id: int,
    user_id: int,
    kind: str,
    duration_seconds: int,
    actor_id: int,
    reason: str,
) -> ActiveRestriction:
    row = await session.scalar(
        select(ActiveRestriction).where(
            ActiveRestriction.chat_id == chat_id,
            ActiveRestriction.user_id == user_id,
            ActiveRestriction.kind == kind,
        )
    )
    if row is None:
        row = ActiveRestriction(chat_id=chat_id, user_id=user_id, kind=kind, created_by=actor_id)
        session.add(row)
    row.created_by = actor_id
    row.reason = reason
    row.expires_at = None if duration_seconds == 0 else utcnow() + timedelta(seconds=duration_seconds)
    await session.flush()
    return row


async def clear_restriction(
    session: AsyncSession,
    *,
    chat_id: int,
    user_id: int,
    kind: str,
) -> None:
    await session.execute(
        delete(ActiveRestriction).where(
            ActiveRestriction.chat_id == chat_id,
            ActiveRestriction.user_id == user_id,
            ActiveRestriction.kind == kind,
        )
    )
    await session.flush()


async def active_restrictions(
    session: AsyncSession,
    *,
    chat_id: int,
    user_id: int,
) -> set[str]:
    rows = (
        await session.scalars(
            select(ActiveRestriction).where(
                ActiveRestriction.chat_id == chat_id,
                ActiveRestriction.user_id == user_id,
            )
        )
    ).all()
    result: set[str] = set()
    for row in rows:
        expires_at = as_utc(row.expires_at)
        if expires_at and expires_at <= utcnow():
            await session.delete(row)
        else:
            result.add(row.kind)
    await session.flush()
    return result


async def upsert_user(session: AsyncSession, user: TgUser | Any) -> User:
    user_id = int(user.id)
    db_user = await session.get(User, user_id)
    if db_user is None:
        candidate = User(
            id=user_id,
            username=getattr(user, "username", None),
            first_name=getattr(user, "first_name", "User") or "User",
            last_name=getattr(user, "last_name", None),
        )
        try:
            async with session.begin_nested():
                session.add(candidate)
                await session.flush()
            db_user = candidate
        except IntegrityError:
            db_user = await session.get(User, user_id, populate_existing=True)
            if db_user is None:
                raise
    db_user.username = getattr(user, "username", None)
    db_user.first_name = getattr(user, "first_name", db_user.first_name) or db_user.first_name
    db_user.last_name = getattr(user, "last_name", None)
    await session.flush()
    return db_user


def _apply_chat_photo(chat: Chat, chat_obj: Any) -> None:
    """Copy Telegram group photo identifiers when the object contains them."""
    if not hasattr(chat_obj, "photo"):
        return
    photo = getattr(chat_obj, "photo", None)
    if photo is None:
        chat.photo_small_file_id = None
        chat.photo_big_file_id = None
        chat.photo_unique_id = None
        return
    chat.photo_small_file_id = getattr(photo, "small_file_id", None)
    chat.photo_big_file_id = getattr(photo, "big_file_id", None)
    chat.photo_unique_id = (
        getattr(photo, "big_file_unique_id", None)
        or getattr(photo, "small_file_unique_id", None)
    )


async def upsert_chat(session: AsyncSession, chat_obj: Any) -> Chat:
    chat_id = int(chat_obj.id)
    title = getattr(chat_obj, "title", None) or getattr(chat_obj, "full_name", None) or "Telegram chat"
    chat = await session.get(Chat, chat_id)
    if chat is None:
        candidate = Chat(
            id=chat_id,
            title=title,
            username=getattr(chat_obj, "username", None),
            settings=default_chat_settings(),
        )
        _apply_chat_photo(candidate, chat_obj)
        try:
            async with session.begin_nested():
                session.add(candidate)
                await session.flush()
            chat = candidate
        except IntegrityError:
            chat = await session.get(Chat, chat_id, populate_existing=True)
            if chat is None:
                raise
    chat.title = title
    chat.username = getattr(chat_obj, "username", None)
    _apply_chat_photo(chat, chat_obj)
    chat.is_active = True
    await session.flush()
    return chat


async def sync_chat_from_telegram(
    session: AsyncSession,
    bot: Bot,
    chat_id: int,
    *,
    sync_administrators: bool = True,
) -> Chat:
    """Refresh a registered group and its creator from Telegram.

    This function is used on startup and for ``my_chat_member`` updates, so a
    restart does not require another /panel command.
    """
    telegram_chat = await bot.get_chat(chat_id)
    chat = await upsert_chat(session, telegram_chat)

    me = await bot.get_me()
    bot_member = await bot.get_chat_member(chat_id, me.id)
    bot_status = getattr(bot_member.status, "value", str(bot_member.status))
    chat.is_active = bot_status not in {"left", "kicked"}
    if not chat.is_active or not sync_administrators:
        await session.flush()
        return chat

    try:
        admins = await bot.get_chat_administrators(chat_id)
    except Exception:
        admins = []
    merged = default_chat_settings()
    merged.update(chat.settings or {})
    for admin in admins:
        admin_user = admin.user
        await upsert_user(session, admin_user)
        role = "owner" if admin.status == ChatMemberStatus.CREATOR else "admin"
        await ensure_membership(session, chat_id, admin_user.id, role)
        if role == "owner":
            merged["owner_user_id"] = admin_user.id
    chat.settings = merged
    await session.flush()
    return chat


def chat_avatar_url(chat: Chat) -> str | None:
    if not (chat.photo_big_file_id or chat.photo_small_file_id):
        return None
    version = chat.photo_unique_id or "1"
    return f"/api/chat-avatars/{chat.id}?v={version}"


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
        candidate = Membership(chat_id=chat_id, user_id=user_id, role=role)
        try:
            async with session.begin_nested():
                session.add(candidate)
                await session.flush()
            membership = candidate
        except IntegrityError:
            membership = await session.scalar(
                select(Membership).where(
                    Membership.chat_id == chat_id,
                    Membership.user_id == user_id,
                )
            )
            if membership is None:
                raise
    # Telegram owner/admin states are authoritative, while the internal
    # AniGuard moderator role must survive ordinary messages.
    if role in {"owner", "admin"}:
        membership.role = role
    elif membership.role in {"owner", "admin"}:
        membership.role = "member"
    elif membership.role != "moderator" and role:
        membership.role = role
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
            # Telegram is the source of truth for title, username and photo.
            # A temporary getChat failure must not hide a persisted group.
            try:
                telegram_chat = await bot.get_chat(chat.id)
                chat = await upsert_chat(session, telegram_chat)
            except Exception:
                pass
            member = await bot.get_chat_member(chat.id, user_id)
            if member.status not in {ChatMemberStatus.CREATOR, ChatMemberStatus.ADMINISTRATOR}:
                continue
            if member.status == ChatMemberStatus.CREATOR:
                await ensure_membership(session, chat.id, user_id, "owner")
                merged = default_chat_settings()
                merged.update(chat.settings or {})
                merged["owner_user_id"] = user_id
                chat.settings = merged
            details = await premium_access_details(session, chat_id=chat.id, user_id=user_id)
            result.append(
                {
                    "id": chat.id,
                    "title": chat.title,
                    "username": chat.username,
                    "premium": details["active"],
                    "premium_until": details["until"].isoformat() if details["until"] else None,
                    "premium_plan": details["plan"],
                    "premium_source": details["source"],
                    "owner_id": details["owner_id"],
                    "avatar_url": chat_avatar_url(chat),
                }
            )
        except Exception:
            continue
    await session.flush()
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
    premium_override: bool = False,
) -> dict[str, Any]:
    chat = await get_chat_or_raise(session, chat_id)
    await ensure_entity_available(session, user_id=actor_id, chat_id=chat_id)
    settings = await get_merged_settings(session, chat_id)
    premium = premium_override or await has_premium_access(session, chat_id=chat_id, user_id=actor_id)
    reason = reason.strip() or str(settings.get("default_reason") or "Причина не указана")

    target_required = {
        "warn", "unwarn", "mute", "unmute", "ban", "unban", "kick", "quarantine", "unquarantine", "case",
        "restrict_media", "unrestrict_media", "restrict_links", "unrestrict_links",
        "restrict_commands", "unrestrict_commands",
    }
    timed_defaults = {
        "mute": "default_mute_seconds",
        "ban": "default_ban_seconds",
        "quarantine": "default_quarantine_seconds",
        "restrict_media": "default_restrict_media_seconds",
        "restrict_links": "default_restrict_links_seconds",
        "restrict_commands": "default_restrict_commands_seconds",
    }
    premium_actions = {"quarantine", "susanoo", "case"}
    if action in target_required and target_id is None:
        raise ValueError("Не указан пользователь. Ответьте на его сообщение или укажите @username.")
    if action in premium_actions and not premium:
        raise PermissionError("Для этого действия нужен AniGuard Premium")
    if action == "quarantine" and not settings.get("premium_quarantine", True):
        raise PermissionError("Модуль «Карантин Pro» отключён в настройках")
    if action == "case" and not settings.get("premium_cases", True):
        raise PermissionError("Модуль «Дела и доказательства» отключён в настройках")

    if action in timed_defaults and duration_seconds is None:
        duration_seconds = int(settings.get(timed_defaults[action], 604800))
    result: dict[str, Any] = {
        "action": action,
        "chat_id": chat_id,
        "target_id": target_id,
        "duration_seconds": duration_seconds,
        "reason": reason,
    }

    membership = await ensure_membership(session, chat_id, target_id) if target_id is not None else None

    if action == "warn":
        assert membership is not None
        membership.warnings += 1
        result["warnings"] = membership.warnings
        if settings["warn_threshold"] and membership.warnings >= int(settings["warn_threshold"]):
            auto_duration = int(settings.get("default_mute_seconds", 604800))
            until = utcnow() + timedelta(seconds=auto_duration)
            await bot.restrict_chat_member(chat_id, target_id, muted_permissions(), until_date=until)
            membership.muted_until = until
            result["auto_muted"] = True
            result["auto_mute_seconds"] = auto_duration
    elif action == "unwarn":
        assert membership is not None
        membership.warnings = max(0, membership.warnings - 1)
        result["warnings"] = membership.warnings
    elif action == "mute":
        until = None if duration_seconds == 0 else utcnow() + timedelta(seconds=int(duration_seconds or 0))
        await bot.restrict_chat_member(chat_id, target_id, muted_permissions(), until_date=until)
        assert membership is not None
        membership.muted_until = until
        result["until"] = until.isoformat() if until else None
    elif action == "unmute":
        await bot.restrict_chat_member(chat_id, target_id, full_permissions())
        assert membership is not None
        membership.muted_until = None
    elif action == "ban":
        until = None if duration_seconds == 0 else utcnow() + timedelta(seconds=int(duration_seconds or 0))
        await bot.ban_chat_member(chat_id, target_id, until_date=until)
        result["until"] = until.isoformat() if until else None
    elif action == "unban":
        await bot.unban_chat_member(chat_id, target_id, only_if_banned=True)
    elif action == "kick":
        await bot.ban_chat_member(chat_id, target_id)
        await bot.unban_chat_member(chat_id, target_id, only_if_banned=True)
    elif action == "purge":
        if not amount:
            raise ValueError("Не указано количество сообщений")
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
        until = None if duration_seconds == 0 else utcnow() + timedelta(seconds=int(duration_seconds or 0))
        await bot.restrict_chat_member(chat_id, target_id, quarantine_permissions(), until_date=until)
        assert membership is not None
        membership.quarantined_until = until
        result["until"] = until.isoformat() if until else None
    elif action == "unquarantine":
        await bot.restrict_chat_member(chat_id, target_id, full_permissions())
        assert membership is not None
        membership.quarantined_until = None
    elif action == "susanoo":
        chat_info = await bot.get_chat(chat_id)
        if chat_info.permissions and "permissions_before_lock" not in settings:
            settings["permissions_before_lock"] = chat_info.permissions.model_dump(exclude_none=True)
        await bot.set_chat_permissions(chat_id, ChatPermissions(can_send_messages=False))
        settings["chat_locked"] = True
        settings["slow_mode_seconds"] = 30
        chat.settings = settings
    elif action == "case":
        result["case_created"] = True
    elif action in {"restrict_media", "restrict_links", "restrict_commands"}:
        kind = action.removeprefix("restrict_")
        await set_restriction(
            session,
            chat_id=chat_id,
            user_id=int(target_id),
            kind=kind,
            duration_seconds=int(duration_seconds or 0),
            actor_id=actor_id,
            reason=reason,
        )
        result["restriction"] = kind
    elif action in {"unrestrict_media", "unrestrict_links", "unrestrict_commands"}:
        kind = action.removeprefix("unrestrict_")
        await clear_restriction(session, chat_id=chat_id, user_id=int(target_id), kind=kind)
        result["restriction_removed"] = kind
    else:
        raise ValueError("Неподдерживаемое действие модерации")

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
    *,
    answer: str,
    options: list[str],
    image_key: str,
    attempts: int = 3,
    failure_action: str = "kick",
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
        token=secrets.token_urlsafe(10),
        expires_at=utcnow() + timedelta(seconds=expires_in),
        answer=answer,
        options=list(options),
        image_key=image_key,
        attempts_left=max(1, min(int(attempts), 9)),
        failure_action=failure_action,
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
    custom_count = await session.scalar(
        select(func.count()).select_from(CustomCommand).where(CustomCommand.chat_id == chat_id)
    )
    game_count = await session.scalar(
        select(func.count()).select_from(GameCommand).where(GameCommand.chat_id == chat_id)
    )
    return {
        "chat": {
            "id": chat.id,
            "title": chat.title,
            "premium": (premium_details := await premium_access_details(session, chat_id=chat_id))["active"],
            "premium_until": premium_details["until"].isoformat() if premium_details["until"] else None,
            "premium_source": premium_details["source"],
            "premium_plan": premium_details["plan"],
            "owner_id": premium_details["owner_id"],
        },
        "metrics": {
            "members": members or 0,
            "actions_today": logs or 0,
            "open_reports": reports or 0,
            "rp_commands": rp_count or 0,
            "custom_commands": custom_count or 0,
            "game_commands": game_count or 0,
            "rules": rule_count or 0,
        },
        "settings": await get_merged_settings(session, chat_id),
    }
