from __future__ import annotations

import html
import re
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware, F, Router
from aiogram.enums import ChatMemberStatus, ChatType
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message, TelegramObject

from app.db import SessionFactory

from .extended import bond_text, trade_accept, trade_cancel, trade_create
from .service import GameError, clan_text
from .social import (
    CLAN_ROLE_LABELS,
    alliance_accept,
    alliance_create,
    alliance_invite,
    alliance_text,
    alliance_war,
    clan_base_text,
    clan_base_upgrade,
    clan_history_text,
    global_notice_text,
    marriage_accept,
    marriage_home_upgrade,
    marriage_propose,
    settlement_mission_join,
    settlement_mission_start,
    settlement_mission_status,
    block_add,
    block_list_text,
    block_remove,
    clan_chat_link,
    clan_intel,
    clan_role_set,
    clan_roles_text,
    duel_accept,
    duel_cancel,
    duel_challenge,
    duel_reject,
    friend_accept,
    friend_reject,
    friend_remove,
    friend_request,
    friends_text,
    mail_inbox,
    mail_send,
    marriage_divorce,
    mentorship_accept,
    mentorship_claim,
    mentorship_request,
    mentorship_text,
    mmo_top_text,
    privacy_toggle,
    public_profile_text,
    settlement_donate,
    settlement_history_text,
    settlement_notify_toggle,
    settlement_register,
    settlement_status,
    settlement_top,
    settlement_upgrade,
    settlement_war_accept,
    settlement_war_attack,
    settlement_war_challenge,
    settlement_war_finish,
    settlement_war_status,
    social_permission_toggle,
    touch_settlement_member,
    tournament_create,
    tournament_join,
    tournament_start,
    trade_reputation_text,
    village_chat_link,
    war_board_text,
    world_broadcast_targets,
)

router = Router(name="naruto-rpg-social")


def _safe(text: str) -> str:
    return (
        html.escape(str(text), quote=False)
        .replace("&lt;b&gt;", "<b>")
        .replace("&lt;/b&gt;", "</b>")
        .replace("&lt;code&gt;", "<code>")
        .replace("&lt;/code&gt;", "</code>")
    )


def _is_group(message: Message) -> bool:
    return message.chat.type in {ChatType.GROUP, ChatType.SUPERGROUP}


async def _require_group(message: Message) -> None:
    if not _is_group(message):
        raise GameError("Эта команда работает только в Telegram-группе.")


async def _require_chat_creator(message: Message) -> None:
    await _require_group(message)
    if not message.from_user:
        raise GameError("Не удалось определить пользователя.")
    member = await message.bot.get_chat_member(message.chat.id, message.from_user.id)
    if member.status != ChatMemberStatus.CREATOR:
        raise GameError("Эту настройку может менять только создатель Telegram-группы.")


def _reply_target(message: Message) -> int | None:
    reply = message.reply_to_message
    if reply and reply.from_user and not reply.from_user.is_bot:
        return int(reply.from_user.id)
    return None


def _target_from(parts: list[str], message: Message, index: int = 0) -> tuple[int | None, int]:
    """Return (target_id, next argument index). Reply target wins."""
    reply_id = _reply_target(message)
    if reply_id is not None:
        return reply_id, index
    if len(parts) > index and parts[index].lstrip("-").isdigit():
        return int(parts[index]), index + 1
    return None, index


async def _run(message: Message, operation: Callable[[Any], Awaitable[str]]) -> None:
    if not message.from_user:
        return
    async with SessionFactory() as session:
        try:
            text = await operation(session)
            await session.commit()
        except GameError as exc:
            await session.rollback()
            await message.answer(f"⚠️ {_safe(str(exc))}")
            return
    await message.answer(_safe(text))


async def _try_dm(bot, user_id: int, text: str, keyboard: InlineKeyboardMarkup | None = None) -> None:
    try:
        await bot.send_message(user_id, _safe(text), reply_markup=keyboard)
    except Exception:
        # A Telegram bot cannot initiate a DM before the user has started it.
        pass


class SocialPresenceMiddleware(BaseMiddleware):
    """Count real RPG participation in a registered Telegram settlement.

    It runs only for updates already handled by the Naruto RPG routers, so ordinary
    chat messages do not generate XP and cannot be used as a spam farm.
    """

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        result = await handler(event, data)
        if isinstance(event, Message) and event.from_user and event.chat.type in {ChatType.GROUP, ChatType.SUPERGROUP}:
            try:
                async with SessionFactory() as session:
                    await touch_settlement_member(session, int(event.chat.id), int(event.from_user.id))
                    await session.commit()
            except Exception:
                pass
        return result


# ---------------------------------------------------------------------------
# Telegram settlement
# ---------------------------------------------------------------------------


@router.message(Command("settlement", "ninja_chat"))
async def settlement_handler(message: Message, command: CommandObject) -> None:
    try:
        await _require_group(message)
    except GameError as exc:
        await message.answer(f"⚠️ {_safe(str(exc))}")
        return
    raw = (command.args or "").strip()
    parts = raw.split()
    uid = message.from_user.id
    if not parts:
        await _run(message, lambda s: settlement_status(s, message.chat.id))
        return
    action = parts[0].lower()
    if action in {"register", "создать", "регистрация"}:
        try:
            await _require_chat_creator(message)
        except GameError as exc:
            await message.answer(f"⚠️ {_safe(str(exc))}")
            return
        village = parts[1].lower() if len(parts) > 1 else None
        await _run(
            message,
            lambda s: settlement_register(
                s,
                message.chat.id,
                message.chat.title or "Поселение шиноби",
                message.chat.username,
                uid,
                village,
            ),
        )
    elif action in {"donate", "внести"} and len(parts) > 1 and parts[1].isdigit():
        await _run(message, lambda s: settlement_donate(s, message.chat.id, uid, int(parts[1])))
    elif action in {"upgrade", "улучшить"} and len(parts) > 1:
        await _run(message, lambda s: settlement_upgrade(s, message.chat.id, uid, parts[1]))
    elif action in {"notify", "уведомления"} and len(parts) > 2:
        enabled = parts[2].lower() in {"on", "1", "yes", "да", "вкл"}
        await _run(message, lambda s: settlement_notify_toggle(s, message.chat.id, uid, parts[1], enabled))
    elif action in {"history", "история"}:
        await _run(message, lambda s: settlement_history_text(s, message.chat.id))
    elif action in {"top", "топ"}:
        await _run(message, settlement_top)
    else:
        await message.answer(
            "🏯 <b>Поселение Telegram</b>\n"
            "/settlement register [konoha|suna|kiri|kumo|iwa]\n"
            "/settlement donate 5000\n"
            "/settlement upgrade walls\n"
            "/settlement notify war on|off\n"
            "/settlement history · /settlement top"
        )


@router.message(Command("clanroles"))
async def clan_roles_handler(message: Message) -> None:
    await message.answer(clan_roles_text())


@router.message(Command("clanrole"))
async def clan_role_handler(message: Message, command: CommandObject) -> None:
    parts = (command.args or "").split()
    target, idx = _target_from(parts, message)
    if target is None or len(parts) <= idx:
        await message.answer("🥷 Ответьте на сообщение игрока: <code>/clanrole commander</code>\nИли: <code>/clanrole USER_ID scout</code>")
        return
    await _run(message, lambda s: clan_role_set(s, message.from_user.id, target, parts[idx]))


@router.message(Command("clanchat"))
async def clan_chat_handler(message: Message, command: CommandObject) -> None:
    if (command.args or "").strip().lower() != "link":
        await message.answer("👥 В клановой Telegram-группе: /clanchat link")
        return
    try:
        await _require_chat_creator(message)
    except GameError as exc:
        await message.answer(f"⚠️ {_safe(str(exc))}")
        return
    await _run(message, lambda s: clan_chat_link(s, message.from_user.id, message.chat.id, message.chat.title or "Клановый чат"))


@router.message(Command("villagechat"))
async def village_chat_handler(message: Message, command: CommandObject) -> None:
    if (command.args or "").strip().lower() != "link":
        await message.answer("🏯 В официальной группе деревни: /villagechat link")
        return
    try:
        await _require_chat_creator(message)
    except GameError as exc:
        await message.answer(f"⚠️ {_safe(str(exc))}")
        return
    await _run(
        message,
        lambda s: village_chat_link(
            s,
            message.from_user.id,
            message.chat.id,
            message.chat.title or "Чат деревни",
            message.chat.username,
        ),
    )


@router.message(Command("intel", "recon"))
async def intel_handler(message: Message, command: CommandObject) -> None:
    name = (command.args or "").strip()
    if not name:
        await message.answer("🕵 /intel Название клана")
        return
    await _run(message, lambda s: clan_intel(s, message.from_user.id, name))


# ---------------------------------------------------------------------------
# Friends, mentor/student, privacy and block list
# ---------------------------------------------------------------------------


@router.message(Command("friend", "friends"))
async def friend_handler(message: Message, command: CommandObject) -> None:
    parts = (command.args or "").split()
    uid = message.from_user.id
    if not parts or parts[0].lower() in {"list", "список"}:
        await _run(message, lambda s: friends_text(s, uid))
        return
    action = parts[0].lower()
    if action in {"add", "добавить", "дружить"}:
        target, _ = _target_from(parts, message, 1)
        if target is None:
            await message.answer("🤝 Ответьте на сообщение: /friend add\nИли: /friend add USER_ID")
            return
        async with SessionFactory() as session:
            try:
                text, req_id = await friend_request(session, uid, target)
                await session.commit()
            except GameError as exc:
                await session.rollback()
                await message.answer(f"⚠️ {_safe(str(exc))}")
                return
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="✅ Принять", callback_data=f"ngs:friend:accept:{req_id}"),
            InlineKeyboardButton(text="❌ Отказать", callback_data=f"ngs:friend:reject:{req_id}"),
        ]])
        await message.answer(_safe(text))
        await _try_dm(message.bot, target, f"🤝 Шиноби {uid} хочет добавить вас в друзья.", kb)
    elif action in {"accept", "принять"} and len(parts) > 1 and parts[1].isdigit():
        await _friend_accept_by_message(message, int(parts[1]))
    elif action in {"reject", "отказать"} and len(parts) > 1 and parts[1].isdigit():
        await _run(message, lambda s: friend_reject(s, uid, int(parts[1])))
    elif action in {"remove", "удалить"}:
        target, _ = _target_from(parts, message, 1)
        if target is None:
            await message.answer("/friend remove USER_ID или ответом на сообщение")
            return
        await _run(message, lambda s: friend_remove(s, uid, target))
    else:
        await message.answer("🤝 /friend add [USER_ID] · /friend accept ID · /friend reject ID · /friend remove USER_ID · /friend list")


async def _friend_accept_by_message(message: Message, req_id: int) -> None:
    async with SessionFactory() as session:
        try:
            text, partner = await friend_accept(session, message.from_user.id, req_id)
            await session.commit()
        except GameError as exc:
            await session.rollback()
            await message.answer(f"⚠️ {_safe(str(exc))}")
            return
    await message.answer(_safe(text))
    await _try_dm(message.bot, partner, text)


@router.callback_query(F.data.startswith("ngs:friend:"))
async def friend_callback(callback: CallbackQuery) -> None:
    parts = (callback.data or "").split(":")
    if len(parts) != 4 or not parts[3].isdigit():
        await callback.answer("Некорректный запрос", show_alert=True)
        return
    action, req_id = parts[2], int(parts[3])
    async with SessionFactory() as session:
        try:
            if action == "accept":
                text, partner = await friend_accept(session, callback.from_user.id, req_id)
            else:
                text = await friend_reject(session, callback.from_user.id, req_id)
                partner = None
            await session.commit()
        except GameError as exc:
            await session.rollback()
            await callback.answer(str(exc), show_alert=True)
            return
    await callback.answer("Готово")
    if callback.message:
        await callback.message.edit_text(_safe(text))
    if partner:
        await _try_dm(callback.bot, partner, text)


@router.message(Command("student", "player_mentor"))
async def mentorship_handler(message: Message, command: CommandObject) -> None:
    parts = (command.args or "").split()
    uid = message.from_user.id
    if not parts or parts[0].lower() in {"list", "status", "статус"}:
        await _run(message, lambda s: mentorship_text(s, uid))
        return
    action = parts[0].lower()
    if action in {"invite", "пригласить"}:
        target, _ = _target_from(parts, message, 1)
        if target is None:
            await message.answer("👨‍🏫 Ответьте ученику: /student invite\nИли: /student invite USER_ID")
            return
        async with SessionFactory() as session:
            try:
                text, req_id = await mentorship_request(session, uid, target)
                await session.commit()
            except GameError as exc:
                await session.rollback()
                await message.answer(f"⚠️ {_safe(str(exc))}")
                return
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✅ Принять наставника", callback_data=f"ngs:mentor:accept:{req_id}")]])
        await message.answer(_safe(text))
        await _try_dm(message.bot, target, f"👨‍🏫 Шиноби {uid} предлагает стать вашим наставником.", kb)
    elif action in {"accept", "принять"} and len(parts) > 1 and parts[1].isdigit():
        await _mentor_accept_by_message(message, int(parts[1]))
    elif action in {"claim", "награда"}:
        target, _ = _target_from(parts, message, 1)
        if target is None:
            await message.answer("/student claim USER_ID")
            return
        await _run(message, lambda s: mentorship_claim(s, uid, target))
    else:
        await message.answer("👨‍🏫 /student invite USER_ID · /student accept ID · /student claim USER_ID · /student list")


async def _mentor_accept_by_message(message: Message, req_id: int) -> None:
    async with SessionFactory() as session:
        try:
            text, mentor_id = await mentorship_accept(session, message.from_user.id, req_id)
            await session.commit()
        except GameError as exc:
            await session.rollback()
            await message.answer(f"⚠️ {_safe(str(exc))}")
            return
    await message.answer(_safe(text))
    await _try_dm(message.bot, mentor_id, text)


@router.callback_query(F.data.startswith("ngs:mentor:accept:"))
async def mentor_callback(callback: CallbackQuery) -> None:
    raw = (callback.data or "").rsplit(":", 1)[-1]
    if not raw.isdigit():
        await callback.answer("Некорректный запрос", show_alert=True)
        return
    async with SessionFactory() as session:
        try:
            text, mentor_id = await mentorship_accept(session, callback.from_user.id, int(raw))
            await session.commit()
        except GameError as exc:
            await session.rollback()
            await callback.answer(str(exc), show_alert=True)
            return
    await callback.answer("Наставник принят")
    if callback.message:
        await callback.message.edit_text(_safe(text))
    await _try_dm(callback.bot, mentor_id, text)


@router.message(Command("privacy"))
async def privacy_handler(message: Message, command: CommandObject) -> None:
    parts = (command.args or "").split()
    if len(parts) != 2:
        await message.answer("🔐 /privacy ryo|inventory|techniques|trades on|off")
        return
    enabled = parts[1].lower() in {"on", "1", "yes", "да", "вкл"}
    await _run(message, lambda s: privacy_toggle(s, message.from_user.id, parts[0], enabled))


@router.message(Command("social"))
async def social_settings_handler(message: Message, command: CommandObject) -> None:
    parts = (command.args or "").split()
    if len(parts) != 2:
        await message.answer("🛡 /social friends|duels|trades|clans|marriage|mentor|mail on|off")
        return
    enabled = parts[1].lower() in {"on", "1", "yes", "да", "вкл"}
    await _run(message, lambda s: social_permission_toggle(s, message.from_user.id, parts[0], enabled))


@router.message(Command("nblock"))
async def block_handler(message: Message, command: CommandObject) -> None:
    parts = (command.args or "").split()
    uid = message.from_user.id
    if not parts or parts[0].lower() == "list":
        await _run(message, lambda s: block_list_text(s, uid))
        return
    action = parts[0].lower()
    target, _ = _target_from(parts, message, 1)
    if target is None:
        await message.answer("🚫 /nblock add USER_ID · /nblock remove USER_ID · /nblock list")
        return
    if action == "add":
        await _run(message, lambda s: block_add(s, uid, target))
    elif action == "remove":
        await _run(message, lambda s: block_remove(s, uid, target))


# ---------------------------------------------------------------------------
# Duels and deals
# ---------------------------------------------------------------------------


@router.message(Command("duel"))
async def duel_handler(message: Message, command: CommandObject) -> None:
    parts = (command.args or "").split()
    uid = message.from_user.id
    if parts and parts[0].lower() in {"accept", "принять"} and len(parts) > 1 and parts[1].isdigit():
        await _duel_accept_message(message, int(parts[1]))
        return
    if parts and parts[0].lower() in {"cancel", "отмена"} and len(parts) > 1 and parts[1].isdigit():
        await _run(message, lambda s: duel_cancel(s, uid, int(parts[1])))
        return
    target, idx = _target_from(parts, message, 0)
    if target is None:
        await message.answer("⚔️ Ответьте на сообщение: <code>/duel 10000</code>\nИли: <code>/duel USER_ID 10000</code>")
        return
    wager = int(parts[idx]) if len(parts) > idx and parts[idx].isdigit() else 0
    async with SessionFactory() as session:
        try:
            text, duel_id = await duel_challenge(session, uid, target, message.chat.id, wager)
            await session.commit()
        except GameError as exc:
            await session.rollback()
            await message.answer(f"⚠️ {_safe(str(exc))}")
            return
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="⚔️ Принять", callback_data=f"ngs:duel:accept:{duel_id}"),
        InlineKeyboardButton(text="❌ Отказаться", callback_data=f"ngs:duel:reject:{duel_id}"),
    ]])
    await message.answer(_safe(text), reply_markup=kb)
    if not _is_group(message):
        await _try_dm(message.bot, target, f"⚔️ Шиноби {uid} вызывает вас на дуэль. Ставка: {wager:,} рё.", kb)


async def _duel_accept_message(message: Message, duel_id: int) -> None:
    async with SessionFactory() as session:
        try:
            text, _winner = await duel_accept(session, message.from_user.id, duel_id)
            await session.commit()
        except GameError as exc:
            await session.rollback()
            await message.answer(f"⚠️ {_safe(str(exc))}")
            return
    await message.answer(_safe(text))


@router.callback_query(F.data.startswith("ngs:duel:"))
async def duel_callback(callback: CallbackQuery) -> None:
    parts = (callback.data or "").split(":")
    if len(parts) != 4 or not parts[3].isdigit():
        await callback.answer("Некорректный вызов", show_alert=True)
        return
    action, duel_id = parts[2], int(parts[3])
    async with SessionFactory() as session:
        try:
            if action == "reject":
                text, partner_id = await duel_reject(session, callback.from_user.id, duel_id)
                _winner = None
            else:
                text, _winner = await duel_accept(session, callback.from_user.id, duel_id)
                partner_id = None
            await session.commit()
        except GameError as exc:
            await session.rollback()
            await callback.answer(str(exc), show_alert=True)
            return
    await callback.answer("Готово")
    if callback.message:
        await callback.message.edit_text(_safe(text))
    if partner_id:
        await _try_dm(callback.bot, partner_id, text)


@router.message(Command("deal"))
async def deal_handler(message: Message, command: CommandObject) -> None:
    parts = (command.args or "").split()
    uid = message.from_user.id
    if parts and parts[0].lower() == "accept" and len(parts) > 1 and parts[1].isdigit():
        await _run(message, lambda s: trade_accept(s, uid, int(parts[1])))
        return
    if parts and parts[0].lower() == "cancel" and len(parts) > 1 and parts[1].isdigit():
        await _run(message, lambda s: trade_cancel(s, uid, int(parts[1])))
        return
    target, idx = _target_from(parts, message, 0)
    rest = parts[idx:]
    if target is None or len(rest) != 6:
        await message.answer(
            "🤝 Безопасная сделка ответом на сообщение:\n"
            "<code>/deal give_item qty give_ryo take_item qty take_ryo</code>\n"
            "Пример: <code>/deal kunai 5 0 chakra_crystal 1 500</code>\n"
            "Или первым параметром укажите USER_ID. Вместо предмета: -"
        )
        return
    give_item, give_qty, give_ryo, take_item, take_qty, take_ryo = rest
    if not (give_qty.isdigit() and give_ryo.isdigit() and take_qty.isdigit() and take_ryo.isdigit()):
        await message.answer("⚠️ Количество и рё должны быть числами.")
        return
    await _run(
        message,
        lambda s: trade_create(s, uid, target, give_item, int(give_qty), int(give_ryo), take_item, int(take_qty), int(take_ryo)),
    )


@router.message(Command("trade_rep"))
async def trade_rep_handler(message: Message) -> None:
    await _run(message, lambda s: trade_reputation_text(s, message.from_user.id))


# ---------------------------------------------------------------------------
# Marriage aliases
# ---------------------------------------------------------------------------


@router.message(Command("marry"))
async def marry_handler(message: Message, command: CommandObject) -> None:
    parts = (command.args or "").split()
    uid = message.from_user.id
    if not parts or parts[0].lower() in {"status", "статус"}:
        await _run(message, lambda s: bond_text(s, uid))
        return
    action = parts[0].lower()
    if action in {"propose", "предложить"}:
        target, _ = _target_from(parts, message, 1)
        if target is None:
            await message.answer("💍 Ответьте на сообщение: /marry propose\nИли: /marry propose USER_ID")
            return
        async with SessionFactory() as session:
            try:
                text, bond_id = await marriage_propose(session, uid, target)
                await session.commit()
            except GameError as exc:
                await session.rollback()
                await message.answer(f"⚠️ {_safe(str(exc))}")
                return
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="💍 Принять союз", callback_data=f"ngs:marry:accept:{bond_id}")]])
        await message.answer(_safe(text), reply_markup=kb)
        await _try_dm(message.bot, target, f"💍 Шиноби {uid} предлагает семейный союз.", kb)
    elif action in {"accept", "принять"} and len(parts) > 1 and parts[1].isdigit():
        async with SessionFactory() as session:
            try:
                text, partner_id = await marriage_accept(session, uid, int(parts[1]))
                await session.commit()
            except GameError as exc:
                await session.rollback()
                await message.answer(f"⚠️ {_safe(str(exc))}")
                return
        await message.answer(_safe(text))
        await _try_dm(message.bot, partner_id, text)
    elif action in {"home", "дом"}:
        await _run(message, lambda s: marriage_home_upgrade(s, uid))
    elif action in {"divorce", "развод"}:
        await _run(message, lambda s: marriage_divorce(s, uid))


# ---------------------------------------------------------------------------
# Telegram settlement wars with live board messages
# ---------------------------------------------------------------------------


async def _set_war_board_ids(war_id: int, attacker_mid: int | None, defender_mid: int | None) -> None:
    from .models import NinjaSettlementWar

    async with SessionFactory() as session:
        row = await session.get(NinjaSettlementWar, war_id)
        if row:
            row.attacker_board_message_id = attacker_mid
            row.defender_board_message_id = defender_mid
            await session.commit()


async def _refresh_war_boards(bot, war_id: int) -> None:
    from .models import NinjaSettlement, NinjaSettlementWar

    async with SessionFactory() as session:
        row = await session.get(NinjaSettlementWar, war_id)
        if not row:
            return
        a = await session.get(NinjaSettlement, row.attacker_chat_id)
        d = await session.get(NinjaSettlement, row.defender_chat_id)
        text = war_board_text(row, a.title if a else str(row.attacker_chat_id), d.title if d else str(row.defender_chat_id))
        data = [
            (row.attacker_chat_id, row.attacker_board_message_id),
            (row.defender_chat_id, row.defender_board_message_id),
        ]
    for chat_id, mid in data:
        if mid:
            try:
                await bot.edit_message_text(_safe(text), chat_id=chat_id, message_id=mid)
            except Exception:
                pass


@router.message(Command("chatwar"))
async def chatwar_handler(message: Message, command: CommandObject) -> None:
    try:
        await _require_group(message)
    except GameError as exc:
        await message.answer(f"⚠️ {_safe(str(exc))}")
        return
    parts = (command.args or "").split()
    if not parts:
        await message.answer("⚔️ /chatwar challenge Название поселения · accept ID · attack ID · status ID · finish ID")
        return
    action = parts[0].lower()
    uid = message.from_user.id
    if action in {"challenge", "вызов"} and len(parts) > 1:
        target_query = " ".join(parts[1:])
        async with SessionFactory() as session:
            try:
                text, war_id, target_chat_id = await settlement_war_challenge(session, message.chat.id, uid, target_query)
                await session.commit()
            except GameError as exc:
                await session.rollback()
                await message.answer(f"⚠️ {_safe(str(exc))}")
                return
        await message.answer(_safe(text))
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⚔️ Принять войну", callback_data=f"ngs:war:accept:{war_id}")]])
        try:
            await message.bot.send_message(target_chat_id, _safe(text + f"\nПринять: /chatwar accept {war_id}"), reply_markup=kb)
        except Exception:
            pass
    elif action == "accept" and len(parts) > 1 and parts[1].isdigit():
        await _accept_chatwar(message, int(parts[1]))
    elif action == "attack" and len(parts) > 1 and parts[1].isdigit():
        async with SessionFactory() as session:
            try:
                text, row = await settlement_war_attack(session, message.chat.id, uid, int(parts[1]))
                war_id = row.id
                await session.commit()
            except GameError as exc:
                await session.rollback()
                await message.answer(f"⚠️ {_safe(str(exc))}")
                return
        await message.answer(_safe(text))
        await _refresh_war_boards(message.bot, war_id)
    elif action == "status" and len(parts) > 1 and parts[1].isdigit():
        await _run(message, lambda s: _war_text_only(s, int(parts[1])))
    elif action == "finish" and len(parts) > 1 and parts[1].isdigit():
        await _finish_chatwar(message, int(parts[1]))


async def _war_text_only(session, war_id: int) -> str:
    text, _row = await settlement_war_status(session, war_id)
    return text


async def _accept_chatwar(message: Message, war_id: int) -> None:
    async with SessionFactory() as session:
        try:
            text, row = await settlement_war_accept(session, message.chat.id, message.from_user.id, war_id)
            attacker_chat, defender_chat = row.attacker_chat_id, row.defender_chat_id
            await session.commit()
        except GameError as exc:
            await session.rollback()
            await message.answer(f"⚠️ {_safe(str(exc))}")
            return
    amsg = dmsg = None
    try:
        amsg = await message.bot.send_message(attacker_chat, _safe(text))
    except Exception:
        pass
    try:
        dmsg = await message.bot.send_message(defender_chat, _safe(text))
    except Exception:
        pass
    await _set_war_board_ids(war_id, amsg.message_id if amsg else None, dmsg.message_id if dmsg else None)


async def _finish_chatwar(message: Message, war_id: int) -> None:
    async with SessionFactory() as session:
        try:
            text, row, _participant_chats = await settlement_war_finish(session, war_id)
            targets = await world_broadcast_targets(session, "war")
            await session.commit()
        except GameError as exc:
            await session.rollback()
            await message.answer(f"⚠️ {_safe(str(exc))}")
            return
    await _refresh_war_boards(message.bot, row.id)
    for chat_id in targets:
        try:
            await message.bot.send_message(chat_id, _safe("📰 <b>Новости мира шиноби</b>\n" + text))
        except Exception:
            pass


@router.callback_query(F.data.startswith("ngs:war:accept:"))
async def chatwar_callback(callback: CallbackQuery) -> None:
    if not callback.message or callback.message.chat.type not in {ChatType.GROUP, ChatType.SUPERGROUP}:
        await callback.answer("Принять войну нужно в группе-защитнике.", show_alert=True)
        return
    raw = (callback.data or "").rsplit(":", 1)[-1]
    if not raw.isdigit():
        return
    # The service additionally checks that the user owns the defender settlement.
    class Proxy:
        pass
    proxy = Proxy()
    proxy.bot = callback.bot
    proxy.chat = callback.message.chat
    proxy.from_user = callback.from_user
    proxy.answer = callback.message.answer
    await _accept_chatwar(proxy, int(raw))
    await callback.answer("Война началась")


# ---------------------------------------------------------------------------
# Mail, alliances, tournaments and rankings
# ---------------------------------------------------------------------------


@router.message(Command("nmail", "shinobi_mail"))
async def mail_handler(message: Message, command: CommandObject) -> None:
    raw = (command.args or "").strip()
    parts = raw.split()
    uid = message.from_user.id
    if not parts or parts[0].lower() in {"inbox", "входящие"}:
        await _run(message, lambda s: mail_inbox(s, uid))
        return
    anonymous = parts[0].lower() in {"anon", "anonymous", "анон"}
    action = "send" if anonymous else parts[0].lower()
    start = 1
    if action not in {"send", "отправить"}:
        await message.answer("✉️ /nmail send USER_ID текст · ответом: /nmail send текст · /nmail anon USER_ID текст · /nmail inbox")
        return
    target, idx = _target_from(parts, message, start)
    if target is None:
        await message.answer("✉️ Укажите USER_ID или ответьте на сообщение получателя.")
        return
    body = " ".join(parts[idx:])
    async with SessionFactory() as session:
        try:
            text, _mail_id = await mail_send(session, uid, target, body, anonymous)
            await session.commit()
        except GameError as exc:
            await session.rollback()
            await message.answer(f"⚠️ {_safe(str(exc))}")
            return
    await message.answer(_safe(text))
    await _try_dm(message.bot, target, "✉️ Вам пришло новое письмо шиноби. /nmail inbox")


@router.message(Command("alliance"))
async def alliance_handler(message: Message, command: CommandObject) -> None:
    raw = (command.args or "").strip()
    parts = raw.split()
    uid = message.from_user.id
    if not parts or parts[0].lower() in {"status", "статус"}:
        await _run(message, lambda s: alliance_text(s, uid))
    elif parts[0].lower() == "create" and len(parts) > 1:
        await _run(message, lambda s: alliance_create(s, uid, raw.split(None, 1)[1]))
    elif parts[0].lower() == "invite" and len(parts) > 1:
        await _run(message, lambda s: alliance_invite(s, uid, raw.split(None, 1)[1]))
    elif parts[0].lower() == "accept" and len(parts) > 1 and parts[1].isdigit():
        await _run(message, lambda s: alliance_accept(s, uid, int(parts[1])))
    elif parts[0].lower() == "war" and len(parts) > 1:
        await _run(message, lambda s: alliance_war(s, uid, raw.split(None, 1)[1]))
    else:
        await message.answer("🏯 /alliance create Название · invite Клан · accept ID · war Альянс · status")


@router.message(Command("tournament"))
async def tournament_handler(message: Message, command: CommandObject) -> None:
    try:
        await _require_group(message)
    except GameError as exc:
        await message.answer(f"⚠️ {_safe(str(exc))}")
        return
    raw = (command.args or "").strip()
    parts = raw.split()
    uid = message.from_user.id
    if not parts:
        await message.answer("🏆 /tournament create 16 Название · /tournament join ID · /tournament start ID")
        return
    if parts[0].lower() == "create":
        max_players = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 16
        title_idx = 2 if len(parts) > 1 and parts[1].isdigit() else 1
        title = " ".join(parts[title_idx:]) or "Турнир шиноби"
        await _run(message, lambda s: _tournament_create_text(s, message.chat.id, uid, title, max_players))
    elif parts[0].lower() == "join" and len(parts) > 1 and parts[1].isdigit():
        await _run(message, lambda s: tournament_join(s, message.chat.id, uid, int(parts[1])))
    elif parts[0].lower() == "start" and len(parts) > 1 and parts[1].isdigit():
        await _run(message, lambda s: tournament_start(s, message.chat.id, uid, int(parts[1])))


async def _tournament_create_text(session, chat_id: int, uid: int, title: str, max_players: int) -> str:
    text, _tid = await tournament_create(session, chat_id, uid, title, max_players)
    return text


@router.message(Command("mmo_top", "server_top"))
async def mmo_top_handler(message: Message) -> None:
    await _run(message, mmo_top_text)



@router.message(Command("clanbase"))
async def clanbase_handler(message: Message, command: CommandObject) -> None:
    parts = (command.args or "").split()
    if len(parts) >= 2 and parts[0].lower() in {"upgrade", "улучшить"}:
        await _run(message, lambda s: clan_base_upgrade(s, message.from_user.id, parts[1]))
    else:
        await _run(message, lambda s: clan_base_text(s, message.from_user.id))


@router.message(Command("clan_history"))
async def clan_history_handler(message: Message) -> None:
    await _run(message, lambda s: clan_history_text(s, message.from_user.id))


@router.message(Command("chatmission"))
async def chatmission_handler(message: Message, command: CommandObject) -> None:
    try:
        await _require_group(message)
    except GameError as exc:
        await message.answer(f"⚠️ {_safe(str(exc))}")
        return
    action = (command.args or "status").strip().lower()
    if action in {"start", "начать"}:
        await _run(message, lambda s: settlement_mission_start(s, message.chat.id, message.from_user.id))
    elif action in {"join", "участвовать", "вступить"}:
        await _run(message, lambda s: settlement_mission_join(s, message.chat.id, message.from_user.id))
    else:
        await _run(message, lambda s: settlement_mission_status(s, message.chat.id))


@router.callback_query(F.data.startswith("ngs:marry:accept:"))
async def marry_callback(callback: CallbackQuery) -> None:
    raw = (callback.data or "").rsplit(":", 1)[-1]
    if not raw.isdigit():
        await callback.answer("Некорректное предложение", show_alert=True)
        return
    async with SessionFactory() as session:
        try:
            text, partner_id = await marriage_accept(session, callback.from_user.id, int(raw))
            await session.commit()
        except GameError as exc:
            await session.rollback()
            await callback.answer(str(exc), show_alert=True)
            return
    await callback.answer("Союз заключён")
    if callback.message:
        await callback.message.edit_text(_safe(text))
    await _try_dm(callback.bot, partner_id, text)


@router.message(Command("worldnotice"))
async def worldnotice_handler(message: Message, command: CommandObject) -> None:
    # This is intentionally restricted to AniGuard administrators. It is the
    # manual control for global Telegram announcements; automatic systems can
    # use the same target resolver.
    from app.config import get_settings

    settings = get_settings()
    if message.from_user.id not in set(settings.admin_ids):
        await message.answer("⛔ Команда доступна только администраторам AniGuard.")
        return
    raw = (command.args or "").strip()
    head, sep, body = raw.partition(" ")
    if not sep:
        await message.answer("🌍 /worldnotice war|raid|achievement|village|world|market Текст")
        return
    async with SessionFactory() as session:
        try:
            text, targets = await global_notice_text(session, head, body)
            await session.commit()
        except GameError as exc:
            await session.rollback()
            await message.answer(f"⚠️ {_safe(str(exc))}")
            return
    delivered = 0
    for chat_id in targets:
        try:
            await message.bot.send_message(chat_id, _safe(text))
            delivered += 1
        except Exception:
            pass
    await message.answer(f"🌍 Глобальное уведомление отправлено: {delivered}/{len(targets)} чатов.")


# ---------------------------------------------------------------------------
# Natural "Наруто, ..." commands for Telegram groups and DMs
# ---------------------------------------------------------------------------


_NARUTO_PREFIX = re.compile(r"^\s*наруто\s*[,：:]?\s*(.+?)\s*$", re.IGNORECASE | re.DOTALL)


@router.message(F.text.regexp(_NARUTO_PREFIX))
async def naruto_address_handler(message: Message) -> None:
    match = _NARUTO_PREFIX.match(message.text or "")
    if not match or not message.from_user:
        return
    raw = match.group(1).strip()
    low = raw.casefold()
    uid = message.from_user.id

    if low in {"профиль", "мой профиль"}:
        target = _reply_target(message) or uid
        await _run(message, lambda s: public_profile_text(s, uid, target))
        return
    if low in {"друзья", "друг"}:
        await _run(message, lambda s: friends_text(s, uid))
        return
    if low in {"клан", "мой клан"}:
        await _run(message, lambda s: clan_text(s, uid))
        return
    if low in {"поселение", "чат", "поселение чата"} and _is_group(message):
        await _run(message, lambda s: settlement_status(s, message.chat.id))
        return
    if low in {"рейтинг", "топ", "мировой рейтинг"}:
        await _run(message, mmo_top_text)
        return
    if low in {"почта", "письма"}:
        await _run(message, lambda s: mail_inbox(s, uid))
        return
    if low in {"наставник", "ученики"}:
        await _run(message, lambda s: mentorship_text(s, uid))
        return
    if low in {"дуэль", "вызвать на дуэль"}:
        target = _reply_target(message)
        if target is None:
            await message.answer("⚔️ Ответьте этой фразой на сообщение соперника: <b>Наруто, дуэль</b>")
            return
        async with SessionFactory() as session:
            try:
                text, duel_id = await duel_challenge(session, uid, target, message.chat.id, 0)
                await session.commit()
            except GameError as exc:
                await session.rollback()
                await message.answer(f"⚠️ {_safe(str(exc))}")
                return
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⚔️ Принять", callback_data=f"ngs:duel:accept:{duel_id}")]])
        await message.answer(_safe(text), reply_markup=kb)
        return
    if low in {"дружить", "добавить в друзья"}:
        target = _reply_target(message)
        if target is None:
            await message.answer("🤝 Ответьте на сообщение игрока: <b>Наруто, дружить</b>")
            return
        async with SessionFactory() as session:
            try:
                text, req_id = await friend_request(session, uid, target)
                await session.commit()
            except GameError as exc:
                await session.rollback()
                await message.answer(f"⚠️ {_safe(str(exc))}")
                return
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✅ Принять", callback_data=f"ngs:friend:accept:{req_id}")]])
        await message.answer(_safe(text), reply_markup=kb)
        return
    if low in {"зарегистрировать чат", "создать поселение"} and _is_group(message):
        try:
            await _require_chat_creator(message)
        except GameError as exc:
            await message.answer(f"⚠️ {_safe(str(exc))}")
            return
        await _run(
            message,
            lambda s: settlement_register(s, message.chat.id, message.chat.title or "Поселение шиноби", message.chat.username, uid, None),
        )
        return
