from __future__ import annotations

import asyncio
import html
import random
import re
import time
import unicodedata
from collections import defaultdict, deque
from datetime import timedelta
from typing import Any, Awaitable, Callable
from urllib.parse import urlparse

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ChatMemberStatus, ChatType, ParseMode
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram import BaseMiddleware
from aiogram.types import (
    BotCommand,
    CallbackQuery,
    ChatPermissions,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LabeledPrice,
    MenuButtonWebApp,
    Message,
    PreCheckoutQuery,
    WebAppInfo,
)
from sqlalchemy import func, select

from app.config import get_settings
from app.db import SessionFactory
from app.models import (
    CaptchaChallenge,
    Chat,
    CustomCommand,
    GameCommand,
    Membership,
    ModerationLog,
    ModerationRule,
    Report,
    RPCommand,
    User,
)
from app.durations import clean_reason, format_duration_ru, parse_duration_prefix
from app.moderation_parser import TIMED_ACTIONS, detect_action, parse_moderation_command
from app.pricing import PREMIUM_PLANS, get_plan
from app.services import (
    active_restrictions,
    add_log,
    as_utc,
    create_captcha,
    create_report,
    ensure_entity_available,
    ensure_membership,
    full_permissions,
    get_block_record,
    get_chat_or_raise,
    get_merged_settings,
    grant_premium,
    has_premium_access,
    is_premium,
    muted_permissions,
    parse_payment_payload,
    perform_action,
    quarantine_permissions,
    require_chat_admin,
    upsert_chat,
    upsert_user,
    utcnow,
)


settings = get_settings()
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
        if from_user and from_user.id in settings.admin_ids:
            return await handler(event, data)
        chat_obj = getattr(event, "chat", None)
        if chat_obj is None and getattr(event, "message", None):
            chat_obj = event.message.chat
        async with SessionFactory() as session:
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
                if from_user and message_obj and (message_obj.text or "").startswith("/"):
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
router.callback_query.outer_middleware(AccessMiddleware())

_flood_buckets: dict[tuple[int, int], deque[float]] = defaultdict(deque)
_slow_buckets: dict[tuple[int, int], float] = {}
_rp_cooldowns: dict[tuple[int, int, int], float] = {}
_custom_cooldowns: dict[tuple[int, int, int], float] = {}
_game_cooldowns: dict[tuple[int, int, int], float] = {}
_duplicate_buckets: dict[tuple[int, int], deque[tuple[float, str]]] = defaultdict(deque)
_join_buckets: dict[int, deque[float]] = defaultdict(deque)
_threat_buckets: dict[int, deque[float]] = defaultdict(deque)


def split_args(command: CommandObject | None) -> list[str]:
    return (command.args or "").split() if command else []


def panel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Открыть AniGuard", web_app=WebAppInfo(url=settings.webapp_url))],
            [InlineKeyboardButton(text="Premium", callback_data="premium:choose")],
        ]
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
                    "owner"
                    if member.status == ChatMemberStatus.CREATOR
                    else "admin"
                    if member.status == ChatMemberStatus.ADMINISTRATOR
                    else "member"
                )
            except Exception:
                role = "member"
            membership = await ensure_membership(session, chat.id, user.id, role)
        else:
            chat = Chat(id=message.chat.id, title="Private")
        await session.commit()
        return chat, user, membership


async def ensure_group_admin(message: Message) -> None:
    if message.chat.type not in {ChatType.GROUP, ChatType.SUPERGROUP}:
        raise ValueError("Эта команда работает только в группе или супергруппе.")
    await require_chat_admin(bot, message.chat.id, message.from_user.id)


async def ensure_group_moderator(message: Message) -> None:
    if message.chat.type not in {ChatType.GROUP, ChatType.SUPERGROUP}:
        raise ValueError("Эта команда работает только в группе или супергруппе.")
    try:
        member = await bot.get_chat_member(message.chat.id, message.from_user.id)
        if member.status in {ChatMemberStatus.CREATOR, ChatMemberStatus.ADMINISTRATOR}:
            return
    except Exception:
        pass
    async with SessionFactory() as session:
        membership = await session.scalar(
            select(Membership).where(
                Membership.chat_id == message.chat.id,
                Membership.user_id == message.from_user.id,
            )
        )
        if membership and membership.role == "moderator":
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
) -> str:
    actor = profile_link(actor_id, "Admin")
    target = profile_link(target_id, "User")
    title = html.escape(ACTION_TITLES.get(action, action))
    lines = [f"{actor} применил {title} к {target}."]
    if show_duration and action in TIMED_ACTIONS:
        lines.extend(["", f"<b>Срок:</b> {html.escape(format_duration_ru(duration_seconds))}"])
    if show_reason:
        lines.append(f"<b>Причина:</b> {html.escape(reason or 'Причина не указана')}")
    return "\n".join(lines)


def render_custom_template(
    template: str,
    *,
    actor_id: int,
    target_id: int,
    command_name: str,
    duration_seconds: int | None,
    reason: str,
    chat_title: str,
) -> str:
    escaped = html.escape(template)
    replacements = {
        "{admin}": profile_link(actor_id, "Admin"),
        "{user}": profile_link(target_id, "User"),
        "{command}": html.escape(command_name),
        "{duration}": html.escape(format_duration_ru(duration_seconds)),
        "{reason}": html.escape(reason or "Причина не указана"),
        "{chat}": html.escape(chat_title),
    }
    for placeholder, value in replacements.items():
        escaped = escaped.replace(placeholder, value)
    return escaped


async def ensure_target_can_be_moderated(chat_id: int, target_id: int) -> None:
    member = await bot.get_chat_member(chat_id, target_id)
    if member.status in {ChatMemberStatus.CREATOR, ChatMemberStatus.ADMINISTRATOR}:
        raise PermissionError("Нельзя применить наказание к владельцу или администратору группы")


async def member_role(chat_id: int, user_id: int) -> str:
    try:
        member = await bot.get_chat_member(chat_id, user_id)
        if member.status == ChatMemberStatus.CREATOR:
            return "owner"
        if member.status == ChatMemberStatus.ADMINISTRATOR:
            return "admin"
    except Exception:
        pass
    async with SessionFactory() as session:
        row = await session.scalar(
            select(Membership).where(Membership.chat_id == chat_id, Membership.user_id == user_id)
        )
        if row and row.role == "moderator":
            return "moderator"
    return "member"


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
        "<b>Команды AniGuard</b>\n\n"
        "<b>Модерация обычным текстом:</b>\n"
        "<code>Мут @username 30 секунд флуд</code>\n"
        "<code>Бан @username 7 дней реклама</code>\n"
        "<code>Бан @username\nфлуд</code>\n"
        "<code>Мут</code> — ответом на сообщение; без срока используется значение из настроек (изначально 7 дней).\n\n"
        "Поддерживаются секунды, минуты, часы, дни, недели, месяцы и «навсегда». "
        "Тем же способом работают карантин, запрет медиа, ссылок и команд.\n\n"
        "/panel — Mini App и настройки\n"
        "/premium — Premium\n"
        "/report — жалоба ответом\n"
        "/profile, /top — профиль и рейтинг\n"
        "/rules, /rplist, /logs, /reports — управление и журнал"
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
        chat_id, user_id, plan_code = parse_payment_payload(query.invoice_payload)
        plan = get_plan(plan_code)
        if query.from_user.id != user_id or query.currency != "XTR" or query.total_amount != plan.stars:
            raise ValueError("Параметры платежа не совпадают")
        await require_chat_admin(bot, chat_id, user_id)
        await query.answer(ok=True)
    except Exception as exc:
        await query.answer(ok=False, error_message=str(exc)[:200])


@router.message(F.successful_payment)
async def successful_payment_handler(message: Message) -> None:
    payment = message.successful_payment
    try:
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
    except Exception as exc:
        await message.answer(f"Платёж получен, но активация не завершена: {html.escape(str(exc))}")


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
            )
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
        await message.answer("Медленный режим отключён." if delay == 0 else f"Задержка установлена: {delay} сек.")
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
        await message.answer("Чат закрыт для участников." if action == "lock" else "Чат снова открыт.")
    except Exception as exc:
        await send_admin_error(message, exc)


@router.message(Command("lock"))
async def lock_handler(message: Message) -> None:
    await chat_state_action(message, "lock")


@router.message(Command("unlock"))
async def unlock_handler(message: Message) -> None:
    await chat_state_action(message, "unlock")


@router.message(Command("report"))
async def report_handler(message: Message, command: CommandObject) -> None:
    if not message.reply_to_message or not message.reply_to_message.from_user:
        await message.answer("Ответьте командой /report на сообщение нарушителя.")
        return
    await ensure_context(message)
    reason = command.args or "Причина не указана"
    async with SessionFactory() as session:
        report = await create_report(
            session,
            chat_id=message.chat.id,
            reporter_id=message.from_user.id,
            target_id=message.reply_to_message.from_user.id,
            message_id=message.reply_to_message.message_id,
            reason=reason,
        )
        await session.commit()
    await message.answer(f"Жалоба AG-{report.id} создана и передана модераторам.")


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
        await ensure_group_admin(message)
        async with SessionFactory() as session:
            rows = (await session.scalars(select(ModerationRule).where(ModerationRule.chat_id == message.chat.id).order_by(ModerationRule.id))).all()
        if not rows:
            await message.answer("Правила не созданы.")
            return
        lines = ["<b>Правила модерации</b>"]
        for row in rows:
            state = "вкл" if row.enabled else "выкл"
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
        raid_window = int(chat_settings.get("raid_window_seconds", 30))
        while join_bucket and now_mono - join_bucket[0] > raid_window:
            join_bucket.popleft()
        for _ in message.new_chat_members:
            join_bucket.append(now_mono)
        raid_detected = len(join_bucket) >= int(chat_settings.get("raid_join_limit", 8))
        if (
            raid_detected
            and premium_access
            and chat_settings.get("premium_raid_lockdown_enabled", False)
            and not chat_settings.get("chat_locked", False)
        ):
            try:
                await bot.set_chat_permissions(chat.id, ChatPermissions(can_send_messages=False))
                chat_settings["chat_locked"] = True
                chat.settings = chat_settings
                await add_log(
                    session,
                    chat.id,
                    "auto_raid_lockdown",
                    actor_id=message.from_user.id if message.from_user else None,
                    reason=f"За {raid_window} сек. вступило {len(join_bucket)} участников",
                )
                await message.answer(
                    "🛡 <b>AniGuard включил экстренную защиту.</b> "
                    "Обнаружено массовое вступление участников, отправка сообщений временно закрыта."
                )
            except Exception:
                pass
        for member in message.new_chat_members:
            if member.is_bot:
                continue
            await upsert_user(session, member)
            await ensure_membership(session, chat.id, member.id)
            if not chat_settings["captcha_enabled"]:
                continue
            challenge = await create_captcha(
                session,
                chat.id,
                member.id,
                int(chat_settings["captcha_timeout_seconds"]),
            )
            until = utcnow() + timedelta(seconds=int(chat_settings["captcha_timeout_seconds"]))
            try:
                await bot.restrict_chat_member(chat.id, member.id, muted_permissions(), until_date=until)
                sent = await message.answer(
                    f"{html.escape(member.first_name)}, подтвердите, что вы не бот.",
                    reply_markup=InlineKeyboardMarkup(
                        inline_keyboard=[[InlineKeyboardButton(
                            text="Пройти проверку",
                            callback_data=f"captcha:{chat.id}:{member.id}:{challenge.token}",
                        )]]
                    ),
                )
                challenge.message_id = sent.message_id
            except Exception:
                pass
        await session.commit()


@router.callback_query(F.data.startswith("captcha:"))
async def captcha_callback(callback: CallbackQuery) -> None:
    try:
        _, raw_chat_id, raw_user_id, token = callback.data.split(":", 3)
        chat_id, user_id = int(raw_chat_id), int(raw_user_id)
        if callback.from_user.id != user_id:
            await callback.answer("Эта кнопка предназначена другому пользователю.", show_alert=True)
            return
        async with SessionFactory() as session:
            challenge = await session.scalar(
                select(CaptchaChallenge).where(
                    CaptchaChallenge.chat_id == chat_id,
                    CaptchaChallenge.user_id == user_id,
                    CaptchaChallenge.token == token,
                    CaptchaChallenge.passed.is_(False),
                )
            )
            if not challenge or (as_utc(challenge.expires_at) and as_utc(challenge.expires_at) < utcnow()):
                raise ValueError("Проверка истекла")
            challenge.passed = True
            await bot.restrict_chat_member(chat_id, user_id, full_permissions())
            await add_log(session, chat_id, "captcha_passed", actor_id=user_id, target_id=user_id)
            await session.commit()
        await callback.answer("Проверка пройдена.")
        if callback.message:
            await callback.message.edit_text("Проверка пройдена. Доступ к беседе открыт.")
    except Exception as exc:
        await callback.answer(str(exc), show_alert=True)


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

        try:
            tg_member = await bot.get_chat_member(chat.id, membership.user_id)
            if tg_member.status in {ChatMemberStatus.CREATOR, ChatMemberStatus.ADMINISTRATOR}:
                return False
        except Exception:
            pass

        async def delete_message() -> None:
            try:
                await message.delete()
            except Exception:
                pass

        text = message.text or message.caption or ""
        lowered = text.casefold()
        restrictions = await active_restrictions(
            session,
            chat_id=chat.id,
            user_id=membership.user_id,
        )
        has_media = any(
            (
                message.photo,
                message.video,
                message.document,
                message.audio,
                message.voice,
                message.video_note,
                message.animation,
                message.sticker,
            )
        )
        has_link_any = "http://" in lowered or "https://" in lowered or "t.me/" in lowered
        premium_access = await has_premium_access(
            session,
            chat_id=chat.id,
            user_id=membership.user_id,
        )

        async def apply_automatic_violation(
            action_name: str,
            reason: str,
            *,
            add_warning: bool = True,
            quarantine: bool = False,
        ) -> bool:
            await delete_message()
            if add_warning and settings_data.get("auto_warn_enabled", True):
                user_member.warnings += 1
            if quarantine and premium_access:
                duration = int(settings_data.get("premium_newcomer_quarantine_seconds", 3600))
                until = utcnow() + timedelta(seconds=duration)
                try:
                    await bot.restrict_chat_member(
                        chat.id,
                        membership.user_id,
                        quarantine_permissions(),
                        until_date=until,
                    )
                    user_member.quarantined_until = until
                except Exception:
                    pass
            if premium_access and settings_data.get("premium_punishment_ladder_enabled", False):
                threshold = max(1, int(settings_data.get("warn_threshold", 3)))
                if user_member.warnings >= threshold * 2:
                    try:
                        await bot.ban_chat_member(chat.id, membership.user_id)
                    except Exception:
                        pass
                elif user_member.warnings >= threshold:
                    duration = int(settings_data.get("premium_ladder_mute_seconds", 3600))
                    until = utcnow() + timedelta(seconds=duration)
                    try:
                        await bot.restrict_chat_member(
                            chat.id,
                            membership.user_id,
                            muted_permissions(),
                            until_date=until,
                        )
                        user_member.muted_until = until
                    except Exception:
                        pass
            threat_bucket = _threat_buckets[chat.id]
            now_value = time.monotonic()
            threat_window = int(settings_data.get("premium_adaptive_window_seconds", 60))
            while threat_bucket and now_value - threat_bucket[0] > threat_window:
                threat_bucket.popleft()
            threat_bucket.append(now_value)
            await add_log(
                session,
                chat.id,
                action_name,
                target_id=membership.user_id,
                reason=reason[:500],
                details={"warnings": user_member.warnings},
            )
            await session.commit()
            return True

        if "media" in restrictions and has_media:
            await delete_message()
            await add_log(session, chat.id, "restricted_media", target_id=membership.user_id, reason=text[:200])
            await session.commit()
            return True
        if "links" in restrictions and has_link_any:
            await delete_message()
            await add_log(session, chat.id, "restricted_link", target_id=membership.user_id, reason=text[:200])
            await session.commit()
            return True

        now_mono = time.monotonic()
        key = (chat.id, membership.user_id)
        slow_seconds = int(settings_data.get("slow_mode_seconds", 0))
        if slow_seconds > 0:
            previous = _slow_buckets.get(key, 0)
            if now_mono - previous < slow_seconds:
                await delete_message()
                return True
            _slow_buckets[key] = now_mono

        blocked_words = [str(word).casefold() for word in settings_data.get("blocked_words", [])]
        blocked_trigger = any(word and word in lowered for word in blocked_words)
        mention_count = sum(
            1 for entity in (message.entities or [])
            if entity.type in {"mention", "text_mention"}
        )
        mass_mentions_trigger = bool(settings_data.get("mass_mentions_enabled", True)) and (
            mention_count > int(settings_data.get("mass_mentions_limit", 5))
        )

        normalized_text = re.sub(r"\s+", " ", lowered).strip()
        duplicate_trigger = False
        if settings_data.get("duplicate_filter_enabled", True) and len(normalized_text) >= 3:
            duplicate_bucket = _duplicate_buckets[key]
            duplicate_window = int(settings_data.get("duplicate_window_seconds", 30))
            while duplicate_bucket and now_mono - duplicate_bucket[0][0] > duplicate_window:
                duplicate_bucket.popleft()
            duplicate_bucket.append((now_mono, normalized_text))
            duplicate_trigger = sum(1 for _, value in duplicate_bucket if value == normalized_text) >= int(
                settings_data.get("duplicate_limit", 3)
            )

        letters = [char for char in text if char.isalpha()]
        upper_count = sum(1 for char in letters if char.isupper())
        caps_trigger = bool(settings_data.get("caps_filter_enabled", False)) and (
            len(letters) >= int(settings_data.get("caps_min_letters", 12))
            and upper_count * 100 / max(1, len(letters)) >= int(settings_data.get("caps_ratio_percent", 75))
        )

        emoji_count = sum(1 for char in text if unicodedata.category(char) in {"So", "Sk"})
        emoji_trigger = bool(settings_data.get("emoji_flood_enabled", False)) and (
            emoji_count > int(settings_data.get("emoji_limit", 20))
        )
        forward_trigger = bool(settings_data.get("forward_filter_enabled", False)) and bool(
            getattr(message, "forward_origin", None)
        )
        media_trigger = bool(settings_data.get("media_filter_enabled", False)) and has_media

        text_link_urls = [
            str(getattr(entity, "url", "") or "")
            for entity in (message.entities or [])
            if entity.type == "text_link"
        ]
        short_domains = {"bit.ly", "tinyurl.com", "clck.ru", "goo.su", "cutt.ly", "t.co"}
        hidden_link_trigger = premium_access and bool(
            settings_data.get("premium_hidden_links_enabled", False)
        ) and any(
            (urlparse(url).hostname or "").casefold() in short_domains
            for url in text_link_urls
        )

        url_count = len(re.findall(r"(?:https?://|t\.me/)", lowered))
        phone_like = bool(re.search(r"(?:\+?\d[\s()\-]*){9,}", text))
        repeated_chars = bool(re.search(r"(.)\1{8,}", lowered))
        spam_words = ("заработок", "быстрый доход", "пиши в лс", "инвестиции", "розыгрыш")
        smart_spam_trigger = premium_access and bool(
            settings_data.get("premium_smart_spam_enabled", False)
        ) and (
            url_count >= 2
            or (phone_like and has_link_any)
            or repeated_chars
            or sum(1 for word in spam_words if word in lowered) >= 2
        )

        joined_at = as_utc(user_member.joined_at) or utcnow()
        age_hours = max(0, (utcnow() - joined_at).total_seconds() / 3600)
        suspicious_newcomer = premium_access and bool(
            settings_data.get("premium_suspicious_newcomers_enabled", False)
        ) and age_hours < int(settings_data.get("newcomer_window_hours", 24)) and (
            not message.from_user.username
            and (has_link_any or mention_count > 0 or smart_spam_trigger)
        )

        bucket = _flood_buckets[key]
        window = int(settings_data.get("flood_window_seconds", 10))
        while bucket and now_mono - bucket[0] > window:
            bucket.popleft()
        bucket.append(now_mono)
        flood_limit = int(settings_data.get("flood_limit", 6))
        if premium_access and settings_data.get("premium_adaptive_protection_enabled", False):
            threat_bucket = _threat_buckets[chat.id]
            adaptive_window = int(settings_data.get("premium_adaptive_window_seconds", 60))
            while threat_bucket and now_mono - threat_bucket[0] > adaptive_window:
                threat_bucket.popleft()
            if len(threat_bucket) >= int(settings_data.get("premium_adaptive_trigger_count", 5)):
                flood_limit = max(3, flood_limit // 2)
        flood_trigger = len(bucket) > flood_limit

        has_link = has_link_any
        link_trigger = False
        if has_link and age_hours < int(settings_data.get("links_newbie_hours", 24)):
            allowed = [str(domain).casefold() for domain in settings_data.get("allowed_domains", [])]
            urls = re.findall(r"(?:https?://)?(?:www\.)?[^\s/]+(?:/[^\s]*)?", text)
            domains = {
                (urlparse(url if "://" in url else "https://" + url).hostname or "").casefold()
                for url in urls
                if "." in url or "t.me/" in url.casefold()
            }
            link_trigger = bool(domains) and not all(
                any(domain == item or domain.endswith("." + item) for item in allowed)
                for domain in domains
            )

        rules = (
            await session.scalars(
                select(ModerationRule).where(
                    ModerationRule.chat_id == chat.id,
                    ModerationRule.enabled.is_(True),
                )
            )
        ).all()
        signals = {
            "flood": flood_trigger,
            "newbie_link": link_trigger,
            "blocked_word": blocked_trigger,
            "mass_mentions": mass_mentions_trigger,
        }
        for rule in rules:
            condition_type = str((rule.condition or {}).get("type", ""))
            if not signals.get(condition_type, False):
                continue
            rule_premium = await has_premium_access(
                session, chat_id=chat.id, user_id=rule.created_by
            )
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
            await add_log(
                session,
                chat.id,
                "custom_rule",
                target_id=membership.user_id,
                reason=rule.name,
                details={"rule_id": rule.id, "condition": condition_type},
            )
            await session.commit()
            if stopped:
                return True

        if settings_data.get("anti_flood_enabled", True) and flood_trigger:
            return await apply_automatic_violation(
                "auto_flood",
                "Превышен лимит сообщений",
            )

        if duplicate_trigger:
            return await apply_automatic_violation(
                "duplicate_message",
                "Повторяющиеся сообщения",
            )

        if caps_trigger:
            return await apply_automatic_violation(
                "caps_flood",
                "Превышена доля заглавных букв",
            )

        if emoji_trigger:
            return await apply_automatic_violation(
                "emoji_flood",
                f"Слишком много эмодзи: {emoji_count}",
            )

        if forward_trigger:
            return await apply_automatic_violation(
                "forwarded_message",
                "Пересланные сообщения запрещены",
                add_warning=False,
            )

        if media_trigger:
            return await apply_automatic_violation(
                "media_filter",
                "Медиа запрещено настройками группы",
                add_warning=False,
            )

        if settings_data.get("word_filter_enabled", True) and blocked_trigger:
            return await apply_automatic_violation(
                "blocked_word",
                text[:200] or "Запрещённое слово",
            )

        if settings_data.get("link_filter_enabled", True) and link_trigger:
            return await apply_automatic_violation(
                "newbie_link",
                text[:200] or "Ссылка от нового участника",
                add_warning=False,
            )

        if mass_mentions_trigger:
            return await apply_automatic_violation(
                "mass_mentions",
                f"Массовые упоминания: {mention_count}",
            )

        if hidden_link_trigger:
            return await apply_automatic_violation(
                "hidden_link",
                "Скрытая или сокращённая ссылка",
            )

        if smart_spam_trigger:
            return await apply_automatic_violation(
                "smart_spam",
                "Сообщение распознано как спам",
            )

        if suspicious_newcomer:
            return await apply_automatic_violation(
                "suspicious_newcomer",
                "Подозрительная активность нового участника",
                quarantine=bool(settings_data.get("premium_auto_quarantine_enabled", False)),
            )

    return False


def role_allows(required: str, role: str) -> bool:
    if required == "admins":
        return role in {"owner", "admin"}
    if required == "moderators":
        return role in {"owner", "admin", "moderator"}
    if required == "verified":
        return role in {"owner", "admin", "moderator", "verified"}
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
        await bot.send_message(
            chat.id,
            render_custom_template(
                command.response_template,
                actor_id=message.from_user.id,
                target_id=target_id,
                command_name=command.name,
                duration_seconds=result.get("duration_seconds"),
                reason=reason,
                chat_title=chat.title,
            ),
        )
        return True


async def try_game_command(message: Message, chat: Chat, membership: Membership) -> bool:
    text = (message.text or "").strip()
    if not text or text.startswith("/"):
        return False
    async with SessionFactory() as session:
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
        response = html.escape(template)
        replacements = {
            "{user}": profile_link(message.from_user.id, message.from_user.first_name),
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
    return None, "себя"


async def try_rp_command(message: Message, chat: Chat, membership: Membership) -> bool:
    text = (message.text or "").strip()
    if not text or text.startswith("/"):
        return False
    async with SessionFactory() as session:
        settings_data = await get_merged_settings(session, chat.id)
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
        if command.access == "admins" and role not in {"owner", "admin"}:
            await message.reply("Эта RP-команда доступна только администраторам.")
            return True
        if command.access == "moderators" and role not in {"owner", "admin", "moderator"}:
            await message.reply("Эта RP-команда доступна только модераторам.")
            return True
        if command.access == "verified" and membership_db:
            joined_at = as_utc(membership_db.joined_at) or utcnow()
            verified = role in {"owner", "admin", "moderator"} or (utcnow() - joined_at >= timedelta(hours=24) and membership_db.warnings == 0)
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

    try:
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


async def configure_bot() -> None:
    commands = [
        BotCommand(command="panel", description="Открыть Mini App"),
        BotCommand(command="help", description="Список команд"),
        BotCommand(command="premium", description="Купить Premium"),
        BotCommand(command="warn", description="Предупредить пользователя"),
        BotCommand(command="mute", description="Выдать мут"),
        BotCommand(command="ban", description="Заблокировать пользователя"),
        BotCommand(command="report", description="Пожаловаться на сообщение"),
        BotCommand(command="profile", description="Профиль участника"),
        BotCommand(command="top", description="Топ участников"),
        BotCommand(command="rules", description="Правила модерации"),
        BotCommand(command="rplist", description="RP-команды"),
        BotCommand(command="logs", description="Журнал модерации"),
        BotCommand(command="reports", description="Открытые жалобы"),
        BotCommand(command="antiflood", description="Антифлуд on/off"),
        BotCommand(command="anime", description="Аниме-режим on/off"),
    ]
    await bot.set_my_commands(commands)
    if settings.webapp_url.startswith("https://"):
        await bot.set_chat_menu_button(
            menu_button=MenuButtonWebApp(
                text="AniGuard",
                web_app=WebAppInfo(url=settings.webapp_url),
            )
        )


async def start_polling() -> None:
    await configure_bot()
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())


async def stop_bot() -> None:
    await dp.stop_polling()
    await bot.session.close()
