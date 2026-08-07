from __future__ import annotations

import html
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import select

from app.db import SessionFactory
from app.models import Membership
from app.roles import normalize_penalty_status
from app.services import active_restrictions, refresh_membership_state

from .content import BOSSES, CRAFT_RECIPES, ITEMS, PROFESSIONS, SUMMONS, TECHNIQUES, VILLAGES
from .models import NinjaBattle
from .service import (
    GameError,
    active_battle_text,
    arena_match,
    battle_action,
    biju_train,
    bingo_text,
    cards_text,
    choose_mentor,
    choose_profession,
    claim_daily,
    clan_donate,
    clan_text,
    contract_summon,
    craft,
    create_clan,
    create_profile,
    draw_card,
    exam,
    explore,
    equip_item,
    unequip_item,
    get_profile,
    home_upgrade,
    inventory_text,
    join_clan,
    learn_technique,
    market_buy,
    market_list,
    market_text,
    mentor_training,
    profile_text,
    raid_attack,
    require_profile,
    run_mission,
    start_battle,
    start_story,
    story_text,
    techniques_text,
    toggle_nukenin,
    top_text,
    train,
    upgrade_item,
    world_text,
)

router = Router(name="naruto-rpg")
_pending_names: dict[int, str] = {}


def _safe(text: str) -> str:
    return (
        html.escape(text, quote=False)
        .replace("&lt;b&gt;", "<b>")
        .replace("&lt;/b&gt;", "</b>")
        .replace("&lt;code&gt;", "<code>")
        .replace("&lt;/code&gt;", "</code>")
    )


def _menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🥷 Профиль", callback_data="ng:menu:profile"),
                InlineKeyboardButton(text="🎁 Ежедневная", callback_data="ng:menu:daily"),
            ],
            [
                InlineKeyboardButton(text="📜 Миссия", callback_data="ng:menu:mission"),
                InlineKeyboardButton(text="🗺 Сюжет", callback_data="ng:menu:story"),
            ],
            [
                InlineKeyboardButton(text="⚔️ Бой", callback_data="ng:menu:battle"),
                InlineKeyboardButton(text="🏆 Арена", callback_data="ng:menu:arena"),
            ],
            [
                InlineKeyboardButton(text="📚 Техники", callback_data="ng:menu:techniques"),
                InlineKeyboardButton(text="🎴 Карточки", callback_data="ng:menu:cards"),
            ],
            [
                InlineKeyboardButton(text="🎒 Инвентарь", callback_data="ng:menu:inventory"),
                InlineKeyboardButton(text="🌍 Мир", callback_data="ng:menu:world"),
            ],
            [
                InlineKeyboardButton(text="👥 Клан", callback_data="ng:menu:clan"),
                InlineKeyboardButton(text="👹 Рейд", callback_data="ng:menu:raid"),
            ],
        ]
    )


def _village_keyboard() -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for key, data in VILLAGES.items():
        rows.append([InlineKeyboardButton(text=str(data["name"]), callback_data=f"ng:create:{key}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _training_keyboard() -> InlineKeyboardMarkup:
    items = [
        ("ninjutsu", "🔥 Ниндзюцу"),
        ("taijutsu", "🥋 Тайдзюцу"),
        ("genjutsu", "👁 Гендзюцу"),
        ("defense", "🛡 Защита"),
        ("speed", "💨 Скорость"),
        ("chakra_control", "🔵 Контроль чакры"),
    ]
    rows = [[InlineKeyboardButton(text=label, callback_data=f"ng:train:{key}")] for key, label in items]
    rows.append([InlineKeyboardButton(text="⬅️ Меню", callback_data="ng:menu:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _profession_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=str(data["name"]), callback_data=f"ng:profession:{key}")]
        for key, data in PROFESSIONS.items()
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _mentor_keyboard() -> InlineKeyboardMarkup:
    mentors = {
        "iruka": "Ирука",
        "asuma": "Асума",
        "kurenai": "Куренай",
        "kakashi": "Какаши",
        "guy": "Гай",
        "yamato": "Ямато",
        "jiraiya": "Джирайя",
        "tsunade": "Цунаде",
        "orochimaru": "Орочимару",
    }
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=name, callback_data=f"ng:mentor:{key}")]
            for key, name in mentors.items()
        ]
    )


def _summon_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=str(data), callback_data=f"ng:summon_contract:{key}")]
            for key, data in SUMMONS.items()
        ]
    )


def _battle_keyboard(state: dict[str, Any]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(text="👊 Атака", callback_data="ng:battle:attack"),
            InlineKeyboardButton(text="🛡 Защита", callback_data="ng:battle:defend"),
            InlineKeyboardButton(text="🔵 Чакра", callback_data="ng:battle:focus"),
        ]
    ]
    entries = list((state.get("player") or {}).get("techniques") or [])
    buttons: list[InlineKeyboardButton] = []
    for entry in entries:
        key = str(entry.get("key") or "")
        tech = TECHNIQUES.get(key)
        if key == "basic_strike":
            continue
        if tech:
            label = tech.name
        else:
            label = str(((state.get("player") or {}).get("custom_techniques") or {}).get(key, {}).get("name") or key)
        buttons.append(InlineKeyboardButton(text=label[:28], callback_data=f"ng:battle:tech:{key}"))
    for index in range(0, min(len(buttons), 8), 2):
        rows.append(buttons[index:index + 2])
    rows.append([
        InlineKeyboardButton(text="💊 Аптечка", callback_data="ng:battle:item:medkit"),
        InlineKeyboardButton(text="🔵 Пилюля", callback_data="ng:battle:item:chakra_pill"),
        InlineKeyboardButton(text="💥 Печать", callback_data="ng:battle:item:explosive_tag"),
    ])
    rows.append([InlineKeyboardButton(text="📊 Обновить", callback_data="ng:battle:show")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _boss_keyboard() -> InlineKeyboardMarkup:
    preferred = ["rogue", "zabuza", "deidara", "sasori", "pain", "obito", "madara", "kaguya", "zero"]
    rows: list[list[InlineKeyboardButton]] = []
    for key in preferred:
        boss = BOSSES.get(key)
        if boss:
            rows.append([InlineKeyboardButton(text=f"⚔️ {boss['name']}", callback_data=f"ng:start_boss:{key}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _callback_allowed(callback: CallbackQuery) -> bool:
    if not callback.from_user or not callback.message:
        return False
    chat = callback.message.chat
    if chat.type not in {ChatType.GROUP, ChatType.SUPERGROUP}:
        return True
    async with SessionFactory() as session:
        membership = await session.scalar(
            select(Membership).where(
                Membership.chat_id == chat.id,
                Membership.user_id == callback.from_user.id,
            )
        )
        if membership is not None:
            await refresh_membership_state(session, membership)
            if normalize_penalty_status(membership.penalty_status) != "none":
                await session.commit()
                await callback.answer("Игровые действия временно недоступны из-за штрафного статуса.", show_alert=True)
                return False
        restrictions = await active_restrictions(session, chat_id=chat.id, user_id=callback.from_user.id)
        await session.commit()
        if "commands" in restrictions:
            await callback.answer("Для вас временно заблокировано использование команд.", show_alert=True)
            return False
    return True


async def _with_session(
    user_id: int,
    operation: Callable[[Any], Awaitable[str]],
) -> str:
    async with SessionFactory() as session:
        try:
            result = await operation(session)
            await session.commit()
            return result
        except GameError:
            await session.rollback()
            raise


async def _answer_game(message: Message, operation: Callable[[Any], Awaitable[str]], *, markup: InlineKeyboardMarkup | None = None) -> None:
    if not message.from_user:
        return
    try:
        text = await _with_session(message.from_user.id, operation)
    except GameError as exc:
        await message.answer(f"⚠️ {_safe(str(exc))}", reply_markup=markup)
        return
    await message.answer(_safe(text), reply_markup=markup)


async def _edit_or_answer(callback: CallbackQuery, text: str, markup: InlineKeyboardMarkup | None = None) -> None:
    if not callback.message:
        return
    try:
        await callback.message.edit_text(_safe(text), reply_markup=markup)
    except Exception:
        await callback.message.answer(_safe(text), reply_markup=markup)


@router.message(Command("ninja", "shinobi", "naruto_game"))
async def ninja_menu(message: Message) -> None:
    if not message.from_user:
        return
    async with SessionFactory() as session:
        profile = await get_profile(session, message.from_user.id)
    if profile is None:
        await message.answer(
            "🥷 Naruto RPG ещё не создан.\n\n"
            "Используйте <code>/ninja_create Имя</code>, затем выберите скрытую деревню.",
        )
        return
    await message.answer(_safe(profile_text(profile)), reply_markup=_menu_keyboard())


@router.message(Command("ninja_create"))
async def ninja_create(message: Message, command: CommandObject) -> None:
    if not message.from_user:
        return
    name = (command.args or message.from_user.first_name or "Шиноби").strip()
    if len(name) < 2 or len(name) > 32:
        await message.answer("Имя шиноби должно содержать от 2 до 32 символов.")
        return
    async with SessionFactory() as session:
        if await get_profile(session, message.from_user.id):
            await message.answer("У вас уже есть персонаж. Откройте его командой /ninja.")
            return
    _pending_names[message.from_user.id] = name
    await message.answer(
        f"🥷 Имя: <b>{html.escape(name)}</b>\n\nВыберите деревню:",
        reply_markup=_village_keyboard(),
    )


@router.callback_query(F.data.startswith("ng:create:"))
async def create_callback(callback: CallbackQuery) -> None:
    if not await _callback_allowed(callback) or not callback.from_user:
        return
    village = (callback.data or "").split(":", 2)[-1]
    name = _pending_names.pop(callback.from_user.id, callback.from_user.first_name or "Шиноби")
    try:
        async with SessionFactory() as session:
            profile = await create_profile(session, callback.from_user.id, name, village)
            await session.commit()
        await _edit_or_answer(callback, profile_text(profile), _menu_keyboard())
        await callback.answer("Путь шиноби начался!")
    except GameError as exc:
        await callback.answer(str(exc), show_alert=True)


@router.message(Command("nprofile", "ninja_profile"))
async def profile_handler(message: Message) -> None:
    await _answer_game(message, lambda s: _profile_op(s, message.from_user.id), markup=_menu_keyboard())


async def _profile_op(session: Any, user_id: int) -> str:
    return profile_text(await require_profile(session, user_id))


@router.message(Command("daily"))
async def daily_handler(message: Message) -> None:
    await _answer_game(message, lambda s: claim_daily(s, message.from_user.id), markup=_menu_keyboard())


@router.message(Command("mission"))
async def mission_handler(message: Message, command: CommandObject) -> None:
    key = (command.args or "").strip().lower() or None
    await _answer_game(message, lambda s: run_mission(s, message.from_user.id, key), markup=_menu_keyboard())


@router.message(Command("train"))
async def train_handler(message: Message, command: CommandObject) -> None:
    stat = (command.args or "").strip().lower()
    if not stat:
        await message.answer("🥋 Выберите направление тренировки:", reply_markup=_training_keyboard())
        return
    await _answer_game(message, lambda s: train(s, message.from_user.id, stat), markup=_training_keyboard())


@router.callback_query(F.data.startswith("ng:train:"))
async def train_callback(callback: CallbackQuery) -> None:
    if not await _callback_allowed(callback) or not callback.from_user:
        return
    stat = (callback.data or "").split(":", 2)[-1]
    try:
        text = await _with_session(callback.from_user.id, lambda s: train(s, callback.from_user.id, stat))
        await _edit_or_answer(callback, text, _training_keyboard())
        await callback.answer()
    except GameError as exc:
        await callback.answer(str(exc), show_alert=True)


@router.message(Command("techniques", "jutsu"))
async def techniques_handler(message: Message) -> None:
    await _answer_game(message, lambda s: techniques_text(s, message.from_user.id), markup=_menu_keyboard())


@router.message(Command("learn"))
async def learn_handler(message: Message, command: CommandObject) -> None:
    key = (command.args or "").strip().lower()
    if not key:
        await message.answer("Использование: <code>/learn rasengan</code>")
        return
    await _answer_game(message, lambda s: learn_technique(s, message.from_user.id, key), markup=_menu_keyboard())


@router.message(Command("battle"))
async def battle_handler(message: Message, command: CommandObject) -> None:
    if not message.from_user:
        return
    boss_key = (command.args or "rogue").strip().lower()
    if boss_key not in BOSSES:
        await message.answer("Выберите противника:", reply_markup=_boss_keyboard())
        return
    try:
        async with SessionFactory() as session:
            battle = await start_battle(session, message.from_user.id, boss_key)
            await session.commit()
        await message.answer(_safe(active_battle_from_state(battle.state)), reply_markup=_battle_keyboard(battle.state))
    except GameError as exc:
        await message.answer(f"⚠️ {_safe(str(exc))}")


def active_battle_from_state(state: dict[str, Any]) -> str:
    from .engine import battle_text
    return battle_text(state)


@router.callback_query(F.data.startswith("ng:start_boss:"))
async def start_boss_callback(callback: CallbackQuery) -> None:
    if not await _callback_allowed(callback) or not callback.from_user:
        return
    boss_key = (callback.data or "").split(":", 2)[-1]
    try:
        async with SessionFactory() as session:
            battle = await start_battle(session, callback.from_user.id, boss_key)
            await session.commit()
        await _edit_or_answer(callback, active_battle_from_state(battle.state), _battle_keyboard(battle.state))
        await callback.answer()
    except GameError as exc:
        await callback.answer(str(exc), show_alert=True)


@router.callback_query(F.data.startswith("ng:battle:"))
async def battle_callback(callback: CallbackQuery) -> None:
    if not await _callback_allowed(callback) or not callback.from_user:
        return
    raw = callback.data or ""
    action = raw.removeprefix("ng:battle:")
    if action == "show":
        async with SessionFactory() as session:
            battle = await session.get(NinjaBattle, callback.from_user.id)
        if battle is None:
            await callback.answer("Активного боя нет.", show_alert=True)
            return
        await _edit_or_answer(callback, active_battle_from_state(battle.state), _battle_keyboard(battle.state))
        await callback.answer()
        return
    if action.startswith("tech:") or action.startswith("item:"):
        action = action
    elif action not in {"attack", "defend", "focus"}:
        action = "attack"
    try:
        async with SessionFactory() as session:
            text, finished = await battle_action(session, callback.from_user.id, action)
            battle = await session.get(NinjaBattle, callback.from_user.id)
            state = dict(battle.state) if battle else None
            await session.commit()
        markup = _menu_keyboard() if finished or state is None else _battle_keyboard(state)
        await _edit_or_answer(callback, text, markup)
        await callback.answer("Бой завершён" if finished else "Ход выполнен")
    except GameError as exc:
        await callback.answer(str(exc), show_alert=True)


@router.message(Command("battle_status"))
async def battle_status_handler(message: Message) -> None:
    if not message.from_user:
        return
    async with SessionFactory() as session:
        text = await active_battle_text(session, message.from_user.id)
        battle = await session.get(NinjaBattle, message.from_user.id)
    if not text or not battle:
        await message.answer("Активного боя нет. Начните: <code>/battle rogue</code>")
        return
    await message.answer(_safe(text), reply_markup=_battle_keyboard(battle.state))


@router.message(Command("cards"))
async def cards_handler(message: Message) -> None:
    await _answer_game(message, lambda s: cards_text(s, message.from_user.id), markup=_menu_keyboard())


@router.message(Command("summon_card", "gacha"))
async def draw_card_handler(message: Message) -> None:
    await _answer_game(message, lambda s: draw_card(s, message.from_user.id), markup=_menu_keyboard())


@router.message(Command("inventory", "ninventory"))
async def inventory_handler(message: Message) -> None:
    await _answer_game(message, lambda s: inventory_text(s, message.from_user.id), markup=_menu_keyboard())


@router.message(Command("craft"))
async def craft_handler(message: Message, command: CommandObject) -> None:
    key = (command.args or "").strip().lower()
    if not key:
        lines = ["🔨 Рецепты:"] + [f"• <code>{k}</code> — {html.escape(str(ITEMS.get(k, {'name': k})['name']))}" for k in CRAFT_RECIPES]
        await message.answer("\n".join(lines))
        return
    await _answer_game(message, lambda s: craft(s, message.from_user.id, key), markup=_menu_keyboard())


@router.message(Command("item_upgrade"))
async def item_upgrade_handler(message: Message, command: CommandObject) -> None:
    key = (command.args or "").strip().lower()
    if not key:
        await message.answer("Использование: <code>/item_upgrade kunai</code>")
        return
    await _answer_game(message, lambda s: upgrade_item(s, message.from_user.id, key), markup=_menu_keyboard())


@router.message(Command("equip"))
async def equip_handler(message: Message, command: CommandObject) -> None:
    key = (command.args or "").strip().lower()
    if not key:
        await message.answer("🎒 /equip ITEM_KEY · пример: /equip kunai")
        return
    await _answer_game(message, lambda s: equip_item(s, message.from_user.id, key), markup=_menu_keyboard())


@router.message(Command("unequip"))
async def unequip_handler(message: Message, command: CommandObject) -> None:
    slot = (command.args or "").strip().lower()
    await _answer_game(message, lambda s: unequip_item(s, message.from_user.id, slot), markup=_menu_keyboard())


@router.message(Command("exam"))
async def exam_handler(message: Message) -> None:
    await _answer_game(message, lambda s: exam(s, message.from_user.id), markup=_menu_keyboard())


@router.message(Command("story"))
async def story_handler(message: Message, command: CommandObject) -> None:
    if (command.args or "").strip().lower() in {"start", "бой", "battle"}:
        if not message.from_user:
            return
        try:
            async with SessionFactory() as session:
                battle = await start_story(session, message.from_user.id)
                await session.commit()
            await message.answer(_safe(active_battle_from_state(battle.state)), reply_markup=_battle_keyboard(battle.state))
        except GameError as exc:
            await message.answer(f"⚠️ {_safe(str(exc))}")
        return
    await _answer_game(message, lambda s: story_text(s, message.from_user.id), markup=_menu_keyboard())


@router.message(Command("explore"))
async def explore_handler(message: Message) -> None:
    await _answer_game(message, lambda s: explore(s, message.from_user.id), markup=_menu_keyboard())


@router.message(Command("profession"))
async def profession_handler(message: Message, command: CommandObject) -> None:
    key = (command.args or "").strip().lower()
    if not key:
        await message.answer("Выберите профессию:", reply_markup=_profession_keyboard())
        return
    await _answer_game(message, lambda s: choose_profession(s, message.from_user.id, key), markup=_menu_keyboard())


@router.callback_query(F.data.startswith("ng:profession:"))
async def profession_callback(callback: CallbackQuery) -> None:
    if not await _callback_allowed(callback) or not callback.from_user:
        return
    key = (callback.data or "").split(":", 2)[-1]
    try:
        text = await _with_session(callback.from_user.id, lambda s: choose_profession(s, callback.from_user.id, key))
        await _edit_or_answer(callback, text, _menu_keyboard())
        await callback.answer()
    except GameError as exc:
        await callback.answer(str(exc), show_alert=True)


@router.message(Command("mentor"))
async def mentor_handler(message: Message, command: CommandObject) -> None:
    key = (command.args or "").strip().lower()
    if not key:
        await message.answer("Выберите наставника:", reply_markup=_mentor_keyboard())
        return
    await _answer_game(message, lambda s: choose_mentor(s, message.from_user.id, key), markup=_menu_keyboard())


@router.callback_query(F.data.startswith("ng:mentor:"))
async def mentor_callback(callback: CallbackQuery) -> None:
    if not await _callback_allowed(callback) or not callback.from_user:
        return
    key = (callback.data or "").split(":", 2)[-1]
    try:
        text = await _with_session(callback.from_user.id, lambda s: choose_mentor(s, callback.from_user.id, key))
        await _edit_or_answer(callback, text, _menu_keyboard())
        await callback.answer()
    except GameError as exc:
        await callback.answer(str(exc), show_alert=True)


@router.message(Command("mentor_train"))
async def mentor_train_handler(message: Message) -> None:
    await _answer_game(message, lambda s: mentor_training(s, message.from_user.id), markup=_menu_keyboard())


@router.message(Command("summoning"))
async def summoning_handler(message: Message, command: CommandObject) -> None:
    key = (command.args or "").strip().lower()
    if not key:
        await message.answer("Выберите контракт призыва:", reply_markup=_summon_keyboard())
        return
    await _answer_game(message, lambda s: contract_summon(s, message.from_user.id, key), markup=_menu_keyboard())


@router.callback_query(F.data.startswith("ng:summon_contract:"))
async def summoning_callback(callback: CallbackQuery) -> None:
    if not await _callback_allowed(callback) or not callback.from_user:
        return
    key = (callback.data or "").split(":", 2)[-1]
    try:
        text = await _with_session(callback.from_user.id, lambda s: contract_summon(s, callback.from_user.id, key))
        await _edit_or_answer(callback, text, _menu_keyboard())
        await callback.answer()
    except GameError as exc:
        await callback.answer(str(exc), show_alert=True)


@router.message(Command("home"))
async def home_handler(message: Message) -> None:
    await _answer_game(message, lambda s: home_upgrade(s, message.from_user.id), markup=_menu_keyboard())


@router.message(Command("nukenin"))
async def nukenin_handler(message: Message) -> None:
    await _answer_game(message, lambda s: toggle_nukenin(s, message.from_user.id), markup=_menu_keyboard())


@router.message(Command("bingo"))
async def bingo_handler(message: Message) -> None:
    await _answer_game(message, lambda s: bingo_text(s), markup=_menu_keyboard())


@router.message(Command("clan"))
async def clan_handler(message: Message, command: CommandObject) -> None:
    raw = (command.args or "").strip()
    head, _, tail = raw.partition(" ")
    action = head.lower()
    if action == "create" and tail:
        await _answer_game(message, lambda s: create_clan(s, message.from_user.id, tail.strip()), markup=_menu_keyboard())
    elif action == "join" and tail:
        await _answer_game(message, lambda s: join_clan(s, message.from_user.id, tail.strip()), markup=_menu_keyboard())
    elif action == "donate" and tail.isdigit():
        await _answer_game(message, lambda s: clan_donate(s, message.from_user.id, int(tail)), markup=_menu_keyboard())
    else:
        await _answer_game(message, lambda s: clan_text(s, message.from_user.id), markup=_menu_keyboard())


@router.message(Command("arena"))
async def arena_handler(message: Message) -> None:
    await _answer_game(message, lambda s: arena_match(s, message.from_user.id), markup=_menu_keyboard())


@router.message(Command("world"))
async def world_handler(message: Message) -> None:
    await _answer_game(message, lambda s: world_text(s), markup=_menu_keyboard())


@router.message(Command("raid"))
async def raid_handler(message: Message) -> None:
    await _answer_game(message, lambda s: raid_attack(s, message.from_user.id), markup=_menu_keyboard())


@router.message(Command("market"))
async def market_handler(message: Message, command: CommandObject) -> None:
    raw = (command.args or "").strip()
    parts = raw.split()
    if parts and parts[0].lower() == "buy" and len(parts) >= 2 and parts[1].isdigit():
        await _answer_game(message, lambda s: market_buy(s, message.from_user.id, int(parts[1])), markup=_menu_keyboard())
        return
    if parts and parts[0].lower() == "sell" and len(parts) >= 4 and parts[2].isdigit() and parts[3].isdigit():
        key, qty, price = parts[1].lower(), int(parts[2]), int(parts[3])
        await _answer_game(message, lambda s: market_list(s, message.from_user.id, key, qty, price), markup=_menu_keyboard())
        return
    await _answer_game(message, lambda s: market_text(s), markup=_menu_keyboard())


@router.message(Command("ninja_top"))
async def top_handler(message: Message) -> None:
    await _answer_game(message, lambda s: top_text(s), markup=_menu_keyboard())


@router.message(Command("biju_train"))
async def biju_handler(message: Message) -> None:
    await _answer_game(message, lambda s: biju_train(s, message.from_user.id), markup=_menu_keyboard())


@router.callback_query(F.data.startswith("ng:menu:"))
async def menu_callback(callback: CallbackQuery) -> None:
    if not await _callback_allowed(callback) or not callback.from_user:
        return
    section = (callback.data or "").split(":", 2)[-1]
    user_id = callback.from_user.id
    try:
        if section in {"home", "profile"}:
            text = await _with_session(user_id, lambda s: _profile_op(s, user_id))
            markup = _menu_keyboard()
        elif section == "daily":
            text = await _with_session(user_id, lambda s: claim_daily(s, user_id))
            markup = _menu_keyboard()
        elif section == "mission":
            text = await _with_session(user_id, lambda s: run_mission(s, user_id, None))
            markup = _menu_keyboard()
        elif section == "story":
            text = await _with_session(user_id, lambda s: story_text(s, user_id))
            markup = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⚔️ Начать главу", callback_data="ng:story:start")],
                [InlineKeyboardButton(text="⬅️ Меню", callback_data="ng:menu:home")],
            ])
        elif section == "battle":
            text = "⚔️ Выберите противника. Более сильные боссы рассчитаны на развитого шиноби."
            markup = _boss_keyboard()
        elif section == "arena":
            text = await _with_session(user_id, lambda s: arena_match(s, user_id))
            markup = _menu_keyboard()
        elif section == "techniques":
            text = await _with_session(user_id, lambda s: techniques_text(s, user_id))
            markup = _menu_keyboard()
        elif section == "cards":
            text = await _with_session(user_id, lambda s: cards_text(s, user_id))
            markup = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🎴 Призвать", callback_data="ng:card:draw")],
                [InlineKeyboardButton(text="⬅️ Меню", callback_data="ng:menu:home")],
            ])
        elif section == "inventory":
            text = await _with_session(user_id, lambda s: inventory_text(s, user_id))
            markup = _menu_keyboard()
        elif section == "world":
            text = await _with_session(user_id, lambda s: world_text(s))
            markup = _menu_keyboard()
        elif section == "clan":
            text = await _with_session(user_id, lambda s: clan_text(s, user_id))
            markup = _menu_keyboard()
        elif section == "raid":
            text = await _with_session(user_id, lambda s: raid_attack(s, user_id))
            markup = _menu_keyboard()
        else:
            text = "🥷 Naruto RPG"
            markup = _menu_keyboard()
        await _edit_or_answer(callback, text, markup)
        await callback.answer()
    except GameError as exc:
        await callback.answer(str(exc), show_alert=True)


@router.callback_query(F.data == "ng:story:start")
async def story_callback(callback: CallbackQuery) -> None:
    if not await _callback_allowed(callback) or not callback.from_user:
        return
    try:
        async with SessionFactory() as session:
            battle = await start_story(session, callback.from_user.id)
            await session.commit()
        await _edit_or_answer(callback, active_battle_from_state(battle.state), _battle_keyboard(battle.state))
        await callback.answer()
    except GameError as exc:
        await callback.answer(str(exc), show_alert=True)


@router.callback_query(F.data == "ng:card:draw")
async def card_draw_callback(callback: CallbackQuery) -> None:
    if not await _callback_allowed(callback) or not callback.from_user:
        return
    try:
        text = await _with_session(callback.from_user.id, lambda s: draw_card(s, callback.from_user.id))
        markup = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🎴 Ещё призыв", callback_data="ng:card:draw")],
            [InlineKeyboardButton(text="⬅️ Меню", callback_data="ng:menu:home")],
        ])
        await _edit_or_answer(callback, text, markup)
        await callback.answer()
    except GameError as exc:
        await callback.answer(str(exc), show_alert=True)
