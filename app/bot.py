from __future__ import annotations

import asyncio
import html
import logging
import random
import re
import time
import unicodedata
from collections import defaultdict, deque
from datetime import timedelta
from pathlib import Path
from typing import Any, Awaitable, Callable
from urllib.parse import urlparse

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ChatMemberStatus, ChatType, ParseMode
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram import BaseMiddleware
from aiogram.types import (
    BotCommand,
    BotCommandScopeChat,
    CallbackQuery,
    ChatPermissions,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    FSInputFile,
    LabeledPrice,
    MenuButtonWebApp,
    Message,
    ChatMemberUpdated,
    PreCheckoutQuery,
    WebAppInfo,
)
from sqlalchemy import delete, func, select

from app.captcha import captcha_by_key, select_captcha
from app.command_catalog import match_builtin_command
from app.game_action_catalog import is_moderation_collision, match_game_action
from app.config import get_settings
from app.media_safety import classify_message_media
from app.defaults import default_basic_commands
from app.db import SessionFactory
from app.models import (
    CaptchaChallenge,
    Chat,
    CustomCommand,
    GameCommand,
    Membership,
    PromoCode,
    PromoRedemption,
    SystemSetting,
    UserWallet,
    ModerationLog,
    ModerationRule,
    Report,
    RoleAssignmentHistory,
    Appeal,
    ModerationCase,
    ModeratorShift,
    PermissionOverride,
    SecurityIncident,
    WeeklyReportSnapshot,
    RPCommand,
    ResponseStylePack,
    User,
)
from app.durations import clean_reason, format_duration_ru, parse_duration_prefix
from app.feature_services import (
    create_appeal,
    create_backup_snapshot,
    create_shift,
    generate_weekly_report,
    run_operations_maintenance,
    set_security_mode,
)
from app.moderation_parser import TIMED_ACTIONS, detect_action, parse_moderation_command
from app.pricing import PREMIUM_PLANS, get_plan
from app.store import complete_store_payment, validate_store_payment
from app.response_styles import build_context, render_action_response, render_template
from app.roles import (
    PENALTY_DEFINITIONS,
    ROLE_DEFINITIONS,
    ROLE_ORDER,
    is_admin_role,
    is_staff_role,
    normalize_penalty_status,
    normalize_role,
    penalty_name,
    role_level,
    role_name,
)
from app.services import (
    active_restrictions,
    add_log,
    assign_member_role,
    as_utc,
    create_captcha,
    create_report,
    ensure_entity_available,
    ensure_membership,
    get_membership,
    refresh_membership_state,
    set_member_penalty_status,
    sync_penalty_status_from_warnings,
    full_permissions,
    get_block_record,
    get_chat_or_raise,
    get_merged_settings,
    grant_premium,
    set_entity_premium,
    set_entity_block,
    has_premium_access,
    is_premium,
    muted_permissions,
    parse_payment_payload,
    perform_action,
    quarantine_permissions,
    require_chat_admin,
    upsert_chat,
    update_chat_settings,
    sync_chat_from_telegram,
    upsert_user,
    utcnow,
)


settings = get_settings()
logger = logging.getLogger(__name__)
bot = Bot(settings.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()
router = Router(name="aniguard")
dp.include_router(router)


class AccessMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[Any, dict[str, Any]], Awaitable[Any]],
        event: Any,
        data: dict[str, Any],
    ) -> Any:
        from_user = getattr(event, "from_user", None)

        message_obj = (
            event
            if isinstance(event, Message)
            else getattr(event, "message", None)
        )

        raw_message_text = (
            getattr(message_obj, "text", None) or ""
            if message_obj
            else ""
        )

        command_token = (
            ((raw_message_text.split(maxsplit=1) or [""])[0])
            .split("@", 1)[0]
            .lower()
        )

        chat_obj = getattr(event, "chat", None)

        if chat_obj is None and message_obj is not None:
            chat_obj = getattr(message_obj, "chat", None)

        callback_data = str(
            getattr(event, "data", "") or ""
        )

        is_registration_callback = callback_data.startswith(
            "register_chat:"
        )

        # Новая или неактивная группа не может пользоваться
        # командами и автомодерацией до завершения регистрации.
        # Исключение делается только для кнопки регистрации.
        if (
            chat_obj
            and chat_obj.type
            in {ChatType.GROUP, ChatType.SUPERGROUP}
        ):
            async with SessionFactory() as registration_session:
                registration_chat = await registration_session.get(
                    Chat,
                    chat_obj.id,
                )

                registration_complete = bool(
                    registration_chat
                    and registration_chat.is_active
                    and (
                        registration_chat.settings or {}
                    ).get("registration_completed", True)
                )

            if not registration_complete:
                if is_registration_callback:
                    return await handler(event, data)

                return None

        if (
            from_user
            and command_token == "/admin"
            and message_obj
            and message_obj.chat.type == ChatType.PRIVATE
            and from_user.id not in settings.admin_ids
        ):
            return None

        if from_user and from_user.id in settings.admin_ids:
            return await handler(event, data)

        async with SessionFactory() as session:
            system_row = await session.get(SystemSetting, "admin_panel")
            maintenance = bool(system_row and isinstance(system_row.value, dict) and system_row.value.get("maintenance"))
            if maintenance:
                message_obj = event if isinstance(event, Message) else getattr(event, "message", None)
                if message_obj and message_obj.chat.type == ChatType.PRIVATE:
                    try:
                        await message_obj.answer("AniGuard временно находится на техническом обслуживании.")
                    except Exception:
                        pass
                return None
            if from_user:
                blocked_user = await get_block_record(session, "user", from_user.id)
                if blocked_user:
                    if chat_obj and chat_obj.type == ChatType.PRIVATE and hasattr(event, "answer"):
                        try:
                            await event.answer(
                                f"Доступ к AniGuard заблокирован. Причина: {html.escape(blocked_user.reason)}"
                            )
                        except Exception:
                            pass
                    return None
            if chat_obj and chat_obj.type in {ChatType.GROUP, ChatType.SUPERGROUP}:
                blocked_chat = await get_block_record(session, "chat", chat_obj.id)
                if blocked_chat:
                    return None
                message_obj = event if isinstance(event, Message) else getattr(event, "message", None)
                if from_user and message_obj and isinstance(event, Message):
                    membership = await session.scalar(
                        select(Membership).where(
                            Membership.chat_id == chat_obj.id,
                            Membership.user_id == from_user.id,
                        )
                    )
                    penalty = "none"
                    if membership is not None:
                        await refresh_membership_state(session, membership)
                        penalty = normalize_penalty_status(membership.penalty_status)
                    raw_text = (message_obj.text or message_obj.caption or "").strip()
                    first, separator, remainder = raw_text.partition(" ")
                    if first.startswith("/"):
                        first = first.split("@", 1)[0]
                    normalized_text = _normalize_phrase(f"{first.lstrip('/')} {remainder}" if separator else first.lstrip("/"))
                    allowed_penalty_prefixes = (
                        "info", "инфо", "информация", "свиток ниндзя",
                        "rules", "правила", "appeal", "апелляция",
                        "support", "поддержка", "admin", "каге",
                        "совет каге", "совет 5 каге", "совет пяти каге",
                    )
                    allowed_penalty_command = any(
                        normalized_text == prefix or normalized_text.startswith(prefix + " ")
                        for prefix in allowed_penalty_prefixes
                    )
                    management_prefixes = (
                        "role", "роль", "дать роль", "rank", "ранг", "дать ранг",
                        "penalty", "статус", "штрафной статус", "role history",
                        "история ролей", "свиток рангов", "panel", "settings",
                        "profile", "top", "logs", "reports", "help",
                    )
                    looks_like_management = raw_text.startswith("/") or any(
                        normalized_text == prefix or normalized_text.startswith(prefix + " ")
                        for prefix in management_prefixes
                    )
                    if penalty == "severe_violator" and not allowed_penalty_command:
                        try:
                            await message_obj.delete()
                            await message_obj.answer(
                                "⛔ При статусе «Злостный нарушитель» доступны только /info, /rules, /appeal, /support и /admin."
                            )
                        except Exception:
                            pass
                        await session.commit()
                        return None
                    if penalty == "violator" and looks_like_management and not allowed_penalty_command:
                        try:
                            await message_obj.delete()
                            await message_obj.answer(
                                "⚠️ При статусе «Нарушитель» управляющие и игровые команды временно недоступны."
                            )
                        except Exception:
                            pass
                        await session.commit()
                        return None

                if from_user and message_obj and isinstance(event, Message) and (message_obj.text or "").startswith("/"):
                    restrictions = await active_restrictions(
                        session, chat_id=chat_obj.id, user_id=from_user.id
                    )
                    if "commands" in restrictions:
                        try:
                            await message_obj.reply("Для вас временно заблокировано использование команд.")
                        except Exception:
                            pass
                        await session.commit()
                        return None
            await session.commit()
        return await handler(event, data)


router.message.outer_middleware(AccessMiddleware())
router.edited_message.outer_middleware(AccessMiddleware())
router.callback_query.outer_middleware(AccessMiddleware())

_flood_buckets: dict[tuple[int, int], deque[float]] = defaultdict(deque)
_slow_buckets: dict[tuple[int, int], float] = {}
_rp_cooldowns: dict[tuple[int, int, int], float] = {}
_custom_cooldowns: dict[tuple[int, int, int], float] = {}
_game_cooldowns: dict[tuple[int, int, int], float] = {}
_builtin_game_action_cooldowns: dict[tuple[int, int, str], float] = {}
_duplicate_buckets: dict[tuple[int, int], deque[tuple[float, str]]] = defaultdict(deque)
_join_buckets: dict[int, deque[float]] = defaultdict(deque)
_threat_buckets: dict[int, deque[float]] = defaultdict(deque)
_typed_flood_buckets: dict[tuple[int, int, str], deque[float]] = defaultdict(deque)
_coordinated_buckets: dict[tuple[int, str], deque[tuple[float, int]]] = defaultdict(deque)
_media_hash_buckets: dict[tuple[int, str], deque[tuple[float, int]]] = defaultdict(deque)
_captcha_worker_task: asyncio.Task[Any] | None = None
_operations_worker_task: asyncio.Task[Any] | None = None


def split_args(command: CommandObject | None) -> list[str]:
    return (command.args or "").split() if command else []


def panel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Открыть AniGuard", web_app=WebAppInfo(url=settings.webapp_url))],
            [InlineKeyboardButton(text="Premium", callback_data="premium:choose")],
        ]
    )


def admin_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[
            InlineKeyboardButton(
                text="Открыть админ-панель",
                web_app=WebAppInfo(url=settings.admin_url),
            )
        ]]
    )


async def ensure_context(message: Message) -> tuple[Chat, User, Membership | None]:
    async with SessionFactory() as session:
        user = await upsert_user(session, message.from_user)
        membership = None
        if message.chat.type in {ChatType.GROUP, ChatType.SUPERGROUP}:
            chat = await upsert_chat(session, message.chat)
            try:
                member = await bot.get_chat_member(message.chat.id, message.from_user.id)
                role = (
                    "creator"
                    if member.status == ChatMemberStatus.CREATOR
                    else "admin"
                    if member.status == ChatMemberStatus.ADMINISTRATOR
                    else "member"
                )
            except Exception:
                role = "member"
            membership = await ensure_membership(session, chat.id, user.id, role)
            if role == "creator":
                merged_settings = await get_merged_settings(session, chat.id)
                merged_settings["owner_user_id"] = user.id
                chat.settings = merged_settings
        else:
            chat = Chat(id=message.chat.id, title="Private")
        await session.commit()
        return chat, user, membership


async def ensure_group_admin(message: Message) -> None:
    if message.chat.type not in {ChatType.GROUP, ChatType.SUPERGROUP}:
        raise ValueError("Эта команда работает только в группе или супергруппе.")
    async with SessionFactory() as session:
        membership = await get_membership(session, message.chat.id, message.from_user.id)
        penalty = normalize_penalty_status(membership.penalty_status)
        internal_admin = is_admin_role(membership.role)
        await session.commit()
    if penalty != "none":
        raise PermissionError("Полномочия временно приостановлены штрафным статусом")
    try:
        member = await bot.get_chat_member(message.chat.id, message.from_user.id)
        if member.status in {ChatMemberStatus.CREATOR, ChatMemberStatus.ADMINISTRATOR}:
            return
    except Exception:
        pass
    if internal_admin:
        return
    raise PermissionError("Команда доступна младшему администратору или более высокой роли")


async def ensure_group_moderator(message: Message) -> None:
    if message.chat.type not in {ChatType.GROUP, ChatType.SUPERGROUP}:
        raise ValueError("Эта команда работает только в группе или супергруппе.")
    async with SessionFactory() as session:
        membership = await get_membership(session, message.chat.id, message.from_user.id)
        penalty = normalize_penalty_status(membership.penalty_status)
        internal_staff = is_staff_role(membership.role)
        await session.commit()
    if penalty != "none":
        raise PermissionError("Полномочия временно приостановлены штрафным статусом")
    try:
        member = await bot.get_chat_member(message.chat.id, message.from_user.id)
        if member.status in {ChatMemberStatus.CREATOR, ChatMemberStatus.ADMINISTRATOR}:
            return
    except Exception:
        pass
    if internal_staff:
        return
    raise PermissionError("Команда доступна администраторам и модераторам AniGuard")


async def resolve_target(message: Message, token: str | None = None) -> int | None:
    if token:
        token = token.strip()
        if token.lstrip("-").isdigit():
            return int(token)
        if token.startswith("@"):
            username = token[1:].lower()
            async with SessionFactory() as session:
                user_id = await session.scalar(
                    select(User.id).where(func.lower(User.username) == username)
                )
                if user_id is not None:
                    return int(user_id)
    entities = message.entities or []
    for entity in entities:
        if entity.type == "text_mention" and entity.user:
            return entity.user.id
    if message.reply_to_message and message.reply_to_message.from_user:
        return message.reply_to_message.from_user.id
    return None


async def send_admin_error(message: Message, exc: Exception) -> None:
    await message.answer(f"<b>Не удалось выполнить действие.</b>\n{html.escape(str(exc))}")


def profile_link(user_id: int, label: str) -> str:
    return f'<a href="tg://user?id={int(user_id)}">{html.escape(label)}</a>'


def _normalize_phrase(value: str | None) -> str:
    text = unicodedata.normalize("NFKD", value or "")
    text = "".join(char for char in text if not unicodedata.combining(char))
    return " ".join(text.casefold().replace("ё", "е").replace("_", " ").replace("-", " ").split())


def _extract_named_value(value: str, aliases: dict[str, str]) -> tuple[str | None, str]:
    normalized = _normalize_phrase(value)
    candidates = sorted(aliases.items(), key=lambda item: len(_normalize_phrase(item[0])), reverse=True)
    for alias, canonical in candidates:
        candidate = _normalize_phrase(alias)
        if normalized == candidate:
            return canonical, ""
        if normalized.startswith(candidate + " "):
            return canonical, normalized[len(candidate):].strip()
    return None, normalized


def _role_alias_map() -> dict[str, str]:
    aliases: dict[str, str] = {}
    for key, definition in ROLE_DEFINITIONS.items():
        aliases[key] = key
        aliases[key.replace("_", " ")] = key
        aliases[definition.ordinary_name] = key
        aliases[definition.naruto_name] = key
    return aliases


def _penalty_alias_map() -> dict[str, str]:
    aliases: dict[str, str] = {}
    for key, definition in PENALTY_DEFINITIONS.items():
        aliases[key] = key
        aliases[key.replace("_", " ")] = key
        aliases[definition.ordinary_name] = key
        aliases[definition.naruto_name] = key
    aliases.update({"снять статус": "none", "без статуса": "none"})
    return aliases


async def _target_and_payload(message: Message, raw: str | None) -> tuple[int | None, str]:
    payload = (raw or "").strip()
    first, _, rest = payload.partition(" ")
    if first and (first.startswith("@") or first.lstrip("-").isdigit()):
        target_id = await resolve_target(message, first)
        return target_id, rest.strip()
    return await resolve_target(message), payload


def _format_dt(value: Any) -> str:
    dt = as_utc(value)
    return dt.strftime("%d.%m.%Y %H:%M") if dt else "не указано"


def _membership_age(value: Any) -> str:
    joined = as_utc(value)
    if not joined:
        return "неизвестно"
    days = max(0, (utcnow() - joined).days)
    if days < 1:
        return "меньше дня"
    if days < 30:
        return f"{days} дн."
    months = days // 30
    if months < 12:
        return f"{months} мес."
    years, rest = divmod(months, 12)
    return f"{years} г. {rest} мес." if rest else f"{years} г."


ACTION_TITLES = {
    "warn": "предупреждение",
    "unwarn": "снятие предупреждения",
    "mute": "мут",
    "unmute": "снятие мута",
    "ban": "бан",
    "unban": "разбан",
    "quarantine": "карантин",
    "unquarantine": "снятие карантина",
    "kick": "исключение из группы",
    "restrict_media": "запрет медиа",
    "unrestrict_media": "снятие запрета медиа",
    "restrict_links": "запрет ссылок",
    "unrestrict_links": "снятие запрета ссылок",
    "restrict_commands": "блокировку команд",
    "unrestrict_commands": "снятие блокировки команд",
}

DEFAULT_DURATION_KEYS = {
    "mute": "default_mute_seconds",
    "ban": "default_ban_seconds",
    "quarantine": "default_quarantine_seconds",
    "restrict_media": "default_restrict_media_seconds",
    "restrict_links": "default_restrict_links_seconds",
    "restrict_commands": "default_restrict_commands_seconds",
}


def moderation_response(
    *,
    actor_id: int,
    target_id: int,
    action: str,
    duration_seconds: int | None,
    reason: str,
    show_duration: bool = True,
    show_reason: bool = True,
    style: str = "ordinary",
    custom_templates: dict[str, Any] | None = None,
    actor_name: str = "Admin",
    actor_username: str | None = None,
    target_name: str = "User",
    target_username: str | None = None,
    chat_title: str = "Беседа",
    chat_id: int | None = None,
    warnings: int = 0,
    warning_limit: int = 3,
    case_id: int | None = None,
    command_name: str | None = None,
    **extra: Any,
) -> str:
    clean_reason_value = reason if show_reason else "Скрыто настройками беседы"
    effective_duration = duration_seconds if show_duration else None
    return render_action_response(
        style=style,
        action=action,
        custom_templates=custom_templates,
        actor_id=actor_id,
        actor_name=actor_name,
        admin_username=(f"@{actor_username}" if actor_username and not actor_username.startswith("@") else actor_username) or "—",
        target_id=target_id,
        target_name=target_name,
        username=(f"@{target_username}" if target_username and not target_username.startswith("@") else target_username) or "—",
        duration_seconds=effective_duration,
        reason=clean_reason_value,
        chat_title=chat_title,
        chat_id=chat_id,
        warnings=warnings,
        warning_limit=warning_limit,
        case_id=case_id,
        command=command_name or f"/{action}",
        **extra,
    )


def render_custom_template(
    template: str,
    *,
    actor_id: int,
    target_id: int,
    command_name: str,
    duration_seconds: int | None,
    reason: str,
    chat_title: str,
    actor_name: str = "Admin",
    target_name: str = "User",
    **extra: Any,
) -> str:
    context = build_context(
        actor_id=actor_id,
        actor_name=actor_name,
        target_id=target_id,
        target_name=target_name,
        command=command_name,
        duration_seconds=duration_seconds,
        reason=reason,
        chat_title=chat_title,
        **extra,
    )
    return render_template(template, context, custom=True)




def _builtin_command_response_context(command: dict[str, Any], key: str, name: str) -> dict[str, Any]:
    return {
        "command_key": key,
        "command": name,
        "command_description": str(command.get("description") or "Описание не указано"),
        "command_number": str(key).removeprefix("anime_") if str(key).startswith("anime_") else "—",
        "command_templates": {
            "ordinary": command.get("ordinary_response"),
            "naruto": command.get("naruto_response"),
        },
    }

async def _chat_style(session, settings_data: dict[str, Any]) -> tuple[str, dict[str, Any] | None]:
    style = str(settings_data.get("response_style") or "ordinary")
    if style != "custom":
        return style, None
    code = str(settings_data.get("custom_style_code") or "").strip().upper()
    if not code:
        return "ordinary", None
    row = await session.scalar(
        select(ResponseStylePack).where(
            func.upper(ResponseStylePack.code) == code,
            ResponseStylePack.status == "approved",
        )
    )
    if row is None:
        return "ordinary", None
    return "custom", dict(row.templates or {})


async def _answer_styled_action(
    message: Message,
    action: str,
    *,
    target_id: int | None = None,
    reason: str = "Причина не указана",
    duration_seconds: int | None = None,
    **extra: Any,
) -> None:
    async with SessionFactory() as session:
        settings_data = await get_merged_settings(session, message.chat.id)
        style, custom_templates = await _chat_style(session, settings_data)
        actor_user = await session.get(User, message.from_user.id) if message.from_user else None
        effective_target = int(target_id or (message.from_user.id if message.from_user else 0))
        target_user = await session.get(User, effective_target) if effective_target else None
        membership = await session.scalar(
            select(Membership).where(Membership.chat_id == message.chat.id, Membership.user_id == effective_target)
        ) if effective_target else None
    text = render_action_response(
        style=style,
        action=action,
        custom_templates=custom_templates,
        actor_id=message.from_user.id if message.from_user else None,
        actor_name=actor_user.first_name if actor_user else (message.from_user.first_name if message.from_user else "Admin"),
        admin_username=(f"@{actor_user.username}" if actor_user and actor_user.username else "—"),
        target_id=effective_target or None,
        target_name=target_user.first_name if target_user else (message.from_user.first_name if message.from_user else "User"),
        username=(f"@{target_user.username}" if target_user and target_user.username else "—"),
        duration_seconds=duration_seconds,
        reason=reason,
        chat_title=message.chat.title or "Беседа",
        chat_id=message.chat.id,
        warnings=int(membership.warnings if membership else 0),
        warning_limit=int(settings_data.get("warn_threshold", 3)),
        **extra,
    )
    await message.answer(text)



def _pack_template(templates: dict[str, Any] | None, kind: str, *names: str) -> str | None:
    if not templates:
        return None
    keys: list[str] = []
    for name in names:
        normalized = _normalize_phrase(name)
        keys.extend([
            f"{kind}.{normalized}",
            f"{kind}.{normalized.replace(' ', '_')}",
            f"{kind}.{str(name).strip().casefold()}",
        ])
    keys.extend([f"{kind}.default", "default"])
    for key in keys:
        value = templates.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return None


async def ensure_target_can_be_moderated(chat_id: int, target_id: int) -> None:
    member = await bot.get_chat_member(chat_id, target_id)
    if member.status in {ChatMemberStatus.CREATOR, ChatMemberStatus.ADMINISTRATOR}:
        raise PermissionError("Нельзя применить наказание к владельцу или администратору группы")


async def member_role(chat_id: int, user_id: int) -> str:
    try:
        member = await bot.get_chat_member(chat_id, user_id)
        if member.status == ChatMemberStatus.CREATOR:
            return "creator"
    except Exception:
        member = None
    async with SessionFactory() as session:
        row = await get_membership(session, chat_id, user_id)
        if member is not None and member.status == ChatMemberStatus.ADMINISTRATOR and role_level(row.role) < 5:
            row.role = "admin"
        effective = "member" if normalize_penalty_status(row.penalty_status) != "none" else normalize_role(row.role)
        await session.commit()
        return effective


REQUIRED_BOT_ADMIN_RIGHTS: tuple[
    tuple[str, str],
    ...
] = (
    (
        "can_delete_messages",
        "Удаление сообщений",
    ),
    (
        "can_restrict_members",
        "Блокировка и ограничение участников",
    ),
    (
        "can_invite_users",
        "Приглашение пользователей",
    ),
)


def registration_keyboard(
    chat_id: int,
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Зарегистрировать беседу",
                    callback_data=(
                        f"register_chat:{chat_id}"
                    ),
                )
            ]
        ]
    )


def missing_registration_rights(
    bot_member: Any,
) -> list[str]:
    status_value = getattr(
        bot_member.status,
        "value",
        str(bot_member.status),
    )

    if status_value not in {
        "administrator",
        "creator",
    }:
        return [
            "Назначить AniGuard администратором",
            "Удаление сообщений",
            "Блокировка и ограничение участников",
            "Приглашение пользователей",
        ]

    missing: list[str] = []

    for attribute, title in REQUIRED_BOT_ADMIN_RIGHTS:
        if not bool(
            getattr(bot_member, attribute, False)
        ):
            missing.append(title)

    return missing


async def send_registration_prompt(
    chat_id: int,
    title: str,
) -> None:
    safe_title = html.escape(
        title or "Новая беседа"
    )

    registration_text = (
        "⚠️ <b>Беседа не зарегистрирована</b>\n\n"
        f"Группа: <b>{safe_title}</b>\n\n"
        "AniGuard добавлен в эту беседу, "
        "но его функции пока отключены.\n\n"
        "Для запуска защиты владелец или "
        "администратор группы должен нажать "
        "кнопку регистрации.\n\n"
        "После нажатия AniGuard проверит свой "
        "статус и необходимые права."
    )

    await bot.send_message(
        chat_id=chat_id,
        text=registration_text,
        reply_markup=registration_keyboard(chat_id),
    )


@router.my_chat_member()
async def bot_membership_changed(
    event: ChatMemberUpdated,
) -> None:
    """Prepare a group and wait for manual registration."""

    if event.chat.type not in {
        ChatType.GROUP,
        ChatType.SUPERGROUP,
    }:
        return

    status_value = getattr(
        event.new_chat_member.status,
        "value",
        str(event.new_chat_member.status),
    )

    should_send_prompt = False

    async with SessionFactory() as session:
        stored_chat = await session.get(
            Chat,
            event.chat.id,
        )

        if status_value in {"left", "kicked"}:
            if stored_chat is not None:
                stored_chat.is_active = False

                stored_settings = dict(
                    stored_chat.settings or {}
                )
                stored_settings[
                    "registration_completed"
                ] = False
                stored_chat.settings = stored_settings

            await session.commit()
            return

        already_registered = bool(
            stored_chat
            and stored_chat.is_active
            and (
                stored_chat.settings or {}
            ).get("registration_completed", True)
        )

        if already_registered:
            try:
                await sync_chat_from_telegram(
                    session,
                    bot,
                    event.chat.id,
                )
            except Exception as exc:
                logger.warning(
                    "Could not refresh registered "
                    "chat %s: %s",
                    event.chat.id,
                    exc,
                )

            await session.commit()
            return

        pending_chat = await upsert_chat(
            session,
            event.chat,
        )

        # upsert_chat активирует запись, поэтому явно
        # возвращаем её в состояние ожидания.
        pending_chat.is_active = False

        pending_settings = dict(
            pending_chat.settings or {}
        )
        pending_settings.update(
            {
                "registration_completed": False,
                "registration_requested_at": (
                    utcnow().isoformat()
                ),
            }
        )
        pending_chat.settings = pending_settings

        await session.commit()
        should_send_prompt = True

    if should_send_prompt:
        try:
            await send_registration_prompt(
                event.chat.id,
                event.chat.title or "Новая беседа",
            )
        except Exception as exc:
            logger.exception(
                "Could not send registration "
                "prompt to chat %s: %s",
                event.chat.id,
                exc,
            )


@router.callback_query(
    F.data.startswith("register_chat:")
)
async def register_chat_callback(
    callback: CallbackQuery,
) -> None:
    if not callback.data:
        await callback.answer(
            "Некорректные данные регистрации.",
            show_alert=True,
        )
        return

    if callback.message is None:
        await callback.answer(
            "Сообщение регистрации недоступно.",
            show_alert=True,
        )
        return

    try:
        _, raw_chat_id = callback.data.split(
            ":",
            1,
        )
        chat_id = int(raw_chat_id)
    except (TypeError, ValueError):
        await callback.answer(
            "Некорректный идентификатор беседы.",
            show_alert=True,
        )
        return

    message_chat = callback.message.chat

    if (
        message_chat.id != chat_id
        or message_chat.type
        not in {
            ChatType.GROUP,
            ChatType.SUPERGROUP,
        }
    ):
        await callback.answer(
            "Эта кнопка относится к другой беседе.",
            show_alert=True,
        )
        return

    try:
        actor_member = await bot.get_chat_member(
            chat_id,
            callback.from_user.id,
        )

        actor_status = getattr(
            actor_member.status,
            "value",
            str(actor_member.status),
        )

        if actor_status not in {
            "creator",
            "administrator",
        }:
            await callback.answer(
                "Регистрацию может выполнить "
                "только владелец или администратор.",
                show_alert=True,
            )
            return

        async with SessionFactory() as session:
            system_row = await session.get(
                SystemSetting,
                "admin_panel",
            )

            maintenance = bool(
                system_row
                and isinstance(system_row.value, dict)
                and system_row.value.get("maintenance")
            )

            if maintenance:
                await callback.answer(
                    "AniGuard временно находится "
                    "на техническом обслуживании.",
                    show_alert=True,
                )
                return

            blocked_user = await get_block_record(
                session,
                "user",
                callback.from_user.id,
            )

            if blocked_user:
                await callback.answer(
                    "Ваш доступ к AniGuard "
                    "заблокирован.",
                    show_alert=True,
                )
                return

            blocked_chat = await get_block_record(
                session,
                "chat",
                chat_id,
            )

            if blocked_chat:
                await callback.answer(
                    "Эта беседа заблокирована "
                    "в AniGuard.",
                    show_alert=True,
                )
                return

        bot_info = await bot.get_me()

        bot_member = await bot.get_chat_member(
            chat_id,
            bot_info.id,
        )

        missing_rights = (
            missing_registration_rights(bot_member)
        )

        if missing_rights:
            rights_text = "\n".join(
                f"• {html.escape(right)}"
                for right in missing_rights
            )

            failure_text = (
                "❌ <b>Регистрация не завершена</b>\n\n"
                "AniGuard не получил все права, "
                "необходимые для модерации.\n\n"
                "<b>Отсутствующие права:</b>\n"
                f"{rights_text}\n\n"
                "Откройте настройки группы → "
                "Администраторы → AniGuard, "
                "выдайте указанные права и "
                "нажмите кнопку ещё раз."
            )

            await callback.message.edit_text(
                failure_text,
                reply_markup=registration_keyboard(
                    chat_id
                ),
            )

            await callback.answer(
                "Не хватает прав администратора.",
                show_alert=True,
            )
            return

        async with SessionFactory() as session:
            registered_chat = (
                await sync_chat_from_telegram(
                    session,
                    bot,
                    chat_id,
                )
            )

            registered_chat.is_active = True

            registered_settings = dict(
                registered_chat.settings or {}
            )
            registered_settings.update(
                {
                    "registration_completed": True,
                    "registered_by": (
                        callback.from_user.id
                    ),
                    "registered_at": (
                        utcnow().isoformat()
                    ),
                }
            )
            registered_chat.settings = (
                registered_settings
            )

            await session.commit()

        success_keyboard = None

        if bot_info.username:
            success_keyboard = InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text=(
                                "🚀 Открыть AniGuard"
                            ),
                            url=(
                                "https://t.me/"
                                f"{bot_info.username}"
                                "?start=panel"
                            ),
                        )
                    ]
                ]
            )

        await callback.message.edit_text(
            (
                "✅ <b>Беседа зарегистрирована</b>\n\n"
                "AniGuard получил необходимые "
                "права администратора.\n\n"
                "🛡 Защита и модерация запущены.\n"
                "⚙️ Настройки доступны в Mini App.\n"
                "📋 Для открытия панели используйте "
                "команду /panel."
            ),
            reply_markup=success_keyboard,
        )

        await callback.answer(
            "Беседа зарегистрирована. "
            "AniGuard начал работу."
        )

    except Exception as exc:
        logger.exception(
            "Could not register chat %s: %s",
            chat_id,
            exc,
        )

        try:
            await callback.answer(
                (
                    "Не удалось зарегистрировать "
                    "беседу: "
                    f"{str(exc)[:120]}"
                ),
                show_alert=True,
            )
        except Exception:
            pass


async def synchronize_registered_chats() -> None:
    """Refresh all persistent groups before Mini App requests arrive."""
    async with SessionFactory() as session:
        stored_ids = set(
            int(value)
            for value in (await session.scalars(select(Chat.id).where(Chat.is_active.is_(True)))).all()
        )
        chat_ids = stored_ids | set(settings.recovery_chat_ids)
        for chat_id in sorted(chat_ids):
            try:
                await sync_chat_from_telegram(session, bot, chat_id)
            except Exception as exc:
                # Do not erase a valid chat on a temporary Telegram/API error.
                logger.warning("Could not refresh Telegram chat %s: %s", chat_id, exc)
        await session.commit()


@router.message(CommandStart())
async def start_handler(message: Message, command: CommandObject) -> None:
    await ensure_context(message)
    if message.chat.type != ChatType.PRIVATE:
        await message.answer("Откройте AniGuard в личном чате с ботом.")
        return

    text = (
        "<b>AniGuard — управление Telegram-беседой</b>\n\n"
        "Модерация, антирейд, жалобы, RP-команды, ранги и Premium через Telegram Stars.\n"
        "Добавьте бота администратором в беседу, отправьте там /panel, затем откройте Mini App."
    )
    await message.answer(text, reply_markup=panel_keyboard())

    if command.args and command.args.startswith("premium_"):
        try:
            chat_id = int(command.args.removeprefix("premium_"))
            await send_premium_plans(message, chat_id)
        except ValueError:
            pass
    elif command.args == "admin" and message.from_user and message.from_user.id in settings.admin_ids:
        await message.answer("Панель владельца AniGuard:", reply_markup=admin_keyboard())


async def send_staff_list(message: Message, *, force_naruto: bool | None = None) -> None:
    if message.chat.type not in {ChatType.GROUP, ChatType.SUPERGROUP}:
        return
    async with SessionFactory() as session:
        try:
            admins = await bot.get_chat_administrators(message.chat.id)
            for admin in admins:
                await upsert_user(session, admin.user)
                await ensure_membership(
                    session,
                    message.chat.id,
                    admin.user.id,
                    "creator" if admin.status == ChatMemberStatus.CREATOR else "admin",
                )
        except Exception:
            pass
        settings_data = await get_merged_settings(session, message.chat.id)
        rows = (
            await session.execute(
                select(Membership, User)
                .join(User, User.id == Membership.user_id)
                .where(Membership.chat_id == message.chat.id)
            )
        ).all()
        members: list[tuple[Membership, User]] = []
        for membership, user in rows:
            await refresh_membership_state(session, membership)
            if is_staff_role(membership.role):
                members.append((membership, user))
        await session.commit()

    naruto = bool(settings_data.get("anime_replies", True)) if force_naruto is None else force_naruto
    members.sort(key=lambda item: (-role_level(item[0].role), (item[1].first_name or "").casefold()))
    if not members:
        await message.answer("Администрация беседы пока не зарегистрирована в AniGuard.")
        return

    title = "🍥 <b>Совет Каге</b>" if naruto else "👥 <b>Администрация беседы</b>"
    lines = [title]
    for role_key in ROLE_ORDER:
        group = [(membership, user) for membership, user in members if normalize_role(membership.role) == role_key]
        if not group:
            continue
        lines.append("")
        lines.append(f"<b>{html.escape(role_name(role_key, naruto=naruto))}:</b>")
        for membership, user in group:
            label = " ".join(filter(None, [user.first_name, user.last_name])) or user.username or str(user.id)
            suffix = f" · до {_format_dt(membership.role_expires_at)}" if membership.role_expires_at else ""
            penalty = normalize_penalty_status(membership.penalty_status)
            if penalty != "none":
                suffix += f" · полномочия приостановлены: {html.escape(penalty_name(penalty, naruto=naruto))}"
            lines.append(f"• {profile_link(user.id, label)}{suffix}")
    await message.answer("\n".join(lines))


@router.message(Command("admin"))
async def admin_handler(message: Message) -> None:
    if message.chat.type in {ChatType.GROUP, ChatType.SUPERGROUP}:
        await send_staff_list(message)
        return
    if not message.from_user or message.from_user.id not in settings.admin_ids:
        return
    await message.answer("Панель владельца AniGuard:", reply_markup=admin_keyboard())


@router.message(F.text.regexp(r"(?i)^\s*/?(?:каге|совет[_\s]+каге|совет[_\s]+5[_\s]+каге|совет[_\s]+пяти[_\s]+каге)\s*$"))
async def staff_alias_handler(message: Message) -> None:
    if message.chat.type in {ChatType.GROUP, ChatType.SUPERGROUP}:
        await send_staff_list(message, force_naruto=True)


async def send_member_info(message: Message, raw_target: str | None = None, *, force_naruto: bool = False) -> None:
    if message.chat.type not in {ChatType.GROUP, ChatType.SUPERGROUP}:
        await message.answer("Команда работает только в беседе.")
        return
    await ensure_context(message)
    target_id, _ = await _target_and_payload(message, raw_target)
    if (raw_target or "").strip() and target_id is None:
        await message.answer("Пользователь не найден. Ответьте на его сообщение или укажите известный @username.")
        return
    target_id = target_id or message.from_user.id
    async with SessionFactory() as session:
        try:
            telegram_member = await bot.get_chat_member(message.chat.id, target_id)
            await upsert_user(session, telegram_member.user)
            telegram_status = getattr(telegram_member.status, "value", str(telegram_member.status))
            incoming_role = "creator" if telegram_member.status == ChatMemberStatus.CREATOR else "admin" if telegram_member.status == ChatMemberStatus.ADMINISTRATOR else "member"
        except Exception:
            telegram_status = "unknown"
            incoming_role = "member"
        membership = await ensure_membership(session, message.chat.id, target_id, incoming_role)
        user = await session.get(User, target_id)
        settings_data = await get_merged_settings(session, message.chat.id)
        await session.commit()
    if not user:
        await message.answer("Пользователь пока не найден в базе AniGuard.")
        return
    naruto = force_naruto or bool(settings_data.get("anime_replies", True))
    full_name = " ".join(filter(None, [user.first_name, user.last_name])) or user.username or str(user.id)
    username = f"@{user.username}" if user.username else "не указан"
    penalty = normalize_penalty_status(membership.penalty_status)
    warning_limit = max(1, int(settings_data.get("warn_threshold", 3)))
    reputation = max(0, min(100, 100 - membership.warnings * 10 - membership.penalty_points * 2))
    active_label = penalty_name(penalty, naruto=naruto) if penalty != "none" else "активен"
    role_until = f" до {_format_dt(membership.role_expires_at)}" if membership.role_expires_at else ""
    penalty_until = f" до {_format_dt(membership.penalty_until)}" if membership.penalty_until else ""
    if naruto:
        lines = [
            "🍥 <b>Свиток ниндзя</b>",
            "",
            f"👤 Пользователь: {profile_link(user.id, full_name)}",
            f"🆔 ID: <code>{user.id}</code>",
            f"🔗 Username: {html.escape(username)}",
            "",
            f"🥷 Роль: <b>{html.escape(role_name(membership.role, naruto=True))}</b>{html.escape(role_until)}",
            f"⚡ Уровень власти: <b>{role_level(membership.role)}</b>",
            "🏘 Деревня: Коноха",
            "",
            f"⚠️ Предупреждения: {membership.warnings} из {warning_limit}",
            f"💬 Сообщений: {membership.message_count:,}".replace(",", " "),
            f"📅 В беседе: {_membership_age(membership.joined_at)}",
            f"🛡 Репутация: {reputation} из 100",
        ]
        if penalty != "none":
            lines.append(f"🚨 Статус: <b>{html.escape(penalty_name(penalty, naruto=True))}</b>{html.escape(penalty_until)}")
        lines.extend(["", f"✅ Состояние: {html.escape(active_label)}", f"🕒 Последняя активность: {_format_dt(membership.last_seen_at)}"])
    else:
        lines = [
            "👤 <b>Информация о пользователе</b>",
            "",
            f"Имя: {profile_link(user.id, full_name)}",
            f"ID: <code>{user.id}</code>",
            f"Username: {html.escape(username)}",
            "",
            f"Роль: <b>{html.escape(role_name(membership.role))}</b>{html.escape(role_until)}",
            f"Уровень власти: <b>{role_level(membership.role)}</b>",
        ]
        if penalty != "none":
            lines.append(f"Штрафной статус: <b>{html.escape(penalty_name(penalty))}</b>{html.escape(penalty_until)}")
        lines.extend([
            "",
            f"Сообщений в беседе: {membership.message_count:,}".replace(",", " "),
            f"Предупреждений: {membership.warnings} из {warning_limit}",
            f"Репутация: {reputation} из 100",
            f"В беседе: {_membership_age(membership.joined_at)}",
            "",
            f"Статус: {html.escape(active_label)}",
            f"Статус Telegram: {html.escape(telegram_status)}",
            f"Последняя активность: {_format_dt(membership.last_seen_at)}",
        ])
    await message.answer("\n".join(lines))


@router.message(Command("info"))
async def info_handler(message: Message, command: CommandObject) -> None:
    await send_member_info(message, command.args)


@router.message(F.text.regexp(r"(?i)^\s*/?(?:инфо|информация)(?:\s+.*)?$"))
async def info_alias_handler(message: Message) -> None:
    raw = re.sub(r"(?i)^\s*/?(?:инфо|информация)\s*", "", message.text or "", count=1)
    await send_member_info(message, raw)


@router.message(F.text.regexp(r"(?i)^\s*/?свиток[_\s]+ниндзя(?:\s+.*)?$"))
async def ninja_scroll_info_handler(message: Message) -> None:
    raw = re.sub(r"(?i)^\s*/?свиток[_\s]+ниндзя\s*", "", message.text or "", count=1)
    await send_member_info(message, raw, force_naruto=True)


async def change_role_from_message(message: Message, raw: str | None, *, naruto: bool = False) -> None:
    try:
        await ensure_context(message)
        target_id, payload = await _target_and_payload(message, raw)
        if target_id is None:
            raise ValueError("Ответьте на сообщение пользователя или укажите @username")
        role_key, trailing = _extract_named_value(payload, _role_alias_map())
        if role_key is None:
            raise ValueError("Не указана роль. Пример: /role @user модератор 7 дней")
        duration = parse_duration_prefix(trailing)
        reason = clean_reason(trailing[duration.consumed:]) or "Назначение роли командой"
        try:
            target_member = await bot.get_chat_member(message.chat.id, target_id)
            await ensure_target_user_known(target_member.user)
        except Exception:
            pass
        async with SessionFactory() as session:
            membership = await assign_member_role(
                session, chat_id=message.chat.id, actor_id=message.from_user.id, target_id=target_id,
                new_role=role_key, duration_seconds=duration.seconds, reason=reason, source="telegram_command",
            )
            await session.commit()
        label = role_name(membership.role, naruto=naruto)
        term = f" на {format_duration_ru(duration.seconds)}" if duration.seconds not in (None, 0) else " бессрочно"
        title = "Ранг назначен" if naruto else "Роль назначена"
        await message.answer(f"✅ <b>{title}</b>\nПользователь: {profile_link(target_id, 'User')}\n{('Ранг' if naruto else 'Роль')}: <b>{html.escape(label)}</b>{html.escape(term)}")
    except Exception as exc:
        await send_admin_error(message, exc)


async def ensure_target_user_known(user: Any) -> None:
    async with SessionFactory() as session:
        await upsert_user(session, user)
        await session.commit()


@router.message(Command("role"))
async def role_handler(message: Message, command: CommandObject) -> None:
    await change_role_from_message(message, command.args)


@router.message(F.text.regexp(r"(?i)^\s*/?(?:роль|дать[_\s]+роль)(?:\s+.*)?$"))
async def role_alias_handler(message: Message) -> None:
    raw = re.sub(r"(?i)^\s*/?(?:роль|дать[_\s]+роль)\s*", "", message.text or "", count=1)
    await change_role_from_message(message, raw)


@router.message(F.text.regexp(r"(?i)^\s*/?(?:дать[_\s]+ранг|ранг)(?:\s+.*)?$"))
async def rank_alias_handler(message: Message) -> None:
    raw = re.sub(r"(?i)^\s*/?(?:дать[_\s]+ранг|ранг)\s*", "", message.text or "", count=1)
    await change_role_from_message(message, raw, naruto=True)


async def change_penalty_from_message(message: Message, raw: str | None, *, naruto: bool = False) -> None:
    try:
        await ensure_context(message)
        target_id, payload = await _target_and_payload(message, raw)
        if target_id is None:
            raise ValueError("Ответьте на сообщение пользователя или укажите @username")
        status_key, trailing = _extract_named_value(payload, _penalty_alias_map())
        if status_key is None:
            raise ValueError("Не указан статус: нарушитель, злостный нарушитель или снять")
        duration = parse_duration_prefix(trailing)
        async with SessionFactory() as session:
            settings_data = await get_merged_settings(session, message.chat.id)
            default_duration = None
            if status_key == "violator":
                default_duration = int(settings_data.get("violator_duration_seconds", 7 * 86400))
            elif status_key == "severe_violator":
                default_duration = int(settings_data.get("severe_violator_duration_seconds", 30 * 86400))
            seconds = duration.seconds if duration.seconds is not None else default_duration
            reason = clean_reason(trailing[duration.consumed:]) or "Назначение штрафного статуса"
            membership = await set_member_penalty_status(
                session, chat_id=message.chat.id, actor_id=message.from_user.id, target_id=target_id,
                status=status_key, duration_seconds=seconds, reason=reason, bot=bot,
            )
            await session.commit()
        label = penalty_name(membership.penalty_status, naruto=naruto)
        term = f" до {_format_dt(membership.penalty_until)}" if membership.penalty_until else ""
        await message.answer(f"🚨 <b>Штрафной статус обновлён</b>\nПользователь: {profile_link(target_id, 'User')}\nСтатус: <b>{html.escape(label)}</b>{html.escape(term)}")
    except Exception as exc:
        await send_admin_error(message, exc)


@router.message(Command("penalty"))
async def penalty_handler(message: Message, command: CommandObject) -> None:
    await change_penalty_from_message(message, command.args)


@router.message(F.text.regexp(r"(?i)^\s*/?(?:штрафной[_\s]+статус|статус)(?:\s+.*)?$"))
async def penalty_alias_handler(message: Message) -> None:
    raw = re.sub(r"(?i)^\s*/?(?:штрафной[_\s]+статус|статус)\s*", "", message.text or "", count=1)
    await change_penalty_from_message(message, raw)


@router.message(Command("role_history"))
async def role_history_handler(message: Message, command: CommandObject) -> None:
    await send_role_history(message, command.args)


@router.message(F.text.regexp(r"(?i)^\s*/?(?:история[_\s]+ролей|свиток[_\s]+рангов)(?:\s+.*)?$"))
async def role_history_alias_handler(message: Message) -> None:
    naruto = "свиток" in _normalize_phrase(message.text)
    raw = re.sub(r"(?i)^\s*/?(?:история[_\s]+ролей|свиток[_\s]+рангов)\s*", "", message.text or "", count=1)
    await send_role_history(message, raw, naruto=naruto)


async def send_role_history(message: Message, raw: str | None, *, naruto: bool = False) -> None:
    try:
        await ensure_context(message)
        target_id, _ = await _target_and_payload(message, raw)
        target_id = target_id or message.from_user.id
        if target_id != message.from_user.id:
            await ensure_group_moderator(message)
        async with SessionFactory() as session:
            rows = (await session.scalars(
                select(RoleAssignmentHistory).where(
                    RoleAssignmentHistory.chat_id == message.chat.id, RoleAssignmentHistory.user_id == target_id
                ).order_by(RoleAssignmentHistory.id.desc()).limit(10)
            )).all()
        title = "📜 <b>Свиток рангов</b>" if naruto else "📋 <b>История ролей</b>"
        lines = [title, f"Пользователь: {profile_link(target_id, 'User')}"]
        if not rows:
            lines.append("История назначений пока пуста.")
        for row in rows:
            until = f" до {_format_dt(row.temporary_until)}" if row.temporary_until else ""
            lines.append(f"\n• {_format_dt(row.created_at)}: {html.escape(role_name(row.old_role, naruto=naruto))} → <b>{html.escape(role_name(row.new_role, naruto=naruto))}</b>{html.escape(until)}\n  Назначил: <code>{row.actor_id or 0}</code> · {html.escape(row.reason or 'Без причины')}")
        await message.answer("\n".join(lines))
    except Exception as exc:
        await send_admin_error(message, exc)


async def submit_appeal(message: Message, raw_reason: str | None) -> None:
    if message.chat.type not in {ChatType.GROUP, ChatType.SUPERGROUP}:
        await message.answer("Апелляцию нужно отправить в беседе, где действует ограничение.")
        return
    raw = clean_reason(raw_reason)
    case_id = None
    case_match = re.match(r"(?i)^\s*(?:AG[- ]?)?(\d{1,9})\b\s*(.*)$", raw or "")
    if case_match:
        case_id = int(case_match.group(1))
        reason = clean_reason(case_match.group(2)) or "Пользователь просит пересмотреть наказание"
    else:
        reason = raw or "Пользователь просит пересмотреть ограничение"
    await ensure_context(message)
    async with SessionFactory() as session:
        appeal = await create_appeal(
            session, chat_id=message.chat.id, user_id=message.from_user.id,
            text=reason, case_id=case_id,
        )
        report = await create_report(
            session, chat_id=message.chat.id, reporter_id=message.from_user.id, target_id=message.from_user.id,
            message_id=message.message_id, reason=f"Апелляция #{appeal.id}: {reason}", category="апелляция",
        )
        await session.commit()
    await _answer_styled_action(
        message,
        "appeal",
        target_id=message.from_user.id,
        reason=reason,
        appeal_id=appeal.id,
        case_id=appeal.case_id,
        report_id=f"AG-{report.id}",
    )


@router.message(Command("appeal"))
async def appeal_handler(message: Message, command: CommandObject) -> None:
    await submit_appeal(message, command.args)


@router.message(F.text.regexp(r"(?i)^\s*/?апелляция(?:\s+.*)?$"))
async def appeal_alias_handler(message: Message) -> None:
    raw = re.sub(r"(?i)^\s*/?апелляция\s*", "", message.text or "", count=1)
    await submit_appeal(message, raw)


@router.message(Command("support"))
async def support_handler(message: Message) -> None:
    await message.answer(
        "🛟 <b>Поддержка AniGuard</b>\nНапишите в @anicode_support_bot и укажите ID беседы и краткое описание проблемы."
    )


@router.message(F.text.regexp(r"(?i)^\s*/?поддержка\s*$"))
async def support_alias_handler(message: Message) -> None:
    await support_handler(message)


@router.message(Command("panel"))
async def panel_handler(message: Message) -> None:
    await ensure_context(message)
    if message.chat.type == ChatType.PRIVATE:
        await message.answer("Панель управления AniGuard:", reply_markup=panel_keyboard())
        return
    try:
        await ensure_group_admin(message)
        me = await bot.get_me()
        deep_link = f"https://t.me/{me.username}?start=panel"
        await message.answer(
            "Беседа зарегистрирована. Откройте личный чат с ботом и запустите Mini App.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="Открыть бота", url=deep_link)]]
            ),
        )
    except Exception as exc:
        await send_admin_error(message, exc)


@router.message(Command("help"))
async def help_handler(message: Message) -> None:
    await message.answer(
        "<b>Команды AniGuard</b>\n"
        "<code>/ban</code> = <code>ban</code> = <code>бан</code>\n"
        "<code>/mute</code> = <code>mute</code> = <code>мут</code>\n"
        "<code>/warn</code> = <code>warn</code> = <code>варн</code>\n\n"
        "У Naruto-команд символы <code>/</code> и <code>_</code> необязательны:\n"
        "<code>/катон_гокаку</code> = <code>катон гокаку</code>.\n\n"
        "Игровые действия работают ответом на сообщение или с @username. "
        "Для названий, совпадающих с модерацией, игровую версию можно вызвать так: "
        "<code>игра расен сюрикен @user</code>.\n\n"
        "Роли: <code>/role @user модератор 7 дней</code>. "
        "Информация: <code>/info @user</code>. Администрация: <code>/admin</code>.\n\n"
        "Полный список разделён на обычные и Premium-команды в Mini App."
    )


async def send_premium_plans(message: Message, chat_id: int | None = None) -> None:
    if chat_id is None and message.chat.type in {ChatType.GROUP, ChatType.SUPERGROUP}:
        chat_id = message.chat.id
    if chat_id is None:
        await message.answer("Откройте Mini App и выберите беседу для покупки Premium.")
        return
    await require_chat_admin(bot, chat_id, message.from_user.id)
    rows = []
    for plan in PREMIUM_PLANS.values():
        rows.append(
            [InlineKeyboardButton(
                text=f"{plan.title} · {plan.days} дней · {plan.stars} ⭐",
                callback_data=f"buy:{plan.code}:{chat_id}",
            )]
        )
    await message.answer(
        "<b>AniGuard Premium</b>\nPremium приобретается для выбранной беседы.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


@router.message(Command("promo"))
async def promo_handler(message: Message, command: CommandObject) -> None:
    code = (command.args or "").strip().upper()
    if not code:
        await message.answer("Использование: <code>/promo КОД</code>")
        return
    async with SessionFactory() as session:
        await upsert_user(session, message.from_user)
        promo = await session.get(PromoCode, code)
        if not promo or not promo.active:
            await message.answer("Промокод не найден или отключён.")
            return
        if promo.uses >= promo.max_uses:
            await message.answer("Лимит активаций промокода исчерпан.")
            return
        redeemed = await session.scalar(
            select(PromoRedemption).where(
                PromoRedemption.code == code, PromoRedemption.user_id == message.from_user.id
            )
        )
        if redeemed:
            await message.answer("Вы уже активировали этот промокод.")
            return
        if promo.reward_type == "premium":
            await set_entity_premium(
                session,
                entity_type="user",
                entity_id=message.from_user.id,
                days=promo.reward_value,
                admin_id=promo.created_by,
                permanent=False,
                plan="promo",
                note=f"Промокод {code}",
            )
            reward_text = f"Premium на {promo.reward_value} дней"
        else:
            wallet = await session.get(UserWallet, message.from_user.id)
            if not wallet:
                wallet = UserWallet(user_id=message.from_user.id, balance=0)
                session.add(wallet)
            wallet.balance += promo.reward_value
            reward_text = f"{promo.reward_value:,} AniCoin".replace(",", " ")
        session.add(PromoRedemption(code=code, user_id=message.from_user.id))
        promo.uses += 1
        await session.commit()
    await message.answer(f"Промокод активирован. Начислено: <b>{reward_text}</b>.")


@router.message(Command("premium"))
async def premium_handler(message: Message) -> None:
    try:
        if message.chat.type == ChatType.PRIVATE:
            await message.answer("Выберите беседу в Mini App или вызовите /premium в нужной группе.")
        else:
            await send_premium_plans(message, message.chat.id)
    except Exception as exc:
        await send_admin_error(message, exc)


@router.callback_query(F.data == "premium:choose")
async def premium_choose_callback(callback: CallbackQuery) -> None:
    await callback.answer()
    await callback.message.answer("Выберите беседу в Mini App, затем откройте раздел Premium.")


@router.callback_query(F.data.startswith("buy:"))
async def premium_buy_callback(callback: CallbackQuery) -> None:
    try:
        _, plan_code, raw_chat_id = callback.data.split(":", 2)
        chat_id = int(raw_chat_id)
        await require_chat_admin(bot, chat_id, callback.from_user.id)
        plan = get_plan(plan_code)
        payload = f"agp:{chat_id}:{callback.from_user.id}:{plan.code}:{int(time.time())}"
        await bot.send_invoice(
            chat_id=callback.from_user.id,
            title=f"AniGuard {plan.title}",
            description=plan.description,
            payload=payload,
            currency="XTR",
            prices=[LabeledPrice(label=f"Premium {plan.days} дней", amount=plan.stars)],
            provider_token="",
        )
        await callback.answer("Счёт отправлен в личный чат с ботом.")
    except Exception as exc:
        await callback.answer(str(exc), show_alert=True)


@router.pre_checkout_query()
async def pre_checkout_handler(query: PreCheckoutQuery) -> None:
    try:
        if query.invoice_payload.startswith("agp:"):
            chat_id, user_id, plan_code = parse_payment_payload(query.invoice_payload)
            plan = get_plan(plan_code)
            if query.from_user.id != user_id or query.currency != "XTR" or query.total_amount != plan.stars:
                raise ValueError("Параметры платежа не совпадают")
            await require_chat_admin(bot, chat_id, user_id)
        elif query.invoice_payload.startswith(("agc:", "aga:")):
            async with SessionFactory() as session:
                await validate_store_payment(
                    session,
                    payload=query.invoice_payload,
                    user_id=query.from_user.id,
                    total_amount=query.total_amount,
                    currency=query.currency,
                )
        else:
            raise ValueError("Неизвестный тип счёта")
        await query.answer(ok=True)
    except Exception as exc:
        await query.answer(ok=False, error_message=str(exc)[:200])


@router.message(F.successful_payment)
async def successful_payment_handler(message: Message) -> None:
    payment = message.successful_payment
    try:
        if payment.invoice_payload.startswith("agp:"):
            chat_id, user_id, plan_code = parse_payment_payload(payment.invoice_payload)
            async with SessionFactory() as session:
                chat = await grant_premium(
                    session,
                    user_id=user_id,
                    chat_id=chat_id,
                    plan_code=plan_code,
                    stars=payment.total_amount,
                    payload=payment.invoice_payload,
                    charge_id=payment.telegram_payment_charge_id,
                )
                await session.commit()
            await message.answer(
                f"<b>Premium активирован.</b>\nБеседа: {html.escape(chat.title)}\n"
                f"Активен до: {chat.premium_until.strftime('%d.%m.%Y %H:%M UTC')}"
            )
            return

        if payment.invoice_payload.startswith(("agc:", "aga:")):
            async with SessionFactory() as session:
                result = await complete_store_payment(
                    session,
                    payload=payment.invoice_payload,
                    user_id=message.from_user.id,
                    total_amount=payment.total_amount,
                    currency=payment.currency,
                    charge_id=payment.telegram_payment_charge_id,
                )
                await session.commit()
            if result["kind"] == "coins":
                text = (
                    f"<b>AniCoin зачислены.</b>\n"
                    f"Получено: {int(result.get('coins', 0)):,} AniCoin\n"
                    f"Баланс: {int(result.get('balance', 0)):,} AniCoin"
                ).replace(",", " ")
                await message.answer(text)
            else:
                await message.answer(
                    f"<b>Рекламный заказ оплачен.</b>\nЗаказ #{result.get('order_id')} передан на запуск."
                )
            return

        raise ValueError("Неизвестный тип платежа")
    except Exception as exc:
        await message.answer(f"Платёж получен, но активация не завершена: {html.escape(str(exc))}")



def _message_evidence(message: Message) -> dict[str, Any] | None:
    source = message.reply_to_message
    if source is None:
        return None
    media: list[dict[str, Any]] = []
    if source.photo:
        media.append({"type": "photo", "file_id": source.photo[-1].file_id})
    for attr, kind in (
        ("video", "video"), ("document", "document"), ("audio", "audio"),
        ("voice", "voice"), ("video_note", "video_note"), ("sticker", "sticker"),
        ("animation", "animation"),
    ):
        obj = getattr(source, attr, None)
        if obj is not None:
            media.append({"type": kind, "file_id": getattr(obj, "file_id", None), "file_name": getattr(obj, "file_name", None)})
    return {
        "message_id": source.message_id,
        "author_id": source.from_user.id if source.from_user else None,
        "text": source.text or source.caption or "",
        "media": media,
        "date": source.date.isoformat() if source.date else None,
        "content_type": str(source.content_type),
    }


async def moderation_command(
    message: Message,
    command: CommandObject | None,
    action: str | None = None,
    *,
    raw_text: str | None = None,
) -> None:
    try:
        await ensure_group_moderator(message)
        await ensure_context(message)
        source_text = raw_text if raw_text is not None else (command.args or "" if command else "")

        async with SessionFactory() as session:
            await ensure_entity_available(session, user_id=message.from_user.id, chat_id=message.chat.id)
            settings_data = await get_merged_settings(session, message.chat.id)
            detected = detect_action(raw_text or "") if action is None else None
            resolved_action = action or (detected[0] if detected else None)
            if not resolved_action:
                raise ValueError("Не удалось определить команду модерации")
            default_duration = int(settings_data.get(DEFAULT_DURATION_KEYS.get(resolved_action, "default_mute_seconds"), 604800))
            parsed = parse_moderation_command(
                source_text,
                default_duration_seconds=default_duration,
                forced_action=resolved_action if raw_text is None else None,
                forced_trigger=resolved_action,
            )
            if parsed is None:
                raise ValueError("Команда не распознана")
            target_id = await resolve_target(message, parsed.target_token)
            if target_id is None:
                raise ValueError("Ответьте на сообщение пользователя или укажите его @username")
            if target_id == message.from_user.id:
                raise PermissionError("Нельзя применить команду к самому себе")
            if not parsed.action.startswith("un"):
                await ensure_target_can_be_moderated(message.chat.id, target_id)
            reason = parsed.reason or str(settings_data.get("default_reason") or "Причина не указана")
            result = await perform_action(
                session,
                bot,
                chat_id=message.chat.id,
                actor_id=message.from_user.id,
                action=parsed.action,
                target_id=target_id,
                duration_seconds=parsed.duration_seconds,
                reason=reason,
                source_message_id=message.reply_to_message.message_id if message.reply_to_message else message.message_id,
                evidence=_message_evidence(message),
            )
            actor_user = await session.get(User, message.from_user.id)
            target_user = await session.get(User, target_id)
            target_membership = await session.scalar(
                select(Membership).where(Membership.chat_id == message.chat.id, Membership.user_id == target_id)
            )
            style, custom_templates = await _chat_style(session, settings_data)
            await session.commit()

        await message.answer(
            moderation_response(
                actor_id=message.from_user.id,
                target_id=target_id,
                action=parsed.action,
                duration_seconds=result.get("duration_seconds"),
                reason=reason,
                show_duration=bool(settings_data.get("show_moderation_duration", True)),
                show_reason=bool(settings_data.get("show_moderation_reason", True)),
                style=style,
                custom_templates=custom_templates,
                actor_name=(actor_user.first_name if actor_user else message.from_user.first_name),
                actor_username=(actor_user.username if actor_user else message.from_user.username),
                target_name=(target_user.first_name if target_user else "User"),
                target_username=(target_user.username if target_user else None),
                chat_title=message.chat.title or "Беседа",
                chat_id=message.chat.id,
                warnings=int(result.get("warnings", target_membership.warnings if target_membership else 0) or 0),
                warning_limit=int(settings_data.get("warn_threshold", 3)),
                case_id=result.get("case_id"),
                command_name=f"/{parsed.action}",
                message_id=message.reply_to_message.message_id if message.reply_to_message else message.message_id,
            )
        )
    except Exception as exc:
        await send_admin_error(message, exc)


@router.message(Command("warn"))
async def warn_handler(message: Message, command: CommandObject) -> None:
    await moderation_command(message, command, "warn")


@router.message(Command("unwarn"))
async def unwarn_handler(message: Message, command: CommandObject) -> None:
    await moderation_command(message, command, "unwarn")


@router.message(Command("mute"))
async def mute_handler(message: Message, command: CommandObject) -> None:
    await moderation_command(message, command, "mute")


@router.message(Command("unmute"))
async def unmute_handler(message: Message, command: CommandObject) -> None:
    await moderation_command(message, command, "unmute")


@router.message(Command("ban"))
async def ban_handler(message: Message, command: CommandObject) -> None:
    await moderation_command(message, command, "ban")


@router.message(Command("unban"))
async def unban_handler(message: Message, command: CommandObject) -> None:
    await moderation_command(message, command, "unban")


@router.message(Command("quarantine"))
async def quarantine_handler(message: Message, command: CommandObject) -> None:
    await moderation_command(message, command, "quarantine")


@router.message(Command("unquarantine"))
async def unquarantine_handler(message: Message, command: CommandObject) -> None:
    await moderation_command(message, command, "unquarantine")


@router.message(Command("kick"))
async def kick_handler(message: Message, command: CommandObject) -> None:
    await moderation_command(message, command, "kick")


@router.message(Command("media"))
async def media_restrict_handler(message: Message, command: CommandObject) -> None:
    await moderation_command(message, command, "restrict_media")


@router.message(Command("unmedia"))
async def media_unrestrict_handler(message: Message, command: CommandObject) -> None:
    await moderation_command(message, command, "unrestrict_media")


@router.message(Command("linksban"))
async def links_restrict_handler(message: Message, command: CommandObject) -> None:
    await moderation_command(message, command, "restrict_links")


@router.message(Command("unlinksban"))
async def links_unrestrict_handler(message: Message, command: CommandObject) -> None:
    await moderation_command(message, command, "unrestrict_links")


@router.message(Command("commandsban"))
async def commands_restrict_handler(message: Message, command: CommandObject) -> None:
    await moderation_command(message, command, "restrict_commands")


@router.message(Command("uncommandsban"))
async def commands_unrestrict_handler(message: Message, command: CommandObject) -> None:
    await moderation_command(message, command, "unrestrict_commands")


@router.message(Command("case"))
async def case_handler(message: Message, command: CommandObject) -> None:
    await moderation_command(message, command, "case")


NATURAL_MODERATION_RE = re.compile(
    r"^\s*(?:снять\s+(?:ограничение\s+команд|запрет\s+(?:ссылок|медиа)|предупреждение|пред|мут|бан)|"
    r"разрешить\s+(?:команды|ссылки|медиа)|заблокировать\s+команды|запретить\s+(?:команды|ссылки|медиа)|"
    r"ограничить\s+(?:команды|ссылки|медиа)|снять\s+карантин|исключить|выгнать|кик|размут|анмут|разбан|анбан|анварн|предупреждение|карантин|мут|бан|пред|варн)(?:\s|$)",
    re.IGNORECASE,
)


@router.message(F.text.regexp(NATURAL_MODERATION_RE))
async def natural_moderation_handler(message: Message) -> None:
    await moderation_command(message, None, raw_text=message.text or "")


@router.message(Command("purge"))
async def purge_handler(message: Message, command: CommandObject) -> None:
    try:
        await ensure_group_admin(message)
        count = min(max(int((command.args or "25").split()[0]), 1), 100)
        end_id = message.message_id
        message_ids = list(range(max(1, end_id - count + 1), end_id + 1))
        try:
            await bot.delete_messages(message.chat.id, message_ids)
        except Exception:
            deleted = 0
            for message_id in message_ids:
                try:
                    await bot.delete_message(message.chat.id, message_id)
                    deleted += 1
                except Exception:
                    continue
            count = deleted
        async with SessionFactory() as session:
            await add_log(
                session,
                message.chat.id,
                "purge",
                actor_id=message.from_user.id,
                reason=f"Удалено сообщений: {count}",
                details={"count": count},
            )
            await session.commit()
        await _answer_styled_action(
            message,
            "purge",
            reason="Массовая очистка сообщений",
            deleted_count=count,
        )
    except Exception as exc:
        await send_admin_error(message, exc)


@router.message(Command("slow"))
async def slow_handler(message: Message, command: CommandObject) -> None:
    try:
        await ensure_group_admin(message)
        raw = (command.args or "15").strip().lower()
        delay = 0 if raw in {"off", "0", "выкл"} else min(max(int(raw), 1), 3600)
        async with SessionFactory() as session:
            await perform_action(
                session,
                bot,
                chat_id=message.chat.id,
                actor_id=message.from_user.id,
                action="slow",
                amount=delay,
                reason="Изменение медленного режима",
            )
            await session.commit()
        await _answer_styled_action(
            message,
            "slow",
            reason="Медленный режим отключён" if delay == 0 else "Ограничение частоты сообщений",
            slow_seconds=delay,
            status="Отключено" if delay == 0 else "Активно",
        )
    except Exception as exc:
        await send_admin_error(message, exc)


async def chat_state_action(message: Message, action: str) -> None:
    try:
        await ensure_group_admin(message)
        await ensure_context(message)
        async with SessionFactory() as session:
            await perform_action(
                session,
                bot,
                chat_id=message.chat.id,
                actor_id=message.from_user.id,
                action=action,
                reason="Чат закрыт" if action == "lock" else "Чат открыт",
            )
            await session.commit()
        await _answer_styled_action(
            message,
            action,
            reason="Экстренное ограничение общения" if action == "lock" else "Обычный режим общения восстановлен",
        )
    except Exception as exc:
        await send_admin_error(message, exc)


@router.message(Command("lock"))
async def lock_handler(message: Message) -> None:
    await chat_state_action(message, "lock")


@router.message(Command("unlock"))
async def unlock_handler(message: Message) -> None:
    await chat_state_action(message, "unlock")


async def submit_smart_report(message: Message, raw: str | None) -> None:
    if not message.reply_to_message or not message.reply_to_message.from_user:
        await message.answer("Ответьте командой /report на сообщение нарушителя.")
        return
    await ensure_context(message)
    async with SessionFactory() as session:
        settings_data = await get_merged_settings(session, message.chat.id)
        categories = [str(item).casefold() for item in settings_data.get("report_categories", [])]
        reason = clean_reason(raw) or "Причина не указана"
        category = "другое"
        first, _, tail = reason.partition(" ")
        normalized_first = first.casefold().strip("#:,.;")
        if normalized_first in categories:
            category = normalized_first
            reason = clean_reason(tail) or category
        report = await create_report(
            session,
            chat_id=message.chat.id,
            reporter_id=message.from_user.id,
            target_id=message.reply_to_message.from_user.id,
            message_id=message.reply_to_message.message_id,
            reason=reason,
            category=category,
        )
        await session.commit()
    await _answer_styled_action(
        message,
        "report",
        target_id=message.reply_to_message.from_user.id,
        reason=reason,
        report_id=f"AG-{report.id}",
        message_id=message.reply_to_message.message_id,
        status=f"Объединено сигналов: {int(report.duplicate_count or 1)}",
    )


@router.message(Command("report"))
async def report_handler(message: Message, command: CommandObject) -> None:
    await submit_smart_report(message, command.args)


@router.message(F.text.regexp(r"(?i)^\s*/?(?:жалоба|репорт)(?:\s+.*)?$"))
async def report_alias_handler(message: Message) -> None:
    raw = re.sub(r"(?i)^\s*/?(?:жалоба|репорт)\s*", "", message.text or "", count=1)
    await submit_smart_report(message, raw)


@router.message(Command("profile"))
async def profile_handler(message: Message, command: CommandObject) -> None:
    await ensure_context(message)
    args = split_args(command)
    target_id = await resolve_target(message, args[0] if args else None) or message.from_user.id
    async with SessionFactory() as session:
        membership = await session.scalar(
            select(Membership).where(
                Membership.chat_id == message.chat.id,
                Membership.user_id == target_id,
            )
        )
        user = await session.get(User, target_id)
    if not membership or not user:
        await message.answer("Профиль пока не найден.")
        return
    level = max(1, membership.xp // 100 + 1)
    await message.answer(
        f"<b>{html.escape(user.first_name)}</b>\n"
        f"Сообщений: {membership.message_count}\n"
        f"Предупреждений: {membership.warnings}\n"
        f"Опыт: {membership.xp} · уровень {level}\n"
        f"AniCoin: {membership.coins}"
    )


@router.message(Command("top"))
async def top_handler(message: Message) -> None:
    async with SessionFactory() as session:
        rows = (
            await session.execute(
                select(Membership, User)
                .join(User, User.id == Membership.user_id)
                .where(Membership.chat_id == message.chat.id)
                .order_by(Membership.xp.desc())
                .limit(10)
            )
        ).all()
    if not rows:
        await message.answer("Рейтинг пока пуст.")
        return
    lines = ["<b>Топ участников</b>"]
    for index, (membership, user) in enumerate(rows, 1):
        lines.append(f"{index}. {html.escape(user.first_name)} — {membership.xp} XP")
    await message.answer("\n".join(lines))


@router.message(Command("topchats"))
async def top_chats_handler(message: Message) -> None:
    async with SessionFactory() as session:
        rows = (
            await session.execute(
                select(Chat, func.count(Membership.id).label("members"))
                .outerjoin(Membership, Membership.chat_id == Chat.id)
                .where(Chat.is_active.is_(True))
                .group_by(Chat.id)
                .order_by(func.count(Membership.id).desc())
                .limit(10)
            )
        ).all()
    lines = ["<b>Топ бесед AniGuard</b>"]
    for index, (chat, member_count) in enumerate(rows, 1):
        lines.append(f"{index}. {html.escape(chat.title)} — {member_count} участников")
    await message.answer("\n".join(lines))




@router.message(Command("settings"))
async def settings_handler(message: Message) -> None:
    await panel_handler(message)


async def toggle_setting_command(message: Message, command: CommandObject, key: str, label: str) -> None:
    try:
        await ensure_group_admin(message)
        raw = (command.args or "").strip().lower()
        async with SessionFactory() as session:
            settings_data = await get_merged_settings(session, message.chat.id)
            if raw in {"on", "1", "вкл", "enable"}:
                settings_data[key] = True
            elif raw in {"off", "0", "выкл", "disable"}:
                settings_data[key] = False
            else:
                await message.answer(f"{label}: {'включено' if settings_data.get(key) else 'отключено'}. Используйте on/off.")
                return
            chat = await get_chat_or_raise(session, message.chat.id)
            chat.settings = settings_data
            await add_log(session, message.chat.id, "setting_changed", actor_id=message.from_user.id, reason=f"{key}={settings_data[key]}")
            await session.commit()
        await message.answer(f"{label}: {'включено' if settings_data[key] else 'отключено'}.")
    except Exception as exc:
        await send_admin_error(message, exc)


@router.message(Command("antiflood"))
async def antiflood_handler(message: Message, command: CommandObject) -> None:
    await toggle_setting_command(message, command, "anti_flood_enabled", "Антифлуд")


@router.message(Command("links"))
async def links_handler(message: Message, command: CommandObject) -> None:
    await toggle_setting_command(message, command, "link_filter_enabled", "Фильтр ссылок")


@router.message(Command("captcha"))
async def captcha_settings_handler(message: Message, command: CommandObject) -> None:
    await toggle_setting_command(message, command, "captcha_enabled", "CAPTCHA")


@router.message(Command("words"))
async def words_handler(message: Message, command: CommandObject) -> None:
    await toggle_setting_command(message, command, "word_filter_enabled", "Фильтр слов")


@router.message(Command("anime"))
async def anime_handler(message: Message, command: CommandObject) -> None:
    await toggle_setting_command(message, command, "anime_enabled", "Аниме-режим")


@router.message(Command("rptoggle"))
async def rp_toggle_handler(message: Message, command: CommandObject) -> None:
    await toggle_setting_command(message, command, "rp_enabled", "RP-команды")


@router.message(Command("susanoo"))
async def susanoo_handler(message: Message, command: CommandObject) -> None:
    try:
        await ensure_group_admin(message)
        parsed = parse_duration_prefix(command.args or "")
        duration = 1800 if parsed.seconds is None else parsed.seconds
        async with SessionFactory() as session:
            await perform_action(
                session,
                bot,
                chat_id=message.chat.id,
                actor_id=message.from_user.id,
                action="susanoo",
                duration_seconds=duration,
                reason="Экстренная защита",
            )
            await session.commit()
        await message.answer(f"Экстренная защита включена. Срок: {format_duration_ru(duration)}.")
    except Exception as exc:
        await send_admin_error(message, exc)


@router.message(Command("rules"))
async def rules_handler(message: Message) -> None:
    try:
        if message.chat.type not in {ChatType.GROUP, ChatType.SUPERGROUP}:
            raise ValueError("Правила доступны только в группе")
        await ensure_context(message)
        async with SessionFactory() as session:
            settings_data = await get_merged_settings(session, message.chat.id)
        rules = [str(item).strip() for item in settings_data.get("group_rules", []) if str(item).strip()]
        if not rules:
            await message.answer("Правила группы пока не добавлены.")
            return
        lines = ["<b>Правила группы</b>"]
        lines.extend(f"{index}. {html.escape(rule)}" for index, rule in enumerate(rules, 1))
        await message.answer("\n".join(lines))
    except Exception as exc:
        await send_admin_error(message, exc)


@router.message(Command("automodrules"))
async def automod_rules_handler(message: Message) -> None:
    try:
        await ensure_group_admin(message)
        async with SessionFactory() as session:
            rows = (
                await session.scalars(
                    select(ModerationRule)
                    .where(ModerationRule.chat_id == message.chat.id)
                    .order_by(ModerationRule.id)
                )
            ).all()
        if not rows:
            await message.answer("Пользовательские правила автомодерации не созданы.")
            return
        lines = ["<b>Правила автомодерации</b>"]
        for row in rows:
            state = "включено" if row.enabled else "выключено"
            lines.append(f"{row.id}. {html.escape(row.name)} · {state}")
        await message.answer("\n".join(lines))
    except Exception as exc:
        await send_admin_error(message, exc)


@router.message(Command("addrule"))
async def add_rule_handler(message: Message, command: CommandObject) -> None:
    try:
        await ensure_group_admin(message)
        parts = [part.strip() for part in (command.args or "").split("|")]
        if len(parts) != 3:
            raise ValueError("Формат: /addrule Название | flood/newbie_link/blocked_word/mass_mentions | delete_warn/mute/quarantine/notify")
        name, condition_type, action_type = parts
        async with SessionFactory() as session:
            await get_chat_or_raise(session, message.chat.id)
            count = await session.scalar(select(func.count()).select_from(ModerationRule).where(ModerationRule.chat_id == message.chat.id))
            premium_access = await has_premium_access(
                session, chat_id=message.chat.id, user_id=message.from_user.id
            )
            if not premium_access and (count or 0) >= 5:
                raise PermissionError("Бесплатный тариф позволяет создать до 5 правил")
            if premium_access and (count or 0) >= 100:
                raise PermissionError("Premium позволяет создать до 100 правил")
            premium_rule = action_type == "quarantine"
            if premium_rule and not premium_access:
                raise PermissionError("Карантин в правилах требует Premium")
            row = ModerationRule(
                chat_id=message.chat.id,
                name=name,
                condition={"type": condition_type},
                actions=[{"type": action_type}],
                enabled=True,
                is_premium=premium_rule,
                created_by=message.from_user.id,
            )
            session.add(row)
            await session.commit()
        await message.answer(f"Правило «{html.escape(name)}» создано.")
    except Exception as exc:
        await send_admin_error(message, exc)


@router.message(Command("delrule"))
async def delete_rule_handler(message: Message, command: CommandObject) -> None:
    try:
        await ensure_group_admin(message)
        rule_id = int((command.args or "").strip())
        async with SessionFactory() as session:
            row = await session.get(ModerationRule, rule_id)
            if not row or row.chat_id != message.chat.id:
                raise ValueError("Правило не найдено")
            await session.delete(row)
            await session.commit()
        await message.answer("Правило удалено.")
    except Exception as exc:
        await send_admin_error(message, exc)


@router.message(Command("rplist"))
async def rp_list_handler(message: Message) -> None:
    async with SessionFactory() as session:
        rows = (await session.scalars(select(RPCommand).where(RPCommand.chat_id == message.chat.id).order_by(RPCommand.name))).all()
    if not rows:
        await message.answer("RP-команды не созданы.")
        return
    lines = ["<b>RP-команды</b>"]
    for row in rows:
        kind = "Premium" if row.is_premium else "Обычная"
        state = "вкл" if row.enabled else "выкл"
        lines.append(f"{row.id}. {html.escape(row.name)} · {kind} · {state}")
    await message.answer("\n".join(lines))


@router.message(Command("addrp"))
async def add_rp_handler(message: Message, command: CommandObject) -> None:
    try:
        await ensure_group_admin(message)
        parts = [part.strip() for part in (command.args or "").split("|", 1)]
        if len(parts) != 2:
            raise ValueError("Формат: /addrp команда | {actor} выполнил действие над {target}")
        name, template = parts
        async with SessionFactory() as session:
            await get_chat_or_raise(session, message.chat.id)
            count = await session.scalar(select(func.count()).select_from(RPCommand).where(RPCommand.chat_id == message.chat.id))
            premium_access = await has_premium_access(
                session, chat_id=message.chat.id, user_id=message.from_user.id
            )
            if not premium_access and (count or 0) >= 25:
                raise PermissionError("Бесплатный тариф позволяет создать до 25 RP-команд")
            existing = await session.scalar(select(RPCommand).where(RPCommand.chat_id == message.chat.id, func.lower(RPCommand.name) == name.lower()))
            if existing:
                raise ValueError("Команда с таким названием уже существует")
            session.add(RPCommand(chat_id=message.chat.id, name=name, response_template=template, created_by=message.from_user.id))
            await session.commit()
        await message.answer(f"RP-команда «{html.escape(name)}» создана.")
    except Exception as exc:
        await send_admin_error(message, exc)


@router.message(Command("delrp"))
async def delete_rp_handler(message: Message, command: CommandObject) -> None:
    try:
        await ensure_group_admin(message)
        name = (command.args or "").strip().casefold()
        async with SessionFactory() as session:
            row = await session.scalar(select(RPCommand).where(RPCommand.chat_id == message.chat.id, func.lower(RPCommand.name) == name))
            if not row:
                raise ValueError("RP-команда не найдена")
            await session.delete(row)
            await session.commit()
        await message.answer("RP-команда удалена.")
    except Exception as exc:
        await send_admin_error(message, exc)


@router.message(Command("logs"))
async def logs_handler(message: Message) -> None:
    try:
        await ensure_group_admin(message)
        async with SessionFactory() as session:
            rows = (await session.scalars(select(ModerationLog).where(ModerationLog.chat_id == message.chat.id).order_by(ModerationLog.created_at.desc()).limit(15))).all()
        lines = ["<b>Последние действия</b>"]
        for row in rows:
            lines.append(f"{row.created_at.strftime('%d.%m %H:%M')} · {html.escape(row.action)} · {html.escape(row.reason or '')}")
        await message.answer("\n".join(lines) if rows else "Журнал пуст.")
    except Exception as exc:
        await send_admin_error(message, exc)


@router.message(Command("reports"))
async def reports_handler(message: Message) -> None:
    try:
        await ensure_group_admin(message)
        async with SessionFactory() as session:
            rows = (await session.scalars(select(Report).where(Report.chat_id == message.chat.id, Report.status.in_(["new", "in_progress"])).order_by(Report.created_at.desc()).limit(15))).all()
        lines = ["<b>Открытые жалобы</b>"]
        for row in rows:
            lines.append(f"AG-{row.id} · пользователь {row.target_id} · {html.escape(row.reason)}")
        await message.answer("\n".join(lines) if rows else "Открытых жалоб нет.")
    except Exception as exc:
        await send_admin_error(message, exc)

def _format_group_template(template: str, *, user: Any, chat: Chat, settings_data: dict[str, Any]) -> str:
    escaped = html.escape(template or "")
    user_name = html.escape(getattr(user, "first_name", "Участник") or "Участник")
    user_link = profile_link(int(user.id), user_name)
    rules = settings_data.get("group_rules") or []
    replacements = {
        "{user}": user_link,
        "{group}": html.escape(chat.title),
        "{members}": "—",
        "{rules}": html.escape("; ".join(str(item) for item in rules)),
        "{time}": str(int(settings_data.get("captcha_timeout_seconds", 60))),
        "{attempts}": str(int(settings_data.get("captcha_attempts", 3))),
    }
    for key, value in replacements.items():
        escaped = escaped.replace(key, value)
    return escaped


async def send_welcome_message(chat: Chat, user: Any, settings_data: dict[str, Any]) -> None:
    if not settings_data.get("welcome_enabled", True):
        return
    text_value = _format_group_template(
        str(settings_data.get("welcome_text") or "Добро пожаловать, {user}, в группу «{group}»!"),
        user=user,
        chat=chat,
        settings_data=settings_data,
    )
    raw_path = str(settings_data.get("welcome_photo_path") or "")
    try:
        if raw_path and Path(raw_path).is_file():
            await bot.send_photo(chat.id, FSInputFile(raw_path), caption=text_value)
        else:
            await bot.send_message(chat.id, text_value)
    except Exception:
        try:
            await bot.send_message(chat.id, text_value)
        except Exception:
            pass


def captcha_keyboard(challenge: CaptchaChallenge) -> InlineKeyboardMarkup:
    options = list(challenge.options or [])
    rows: list[list[InlineKeyboardButton]] = []
    for offset in range(0, len(options), 3):
        rows.append([
            InlineKeyboardButton(
                text=option,
                callback_data=f"captcha:{challenge.id}:{index}",
            )
            for index, option in enumerate(options[offset:offset + 3], start=offset)
        ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def edit_captcha_message(
    challenge: CaptchaChallenge,
    text_value: str,
    *,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> None:
    if not challenge.message_id:
        return
    try:
        await bot.edit_message_caption(
            chat_id=challenge.chat_id,
            message_id=challenge.message_id,
            caption=text_value,
            reply_markup=reply_markup,
        )
        return
    except Exception:
        pass
    try:
        await bot.edit_message_text(
            text=text_value,
            chat_id=challenge.chat_id,
            message_id=challenge.message_id,
            reply_markup=reply_markup,
        )
    except Exception:
        pass


async def apply_captcha_failure(
    session: Any,
    challenge: CaptchaChallenge,
    *,
    reason: str,
) -> None:
    if challenge.resolved_at or challenge.passed:
        return
    challenge.resolved_at = utcnow()
    action = challenge.failure_action or "kick"
    settings_data = await get_merged_settings(session, challenge.chat_id)
    result_text = "Проверка не пройдена."
    try:
        if action == "ban":
            await bot.ban_chat_member(challenge.chat_id, challenge.user_id)
            result_text = "Проверка не пройдена. Пользователь заблокирован."
        elif action == "quarantine":
            duration = int(settings_data.get("newcomer_quarantine_seconds", 3600))
            until = utcnow() + timedelta(seconds=duration)
            await bot.restrict_chat_member(
                challenge.chat_id,
                challenge.user_id,
                quarantine_permissions(),
                until_date=until,
            )
            membership = await session.scalar(
                select(Membership).where(
                    Membership.chat_id == challenge.chat_id,
                    Membership.user_id == challenge.user_id,
                )
            )
            if membership:
                membership.quarantined_until = until
            result_text = "Проверка не пройдена. Пользователь оставлен в карантине."
        elif action == "notify":
            await bot.send_message(
                challenge.chat_id,
                f"Новый участник <a href=\"tg://user?id={challenge.user_id}\">не прошёл CAPTCHA</a>. "
                "Требуется решение модератора.",
            )
            result_text = "Проверка не пройдена. Модераторы уведомлены."
        else:
            await bot.ban_chat_member(challenge.chat_id, challenge.user_id)
            await bot.unban_chat_member(challenge.chat_id, challenge.user_id, only_if_banned=True)
            result_text = "Проверка не пройдена. Пользователь удалён из группы."
    except Exception:
        result_text = "Проверка не пройдена. Боту не удалось применить выбранное действие."
    await add_log(
        session,
        challenge.chat_id,
        "captcha_failed",
        target_id=challenge.user_id,
        reason=reason,
        details={"failure_action": action},
    )
    await edit_captcha_message(challenge, result_text)


async def captcha_expiry_worker() -> None:
    while True:
        try:
            async with SessionFactory() as session:
                rows = (
                    await session.scalars(
                        select(CaptchaChallenge).where(
                            CaptchaChallenge.passed.is_(False),
                            CaptchaChallenge.resolved_at.is_(None),
                            CaptchaChallenge.expires_at <= utcnow(),
                        ).limit(100)
                    )
                ).all()
                for challenge in rows:
                    await apply_captcha_failure(session, challenge, reason="Время на CAPTCHA истекло")
                if rows:
                    await session.commit()
        except asyncio.CancelledError:
            raise
        except Exception:
            pass
        await asyncio.sleep(15)


async def operations_maintenance_worker() -> None:
    """Advance shifts, probation, emergency deadlines, reports and backups."""
    while True:
        events: list[dict[str, Any]] = []
        try:
            async with SessionFactory() as session:
                events = await run_operations_maintenance(session)
                await session.commit()
            for event in events:
                kind = event.get("kind")
                chat_id = int(event.get("chat_id") or 0)
                if not chat_id:
                    continue
                try:
                    if kind == "emergency_expired":
                        await bot.set_chat_permissions(chat_id, full_permissions())
                        await bot.send_message(chat_id, "Экстренный режим завершён автоматически. Ограничения беседы сняты.")
                    elif kind == "shift_started":
                        await bot.send_message(chat_id, f"Смена модератора <code>{event.get('user_id')}</code> началась.")
                    elif kind == "shift_completed":
                        await bot.send_message(
                            chat_id,
                            "Смена модератора завершена.\n"
                            f"Предупреждений: {event.get('warnings_issued', 0)}\n"
                            f"Мутов: {event.get('mutes_issued', 0)}\n"
                            f"Удалено сообщений: {event.get('messages_deleted', 0)}",
                        )
                    elif kind == "probation_review":
                        await bot.send_message(
                            chat_id,
                            f"Испытательный срок сотрудника <code>{event.get('user_id')}</code> завершён. "
                            "Решение доступно владельцу в админ-панели.",
                        )
                    elif kind == "weekly_report":
                        payload = event.get("payload") or {}
                        await bot.send_message(
                            chat_id,
                            "<b>Еженедельный отчёт</b>\n\n"
                            f"Новых участников: {payload.get('new_members', 0)}\n"
                            f"Действий модерации: {payload.get('moderation_actions', 0)}\n"
                            f"Жалоб: {payload.get('reports', 0)}\n"
                            f"Предупреждений: {payload.get('warnings', 0)}\n"
                            f"Мутов: {payload.get('mutes', 0)}\n"
                            f"Банов: {payload.get('bans', 0)}\n"
                            f"Апелляций: {payload.get('appeals', 0)}",
                        )
                except Exception:
                    # Database transitions must not be rolled back because Telegram
                    # temporarily refused an informational message.
                    pass
        except asyncio.CancelledError:
            raise
        except Exception:
            pass
        await asyncio.sleep(30)


@router.message(F.new_chat_members)
async def new_members_handler(message: Message) -> None:
    await ensure_context(message)
    async with SessionFactory() as session:
        chat = await upsert_chat(session, message.chat)
        chat_settings = await get_merged_settings(session, chat.id)
        premium_access = await has_premium_access(
            session,
            chat_id=chat.id,
            user_id=message.from_user.id if message.from_user else None,
        )
        now_mono = time.monotonic()
        join_bucket = _join_buckets[chat.id]
        raid_window = int(chat_settings.get("anti_raid_window_seconds", chat_settings.get("raid_window_seconds", 30)))
        while join_bucket and now_mono - join_bucket[0] > raid_window:
            join_bucket.popleft()
        for _ in message.new_chat_members:
            join_bucket.append(now_mono)
        raid_detected = len(join_bucket) >= int(chat_settings.get("anti_raid_join_threshold", chat_settings.get("raid_join_limit", 8)))
        raid_lock_enabled = bool(
            chat_settings.get("anti_raid_enabled", False)
            or chat_settings.get("raid_lockdown_enabled", chat_settings.get("premium_raid_lockdown_enabled", False))
            or chat_settings.get("anti_raid_auto_enabled", False)
        )
        if raid_detected and raid_lock_enabled and not chat_settings.get("chat_locked", False):
            try:
                chat_info = await bot.get_chat(chat.id)
                if chat_info.permissions and "permissions_before_lock" not in chat_settings:
                    chat_settings["permissions_before_lock"] = chat_info.permissions.model_dump(exclude_none=True)
                await bot.set_chat_permissions(chat.id, ChatPermissions(can_send_messages=False))
                chat_settings["chat_locked"] = True
                chat_settings["anti_raid_enabled"] = True
                chat.settings = chat_settings
                await set_security_mode(
                    session,
                    chat_id=chat.id,
                    actor_id=message.from_user.id if message.from_user else 0,
                    kind="anti_raid",
                    enabled=True,
                    reason=f"За {raid_window} сек. вступило {len(join_bucket)} участников",
                )
                await add_log(
                    session,
                    chat.id,
                    "auto_raid_lockdown",
                    actor_id=message.from_user.id if message.from_user else None,
                    reason=f"За {raid_window} сек. вступило {len(join_bucket)} участников",
                )
                await message.answer(
                    "<b>AniGuard включил экстренную защиту.</b> "
                    "Обнаружено массовое вступление, отправка сообщений временно закрыта."
                )
            except Exception:
                pass

        for member in message.new_chat_members:
            if member.is_bot:
                continue
            await upsert_user(session, member)
            await ensure_membership(session, chat.id, member.id)
            mute_on_rejoin = {
                int(value)
                for value in chat_settings.get("mute_on_rejoin_user_ids", [])
                if str(value).lstrip("-").isdigit()
            }
            if member.id in mute_on_rejoin:
                try:
                    await bot.restrict_chat_member(chat.id, member.id, muted_permissions())
                    await add_log(
                        session,
                        chat.id,
                        "mute_on_rejoin",
                        actor_id=None,
                        target_id=member.id,
                        reason="Повторный вход после команды «Ура Ренге»",
                    )
                    await message.answer(
                        f"{profile_link(member.id, member.full_name or 'Пользователь')} повторно вошёл и был ограничен."
                    )
                except Exception:
                    logger.exception("Could not mute returning member %s in %s", member.id, chat.id)
                continue
            if chat_settings.get("auto_ban_newcomers", False):
                try:
                    await bot.ban_chat_member(chat.id, member.id)
                    await add_log(session, chat.id, "auto_ban_newcomer", actor_id=None, target_id=member.id, reason="Режим мудреца")
                except Exception:
                    logger.exception("Could not auto-ban newcomer %s in %s", member.id, chat.id)
                continue
            if not chat_settings.get("captcha_enabled", True):
                await send_welcome_message(chat, member, chat_settings)
                continue

            selected = select_captcha(str(chat_settings.get("captcha_image_set", "random")))
            timeout_seconds = int(chat_settings.get("captcha_timeout_seconds", 60))
            challenge = await create_captcha(
                session,
                chat.id,
                member.id,
                timeout_seconds,
                answer=str(selected["answer"]),
                options=list(selected["options"]),
                image_key=str(selected["key"]),
                attempts=int(chat_settings.get("captcha_attempts", 3)),
                failure_action=str(chat_settings.get("captcha_failure_action", "kick")),
            )
            await session.flush()
            try:
                await bot.restrict_chat_member(chat.id, member.id, muted_permissions())
                caption = _format_group_template(
                    str(chat_settings.get("captcha_message") or "{user}, выберите смайл, соответствующий изображению."),
                    user=member,
                    chat=chat,
                    settings_data=chat_settings,
                )
                sent = await bot.send_photo(
                    chat.id,
                    FSInputFile(selected["path"]),
                    caption=caption,
                    reply_markup=captcha_keyboard(challenge),
                )
                challenge.message_id = sent.message_id
            except Exception as exc:
                await add_log(
                    session,
                    chat.id,
                    "captcha_send_failed",
                    target_id=member.id,
                    reason=str(exc)[:500],
                )
                try:
                    await bot.restrict_chat_member(chat.id, member.id, full_permissions())
                except Exception:
                    pass
        await session.commit()


@router.callback_query(F.data.startswith("captcha:"))
async def captcha_callback(callback: CallbackQuery) -> None:
    try:
        _, raw_challenge_id, raw_choice = callback.data.split(":", 2)
        challenge_id = int(raw_challenge_id)
        choice_index = int(raw_choice)
        async with SessionFactory() as session:
            challenge = await session.get(CaptchaChallenge, challenge_id)
            if not challenge or challenge.resolved_at or challenge.passed:
                raise ValueError("Проверка уже завершена")
            if callback.from_user.id != challenge.user_id:
                await callback.answer("Эта CAPTCHA предназначена другому пользователю.", show_alert=True)
                return
            expires_at = as_utc(challenge.expires_at)
            if expires_at and expires_at <= utcnow():
                await apply_captcha_failure(session, challenge, reason="Время на CAPTCHA истекло")
                await session.commit()
                raise ValueError("Время проверки истекло")
            options = list(challenge.options or [])
            if choice_index < 0 or choice_index >= len(options):
                raise ValueError("Некорректный вариант ответа")
            selected = options[choice_index]
            if selected == challenge.answer:
                challenge.passed = True
                challenge.resolved_at = utcnow()
                await bot.restrict_chat_member(challenge.chat_id, challenge.user_id, full_permissions())
                await add_log(
                    session,
                    challenge.chat_id,
                    "captcha_passed",
                    actor_id=challenge.user_id,
                    target_id=challenge.user_id,
                    details={"image_key": challenge.image_key},
                )
                chat = await get_chat_or_raise(session, challenge.chat_id)
                user = await session.get(User, challenge.user_id)
                settings_data = await get_merged_settings(session, challenge.chat_id)
                await session.commit()
                await edit_captcha_message(challenge, "Проверка пройдена. Доступ к беседе открыт.")
                if user and settings_data.get("welcome_after_captcha", True):
                    await send_welcome_message(chat, user, settings_data)
                await callback.answer("Проверка пройдена.")
                return

            challenge.attempts_left = max(0, int(challenge.attempts_left or 1) - 1)
            if challenge.attempts_left <= 0:
                await apply_captcha_failure(session, challenge, reason="Закончились попытки CAPTCHA")
                await session.commit()
                await callback.answer("Попытки закончились.", show_alert=True)
                return
            await session.commit()
            await callback.answer(
                f"Неверный ответ. Осталось попыток: {challenge.attempts_left}",
                show_alert=True,
            )
    except Exception as exc:
        await callback.answer(str(exc), show_alert=True)


def _message_domains(message: Message, text: str) -> set[str]:
    urls: list[str] = []
    urls.extend(re.findall(r"(?:https?://|www\.|t\.me/)[^\s<>()]+", text, flags=re.I))
    for entity in (message.entities or []) + (message.caption_entities or []):
        if entity.type == "text_link" and getattr(entity, "url", None):
            urls.append(str(entity.url))
    domains: set[str] = set()
    for raw in urls:
        candidate = raw if "://" in raw else "https://" + raw
        host = (urlparse(candidate).hostname or "").casefold().removeprefix("www.")
        if host:
            domains.add(host)
    return domains


def _contains_mixed_alphabet(text: str) -> bool:
    for token in re.findall(r"[A-Za-zА-Яа-яЁё]{4,}", text):
        if re.search(r"[A-Za-z]", token) and re.search(r"[А-Яа-яЁё]", token):
            return True
    return False


def _normalize_obfuscated(text: str) -> str:
    table = str.maketrans({
        "0": "о", "1": "и", "3": "з", "4": "а", "5": "с", "6": "б", "7": "т", "8": "в",
        "a": "а", "c": "с", "e": "е", "o": "о", "p": "р", "x": "х", "y": "у", "k": "к", "m": "м", "t": "т", "b": "в", "h": "н",
    })
    normalized = unicodedata.normalize("NFKC", text.casefold()).translate(table)
    return re.sub(r"[^a-zа-яё0-9]+", "", normalized)


def _media_unique_id(message: Message) -> str | None:
    candidates = [
        message.video, message.document, message.audio, message.voice,
        message.video_note, message.animation, message.sticker,
    ]
    if message.photo:
        candidates.append(message.photo[-1])
    for item in candidates:
        value = getattr(item, "file_unique_id", None) if item else None
        if value:
            return str(value)
    return None


async def enforce_message_protection(message: Message, chat: Chat, membership: Membership) -> bool:
    async with SessionFactory() as session:
        settings_data = await get_merged_settings(session, chat.id)
        user_member = await session.scalar(
            select(Membership).where(
                Membership.chat_id == chat.id,
                Membership.user_id == membership.user_id,
            )
        )
        if user_member is None:
            return False

        privileged_sender = False

        try:
            tg_member = await bot.get_chat_member(
                chat.id,
                membership.user_id,
            )

            privileged_sender = tg_member.status in {
                ChatMemberStatus.CREATOR,
                ChatMemberStatus.ADMINISTRATOR,
            }

        except Exception:
            tg_member = None

        async def delete_message() -> None:
            try:
                await message.delete()
            except Exception:
                pass

        text = message.text or message.caption or ""
        lowered = text.casefold()
        restrictions = await active_restrictions(session, chat_id=chat.id, user_id=membership.user_id)
        has_media = any((
            message.photo, message.video, message.document, message.audio,
            message.voice, message.video_note, message.animation, message.sticker,
        ))
        domains = _message_domains(message, text)
        has_link_any = bool(domains) or "t.me/" in lowered
        premium_access = await has_premium_access(session, chat_id=chat.id, user_id=membership.user_id)

        def enabled(key: str, old_key: str | None = None) -> bool:
            value = bool(settings_data.get(key, False))
            if old_key:
                value = value or bool(settings_data.get(old_key, False))
            return value

        async def cleanup_recent_messages() -> None:
            if not settings_data.get("auto_cleanup_enabled", False):
                return
            count = max(1, min(int(settings_data.get("auto_cleanup_count", 10)), 50))
            # Telegram does not expose the author of arbitrary history to bots. We attempt
            # deletion only around the triggering message; failures are safely ignored.
            for message_id in range(max(1, message.message_id - count + 1), message.message_id):
                try:
                    await bot.delete_message(chat.id, message_id)
                except Exception:
                    continue

        async def apply_automatic_violation(
            action_name: str,
            reason: str,
            *,
            add_warning: bool = True,
            quarantine: bool = False,
        ) -> bool:
            await delete_message()
            await cleanup_recent_messages()
            if add_warning and settings_data.get("auto_warn_enabled", True):
                user_member.warnings += 1
            if quarantine and premium_access:
                duration = int(settings_data.get("newcomer_quarantine_seconds", settings_data.get("premium_newcomer_quarantine_seconds", 3600)))
                until = utcnow() + timedelta(seconds=duration)
                try:
                    await bot.restrict_chat_member(chat.id, membership.user_id, quarantine_permissions(), until_date=until)
                    user_member.quarantined_until = until
                except Exception:
                    pass
            if premium_access and enabled("punishment_ladder_enabled", "premium_punishment_ladder_enabled"):
                threshold = max(1, int(settings_data.get("warn_threshold", 3)))
                if user_member.warnings >= threshold * 2:
                    try:
                        await bot.ban_chat_member(chat.id, membership.user_id)
                    except Exception:
                        pass
                elif user_member.warnings >= threshold:
                    duration = int(settings_data.get("ladder_mute_seconds", settings_data.get("premium_ladder_mute_seconds", 3600)))
                    until = utcnow() + timedelta(seconds=duration)
                    try:
                        await bot.restrict_chat_member(chat.id, membership.user_id, muted_permissions(), until_date=until)
                        user_member.muted_until = until
                    except Exception:
                        pass
            threat_bucket = _threat_buckets[chat.id]
            now_value = time.monotonic()
            threat_window = int(settings_data.get("adaptive_window_seconds", settings_data.get("premium_adaptive_window_seconds", 60)))
            while threat_bucket and now_value - threat_bucket[0] > threat_window:
                threat_bucket.popleft()
            threat_bucket.append(now_value)
            if premium_access and enabled("auto_chat_close_enabled") and len(threat_bucket) >= int(settings_data.get("adaptive_trigger_count", 5)):
                try:
                    await bot.set_chat_permissions(chat.id, ChatPermissions(can_send_messages=False))
                    settings_data["chat_locked"] = True
                    db_chat = await session.get(Chat, chat.id)
                    if db_chat:
                        db_chat.settings = settings_data
                except Exception:
                    pass
            if add_warning:
                await sync_penalty_status_from_warnings(
                    session, chat_id=chat.id, actor_id=message.from_user.id,
                    membership=user_member, settings=settings_data, bot=bot,
                )
            await add_log(
                session,
                chat.id,
                action_name,
                target_id=membership.user_id,
                reason=reason[:500],
                details={"warnings": user_member.warnings, "message_id": message.message_id},
            )
            await session.commit()
            return True

        if "media" in restrictions and has_media:
            return await apply_automatic_violation("restricted_media", "Для пользователя запрещены медиа", add_warning=False)
        if "links" in restrictions and has_link_any:
            return await apply_automatic_violation("restricted_link", "Для пользователя запрещены ссылки", add_warning=False)

        now_mono = time.monotonic()
        key = (chat.id, membership.user_id)
        slow_seconds = int(settings_data.get("slow_mode_seconds", 0))
        if slow_seconds > 0:
            previous = _slow_buckets.get(key, 0)
            if now_mono - previous < slow_seconds:
                await delete_message()
                return True
            _slow_buckets[key] = now_mono

        joined_at = as_utc(user_member.joined_at) or utcnow()
        age_hours = max(0, (utcnow() - joined_at).total_seconds() / 3600)
        newcomer = age_hours < int(settings_data.get("newcomer_window_hours", 24))

        blocked_words = [str(word).casefold().strip() for word in settings_data.get("blocked_words", []) if str(word).strip()]
        blocked_trigger = any(word in lowered for word in blocked_words)
        if enabled("obfuscation_filter_enabled") and premium_access and blocked_words:
            normalized = _normalize_obfuscated(text)
            blocked_trigger = blocked_trigger or any(_normalize_obfuscated(word) in normalized for word in blocked_words)

        entities = list(message.entities or []) + list(message.caption_entities or [])
        mention_count = sum(1 for entity in entities if entity.type in {"mention", "text_mention"})
        custom_emoji_count = sum(1 for entity in entities if entity.type == "custom_emoji")
        mass_mentions_trigger = enabled("mass_mentions_enabled") and mention_count > int(settings_data.get("mass_mentions_limit", 5))

        normalized_text = re.sub(r"\s+", " ", lowered).strip()
        duplicate_trigger = False
        if enabled("duplicate_filter_enabled") and len(normalized_text) >= 3:
            bucket = _duplicate_buckets[key]
            window = int(settings_data.get("duplicate_window_seconds", 30))
            while bucket and now_mono - bucket[0][0] > window:
                bucket.popleft()
            bucket.append((now_mono, normalized_text))
            duplicate_trigger = sum(1 for _, value in bucket if value == normalized_text) >= int(settings_data.get("duplicate_limit", 3))

        def typed_flood(kind: str, active: bool, limit_key: str, window_key: str, default_limit: int, default_window: int) -> bool:
            if not active:
                return False
            bucket = _typed_flood_buckets[(chat.id, membership.user_id, kind)]
            window = int(settings_data.get(window_key, default_window))
            while bucket and now_mono - bucket[0] > window:
                bucket.popleft()
            bucket.append(now_mono)
            return len(bucket) > int(settings_data.get(limit_key, default_limit))

        letters = [char for char in text if char.isalpha()]
        upper_count = sum(1 for char in letters if char.isupper())
        caps_trigger = enabled("caps_filter_enabled") and len(letters) >= int(settings_data.get("caps_min_letters", 12)) and upper_count * 100 / max(1, len(letters)) >= int(settings_data.get("caps_ratio_percent", 75))
        emoji_count = sum(1 for char in text if unicodedata.category(char) in {"So", "Sk"})
        emoji_trigger = enabled("emoji_flood_enabled") and emoji_count > int(settings_data.get("emoji_limit", 20))
        line_trigger = enabled("line_flood_enabled") and text.count("\n") + 1 > int(settings_data.get("line_limit", 18))
        long_trigger = enabled("long_message_filter_enabled") and len(text) > int(settings_data.get("max_message_length", 3500))
        hashtag_trigger = enabled("hashtag_flood_enabled") and len(re.findall(r"(?<!\w)#[\wА-Яа-яЁё]+", text)) > int(settings_data.get("hashtag_limit", 8))
        invisible_trigger = enabled("invisible_symbols_filter_enabled") and any(unicodedata.category(ch) in {"Cf", "Cc"} and ch not in "\n\r\t" for ch in text)
        mixed_trigger = premium_access and enabled("mixed_alphabet_filter_enabled") and _contains_mixed_alphabet(text)

        command_like = bool(re.match(r"^\s*(?:/|[.!])?\w+", text)) and any(lowered.strip().startswith(prefix) for prefix in ("/", "мут", "бан", "варн", "кик", "карантин", "разбан", "размут", "очистить"))
        command_flood_trigger = typed_flood("commands", enabled("command_flood_enabled") and command_like, "command_flood_limit", "command_flood_window_seconds", 6, 20)
        sticker_trigger = typed_flood("sticker", enabled("sticker_flood_enabled") and bool(message.sticker), "sticker_limit", "sticker_window_seconds", 5, 20)
        voice_trigger = typed_flood("voice", enabled("voice_flood_enabled") and bool(message.voice or message.video_note), "voice_limit", "voice_window_seconds", 4, 60)

        forward_trigger = enabled("forward_filter_enabled") and bool(getattr(message, "forward_origin", None))
        channel_trigger = enabled("channel_sender_filter_enabled") and bool(message.sender_chat and message.sender_chat.id != chat.id)
        media_trigger = enabled("media_filter_enabled") and has_media
        contact_trigger = enabled("contact_location_filter_enabled") and bool(message.contact or message.location or message.venue)
        poll_trigger = enabled("poll_filter_enabled") and bool(message.poll)
        game_trigger = enabled("game_filter_enabled") and bool(message.game)
        custom_emoji_trigger = premium_access and enabled("custom_emoji_flood_enabled") and custom_emoji_count > int(settings_data.get("custom_emoji_limit", 12))
        newcomer_media_trigger = premium_access and enabled("newcomer_media_filter_enabled") and newcomer and has_media

        dangerous_file_trigger = False
        if enabled("dangerous_file_filter_enabled") and message.document and message.document.file_name:
            extension = message.document.file_name.rsplit(".", 1)[-1].casefold() if "." in message.document.file_name else ""
            dangerous_file_trigger = extension in {str(item).casefold().lstrip(".") for item in settings_data.get("dangerous_extensions", [])}

        media_duplicate_trigger = False
        media_id = _media_unique_id(message)
        if premium_access and enabled("media_duplicate_filter_enabled") and media_id:
            bucket = _media_hash_buckets[(chat.id, media_id)]
            window = int(settings_data.get("duplicate_window_seconds", 30))
            while bucket and now_mono - bucket[0][0] > window:
                bucket.popleft()
            bucket.append((now_mono, membership.user_id))
            media_duplicate_trigger = len(bucket) >= int(settings_data.get("duplicate_limit", 3))

        invite_trigger = enabled("invite_link_filter_enabled") and bool(re.search(r"(?:t\.me/(?:joinchat/|\+)|telegram\.me/(?:joinchat/|\+))", lowered))
        short_domains = {"bit.ly", "tinyurl.com", "clck.ru", "goo.su", "cutt.ly", "t.co", "is.gd", "rb.gy"}
        short_trigger = premium_access and enabled("short_link_filter_enabled") and bool(domains & short_domains)
        text_link_urls = [str(getattr(entity, "url", "") or "") for entity in entities if entity.type == "text_link"]
        hidden_trigger = premium_access and enabled("hidden_link_filter_enabled", "premium_hidden_links_enabled") and bool(text_link_urls)
        suspicious_tlds = (".zip", ".mov", ".top", ".click", ".work", ".support")
        phishing_words = ("telegram premium бесплатно", "получить подарок", "войти в telegram", "подтвердить аккаунт", "wallet connect", "разблокировать аккаунт")
        phishing_trigger = premium_access and enabled("phishing_filter_enabled") and (any(domain.startswith("xn--") or domain.endswith(suspicious_tlds) for domain in domains) or (has_link_any and any(word in lowered for word in phishing_words)))
        whitelist = [str(domain).casefold().removeprefix("www.") for domain in settings_data.get("allowed_domains", [])]
        whitelist_trigger = premium_access and enabled("domain_whitelist_enabled") and bool(domains) and any(not any(domain == item or domain.endswith("." + item) for item in whitelist) for domain in domains)

        allowed = whitelist
        newbie_link_trigger = False
        if enabled("link_filter_enabled") and has_link_any and newcomer:
            newbie_link_trigger = bool(domains) and not all(any(domain == item or domain.endswith("." + item) for item in allowed) for domain in domains)

        url_count = len(domains)
        phone_like = bool(re.search(r"(?:\+?\d[\s()\-]*){9,}", text))
        repeated_chars = bool(re.search(r"(.)\1{8,}", lowered))
        spam_words = ("заработок", "быстрый доход", "пиши в лс", "инвестиции", "розыгрыш", "без вложений")
        smart_spam_trigger = premium_access and enabled("smart_spam_enabled", "premium_smart_spam_enabled") and (url_count >= 2 or (phone_like and has_link_any) or repeated_chars or sum(1 for word in spam_words if word in lowered) >= 2)
        financial_trigger = premium_access and enabled("financial_spam_filter_enabled") and any(word in lowered for word in ("гарантированный доход", "удвою депозит", "крипто сигнал", "инвестиционный проект", "пассивный доход"))
        giveaway_trigger = premium_access and enabled("fake_giveaway_filter_enabled") and any(word in lowered for word in ("вы выиграли", "заберите приз", "получить подарок", "розыгрыш telegram premium", "оплатите комиссию"))

        coordinated_trigger = False
        if premium_access and enabled("coordinated_spam_enabled") and len(normalized_text) >= 8:
            bucket = _coordinated_buckets[(chat.id, normalized_text)]
            window = int(settings_data.get("coordinated_spam_window_seconds", 60))
            while bucket and now_mono - bucket[0][0] > window:
                bucket.popleft()
            bucket.append((now_mono, membership.user_id))
            coordinated_trigger = len({user_id for _, user_id in bucket}) >= int(settings_data.get("coordinated_spam_users", 3))

        image_text_trigger = False
        if premium_access and enabled("image_text_filter_enabled") and (message.photo or message.document):
            searchable = " ".join(filter(None, [message.caption or "", getattr(message.document, "file_name", "") if message.document else ""])).casefold()
            image_text_trigger = any(word in searchable for word in blocked_words) or any(word in searchable for word in spam_words)

        # ANIGUARD_LOCAL_MEDIA_START
        local_media_safety_trigger = False
        local_media_safety_reason = ""
        local_media_premium = premium_access

        if not local_media_premium:
            owner_user_id = settings_data.get("owner_user_id")
            try:
                owner_user_id = int(owner_user_id) if owner_user_id is not None else None
            except (TypeError, ValueError):
                owner_user_id = None

            if owner_user_id:
                local_media_premium = await has_premium_access(
                    session,
                    chat_id=chat.id,
                    user_id=owner_user_id,
                )

        if (
            local_media_premium
            and enabled("media_safety_filter_enabled")
            and bool(message.photo or message.sticker or message.animation or message.document)
        ):
            local_media_result = await classify_message_media(bot, message)

            if local_media_result and bool(local_media_result.get("unsafe")):
                local_media_safety_trigger = True
                local_labels = [
                    str(value).strip()
                    for value in (local_media_result.get("labels") or [])
                    if str(value).strip()
                ]
                local_media_safety_reason = (
                    "Локальная проверка медиа: "
                    + (", ".join(local_labels) or "обнаружен запрещённый визуальный контент")
                )
        # ANIGUARD_LOCAL_MEDIA_END
        # Администраторы и владелец по-прежнему не проверяются
        # обычными фильтрами флуда, капса и ссылок.
        # Однако локальная проверка запрещённого медиа работает для всех.
        if privileged_sender:
            if local_media_safety_trigger:
                applied = await apply_automatic_violation(
                    "local_unsafe_media",
                    (
                        local_media_safety_reason
                        or "Обнаружен запрещённый визуальный контент"
                    ),
                    add_warning=False,
                    quarantine=False,
                )

                try:
                    await bot.send_message(
                        chat.id,
                        (
                            "🚫 <b>Медиа удалено</b>\n\n"
                            "Локальная проверка обнаружила "
                            "откровенный или запрещённый контент."
                        ),
                    )
                except Exception:
                    logger.exception(
                        "Не удалось отправить уведомление "
                        "о локальной NSFW-проверке"
                    )

                return applied

            return False


        suspicious_newcomer = premium_access and newcomer and (
            (enabled("suspicious_profile_filter_enabled", "premium_suspicious_newcomers_enabled") and not message.from_user.username and (has_link_any or mention_count > 0 or smart_spam_trigger))
            or (enabled("account_risk_filter_enabled") and not message.from_user.username and not message.from_user.last_name)
        )

        flood_bucket = _flood_buckets[key]
        window = int(settings_data.get("flood_window_seconds", 10))
        while flood_bucket and now_mono - flood_bucket[0] > window:
            flood_bucket.popleft()
        flood_bucket.append(now_mono)
        flood_limit = int(settings_data.get("flood_limit", 6))
        current_hour = utcnow().hour
        night_start = int(settings_data.get("night_start_hour", 23))
        night_end = int(settings_data.get("night_end_hour", 7))
        night_active = current_hour >= night_start or current_hour < night_end if night_start > night_end else night_start <= current_hour < night_end
        if premium_access and enabled("night_protection_enabled") and night_active:
            flood_limit = min(flood_limit, int(settings_data.get("night_flood_limit", 4)))
        if premium_access and enabled("adaptive_protection_enabled", "premium_adaptive_protection_enabled"):
            threat_bucket = _threat_buckets[chat.id]
            adaptive_window = int(settings_data.get("adaptive_window_seconds", 60))
            while threat_bucket and now_mono - threat_bucket[0] > adaptive_window:
                threat_bucket.popleft()
            if len(threat_bucket) >= int(settings_data.get("adaptive_trigger_count", 5)):
                flood_limit = max(3, flood_limit // 2)
        flood_trigger = len(flood_bucket) > flood_limit

        rules = (await session.scalars(select(ModerationRule).where(ModerationRule.chat_id == chat.id, ModerationRule.enabled.is_(True)))).all()
        signals = {"flood": flood_trigger, "newbie_link": newbie_link_trigger, "blocked_word": blocked_trigger, "mass_mentions": mass_mentions_trigger}
        for rule in rules:
            condition_type = str((rule.condition or {}).get("type", ""))
            if not signals.get(condition_type, False):
                continue
            rule_premium = await has_premium_access(session, chat_id=chat.id, user_id=rule.created_by)
            if rule.is_premium and not rule_premium:
                continue
            stopped = False
            for action_config in rule.actions or []:
                action_type = str(action_config.get("type", ""))
                if action_type in {"delete", "delete_warn", "mute", "quarantine"}:
                    await delete_message()
                    stopped = True
                if action_type == "delete_warn":
                    user_member.warnings += 1
                elif action_type == "mute":
                    duration = int(action_config.get("duration_seconds", settings_data.get("default_mute_seconds", 1800)))
                    until = utcnow() + timedelta(seconds=duration)
                    await bot.restrict_chat_member(chat.id, membership.user_id, muted_permissions(), until_date=until)
                    user_member.muted_until = until
                elif action_type == "quarantine" and rule_premium:
                    duration = int(action_config.get("duration_seconds", 3600))
                    until = utcnow() + timedelta(seconds=duration)
                    await bot.restrict_chat_member(chat.id, membership.user_id, quarantine_permissions(), until_date=until)
                    user_member.quarantined_until = until
                elif action_type == "notify":
                    await bot.send_message(chat.id, f"Правило «{html.escape(rule.name)}» сработало для пользователя {membership.user_id}.")
            await add_log(session, chat.id, "custom_rule", target_id=membership.user_id, reason=rule.name, details={"rule_id": rule.id, "condition": condition_type})
            await session.commit()
            if stopped:
                return True

        checks = [
            (enabled("anti_flood_enabled") and flood_trigger, "auto_flood", "Превышен лимит сообщений", True, False),
            (duplicate_trigger, "duplicate_message", "Повторяющиеся сообщения", True, False),
            (line_trigger, "line_flood", "Слишком много строк", True, False),
            (long_trigger, "long_message", "Сообщение превышает допустимую длину", False, False),
            (caps_trigger, "caps_flood", "Превышена доля заглавных букв", True, False),
            (emoji_trigger, "emoji_flood", f"Слишком много эмодзи: {emoji_count}", True, False),
            (hashtag_trigger, "hashtag_flood", "Слишком много хэштегов", True, False),
            (mass_mentions_trigger, "mass_mentions", f"Массовые упоминания: {mention_count}", True, False),
            (command_flood_trigger, "command_flood", "Флуд командами бота", True, False),
            (invisible_trigger, "invisible_symbols", "Невидимые управляющие символы", True, False),
            (mixed_trigger, "mixed_alphabet", "Подмена кириллицы и латиницы", True, False),
            (enabled("word_filter_enabled") and blocked_trigger, "blocked_word", text[:200] or "Запрещённое слово", True, False),
            (forward_trigger, "forwarded_message", "Пересланные сообщения запрещены", False, False),
            (channel_trigger, "channel_sender", "Сообщения от имени сторонних каналов запрещены", False, False),
            (media_trigger, "media_filter", "Медиа запрещено настройками группы", False, False),
            (sticker_trigger, "sticker_flood", "Флуд стикерами", True, False),
            (voice_trigger, "voice_flood", "Флуд голосовыми сообщениями", True, False),
            (dangerous_file_trigger, "dangerous_file", "Опасное расширение файла", True, False),
            (contact_trigger, "contact_location", "Контакты и геолокация запрещены", False, False),
            (poll_trigger, "poll_filter", "Опросы запрещены", False, False),
            (game_trigger, "game_filter", "Встроенные игры запрещены", False, False),
            (custom_emoji_trigger, "custom_emoji_flood", "Слишком много кастомных эмодзи", True, False),
            (newcomer_media_trigger, "newcomer_media", "Медиа новых участников временно запрещены", False, True),
            (media_duplicate_trigger, "media_duplicate", "Повторная отправка одинакового медиа", True, False),
            (invite_trigger, "invite_link", "Пригласительные ссылки запрещены", True, False),
            (short_trigger, "short_link", "Сокращённая ссылка запрещена", True, False),
            (hidden_trigger, "hidden_link", "Скрытая ссылка запрещена", True, False),
            (phishing_trigger, "phishing", "Ссылка похожа на фишинговую", True, True),
            (whitelist_trigger, "domain_whitelist", "Домен отсутствует в белом списке", False, False),
            (newbie_link_trigger, "newbie_link", text[:200] or "Ссылка от нового участника", False, False),
            (smart_spam_trigger, "smart_spam", "Сообщение распознано как спам", True, False),
            (financial_trigger, "financial_spam", "Подозрительное финансовое предложение", True, True),
            (giveaway_trigger, "fake_giveaway", "Подозрительный розыгрыш", True, True),
            (coordinated_trigger, "coordinated_spam", "Координированный спам нескольких аккаунтов", True, True),
            (image_text_trigger, "image_text", "Запрещённый текст в подписи или имени медиа", True, False),
            (local_media_safety_trigger, "local_unsafe_media", local_media_safety_reason or "Запрещённый визуальный контент", True, False),
            (suspicious_newcomer, "suspicious_newcomer", "Подозрительная активность нового участника", True, enabled("auto_quarantine_enabled", "premium_auto_quarantine_enabled")),
        ]
        for triggered, action_name, reason, add_warning, quarantine in checks:
            if triggered:
                return await apply_automatic_violation(action_name, reason, add_warning=add_warning, quarantine=quarantine)

    return False


def role_allows(required: str, role: str) -> bool:
    level = role_level(role)
    if required == "admins":
        return level >= 5
    if required == "moderators":
        return level >= 2
    if required == "verified":
        return level >= 2 or normalize_role(role) == "member"
    return True


async def _delete_recent_messages(chat_id: int, from_message_id: int, amount: int) -> int:
    deleted = 0
    for message_id in range(from_message_id, max(0, from_message_id - max(1, min(amount, 100))), -1):
        try:
            await bot.delete_message(chat_id, message_id)
            deleted += 1
        except Exception:
            continue
    return deleted


async def _send_compact_command_result(
    message: Message,
    *,
    command: dict[str, Any],
    target_id: int | None = None,
    duration_seconds: int | None = None,
    reason: str = "",
    chat_title: str = "",
) -> None:
    response = str(command.get("response") or "✅ Команда выполнена и записана в журнал AniGuard.")
    if target_id is None:
        text = html.escape(response).replace("{command}", html.escape(str(command.get("name") or "команда")))
        text = text.replace("{reason}", html.escape(reason or ""))
        await message.answer(text)
        return
    await message.answer(render_custom_template(
        response,
        actor_id=message.from_user.id,
        target_id=target_id,
        command_name=str(command.get("name") or "команда"),
        duration_seconds=duration_seconds,
        reason=reason,
        chat_title=chat_title,
    ))


async def _unlock_chat_later(chat_id: int, seconds: int) -> None:
    await asyncio.sleep(max(1, seconds))
    async with SessionFactory() as session:
        try:
            await perform_action(session, bot, chat_id=chat_id, actor_id=bot.id, action="unlock", reason="Таймер завершён", premium_override=True)
            await session.commit()
            await bot.send_message(chat_id, "Чат открыт.")
        except Exception:
            logger.exception("Could not unlock chat %s after timer", chat_id)


async def _remind_later(chat_id: int, text: str, seconds: int = 600) -> None:
    await asyncio.sleep(max(1, seconds))
    try:
        await bot.send_message(chat_id, f"Напоминание: {html.escape(text or 'время вышло')}" )
    except Exception:
        logger.exception("Could not send reminder to %s", chat_id)


async def _ban_later(chat_id: int, target_id: int, actor_id: int, seconds: int = 60) -> None:
    await asyncio.sleep(max(1, seconds))
    async with SessionFactory() as session:
        try:
            await perform_action(
                session,
                bot,
                chat_id=chat_id,
                actor_id=actor_id,
                action="ban",
                target_id=target_id,
                duration_seconds=0,
                reason="Таймер ультиматума завершён",
                premium_override=True,
            )
            await session.commit()
            await bot.send_message(chat_id, f"Пользователь {target_id} заблокирован после ультиматума.")
        except Exception:
            logger.exception("Could not ban user %s in chat %s after timer", target_id, chat_id)


async def _execute_builtin_special(
    message: Message,
    chat: Chat,
    session: Any,
    command: dict[str, Any],
    remainder: str,
    target_id: int | None,
    role: str,
) -> tuple[bool, str]:
    special = str(command.get("special") or "")
    if not special:
        return False, ""

    if special == "ban_purge":
        if target_id is None:
            raise ValueError("Укажите пользователя")
        await ensure_target_can_be_moderated(chat.id, target_id)
        await perform_action(session, bot, chat_id=chat.id, actor_id=message.from_user.id, action="ban", target_id=target_id, duration_seconds=0, reason="Расен-сюрикен", premium_override=True)
        deleted = await _delete_recent_messages(chat.id, message.message_id, int(command.get("fixed_amount") or 100))
        return True, f"Бан выполнен. Удалено: {deleted}."

    if special == "vote_ban":
        if target_id is None:
            raise ValueError("Укажите пользователя")
        await bot.send_poll(chat.id, f"Забанить пользователя {target_id}?", ["Да", "Нет"], is_anonymous=False)
        return True, "Голосование создано."

    if special == "undo_last":
        last = await session.scalar(select(ModerationLog).where(ModerationLog.chat_id == chat.id).order_by(ModerationLog.id.desc()))
        if not last or not last.target_id:
            return True, "Нет действия для отмены."
        inverse = {"warn":"unwarn", "mute":"unmute", "ban":"unban", "quarantine":"unquarantine", "restrict_media":"unrestrict_media", "restrict_links":"unrestrict_links", "restrict_commands":"unrestrict_commands"}.get(last.action)
        if not inverse:
            return True, "Последнее действие нельзя отменить."
        await perform_action(session, bot, chat_id=chat.id, actor_id=message.from_user.id, action=inverse, target_id=last.target_id, reason="Изанаги", premium_override=True)
        return True, "Последнее действие отменено."

    if special == "settings_patch":
        patch = dict(command.get("settings_patch") or {})
        if not patch:
            return True, "Режим не изменил настройки."
        await update_chat_settings(session, chat.id, patch)
        return True, f"Режим включён: {command.get('name')}."

    if special == "unmute_all":
        rows = (await session.scalars(select(Membership).where(Membership.chat_id == chat.id))).all()
        count = 0
        for row in rows:
            if row.muted_until:
                try:
                    await bot.restrict_chat_member(chat.id, row.user_id, full_permissions())
                    row.muted_until = None
                    count += 1
                except Exception:
                    continue
        return True, f"Муты сняты: {count}."

    if special == "pin_reply":
        if not message.reply_to_message:
            raise ValueError("Ответьте на сообщение")
        await bot.pin_chat_message(chat.id, message.reply_to_message.message_id, disable_notification=True)
        return True, "Сообщение закреплено."
    if special == "unpin_reply":
        if not message.reply_to_message:
            raise ValueError(
                "Ответьте командой «открепить» "
                "на закреплённое сообщение"
            )

        await bot.unpin_chat_message(
            chat_id=chat.id,
            message_id=message.reply_to_message.message_id,
        )

        return True, "Сообщение откреплено."

    if special == "unpin_all":
        await bot.unpin_all_chat_messages(chat.id)
        return True, "Все закреплённые сообщения сняты."

    if special == "chat_link":
        telegram_chat = await bot.get_chat(chat.id)
        username = str(getattr(telegram_chat, "username", "") or "").strip().lstrip("@")
        if username:
            link = f"https://t.me/{username}"
        else:
            link = await bot.export_chat_invite_link(chat.id)
        await message.answer(
            "🔗 <b>Актуальная ссылка на чат</b>\n"
            + html.escape(link)
        )
        return True, ""

    if special == "chat_link_one":
        invite = await bot.create_chat_invite_link(
            chat.id,
            name="AniGuard: 1 пользователь",
            member_limit=1,
        )
        await message.answer(
            "🔗 <b>Ссылка для одного пользователя</b>\n"
            + html.escape(invite.invite_link)
            + "\n\nПосле вступления одного человека ссылка перестанет работать."
        )
        return True, ""


    if special == "reset_newcomer":
        if target_id is None:
            raise ValueError("Укажите пользователя")
        row = await ensure_membership(session, chat.id, target_id, "member")
        row.role = "member"
        row.joined_at = utcnow()
        row.warnings = 0
        return True, "Статус новичка восстановлен."

    if special == "announce":
        text = remainder.strip()
        if not text:
            raise ValueError("Добавьте текст объявления")
        await bot.send_message(chat.id, f"<b>Объявление:</b>\n{html.escape(text)}")
        return True, "Объявление отправлено."

    if special == "reports_summary":
        rows = (await session.scalars(select(Report).where(Report.chat_id == chat.id, Report.status == "new").order_by(Report.id.desc()).limit(10))).all()
        if not rows:
            return True, "Открытых жалоб нет."
        lines = [f"#{row.id}: {row.target_id} — {row.reason[:80]}" for row in rows]
        await message.answer("<b>Жалобы:</b>\n" + "\n".join(html.escape(line) for line in lines))
        return True, ""

    if special in {"logs_summary", "search_logs"}:
        query = select(ModerationLog).where(ModerationLog.chat_id == chat.id)
        if special == "search_logs" and remainder.strip():
            term = remainder.strip().casefold()
            all_rows = (await session.scalars(query.order_by(ModerationLog.id.desc()).limit(100))).all()
            rows = [row for row in all_rows if term in f"{row.action} {row.reason or ''} {row.target_id or ''}".casefold()][:15]
        else:
            rows = (await session.scalars(query.order_by(ModerationLog.id.desc()).limit(15))).all()
        if not rows:
            return True, "Логи не найдены."
        lines = [f"#{row.id} {row.action} → {row.target_id or 'чат'}" for row in rows]
        await message.answer("<b>Логи:</b>\n" + "\n".join(lines))
        return True, ""

    if special == "add_blocked_words":
        words = [word.strip().casefold() for word in re.split(r"[,\s]+", remainder) if word.strip()]
        if not words:
            raise ValueError("Добавьте слово")
        settings_data = await get_merged_settings(session, chat.id)
        blocked = list(dict.fromkeys([*(settings_data.get("blocked_words") or []), *words]))
        await update_chat_settings(session, chat.id, {"blocked_words": blocked, "word_filter_enabled": True})
        return True, f"Добавлено слов: {len(words)}."

    if special == "delete_reply":
        if not message.reply_to_message:
            raise ValueError("Ответьте на сообщение")
        await bot.delete_message(chat.id, message.reply_to_message.message_id)
        try:
            await message.delete()
        except Exception:
            pass
        return True, "Сообщение удалено."

    if special == "delete_warn":
        if not message.reply_to_message or not message.reply_to_message.from_user:
            raise ValueError("Ответьте на сообщение")
        target_id = message.reply_to_message.from_user.id
        await ensure_target_can_be_moderated(chat.id, target_id)
        await bot.delete_message(chat.id, message.reply_to_message.message_id)
        await perform_action(session, bot, chat_id=chat.id, actor_id=message.from_user.id, action="warn", target_id=target_id, reason="Взрывная печать", premium_override=True)
        return True, "Сообщение удалено. Варн выдан."

    if special == "reject_report":
        match = re.search(r"\d+", remainder)
        if not match:
            raise ValueError("Укажите ID жалобы")
        report = await session.get(Report, int(match.group()))
        if not report or report.chat_id != chat.id:
            return True, "Жалоба не найдена."
        report.status = "rejected"
        report.closed_at = utcnow()
        report.assigned_to = message.from_user.id
        return True, f"Жалоба #{report.id} отклонена."

    if special in {"add_whitelist", "mark_dangerous"}:
        if target_id is None:
            raise ValueError("Укажите пользователя")
        settings_data = await get_merged_settings(session, chat.id)
        key = "whitelist_user_ids" if special == "add_whitelist" else "dangerous_user_ids"
        values = [int(value) for value in settings_data.get(key, []) if str(value).lstrip('-').isdigit()]
        if target_id not in values:
            values.append(target_id)
        await update_chat_settings(session, chat.id, {key: values})
        return True, "Белый список обновлён." if special == "add_whitelist" else "Пользователь помечен."

    if special == "call_moderators":
        admins = await bot.get_chat_administrators(chat.id)
        mentions = [profile_link(item.user.id, item.user.full_name or "Модератор") for item in admins if not item.user.is_bot]
        await message.answer("Модераторы: " + ", ".join(mentions[:20]))
        return True, ""

    if special == "unsupported_alts":
        return True, "Telegram не раскрывает альтернативные аккаунты."

    if special == "user_history":
        if target_id is None:
            raise ValueError("Укажите пользователя")
        rows = (await session.scalars(select(ModerationLog).where(ModerationLog.chat_id == chat.id, ModerationLog.target_id == target_id).order_by(ModerationLog.id.desc()).limit(15))).all()
        if not rows:
            return True, "Нарушений не найдено."
        await message.answer("<b>История:</b>\n" + "\n".join(f"#{row.id} {html.escape(row.action)}" for row in rows))
        return True, ""

    if special == "global_block":
        if not is_admin_role(role):
            raise PermissionError("Нужны права администратора")
        if target_id is None:
            raise ValueError("Укажите пользователя")
        await ensure_target_can_be_moderated(chat.id, target_id)
        await perform_action(session, bot, chat_id=chat.id, actor_id=message.from_user.id, action="ban", target_id=target_id, duration_seconds=0, reason="Глобальный список AniGuard", premium_override=True)
        await set_entity_block(session, entity_type="user", entity_id=target_id, blocked=True, admin_id=message.from_user.id, reason="Глобальный список AniGuard")
        return True, "Пользователь заблокирован в AniGuard."

    if special in {"top_offenders", "violation_stats"}:
        rows = (await session.scalars(select(Membership).where(Membership.chat_id == chat.id).order_by(Membership.warnings.desc(), Membership.penalty_points.desc()).limit(10))).all()
        if special == "violation_stats":
            logs_count = int(await session.scalar(select(func.count()).select_from(ModerationLog).where(ModerationLog.chat_id == chat.id)) or 0)
            warnings = sum(row.warnings for row in rows)
            return True, f"Действий: {logs_count}. Варнов в топе: {warnings}."
        if not rows:
            return True, "Данных нет."
        await message.answer("<b>Топ нарушителей:</b>\n" + "\n".join(f"{index}. {profile_link(row.user_id, 'User')} — {row.warnings}" for index, row in enumerate(rows, 1)))
        return True, ""

    if special == "moderator_stats":
        rows = (await session.execute(select(ModerationLog.actor_id, func.count(ModerationLog.id)).where(ModerationLog.chat_id == chat.id).group_by(ModerationLog.actor_id).order_by(func.count(ModerationLog.id).desc()).limit(10))).all()
        if not rows:
            return True, "Данных нет."
        await message.answer("<b>Модераторы:</b>\n" + "\n".join(f"{profile_link(actor or 0, 'Admin')} — {count}" for actor, count in rows))
        return True, ""

    if special == "weekly_report":
        since = utcnow() - timedelta(days=7)
        logs = int(await session.scalar(select(func.count()).select_from(ModerationLog).where(ModerationLog.chat_id == chat.id, ModerationLog.created_at >= since)) or 0)
        reports = int(await session.scalar(select(func.count()).select_from(Report).where(Report.chat_id == chat.id, Report.created_at >= since)) or 0)
        messages = int(await session.scalar(select(func.sum(Membership.message_count)).where(Membership.chat_id == chat.id)) or 0)
        return True, f"7 дней: действий {logs}, жалоб {reports}, сообщений {messages}."

    if special == "activity_stats":
        rows = (await session.scalars(select(Membership).where(Membership.chat_id == chat.id).order_by(Membership.message_count.desc()).limit(10))).all()
        if not rows:
            return True, "Данных нет."
        await message.answer("<b>Активность:</b>\n" + "\n".join(f"{index}. {profile_link(row.user_id, 'User')} — {row.message_count}" for index, row in enumerate(rows, 1)))
        return True, ""

    if special == "bot_check":
        if target_id is None:
            raise ValueError("Укажите пользователя")
        member = await bot.get_chat_member(chat.id, target_id)
        return True, "Это бот." if member.user.is_bot else "Это пользователь."

    if special == "ping_members":
        rows = (await session.scalars(select(Membership).where(Membership.chat_id == chat.id).order_by(Membership.last_seen_at.desc()).limit(20))).all()
        mentions = [profile_link(row.user_id, "忍") for row in rows]
        await message.answer("Даттебайо! " + " ".join(mentions))
        return True, ""

    if special == "random_mute":
        rows = (await session.scalars(select(Membership).where(Membership.chat_id == chat.id, Membership.role == "member"))).all()
        if not rows:
            return True, "Нет доступных участников."
        chosen = random.choice(rows)
        await ensure_target_can_be_moderated(chat.id, chosen.user_id)
        await perform_action(session, bot, chat_id=chat.id, actor_id=message.from_user.id, action="mute", target_id=chosen.user_id, duration_seconds=300, reason="Расенган-рулетка", premium_override=True)
        return True, f"Мут 5 минут: {chosen.user_id}."

    if special in {"quiz_poll", "anonymous_poll"}:
        question = remainder.strip() or ("Экзамен на выживание" if special == "quiz_poll" else "Теневой клон: ваш выбор?")
        await bot.send_poll(chat.id, question[:300], ["Да", "Нет"], is_anonymous=True, type="quiz" if special == "quiz_poll" else "regular", correct_option_id=0 if special == "quiz_poll" else None)
        return True, "Опрос создан."

    if special == "reminder":
        text = remainder.strip() or "Призыв жабы"
        asyncio.create_task(_remind_later(chat.id, text), name=f"aniguard-reminder-{chat.id}-{message.message_id}")
        return True, "Напоминание создано на 10 минут."

    if special == "lock_timed":
        seconds = int(command.get("fixed_duration_seconds") or 3600)
        await perform_action(session, bot, chat_id=chat.id, actor_id=message.from_user.id, action="lock", reason=str(command.get("name") or "Таймер"), premium_override=True)
        asyncio.create_task(_unlock_chat_later(chat.id, seconds), name=f"aniguard-unlock-{chat.id}-{message.message_id}")
        return True, f"Чат закрыт на {format_duration_ru(seconds)}."

    if special == "random_decision":
        return True, random.choice(["Решение: варн.", "Решение: мут.", "Решение: без наказания."])

    if special == "random_unmute":
        rows = (await session.scalars(select(Membership).where(Membership.chat_id == chat.id, Membership.muted_until.is_not(None)))).all()
        if not rows:
            return True, "Активных мутов нет."
        chosen = random.choice(rows)
        await perform_action(session, bot, chat_id=chat.id, actor_id=message.from_user.id, action="unmute", target_id=chosen.user_id, reason="Печать удачи", premium_override=True)
        return True, f"Мут снят: {chosen.user_id}."

    if special == "motivation":
        await message.answer("Огонь воли не гаснет. Продолжайте двигаться вперёд.")
        return True, ""

    if special == "kick_all":
        if normalize_role(role) != "creator":
            raise PermissionError("Команда доступна владельцу")
        if "подтвердить" not in remainder.casefold():
            return True, "Повторите команду со словом «подтвердить»."
        rows = (await session.scalars(select(Membership).where(Membership.chat_id == chat.id, Membership.role == "member").limit(100))).all()
        count = 0
        for row in rows:
            try:
                await bot.ban_chat_member(chat.id, row.user_id)
                await bot.unban_chat_member(chat.id, row.user_id, only_if_banned=True)
                count += 1
            except Exception:
                continue
        return True, f"Удалено участников: {count}."

    if special == "auto_ban_newcomers":
        await update_chat_settings(session, chat.id, {"auto_ban_newcomers": True})
        return True, "Автобан новичков включён."

    if special == "reset_restrictions":
        await perform_action(session, bot, chat_id=chat.id, actor_id=message.from_user.id, action="unlock", reason="Восемь врат", premium_override=True)
        rows = (await session.scalars(select(Membership).where(Membership.chat_id == chat.id))).all()
        for row in rows:
            if row.muted_until or row.quarantined_until:
                try:
                    await bot.restrict_chat_member(chat.id, row.user_id, full_permissions())
                except Exception:
                    pass
                row.muted_until = None
                row.quarantined_until = None
        return True, "Ограничения сняты."

    if special == "unsupported_recreate":
        return True, "Telegram API не позволяет удалить и пересоздать чат."

    if special == "reset_moderation":
        from app.defaults import default_chat_settings
        current = await get_merged_settings(session, chat.id)
        owner_id = current.get("owner_user_id")
        reset = default_chat_settings()
        reset["owner_user_id"] = owner_id
        chat.settings = reset
        return True, "Настройки модерации сброшены."

    if special == "unsupported_mass_tag":
        return True, "Telegram не позволяет получить и заблокировать всех участников по произвольному тегу."

    if special == "mass_ban_list":
        if not is_admin_role(role):
            raise PermissionError("Нужны права администратора")
        if "подтвердить" not in remainder.casefold():
            return True, "Укажите ID пользователей и добавьте слово «подтвердить»."
        user_ids = [int(value) for value in re.findall(r"-?\d{4,}", remainder)]
        user_ids = list(dict.fromkeys(user_ids))[:20]
        if not user_ids:
            raise ValueError("Добавьте ID пользователей")
        count = 0
        for user_id in user_ids:
            try:
                await ensure_target_can_be_moderated(chat.id, user_id)
                await perform_action(session, bot, chat_id=chat.id, actor_id=message.from_user.id, action="ban", target_id=user_id, duration_seconds=0, reason="Муген Цукуёми", premium_override=True)
                count += 1
            except Exception:
                continue
        return True, f"Заблокировано пользователей: {count}."

    if special == "kick_warn":
        if target_id is None:
            raise ValueError("Укажите пользователя")
        await ensure_target_can_be_moderated(chat.id, target_id)
        await perform_action(session, bot, chat_id=chat.id, actor_id=message.from_user.id, action="warn", target_id=target_id, reason="Листок-ураган", premium_override=True)
        await perform_action(session, bot, chat_id=chat.id, actor_id=message.from_user.id, action="kick", target_id=target_id, reason="Листок-ураган", premium_override=True)
        return True, "Предупреждение выдано. Пользователь удалён."

    if special == "kick_delete":
        if target_id is None:
            raise ValueError("Укажите пользователя")
        if message.reply_to_message:
            try:
                await bot.delete_message(chat.id, message.reply_to_message.message_id)
            except Exception:
                pass
        await ensure_target_can_be_moderated(chat.id, target_id)
        await perform_action(session, bot, chat_id=chat.id, actor_id=message.from_user.id, action="kick", target_id=target_id, reason="Омотэ Ренге", premium_override=True)
        return True, "Последнее сообщение удалено. Пользователь исключён."

    if special == "kick_reentry_mute":
        if target_id is None:
            raise ValueError("Укажите пользователя")
        await ensure_target_can_be_moderated(chat.id, target_id)
        settings_data = await get_merged_settings(session, chat.id)
        values = [int(value) for value in settings_data.get("mute_on_rejoin_user_ids", []) if str(value).lstrip('-').isdigit()]
        if target_id not in values:
            values.append(target_id)
        await update_chat_settings(session, chat.id, {"mute_on_rejoin_user_ids": values})
        await perform_action(session, bot, chat_id=chat.id, actor_id=message.from_user.id, action="kick", target_id=target_id, reason="Ура Ренге", premium_override=True)
        return True, "Пользователь исключён. При повторном входе будет ограничен."

    if special == "warn_ban_timer":
        if target_id is None:
            raise ValueError("Укажите пользователя")
        await ensure_target_can_be_moderated(chat.id, target_id)
        await perform_action(session, bot, chat_id=chat.id, actor_id=message.from_user.id, action="warn", target_id=target_id, reason="Ультиматум Хокаге", premium_override=True)
        asyncio.create_task(_ban_later(chat.id, target_id, message.from_user.id, 60), name=f"aniguard-ban-timer-{chat.id}-{target_id}")
        return True, "Предупреждение выдано. Бан будет применён через 1 минуту."

    if special == "warns_summary":
        if target_id is None:
            raise ValueError("Укажите пользователя")
        member = await ensure_membership(session, chat.id, target_id)
        rows = (await session.scalars(select(ModerationLog).where(ModerationLog.chat_id == chat.id, ModerationLog.target_id == target_id, ModerationLog.action == "warn").order_by(ModerationLog.id.desc()).limit(10))).all()
        return True, f"Предупреждений: {member.warnings}. Записей в журнале: {len(rows)}."

    if special == "reset_target_restrictions":
        if target_id is None:
            raise ValueError("Укажите пользователя")
        member = await ensure_membership(session, chat.id, target_id)
        try:
            await bot.restrict_chat_member(chat.id, target_id, full_permissions())
        except Exception:
            pass
        member.muted_until = None
        member.quarantined_until = None
        for kind in ("media", "links", "commands"):
            try:
                from app.services import clear_restriction
                await clear_restriction(session, chat_id=chat.id, user_id=target_id, kind=kind)
            except Exception:
                pass
        return True, "Все ограничения пользователя сняты."

    if special == "unwarn_all":
        rows = (await session.scalars(select(Membership).where(Membership.chat_id == chat.id))).all()
        changed = 0
        for row in rows:
            if row.warnings:
                row.warnings = 0
                changed += 1
        return True, f"Предупреждения обнулены у {changed} участников."

    if special == "clear_user_history":
        if target_id is None:
            raise ValueError("Укажите пользователя")
        member = await ensure_membership(session, chat.id, target_id)
        member.warnings = 0
        member.penalty_points = 0
        return True, "Счётчики нарушений пользователя обнулены. Журнал аудита сохранён."

    if special in {"profile_summary", "role_summary", "presence_summary", "active_penalties", "serious_violations"}:
        if target_id is None:
            raise ValueError("Укажите пользователя")
        member = await ensure_membership(session, chat.id, target_id)
        settings_data = await get_merged_settings(session, chat.id)
        rank_labels = settings_data.get("rank_labels") or {}
        rank = rank_labels.get(str(target_id)) or member.role
        if special == "profile_summary":
            return True, f"ID: {target_id}. Роль: {rank}. Варны: {member.warnings}. Сообщения: {member.message_count}. XP: {member.xp}."
        if special == "role_summary":
            return True, f"Роль AniGuard: {rank}. Системная роль: {member.role}."
        if special == "presence_summary":
            seen = member.last_seen_at.isoformat() if member.last_seen_at else "нет данных"
            return True, f"Последняя известная активность: {seen}. Telegram не предоставляет точный онлайн-статус боту."
        if special == "active_penalties":
            active = []
            if member.warnings: active.append(f"варны: {member.warnings}")
            if member.muted_until: active.append("мут")
            if member.quarantined_until: active.append("карантин")
            return True, "Активные наказания: " + (", ".join(active) if active else "нет") + "."
        rows = (await session.scalars(select(ModerationLog).where(ModerationLog.chat_id == chat.id, ModerationLog.target_id == target_id).order_by(ModerationLog.id.desc()).limit(10))).all()
        serious = [row for row in rows if row.action in {"ban", "mute", "quarantine"}]
        return True, f"Серьёзных нарушений в последних записях: {len(serious)}."

    if special == "rules_summary":
        settings_data = await get_merged_settings(session, chat.id)
        rules = [str(item).strip() for item in settings_data.get("group_rules", []) if str(item).strip()]
        if not rules:
            return True, "Правила беседы пока не заполнены."
        await message.answer("<b>Правила:</b>\n" + "\n".join(f"{index}. {html.escape(rule)}" for index, rule in enumerate(rules, 1)))
        return True, ""

    if special == "members_summary":
        rows = (await session.scalars(select(Membership).where(Membership.chat_id == chat.id).order_by(Membership.message_count.desc()).limit(30))).all()
        if not rows:
            return True, "Участники не найдены."
        await message.answer("<b>Участники:</b>\n" + "\n".join(f"{index}. {profile_link(row.user_id, 'User')} — {row.role}" for index, row in enumerate(rows, 1)))
        return True, ""

    if special == "set_role":
        if target_id is None:
            raise ValueError("Укажите пользователя")
        patch = dict(command.get("settings_patch") or {})
        if patch.get("owner_only") and normalize_role(role) != "creator":
            raise PermissionError("Команда доступна владельцу")
        member = await ensure_membership(session, chat.id, target_id)
        member.role = str(patch.get("role") or "member")
        settings_data = await get_merged_settings(session, chat.id)
        labels = dict(settings_data.get("rank_labels") or {})
        labels[str(target_id)] = str(patch.get("rank_label") or member.role)
        update = {"rank_labels": labels}
        if patch.get("whitelist"):
            values = [int(value) for value in settings_data.get("whitelist_user_ids", []) if str(value).lstrip('-').isdigit()]
            if target_id not in values: values.append(target_id)
            update["whitelist_user_ids"] = values
        if patch.get("dangerous"):
            values = [int(value) for value in settings_data.get("dangerous_user_ids", []) if str(value).lstrip('-').isdigit()]
            if target_id not in values: values.append(target_id)
            update["dangerous_user_ids"] = values
        await update_chat_settings(session, chat.id, update)
        return True, f"Назначен ранг: {labels[str(target_id)]}."

    if special == "remove_role":
        if target_id is None:
            raise ValueError("Укажите пользователя")
        member = await ensure_membership(session, chat.id, target_id)
        member.role = "member"
        settings_data = await get_merged_settings(session, chat.id)
        labels = dict(settings_data.get("rank_labels") or {})
        labels.pop(str(target_id), None)
        await update_chat_settings(session, chat.id, {"rank_labels": labels})
        return True, "Ранг AniGuard снят."

    if special == "promote_activity":
        if target_id is None:
            raise ValueError("Укажите пользователя")
        member = await ensure_membership(session, chat.id, target_id)
        if member.message_count < 100 and member.xp < 500:
            return True, "Недостаточно активности для повышения. Нужно 100 сообщений или 500 XP."
        settings_data = await get_merged_settings(session, chat.id)
        labels = dict(settings_data.get("rank_labels") or {})
        labels[str(target_id)] = "Чунин"
        await update_chat_settings(session, chat.id, {"rank_labels": labels})
        return True, "Экзамен пройден. Назначен ранг Чунин."

    if special == "chat_media_lock":
        await bot.set_chat_permissions(chat.id, ChatPermissions(can_send_messages=True, can_send_audios=False, can_send_documents=False, can_send_photos=False, can_send_videos=False, can_send_video_notes=False, can_send_voice_notes=False, can_send_polls=False, can_send_other_messages=False, can_add_web_page_previews=False))
        return True, "Отправка медиа в чате запрещена."

    if special == "chat_media_unlock":
        await bot.set_chat_permissions(chat.id, full_permissions())
        return True, "Отправка медиа разрешена."

    if special == "unsupported_nickname_lock":
        return True, "Telegram Bot API не позволяет запретить конкретному пользователю менять имя."

    if special == "captcha_each_message":
        if target_id is None:
            raise ValueError("Укажите пользователя")
        settings_data = await get_merged_settings(session, chat.id)
        values = [int(value) for value in settings_data.get("captcha_each_message_user_ids", []) if str(value).lstrip('-').isdigit()]
        if target_id not in values: values.append(target_id)
        await update_chat_settings(session, chat.id, {"captcha_each_message_user_ids": values, "captcha_enabled": True})
        return True, "Усиленная CAPTCHA для пользователя включена."

    if special == "restrict_rate":
        if target_id is None:
            raise ValueError("Укажите пользователя")
        settings_data = await get_merged_settings(session, chat.id)
        limits = dict(settings_data.get("per_user_rate_limits") or {})
        limits[str(target_id)] = {"messages": 3, "seconds": 60}
        await update_chat_settings(session, chat.id, {"per_user_rate_limits": limits})
        return True, "Лимит установлен: 3 сообщения в минуту."

    if special == "unsupported_hide_user":
        if target_id is None:
            raise ValueError("Укажите пользователя")
        await ensure_target_can_be_moderated(chat.id, target_id)
        await perform_action(
            session,
            bot,
            chat_id=chat.id,
            actor_id=message.from_user.id,
            action="mute",
            target_id=target_id,
            duration_seconds=0,
            reason="Печать забвения",
            premium_override=True,
        )
        return True, "Telegram не умеет скрывать сообщения только от остальных. Вместо этого применён бессрочный мут."

    if special == "ping_target":
        if target_id is None:
            raise ValueError("Укажите пользователя")
        await message.answer("Призыв: " + profile_link(target_id, "пользователь"))
        return True, ""

    if special == "birthday_message":
        if target_id is None:
            raise ValueError("Укажите пользователя")
        await message.answer(f"🎉 {profile_link(target_id, 'Ниндзя')}, с днём рождения! Желаем силы, чакры и удачных миссий!")
        return True, ""

    if special == "night_mode":
        seconds = int(command.get("fixed_duration_seconds") or 28800)
        await perform_action(session, bot, chat_id=chat.id, actor_id=message.from_user.id, action="lock", reason="Ночной режим", premium_override=True)
        asyncio.create_task(_unlock_chat_later(chat.id, seconds), name=f"aniguard-night-mode-{chat.id}-{message.message_id}")
        return True, f"Ночной режим включён на {format_duration_ru(seconds)}."

    if special == "owner_control_summary":
        if normalize_role(role) != "creator":
            raise PermissionError("Команда доступна владельцу")
        settings_data = await get_merged_settings(session, chat.id)
        return True, f"Полный контроль владельца активен. Варнов до автомата: {settings_data.get('warn_threshold', 3)}. Антифлуд: {'включён' if settings_data.get('anti_flood_enabled') else 'выключен'}."

    return True, "Команда зарегистрирована, но действие недоступно."


async def try_basic_moderation_command(message: Message, chat: Chat, membership: Membership) -> bool:
    """Execute ordinary and Naruto-styled built-in commands.

    Syntax is flexible: slash is optional, underscores and spaces are
    interchangeable, and Russian/English aliases share the same action.
    """
    text = (message.text or "").strip()
    if not text or not message.from_user:
        return False

    async with SessionFactory() as session:
        settings_data = await get_merged_settings(session, chat.id)
        configured = default_basic_commands()
        stored = settings_data.get("basic_moderation_commands")
        if isinstance(stored, dict):
            for key, value in stored.items():
                if key in configured and isinstance(value, dict):
                    configured[key].update({field: field_value for field, field_value in value.items() if field in {"name", "trigger", "action", "response"}})
                    if "response" in value:
                        configured[key]["_response_overridden"] = True

        matched = match_builtin_command(text, configured)
        if matched is None:
            return False
        matched_key, config, remainder = matched

        role = await member_role(chat.id, message.from_user.id)
        if not is_staff_role(role):
            await message.reply("Команда доступна модераторам.")
            return True
        restrictions = await active_restrictions(session, chat_id=chat.id, user_id=message.from_user.id)
        if "commands" in restrictions:
            await message.reply("Команды временно недоступны.")
            return True

        premium = await has_premium_access(session, chat_id=chat.id, user_id=message.from_user.id)
        if bool(config.get("premium")) and not premium:
            await message.reply("Нужен AniGuard Premium.")
            return True

        action = str(config.get("action") or matched_key)
        name = str(config.get("name") or matched_key)
        response = str(config.get("response") or "")
        style, custom_style_templates = await _chat_style(session, settings_data)
        target_required = config.get("target_required")
        if target_required is None:
            target_required = action in {"warn", "unwarn", "mute", "unmute", "ban", "unban", "kick", "quarantine", "unquarantine", "restrict_media", "unrestrict_media", "restrict_links", "unrestrict_links", "restrict_commands", "unrestrict_commands"}

        default_duration = int(settings_data.get(DEFAULT_DURATION_KEYS.get(action, "default_mute_seconds"), 604800))
        parsed = parse_moderation_command(
            remainder,
            default_duration_seconds=default_duration,
            forced_action=action if action in TIMED_ACTIONS | {"warn", "unwarn", "unmute", "unban", "unquarantine", "kick", "restrict_media", "unrestrict_media", "restrict_links", "unrestrict_links", "restrict_commands", "unrestrict_commands", "purge", "slow", "lock", "unlock"} else "warn",
            forced_trigger=str(config.get("trigger") or action),
        )
        target_token = parsed.target_token if parsed else None
        if target_token is None and target_required:
            target_match = re.match(r"^\s*(@[A-Za-z0-9_]{5,}|-?\d{4,})\b", remainder)
            if target_match:
                target_token = target_match.group(1)
                remainder = remainder[target_match.end():].strip()
        target_id = await resolve_target(message, target_token)
        if target_required and target_id is None:
            await message.reply("Ответьте пользователю или укажите @username/ID.")
            return True
        if target_id == message.from_user.id and action not in {"unmute", "unwarn", "unban"}:
            await message.reply("Нельзя применить команду к себе.")
            return True

        special_handled, special_result = await _execute_builtin_special(message, chat, session, config, remainder, target_id, role)
        if special_handled:
            await session.commit()
            if special_result:
                special_key = str(
                    config.get("special") or ""
                ).strip()

                normalized_result = _normalize_phrase(
                    special_result
                )

                direct_response = None

                if (
                    special_key == "pin_reply"
                    or normalized_result
                    == "сообщение закреплено"
                ):
                    direct_response = (
                        "Сообщение закреплено"
                    )

                elif (
                    special_key == "unpin_reply"
                    or normalized_result
                    == "сообщение откреплено"
                ):
                    direct_response = (
                        "Сообщение откреплено"
                    )

                elif (
                    special_key
                    in {
                        "unpin_all",
                        "unpinall",
                    }
                    or normalized_result
                    in {
                        "все сообщения откреплены",
                        (
                            "все закрепленные "
                            "сообщения сняты"
                        ),
                        "все закрепы сняты",
                    }
                ):
                    direct_response = (
                        "Все сообщения откреплены"
                    )

                if direct_response:
                    await message.answer(
                        direct_response
                    )

                elif (
                    style != "custom"
                    and config.get(
                        "_response_overridden"
                    )
                    and response
                ):
                    await _send_compact_command_result(
                        message,
                        command=config,
                        target_id=target_id,
                        reason=special_result,
                        chat_title=chat.title,
                    )

                else:
                    # ANIGUARD_SPECIAL_CASE_TITLES
                    special_response = render_action_response(style=style, action=action if action in ACTION_TITLES else 'case', custom_templates=custom_style_templates, actor_id=message.from_user.id, actor_name=message.from_user.first_name, target_id=target_id or message.from_user.id, target_name='User' if target_id else message.from_user.first_name, reason=special_result, chat_title=chat.title, chat_id=chat.id, status='Выполнено', **_builtin_command_response_context(config, matched_key, name))

                    special_title = {
                        "pin_reply": "Сообщение закреплено",
                        "unpin_reply": "Сообщение откреплено",
                        "unpin_all": "Все сообщения откреплены",
                        "unpinall": "Все сообщения откреплены",
                    }.get(
                        str(config.get("special") or "").strip()
                    )

                    if special_title:
                        special_response = special_response.replace(
                            "Открытие дела выполнено",
                            special_title,
                            1,
                        )

                    await message.answer(special_response)
            return True

        if action == "purge":
            amount_match = re.match(r"^(\d{1,3})", remainder)
            amount = int(config.get("fixed_amount") or (amount_match.group(1) if amount_match else 10))
            deleted = await _delete_recent_messages(chat.id, message.message_id, amount)
            await perform_action(session, bot, chat_id=chat.id, actor_id=message.from_user.id, action="purge", amount=max(1, min(amount, 100)), reason=f"Удалено: {deleted}", premium_override=True)
            await session.commit()
            await message.answer(render_action_response(
                style=style, action="purge", custom_templates=custom_style_templates,
                actor_id=message.from_user.id, actor_name=message.from_user.first_name,
                target_id=message.from_user.id, target_name=message.from_user.first_name,
                reason=name, chat_title=chat.title, chat_id=chat.id, deleted_count=deleted,
                **_builtin_command_response_context(config, matched_key, name),
            ))
            return True

        if action in {"slow", "lock", "unlock"}:
            amount = int(config.get("fixed_amount") or 15) if action == "slow" else None
            await perform_action(session, bot, chat_id=chat.id, actor_id=message.from_user.id, action=action, amount=amount, reason=name, premium_override=premium)
            await session.commit()
            await message.answer(render_action_response(
                style=style, action=action, custom_templates=custom_style_templates,
                actor_id=message.from_user.id, actor_name=message.from_user.first_name,
                target_id=message.from_user.id, target_name=message.from_user.first_name,
                reason=name, chat_title=chat.title, chat_id=chat.id,
                slow_seconds=amount or 0,
                **_builtin_command_response_context(config, matched_key, name),
            ))
            return True

        if target_id is not None and action not in {"unwarn", "unmute", "unban", "unquarantine", "unrestrict_media", "unrestrict_links", "unrestrict_commands"}:
            await ensure_target_can_be_moderated(chat.id, target_id)
        duration = config.get("fixed_duration_seconds")
        if duration is None and parsed:
            duration = parsed.duration_seconds
        reason = (parsed.reason if parsed else "") or str(settings_data.get("default_reason") or "Причина не указана")
        result = await perform_action(
            session,
            bot,
            chat_id=chat.id,
            actor_id=message.from_user.id,
            action=action,
            target_id=target_id,
            duration_seconds=duration,
            reason=reason,
            premium_override=premium,
            source_message_id=message.reply_to_message.message_id if message.reply_to_message else message.message_id,
            evidence=_message_evidence(message),
        )
        target_user = await session.get(User, target_id) if target_id else None
        target_member = await session.scalar(select(Membership).where(Membership.chat_id == chat.id, Membership.user_id == target_id)) if target_id else None
        await session.commit()
        if style != "custom" and config.get("_response_overridden") and response:
            await _send_compact_command_result(
                message,
                command={**config, "response": response},
                target_id=target_id,
                duration_seconds=result.get("duration_seconds"),
                reason=reason,
                chat_title=chat.title,
            )
        else:
            await message.answer(moderation_response(
                actor_id=message.from_user.id,
                target_id=target_id or message.from_user.id,
                action=action,
                duration_seconds=result.get("duration_seconds"),
                reason=reason,
                style=style,
                custom_templates=custom_style_templates,
                actor_name=message.from_user.first_name,
                actor_username=message.from_user.username,
                target_name=target_user.first_name if target_user else "User",
                target_username=target_user.username if target_user else None,
                chat_title=chat.title,
                chat_id=chat.id,
                warnings=int(result.get("warnings", target_member.warnings if target_member else 0) or 0),
                warning_limit=int(settings_data.get("warn_threshold", 3)),
                case_id=result.get("case_id"),
                command_name=name,
                **_builtin_command_response_context(config, matched_key, name),
            ))
        return True


async def try_custom_command(message: Message, chat: Chat, membership: Membership) -> bool:
    text = (message.text or "").strip()
    if not text or text.startswith("/"):
        return False
    async with SessionFactory() as session:
        commands = (
            await session.scalars(
                select(CustomCommand).where(
                    CustomCommand.chat_id == chat.id,
                    CustomCommand.enabled.is_(True),
                )
            )
        ).all()
        matched: tuple[CustomCommand, str] | None = None
        lowered = text.casefold()
        for command in sorted(commands, key=lambda item: len(item.trigger), reverse=True):
            trigger = command.trigger.casefold().strip()
            if lowered == trigger:
                matched = (command, "")
                break
            if lowered.startswith(trigger + " ") or lowered.startswith(trigger + "\n"):
                matched = (command, text[len(command.trigger):].strip())
                break
        if not matched:
            return False

        command, remainder = matched
        restrictions = await active_restrictions(session, chat_id=chat.id, user_id=membership.user_id)
        if "commands" in restrictions:
            await message.reply("Для вас временно заблокировано использование команд.")
            return True
        premium = await has_premium_access(session, chat_id=chat.id, user_id=command.creator_id)
        if not premium:
            await message.reply("Эта кастомная команда заморожена до продления AniGuard Premium.")
            return True
        role = await member_role(chat.id, membership.user_id)
        if not role_allows(command.required_role, role):
            await message.reply("У вас недостаточно прав для этой команды.")
            return True
        if command.target_mode == "reply" and not message.reply_to_message:
            await message.reply("Эту команду нужно отправить ответом на сообщение пользователя.")
            return True

        now_mono = time.monotonic()
        cooldown_key = (chat.id, membership.user_id, command.id)
        remaining = command.cooldown_seconds - (now_mono - _custom_cooldowns.get(cooldown_key, 0))
        if remaining > 0:
            await message.reply(f"Команда будет доступна через {int(remaining) + 1} сек.")
            return True

        settings_data = await get_merged_settings(session, chat.id)
        style, custom_style_templates = await _chat_style(session, settings_data)
        default_duration = command.duration_seconds
        if default_duration is None:
            default_duration = int(settings_data.get(DEFAULT_DURATION_KEYS.get(command.action_type, "default_mute_seconds"), 604800))
        parsed = parse_moderation_command(
            remainder,
            default_duration_seconds=int(default_duration),
            forced_action=command.action_type,
            forced_trigger=command.trigger,
        )
        if parsed is None:
            await message.reply("Не удалось разобрать параметры команды.")
            return True
        target_id = await resolve_target(message, parsed.target_token)
        if target_id is None:
            await message.reply("Ответьте на сообщение пользователя или укажите его @username.")
            return True
        if target_id == message.from_user.id:
            await message.reply("Нельзя применить команду к самому себе.")
            return True
        await ensure_target_can_be_moderated(chat.id, target_id)
        reason = parsed.reason or str(settings_data.get("default_reason") or "Причина не указана")
        if command.require_reason and not parsed.reason:
            await message.reply("Для этой команды обязательно нужно указать причину.")
            return True

        result = await perform_action(
            session,
            bot,
            chat_id=chat.id,
            actor_id=message.from_user.id,
            action=command.action_type,
            target_id=target_id,
            duration_seconds=parsed.duration_seconds,
            reason=reason,
            premium_override=True,
        )
        _custom_cooldowns[cooldown_key] = now_mono
        await session.commit()
        if command.delete_trigger:
            try:
                await message.delete()
            except Exception:
                pass
        if style == "custom":
            response_text = render_action_response(
                style=style,
                action=command.action_type,
                custom_templates=custom_style_templates,
                actor_id=message.from_user.id,
                actor_name=message.from_user.first_name,
                target_id=target_id,
                target_name="User",
                duration_seconds=result.get("duration_seconds"),
                reason=reason,
                chat_title=chat.title,
                chat_id=chat.id,
                case_id=result.get("case_id"),
                command=command.name,
                command_key=f"custom_{command.id}",
                command_description=f"Пользовательская команда модерации: {command.action_type}",
            )
        else:
            response_text = render_custom_template(
                command.response_template,
                actor_id=message.from_user.id,
                target_id=target_id,
                command_name=command.name,
                duration_seconds=result.get("duration_seconds"),
                reason=reason,
                chat_title=chat.title,
                case_id=result.get("case_id"),
            )
        await bot.send_message(chat.id, response_text)
        return True



async def try_builtin_game_action(message: Message, chat: Chat, membership: Membership) -> bool:
    """Run one of the built-in Naruto game actions.

    Slash is optional, underscores/spaces are interchangeable. For names shared
    with moderation commands, moderators receive moderation by default and can
    force the game version with `игра <команда>` or `/игра_<команда>`.
    """
    text = (message.text or "").strip()
    if not text or not message.from_user:
        return False
    matched = match_game_action(text)
    if matched is None:
        return False
    key, command, remainder, forced = matched

    role = await member_role(chat.id, membership.user_id)
    if not forced and is_moderation_collision(str(command.get("trigger") or "")) and is_staff_role(role):
        return False

    async with SessionFactory() as session:
        settings_data = await get_merged_settings(session, chat.id)
        style, custom_style_templates = await _chat_style(session, settings_data)
        restrictions = await active_restrictions(session, chat_id=chat.id, user_id=membership.user_id)
        if "commands" in restrictions:
            await message.reply("Команды временно недоступны.")
            return True
        if bool(command.get("premium")) and not await has_premium_access(session, chat_id=chat.id, user_id=membership.user_id):
            await message.reply("Для этой игровой техники нужен AniGuard Premium.")
            return True

        target_id, target_name = await resolve_rp_target(message, remainder)
        if bool(command.get("target_required")) and target_id is None and target_name == "себя":
            await message.reply("Ответьте на сообщение пользователя или укажите @username.")
            return True

        now_mono = time.monotonic()
        cooldown_key = (chat.id, membership.user_id, key)
        cooldown = int(command.get("cooldown_seconds") or 0)
        remaining = cooldown - (now_mono - _builtin_game_action_cooldowns.get(cooldown_key, 0))
        if remaining > 0:
            await message.reply(f"Техника будет доступна через {int(remaining) + 1} сек.")
            return True

        actor = profile_link(message.from_user.id, message.from_user.first_name)
        target = profile_link(target_id, target_name) if target_id else html.escape(target_name)
        override = _pack_template(custom_style_templates, "game", str(command.get("trigger") or key), key)
        if override:
            response = render_template(override, build_context(
                actor_id=message.from_user.id, actor_name=message.from_user.first_name,
                target_id=target_id, target_name=target_name, chat_title=chat.title,
                command=str(command.get("trigger") or key), text=remainder,
                xp=int(command.get("reward_xp") or 0), coins=int(command.get("reward_coins") or 0),
            ), custom=True)
        else:
            response = html.escape(str(command.get("response") or ""))
            response = response.replace("{actor}", actor).replace("{user}", actor).replace("{target}", target)

        db_member = await session.scalar(
            select(Membership).where(Membership.chat_id == chat.id, Membership.user_id == membership.user_id)
        )
        if db_member:
            db_member.xp += int(command.get("reward_xp") or 0)
            db_member.coins += int(command.get("reward_coins") or 0)
        if target_id and int(command.get("number") or 0) == 96:
            target_member = await session.scalar(
                select(Membership).where(Membership.chat_id == chat.id, Membership.user_id == target_id)
            )
            if target_member:
                target_member.xp += 100

        _builtin_game_action_cooldowns[cooldown_key] = now_mono
        await session.commit()
        await message.answer(response)
        return True


async def try_game_command(message: Message, chat: Chat, membership: Membership) -> bool:
    text = (message.text or "").strip()
    if not text or text.startswith("/"):
        return False
    async with SessionFactory() as session:
        settings_data = await get_merged_settings(session, chat.id)
        style, custom_style_templates = await _chat_style(session, settings_data)
        commands = (
            await session.scalars(
                select(GameCommand).where(GameCommand.chat_id == chat.id, GameCommand.enabled.is_(True))
            )
        ).all()
        matched: tuple[GameCommand, str] | None = None
        lowered = text.casefold()
        for command in sorted(commands, key=lambda item: len(item.trigger), reverse=True):
            trigger = command.trigger.casefold().strip()
            if lowered == trigger:
                matched = (command, "")
                break
            if lowered.startswith(trigger + " ") or lowered.startswith(trigger + "\n"):
                matched = (command, text[len(command.trigger):].strip())
                break
        if not matched:
            return False
        command, remainder = matched
        restrictions = await active_restrictions(session, chat_id=chat.id, user_id=membership.user_id)
        if "commands" in restrictions:
            await message.reply("Для вас временно заблокировано использование команд.")
            return True
        role = await member_role(chat.id, membership.user_id)
        allowed = role_allows(command.access, role)
        if command.access == "verified" and role == "member":
            db_access_member = await session.scalar(
                select(Membership).where(
                    Membership.chat_id == chat.id,
                    Membership.user_id == membership.user_id,
                )
            )
            if db_access_member:
                joined_at = as_utc(db_access_member.joined_at) or utcnow()
                allowed = (
                    utcnow() - joined_at >= timedelta(hours=24)
                    and db_access_member.warnings == 0
                )
        if not allowed:
            await message.reply("У вас недостаточно прав для этой игровой команды.")
            return True

        now_mono = time.monotonic()
        cooldown_key = (chat.id, membership.user_id, command.id)
        remaining = command.cooldown_seconds - (now_mono - _game_cooldowns.get(cooldown_key, 0))
        if remaining > 0:
            await message.reply(f"Команда будет доступна через {int(remaining) + 1} сек.")
            return True

        db_member = await session.scalar(
            select(Membership).where(Membership.chat_id == chat.id, Membership.user_id == membership.user_id)
        )
        if db_member:
            db_member.xp += command.reward_xp
            db_member.coins += command.reward_coins
        variants = [command.response_template, *(command.response_variants or [])]
        template = random.choice([item for item in variants if item.strip()])
        target_id, target_name = await resolve_rp_target(message, remainder)
        override = _pack_template(custom_style_templates, "game", command.trigger, command.name)
        if override:
            response = render_template(override, build_context(
                actor_id=message.from_user.id, actor_name=message.from_user.first_name,
                target_id=target_id, target_name=target_name, chat_title=chat.title,
                command=command.trigger, text=remainder, xp=command.reward_xp, coins=command.reward_coins,
            ), custom=True)
        else:
            response = html.escape(template)
            replacements = {
                "{user}": profile_link(message.from_user.id, message.from_user.first_name),
                "{actor}": profile_link(message.from_user.id, message.from_user.first_name),
                "{target}": profile_link(target_id, target_name) if target_id else html.escape(target_name),
                "{text}": html.escape(remainder),
                "{chat}": html.escape(chat.title),
                "{xp}": str(command.reward_xp),
                "{coins}": str(command.reward_coins),
            }
            for placeholder, value in replacements.items():
                response = response.replace(placeholder, value)
        _game_cooldowns[cooldown_key] = now_mono
        await session.commit()
        await message.answer(response)
        return True


async def resolve_rp_target(message: Message, remainder: str) -> tuple[int | None, str]:
    if message.reply_to_message and message.reply_to_message.from_user:
        user = message.reply_to_message.from_user
        return user.id, user.first_name
    match = re.search(r"@([A-Za-z0-9_]{5,})", remainder)
    if match:
        username = match.group(1).lower()
        async with SessionFactory() as session:
            user = await session.scalar(select(User).where(func.lower(User.username) == username))
            if user:
                return user.id, user.first_name
        return None, f"@{username}"
    return None, "себя"


async def try_rp_command(message: Message, chat: Chat, membership: Membership) -> bool:
    text = (message.text or "").strip()
    if not text or text.startswith("/"):
        return False
    async with SessionFactory() as session:
        settings_data = await get_merged_settings(session, chat.id)
        style, custom_style_templates = await _chat_style(session, settings_data)
        if not settings_data["rp_enabled"]:
            return False
        commands = (
            await session.scalars(
                select(RPCommand).where(RPCommand.chat_id == chat.id, RPCommand.enabled.is_(True))
            )
        ).all()
        command_match: tuple[RPCommand, str] | None = None
        lowered = text.casefold()
        for command in sorted(commands, key=lambda item: len(item.name), reverse=True):
            names = [command.name, *(command.aliases or [])]
            for name in names:
                name_lower = name.casefold()
                if lowered == name_lower or lowered.startswith(name_lower + " "):
                    command_match = (command, text[len(name):].strip())
                    break
            if command_match:
                break
        if not command_match:
            return False

        command, remainder = command_match
        restrictions = await active_restrictions(session, chat_id=chat.id, user_id=membership.user_id)
        if "commands" in restrictions:
            await message.reply("Для вас временно заблокировано использование команд.")
            return True
        if command.is_premium and not await has_premium_access(session, chat_id=chat.id, user_id=command.created_by):
            await message.reply("Эта RP-команда заморожена до продления AniGuard Premium.")
            return True

        membership_db = await session.scalar(
            select(Membership).where(
                Membership.chat_id == chat.id,
                Membership.user_id == membership.user_id,
            )
        )
        role = membership_db.role if membership_db else "member"
        if command.access == "admins" and not is_admin_role(role):
            await message.reply("Эта RP-команда доступна только администраторам.")
            return True
        if command.access == "moderators" and not is_staff_role(role):
            await message.reply("Эта RP-команда доступна только модераторам.")
            return True
        if command.access == "verified" and membership_db:
            joined_at = as_utc(membership_db.joined_at) or utcnow()
            verified = is_staff_role(role) or (utcnow() - joined_at >= timedelta(hours=24) and membership_db.warnings == 0)
            if not verified:
                await message.reply("Эта RP-команда доступна только проверенным участникам.")
                return True

        now_mono = time.monotonic()
        cooldown_key = (chat.id, membership.user_id, command.id)
        remaining = command.cooldown_seconds - (now_mono - _rp_cooldowns.get(cooldown_key, 0))
        if remaining > 0:
            await message.reply(f"Команда будет доступна через {int(remaining) + 1} сек.")
            return True

        target_id, target_name = await resolve_rp_target(message, remainder)
        actor_name = message.from_user.first_name
        variants = [command.response_template, *(command.response_variants or [])]
        template = random.choice([variant for variant in variants if variant.strip()])
        override = _pack_template(custom_style_templates, "rp", command.name, *(command.aliases or []))
        if override:
            response = render_template(override, build_context(
                actor_id=message.from_user.id, actor_name=actor_name,
                target_id=target_id, target_name=target_name, chat_title=chat.title,
                command=command.name, text=remainder, xp=command.reward_xp, coins=command.reward_coins,
            ), custom=True)
        else:
            response = (
                template.replace("{actor}", html.escape(actor_name))
                .replace("{target}", html.escape(target_name))
                .replace("{text}", html.escape(remainder))
                .replace("{chat}", html.escape(chat.title))
            )
        _rp_cooldowns[cooldown_key] = now_mono
        if membership_db:
            membership_db.xp += command.reward_xp
            membership_db.coins += command.reward_coins
        await session.commit()
        await message.answer(response)
        return True


async def enforce_penalty_status_message(message: Message, chat: Chat, membership: Membership) -> bool:
    async with SessionFactory() as session:
        current = await get_membership(session, chat.id, membership.user_id)
        settings_data = await get_merged_settings(session, chat.id)
        penalty = normalize_penalty_status(current.penalty_status)
        await session.commit()
    if penalty == "none":
        return False

    async def remove() -> None:
        try:
            await message.delete()
        except Exception:
            pass

    if penalty == "severe_violator":
        await remove()
        await message.answer("⛔ Для пользователя со статусом «Злостный нарушитель» доступна только апелляция и просмотр информации.")
        return True

    text = (message.text or message.caption or "").strip()
    entities = list(message.entities or []) + list(message.caption_entities or [])
    mention_count = sum(1 for entity in entities if entity.type in {"mention", "text_mention"})
    has_link = bool(re.search(r"(?i)(?:https?://|www\.|t\.me/|telegram\.me/)", text))
    has_media = bool(message.photo or message.video or message.animation or message.document or message.audio or message.voice or message.video_note or message.sticker)
    looks_like_command = bool(
        text.startswith("/")
        or match_game_action(text)
        or match_builtin_command(text)
        or detect_action(text)
        or _normalize_phrase(text).startswith("игра ")
    )
    if has_media or has_link or message.poll or mention_count > 1 or looks_like_command:
        await remove()
        await message.answer("⚠️ Статус «Нарушитель»: ссылки, медиа, массовые упоминания и игровые команды временно недоступны.")
        return True
    slow_seconds = max(1, int(settings_data.get("violator_slow_mode_seconds", 30)))
    key = (chat.id, membership.user_id)
    now_value = time.monotonic()
    previous = _slow_buckets.get(key, 0.0)
    if now_value - previous < slow_seconds:
        await remove()
        await message.answer(f"⏳ Для статуса «Нарушитель» действует интервал {slow_seconds} секунд между сообщениями.")
        return True
    _slow_buckets[key] = now_value
    return False


@router.edited_message((F.chat.type == ChatType.GROUP) | (F.chat.type == ChatType.SUPERGROUP))
async def edited_group_message_pipeline(message: Message) -> None:
    if not message.from_user or message.from_user.is_bot:
        return
    chat, _, membership = await ensure_context(message)
    if membership is None:
        return
    async with SessionFactory() as session:
        settings_data = await get_merged_settings(session, chat.id)
        premium = await has_premium_access(session, chat_id=chat.id, user_id=message.from_user.id)
    if not premium or not settings_data.get("edited_message_filter_enabled", False):
        return
    try:
        await enforce_message_protection(message, chat, membership)
    except Exception as exc:
        await send_admin_error(message, exc)


@router.message((F.chat.type == ChatType.GROUP) | (F.chat.type == ChatType.SUPERGROUP))
async def group_message_pipeline(message: Message) -> None:
    if not message.from_user or message.from_user.is_bot:
        return
    chat, _, membership = await ensure_context(message)
    if membership is None:
        return
    async with SessionFactory() as access_session:
        try:
            await ensure_entity_available(
                access_session,
                user_id=message.from_user.id,
                chat_id=chat.id,
            )
            await access_session.commit()
        except PermissionError:
            return

    if await enforce_penalty_status_message(message, chat, membership):
        return

    try:
        if await try_builtin_game_action(message, chat, membership):
            return
        if await try_basic_moderation_command(message, chat, membership):
            return
        if await try_custom_command(message, chat, membership):
            return
        if await try_game_command(message, chat, membership):
            return
    except Exception as exc:
        await send_admin_error(message, exc)
        return

    blocked = await enforce_message_protection(message, chat, membership)
    if blocked:
        return

    async with SessionFactory() as session:
        settings_data = await get_merged_settings(session, chat.id)
        db_chat = await session.get(Chat, chat.id)
        if db_chat:
            settings_data["last_message_id"] = message.message_id
            db_chat.settings = settings_data
        db_membership = await session.scalar(
            select(Membership).where(
                Membership.chat_id == chat.id,
                Membership.user_id == membership.user_id,
            )
        )
        if db_membership:
            db_membership.message_count += 1
            db_membership.last_seen_at = utcnow()
            if settings_data["ranks_enabled"]:
                db_membership.xp += int(settings_data["xp_per_message"])
            if settings_data["economy_enabled"]:
                db_membership.coins += int(settings_data["coins_per_message"])
        await session.commit()

    await try_rp_command(message, chat, membership)


# ---------------------------------------------------------------------------
# Operations suite v20 commands
# ---------------------------------------------------------------------------

def _parse_case_number(raw: str | None) -> int:
    match = re.search(r"(?i)(?:AG[- ]?)?(\d{1,9})", raw or "")
    if not match:
        raise ValueError("Укажите номер дела, например: /caseinfo 1842")
    return int(match.group(1))


async def send_case_info(message: Message, raw: str | None) -> None:
    try:
        await ensure_group_moderator(message)
        case_id = _parse_case_number(raw)
        async with SessionFactory() as session:
            row = await session.get(ModerationCase, case_id)
            if row is None or row.chat_id != message.chat.id:
                raise ValueError("Дело не найдено")
            appeal_count = int(await session.scalar(select(func.count()).select_from(Appeal).where(Appeal.case_id == row.id)) or 0)
        await message.answer(
            f"📁 <b>Дело {html.escape(row.code)}</b>\n\n"
            f"Действие: <b>{html.escape(row.action)}</b>\n"
            f"Пользователь: <code>{row.target_id or 0}</code>\n"
            f"Модератор: <code>{row.actor_id or 0}</code>\n"
            f"Причина: {html.escape(row.reason)}\n"
            f"Статус: <b>{html.escape(row.status)}</b>\n"
            f"Доказательств: {row.evidence_count}\n"
            f"Апелляций: {appeal_count}\n"
            f"Создано: {_format_dt(row.created_at)}"
        )
    except Exception as exc:
        await send_admin_error(message, exc)


@router.message(Command("caseinfo"))
async def caseinfo_handler(message: Message, command: CommandObject) -> None:
    await send_case_info(message, command.args)


@router.message(F.text.regexp(r"(?i)^\s*/?дело(?:\s+.*)?$"))
async def caseinfo_alias_handler(message: Message) -> None:
    raw = re.sub(r"(?i)^\s*/?дело\s*", "", message.text or "", count=1)
    await send_case_info(message, raw)


@router.message(Command("cases"))
async def cases_handler(message: Message, command: CommandObject) -> None:
    try:
        await ensure_group_moderator(message)
        target_id = await resolve_target(message, (command.args or "").strip() or None)
        async with SessionFactory() as session:
            query = select(ModerationCase).where(ModerationCase.chat_id == message.chat.id)
            if target_id is not None:
                query = query.where(ModerationCase.target_id == target_id)
            rows = (await session.scalars(query.order_by(ModerationCase.id.desc()).limit(10))).all()
        if not rows:
            await message.answer("Открытых дел не найдено.")
            return
        lines = ["📁 <b>Последние дела</b>"]
        for row in rows:
            lines.append(f"• <code>{row.code}</code> · {html.escape(row.action)} · {html.escape(row.status)} · <code>{row.target_id or 0}</code>")
        await message.answer("\n".join(lines))
    except Exception as exc:
        await send_admin_error(message, exc)


@router.message(F.text.regexp(r"(?i)^\s*/?(?:дела|досье[_\s]+анбу)(?:\s+.*)?$"))
async def cases_alias_handler(message: Message) -> None:
    raw = re.sub(r"(?i)^\s*/?(?:дела|досье[_\s]+анбу)\s*", "", message.text or "", count=1)
    await cases_handler(message, CommandObject(prefix="/", command="cases", mention=None, args=raw))


@router.message(Command("closecase"))
async def closecase_handler(message: Message, command: CommandObject) -> None:
    try:
        await ensure_group_admin(message)
        case_id = _parse_case_number(command.args)
        async with SessionFactory() as session:
            row = await session.get(ModerationCase, case_id)
            if row is None or row.chat_id != message.chat.id:
                raise ValueError("Дело не найдено")
            row.status = "closed"
            row.closed_at = utcnow()
            row.closed_by = message.from_user.id
            await session.commit()
        await message.answer(f"Дело AG-{case_id:06d} закрыто.")
    except Exception as exc:
        await send_admin_error(message, exc)


@router.message(F.text.regexp(r"(?i)^\s*/?закрыть[_\s]+дело(?:\s+.*)?$"))
async def closecase_alias_handler(message: Message) -> None:
    raw = re.sub(r"(?i)^\s*/?закрыть[_\s]+дело\s*", "", message.text or "", count=1)
    await closecase_handler(message, CommandObject(prefix="/", command="closecase", mention=None, args=raw))


async def toggle_anti_raid(message: Message, raw: str | None) -> None:
    try:
        await ensure_group_admin(message)
        value = _normalize_phrase(raw or "вкл")
        enabled = value not in {"выкл", "off", "0", "нет"}
        async with SessionFactory() as session:
            await set_security_mode(session, chat_id=message.chat.id, actor_id=message.from_user.id, kind="anti_raid", enabled=enabled, reason="Команда администратора")
            await session.commit()
        await message.answer("🛡 Антирейд включён. CAPTCHA и карантин новичков активны." if enabled else "Антирейд отключён.")
    except Exception as exc:
        await send_admin_error(message, exc)


@router.message(Command("antiraid"))
async def antiraid_handler(message: Message, command: CommandObject) -> None:
    await toggle_anti_raid(message, command.args)


@router.message(F.text.regexp(r"(?i)^\s*/?(?:антирейд|барьер[_\s]+деревни)(?:\s+.*)?$"))
async def antiraid_alias_handler(message: Message) -> None:
    raw = re.sub(r"(?i)^\s*/?(?:антирейд|барьер[_\s]+деревни)\s*", "", message.text or "", count=1)
    await toggle_anti_raid(message, raw)


async def toggle_emergency(message: Message, raw: str | None) -> None:
    try:
        await ensure_group_admin(message)
        text = _normalize_phrase(raw or "вкл")
        enabled = text not in {"выкл", "off", "0", "снять"}
        duration = parse_duration_prefix(raw or "") if enabled else None
        seconds = duration.seconds if duration else None
        async with SessionFactory() as session:
            await set_security_mode(session, chat_id=message.chat.id, actor_id=message.from_user.id, kind="emergency", enabled=enabled, reason="Экстренный режим", duration_seconds=seconds)
            await session.commit()
        await bot.set_chat_permissions(message.chat.id, ChatPermissions(can_send_messages=False) if enabled else full_permissions())
        await message.answer("🚨 Экстренный режим включён. Писать может только администрация." if enabled else "Экстренный режим снят.")
    except Exception as exc:
        await send_admin_error(message, exc)


@router.message(Command("emergency"))
async def emergency_handler(message: Message, command: CommandObject) -> None:
    await toggle_emergency(message, command.args)


@router.message(F.text.regexp(r"(?i)^\s*/?(?:тревога|красная[_\s]+тревога[_\s]+конохи)(?:\s+.*)?$"))
async def emergency_alias_handler(message: Message) -> None:
    raw = re.sub(r"(?i)^\s*/?(?:тревога|красная[_\s]+тревога[_\s]+конохи)\s*", "", message.text or "", count=1)
    await toggle_emergency(message, raw)


@router.message(Command("shift"))
async def shift_handler(message: Message, command: CommandObject) -> None:
    try:
        await ensure_group_admin(message)
        target_id, payload = await _target_and_payload(message, command.args)
        if target_id is None:
            raise ValueError("Ответьте модератору или укажите @username")
        duration = parse_duration_prefix(payload or "8ч")
        seconds = duration.seconds if duration else 8 * 3600
        starts = utcnow()
        ends = starts + timedelta(seconds=seconds)
        async with SessionFactory() as session:
            row = await create_shift(session, chat_id=message.chat.id, user_id=target_id, actor_id=message.from_user.id, starts_at=starts, ends_at=ends)
            await session.commit()
        await message.answer(f"Смена #{row.id} назначена пользователю <code>{target_id}</code> до {_format_dt(ends)}.")
    except Exception as exc:
        await send_admin_error(message, exc)


@router.message(F.text.regexp(r"(?i)^\s*/?(?:смена|семь[_\s]+мечников)(?:\s+.*)?$"))
async def shift_alias_handler(message: Message) -> None:
    raw = re.sub(r"(?i)^\s*/?(?:смена|семь[_\s]+мечников)\s*", "", message.text or "", count=1)
    await shift_handler(message, CommandObject(prefix="/", command="shift", mention=None, args=raw))


@router.message(Command("weekly_report"))
async def weekly_report_handler(message: Message) -> None:
    try:
        await ensure_group_moderator(message)
        async with SessionFactory() as session:
            row = await generate_weekly_report(session, chat_id=message.chat.id, generated_by=message.from_user.id)
            await session.commit()
        p = row.payload
        await message.answer(
            "📊 <b>Отчёт за неделю</b>\n\n"
            f"Действий модерации: {p['moderation_actions']}\n"
            f"Новых участников: {p['new_members']}\n"
            f"Жалоб: {p['reports']}\n"
            f"Предупреждений: {p['warnings']}\n"
            f"Мутов: {p['mutes']}\n"
            f"Банов: {p['bans']}\n"
            f"Апелляций: {p['appeals']}\n"
            f"Принято апелляций: {p['accepted_appeals']}\n"
            f"Лучший модератор: <code>{p['top_moderator_id'] or 0}</code>"
        )
    except Exception as exc:
        await send_admin_error(message, exc)


@router.message(F.text.regexp(r"(?i)^\s*/?(?:отчет|отчёт)[_\s]+хокаге\s*$"))
async def weekly_report_alias_handler(message: Message) -> None:
    await weekly_report_handler(message)


@router.message(Command("backup"))
async def backup_handler(message: Message) -> None:
    try:
        await ensure_group_admin(message)
        async with SessionFactory() as session:
            row = await create_backup_snapshot(session, chat_id=message.chat.id, created_by=message.from_user.id)
            await session.commit()
        await message.answer(f"✅ Резервная копия #{row.id} создана. Контрольная сумма: <code>{row.checksum[:16]}</code>")
    except Exception as exc:
        await send_admin_error(message, exc)


@router.message(Command("testmode"))
async def testmode_handler(message: Message, command: CommandObject) -> None:
    try:
        await ensure_group_admin(message)
        enabled = _normalize_phrase(command.args or "вкл") not in {"выкл", "off", "0"}
        async with SessionFactory() as session:
            await update_chat_settings(session, message.chat.id, {"test_mode_enabled": enabled})
            await session.commit()
        await message.answer("🧪 Тестовый режим включён: наказания только рассчитываются." if enabled else "Тестовый режим отключён.")
    except Exception as exc:
        await send_admin_error(message, exc)


@router.message(F.text.regexp(r"(?i)^\s*/?тестовый[_\s]+режим(?:\s+.*)?$"))
async def testmode_alias_handler(message: Message) -> None:
    raw = re.sub(r"(?i)^\s*/?тестовый[_\s]+режим\s*", "", message.text or "", count=1)
    await testmode_handler(message, CommandObject(prefix="/", command="testmode", mention=None, args=raw))


@router.message(Command("chatinfo"))
async def chatinfo_handler(message: Message) -> None:
    try:
        await ensure_context(message)
        async with SessionFactory() as session:
            chat = await session.get(Chat, message.chat.id)
            settings_data = await get_merged_settings(session, message.chat.id)
            members = int(await session.scalar(select(func.count()).select_from(Membership).where(Membership.chat_id == message.chat.id)) or 0)
            admins = int(await session.scalar(select(func.count()).select_from(Membership).where(Membership.chat_id == message.chat.id, Membership.role.in_(["creator", "senior_admin", "admin", "junior_admin"]))) or 0)
            active = int(await session.scalar(select(func.count()).select_from(Membership).where(Membership.chat_id == message.chat.id, Membership.last_seen_at >= utcnow() - timedelta(days=7))) or 0)
        await message.answer(
            "💬 <b>Информация о беседе</b>\n\n"
            f"Название: {html.escape(chat.title if chat else message.chat.title or 'Беседа')}\n"
            f"ID: <code>{message.chat.id}</code>\n"
            f"Участников в базе: {members}\n"
            f"Администрации: {admins}\n"
            f"Активных за неделю: {active}\n"
            f"Антирейд: {'включён' if settings_data.get('anti_raid_enabled') else 'выключен'}\n"
            f"Автомодерация: {'включена' if settings_data.get('anti_flood_enabled') else 'выключена'}\n"
            f"Тестовый режим: {'включён' if settings_data.get('test_mode_enabled') else 'выключен'}"
        )
    except Exception as exc:
        await send_admin_error(message, exc)


@router.message(F.text.regexp(r"(?i)^\s*/?карта[_\s]+деревни\s*$"))
async def chatinfo_alias_handler(message: Message) -> None:
    await chatinfo_handler(message)


@router.message(Command("permissions"))
async def permissions_handler(message: Message, command: CommandObject) -> None:
    try:
        await ensure_group_moderator(message)
        target_id = await resolve_target(message, (command.args or "").strip() or None) or message.from_user.id
        async with SessionFactory() as session:
            membership = await session.scalar(select(Membership).where(Membership.chat_id == message.chat.id, Membership.user_id == target_id))
            rows = (await session.scalars(select(PermissionOverride).where(PermissionOverride.chat_id == message.chat.id, PermissionOverride.user_id == target_id))).all()
        lines = [f"🔐 <b>Полномочия пользователя</b>", f"Пользователь: <code>{target_id}</code>", f"Роль: {html.escape(role_name(membership.role if membership else 'member'))}"]
        if rows:
            lines.append("")
            lines.extend(f"• {html.escape(r.permission)}: {'разрешено' if r.allowed else 'запрещено'}" + (f" · лимит {r.limit_value} сек." if r.limit_value is not None else "") for r in rows)
        else:
            lines.append("Индивидуальные изменения отсутствуют — действуют права роли.")
        await message.answer("\n".join(lines))
    except Exception as exc:
        await send_admin_error(message, exc)


async def configure_bot() -> None:
    commands = [
        BotCommand(command="panel", description="Открыть Mini App"),
        BotCommand(command="help", description="Список команд"),
        BotCommand(command="premium", description="Купить Premium"),
        BotCommand(command="promo", description="Активировать промокод"),
        BotCommand(command="warn", description="Предупредить пользователя"),
        BotCommand(command="unwarn", description="Снять предупреждение"),
        BotCommand(command="mute", description="Выдать мут"),
        BotCommand(command="unmute", description="Снять мут"),
        BotCommand(command="ban", description="Заблокировать пользователя"),
        BotCommand(command="unban", description="Снять бан"),
        BotCommand(command="kick", description="Удалить пользователя"),
        BotCommand(command="quarantine", description="Поместить в карантин"),
        BotCommand(command="unquarantine", description="Снять карантин"),
        BotCommand(command="purge", description="Очистить сообщения"),
        BotCommand(command="lock", description="Закрыть чат"),
        BotCommand(command="unlock", description="Открыть чат"),
        BotCommand(command="report", description="Пожаловаться на сообщение"),
        BotCommand(command="appeal", description="Подать апелляцию"),
        BotCommand(command="support", description="Поддержка AniGuard"),
        BotCommand(command="info", description="Информация об участнике"),
        BotCommand(command="admin", description="Состав администрации"),
        BotCommand(command="role", description="Назначить роль"),
        BotCommand(command="penalty", description="Штрафной статус"),
        BotCommand(command="role_history", description="История ролей"),
        BotCommand(command="profile", description="Профиль участника"),
        BotCommand(command="top", description="Топ участников"),
        BotCommand(command="rules", description="Правила модерации"),
        BotCommand(command="rplist", description="RP-команды"),
        BotCommand(command="logs", description="Журнал модерации"),
        BotCommand(command="reports", description="Открытые жалобы"),
        BotCommand(command="antiflood", description="Антифлуд on/off"),
        BotCommand(command="anime", description="Аниме-режим on/off"),
        BotCommand(command="caseinfo", description="Открыть дело модерации"),
        BotCommand(command="cases", description="Список дел"),
        BotCommand(command="closecase", description="Закрыть дело"),
        BotCommand(command="antiraid", description="Антирейд on/off"),
        BotCommand(command="emergency", description="Экстренный режим"),
        BotCommand(command="shift", description="Назначить смену"),
        BotCommand(command="weekly_report", description="Отчёт за неделю"),
        BotCommand(command="backup", description="Резервная копия беседы"),
        BotCommand(command="testmode", description="Тестовый режим"),
        BotCommand(command="chatinfo", description="Информация о беседе"),
        BotCommand(command="permissions", description="Индивидуальные права"),
    ]
    await bot.set_my_commands(commands)
    admin_commands = [
        BotCommand(command="admin", description="Панель владельца"),
        *[command for command in commands if command.command != "admin"],
    ]
    for admin_id in settings.admin_ids:
        try:
            await bot.set_my_commands(admin_commands, scope=BotCommandScopeChat(chat_id=admin_id))
        except Exception:
            pass
    if settings.webapp_url.startswith("https://"):
        await bot.set_chat_menu_button(
            menu_button=MenuButtonWebApp(
                text="AniGuard",
                web_app=WebAppInfo(url=settings.webapp_url),
            )
        )


async def start_polling() -> None:
    global _captcha_worker_task, _operations_worker_task
    await configure_bot()
    await synchronize_registered_chats()
    _captcha_worker_task = asyncio.create_task(captcha_expiry_worker(), name="aniguard-captcha-expiry")
    _operations_worker_task = asyncio.create_task(operations_maintenance_worker(), name="aniguard-operations-maintenance")
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        for task_name in ("_captcha_worker_task", "_operations_worker_task"):
            task = globals().get(task_name)
            if task:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                globals()[task_name] = None


async def stop_bot() -> None:
    global _captcha_worker_task, _operations_worker_task
    for task_name in ("_captcha_worker_task", "_operations_worker_task"):
        task = globals().get(task_name)
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            globals()[task_name] = None
    await dp.stop_polling()
    await bot.session.close()
