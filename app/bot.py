from __future__ import annotations

import asyncio
import html
import random
import re
import time
from collections import defaultdict, deque
from datetime import timedelta
from typing import Any
from urllib.parse import urlparse

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ChatMemberStatus, ChatType, ParseMode
from aiogram.filters import Command, CommandObject, CommandStart
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
from app.models import CaptchaChallenge, Chat, Membership, ModerationLog, ModerationRule, Report, RPCommand, User
from app.pricing import PREMIUM_PLANS, get_plan
from app.services import (
    add_log,
    as_utc,
    create_captcha,
    create_report,
    ensure_membership,
    full_permissions,
    get_chat_or_raise,
    get_merged_settings,
    grant_premium,
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

_flood_buckets: dict[tuple[int, int], deque[float]] = defaultdict(deque)
_slow_buckets: dict[tuple[int, int], float] = {}
_rp_cooldowns: dict[tuple[int, int, int], float] = {}


DURATION_PATTERN = re.compile(r"^(\d+)(s|m|h|d|w)?$", re.IGNORECASE)


def parse_duration(value: str | None, default: int = 1800) -> int:
    if not value:
        return default
    match = DURATION_PATTERN.fullmatch(value.strip())
    if not match:
        raise ValueError("Формат срока: 30m, 2h, 7d или число секунд")
    amount = int(match.group(1))
    unit = (match.group(2) or "s").lower()
    multiplier = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}[unit]
    return min(amount * multiplier, 31_536_000)


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


async def resolve_target(message: Message, token: str | None = None) -> int | None:
    if message.reply_to_message and message.reply_to_message.from_user:
        return message.reply_to_message.from_user.id
    if not token:
        return None
    token = token.strip()
    if token.lstrip("-").isdigit():
        return int(token)
    if token.startswith("@"):
        username = token[1:].lower()
        async with SessionFactory() as session:
            return await session.scalar(
                select(User.id).where(func.lower(User.username) == username)
            )
    return None


async def send_admin_error(message: Message, exc: Exception) -> None:
    await message.answer(f"<b>Не удалось выполнить действие.</b>\n{html.escape(str(exc))}")


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
        "<b>Команды AniGuard</b>\n"
        "/panel, /settings — открыть Mini App\n"
        "/premium — тарифы Premium\n"
        "/warn, /unwarn — предупреждения\n"
        "/mute, /unmute — ограничение сообщений\n"
        "/ban, /unban — блокировка\n"
        "/purge — очистка сообщений\n"
        "/slow — программный медленный режим\n"
        "/lock, /unlock — закрыть или открыть чат\n"
        "/quarantine — карантин Premium\n"
        "/report — пожаловаться ответом\n"
        "/case — создать дело Premium\n"
        "/profile — профиль участника\n"
        "/top, /topchats — рейтинги\n"
        "/antiflood, /links, /captcha, /words — on/off\n"
        "/anime, /rptoggle — on/off\n"
        "/rules, /addrule, /delrule — правила\n"
        "/rplist, /addrp, /delrp — RP-конструктор\n"
        "/logs, /reports — журнал и жалобы\n"
        "/susanoo — экстренная защита Premium"
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
    command: CommandObject,
    action: str,
    *,
    needs_target: bool = True,
    default_duration: int | None = None,
) -> None:
    try:
        await ensure_group_admin(message)
        await ensure_context(message)
        args = split_args(command)
        target_id = await resolve_target(message, args[0] if args else None) if needs_target else None
        arg_offset = 1 if target_id is not None and args and not message.reply_to_message else 0
        duration = default_duration
        if action in {"mute", "ban", "quarantine"}:
            duration = parse_duration(args[arg_offset] if len(args) > arg_offset else None, default_duration or 1800)
        reason_start = arg_offset + (1 if action in {"mute", "ban", "quarantine"} and len(args) > arg_offset else 0)
        reason = " ".join(args[reason_start:]) or "Команда модератора"
        async with SessionFactory() as session:
            result = await perform_action(
                session,
                bot,
                chat_id=message.chat.id,
                actor_id=message.from_user.id,
                action=action,
                target_id=target_id,
                duration_seconds=duration,
                reason=reason,
            )
            await session.commit()
        await message.answer(f"<b>Действие выполнено:</b> {html.escape(action)}\n<code>{html.escape(str(result))}</code>")
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
    await moderation_command(message, command, "mute", default_duration=1800)


@router.message(Command("unmute"))
async def unmute_handler(message: Message, command: CommandObject) -> None:
    await moderation_command(message, command, "unmute")


@router.message(Command("ban"))
async def ban_handler(message: Message, command: CommandObject) -> None:
    await moderation_command(message, command, "ban", default_duration=604800)


@router.message(Command("unban"))
async def unban_handler(message: Message, command: CommandObject) -> None:
    await moderation_command(message, command, "unban")


@router.message(Command("quarantine"))
async def quarantine_handler(message: Message, command: CommandObject) -> None:
    await moderation_command(message, command, "quarantine", default_duration=3600)


@router.message(Command("case"))
async def case_handler(message: Message, command: CommandObject) -> None:
    await moderation_command(message, command, "case")


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


@router.message(Command("lock"))
async def lock_handler(message: Message, command: CommandObject) -> None:
    await moderation_command(message, command, "lock", needs_target=False)


@router.message(Command("unlock"))
async def unlock_handler(message: Message, command: CommandObject) -> None:
    await moderation_command(message, command, "unlock", needs_target=False)


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
        duration = parse_duration((command.args or "30m").split()[0], 1800)
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
        await message.answer(f"Экстренная защита включена на {duration} сек.")
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
            chat = await get_chat_or_raise(session, message.chat.id)
            count = await session.scalar(select(func.count()).select_from(ModerationRule).where(ModerationRule.chat_id == message.chat.id))
            if not is_premium(chat) and (count or 0) >= 5:
                raise PermissionError("Бесплатный тариф позволяет создать до 5 правил")
            if is_premium(chat) and (count or 0) >= 100:
                raise PermissionError("Premium позволяет создать до 100 правил")
            premium_rule = action_type == "quarantine"
            if premium_rule and not is_premium(chat):
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
            chat = await get_chat_or_raise(session, message.chat.id)
            count = await session.scalar(select(func.count()).select_from(RPCommand).where(RPCommand.chat_id == message.chat.id))
            if not is_premium(chat) and (count or 0) >= 25:
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

        now_mono = time.monotonic()
        key = (chat.id, membership.user_id)
        slow_seconds = int(settings_data.get("slow_mode_seconds", 0))
        if slow_seconds > 0:
            previous = _slow_buckets.get(key, 0)
            if now_mono - previous < slow_seconds:
                await delete_message()
                return True
            _slow_buckets[key] = now_mono

        text = message.text or message.caption or ""
        lowered = text.casefold()
        blocked_words = [str(word).casefold() for word in settings_data.get("blocked_words", [])]
        blocked_trigger = any(word and word in lowered for word in blocked_words)
        mention_count = sum(1 for entity in (message.entities or []) if entity.type in {"mention", "text_mention"})
        mass_mentions_trigger = mention_count > int(settings_data.get("mass_mentions_limit", 5))

        bucket = _flood_buckets[key]
        window = int(settings_data.get("flood_window_seconds", 10))
        while bucket and now_mono - bucket[0] > window:
            bucket.popleft()
        bucket.append(now_mono)
        flood_trigger = len(bucket) > int(settings_data.get("flood_limit", 6))

        has_link = "http://" in lowered or "https://" in lowered or "t.me/" in lowered
        joined_at = as_utc(user_member.joined_at) or utcnow()
        age_hours = max(0, (utcnow() - joined_at).total_seconds() / 3600)
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
            if rule.is_premium and not is_premium(chat):
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
                elif action_type == "quarantine" and is_premium(chat):
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

        if settings_data["anti_flood_enabled"] and flood_trigger:
            await delete_message()
            user_member.warnings += 1
            await add_log(
                session,
                chat.id,
                "auto_flood",
                target_id=membership.user_id,
                reason="Превышен лимит сообщений",
            )
            await session.commit()
            return True

        if settings_data["word_filter_enabled"] and blocked_trigger:
            await delete_message()
            user_member.warnings += 1
            await add_log(session, chat.id, "blocked_word", target_id=membership.user_id, reason=text[:200])
            await session.commit()
            return True

        if settings_data["link_filter_enabled"] and link_trigger:
            await delete_message()
            await add_log(session, chat.id, "newbie_link", target_id=membership.user_id, reason=text[:200])
            await session.commit()
            return True

        if mass_mentions_trigger:
            await delete_message()
            await add_log(session, chat.id, "mass_mentions", target_id=membership.user_id, details={"count": mention_count})
            await session.commit()
            return True

    return False


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
        if command.is_premium and not is_premium(chat):
            await message.reply("Эта RP-команда доступна только с AniGuard Premium.")
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
