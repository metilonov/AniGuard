from __future__ import annotations

import copy
import html
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, FSInputFile, InlineKeyboardButton, InlineKeyboardMarkup, Message
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

from .social import (
    alliance_text,
    clan_base_text,
    clan_history_text,
    friends_text,
    mail_inbox,
    mentorship_text,
    mmo_top_text,
    settlement_top,
)
from .advanced import (
    dynamic_mission_choose,
    dynamic_mission_create,
    dynamic_mission_prepare,
    dynamic_mission_text,
    ensure_world_event,
    legacy_text,
    path_text,
    recommendations_text,
    research_text,
    return_summary_text,
    territory_expedition,
    territory_map_text,
    world_event_participate,
    world_events_text,
)
from .extended import (
    bank_text,
    black_market_text,
    card_arena,
    card_team_text,
    chronicle_text,
    contracts_text,
)
from .v3 import (
    bijuu_text,
    election_status,
    government_text,
    newspaper_text,
    project_status,
    world_chronicle_text,
    world_pulse_text,
)
from .media import build_battle_animation, get_screen_media

from .v4 import (
    activity_digest_text,
    arena_center_text,
    cards_center_text,
    combat_readiness_text,
    command_center_text,
    daily_center_text,
    development_center_text,
    economy_center_text,
    hub_text,
    inventory_center_text,
    mission_center_text,
    mmo_v4_text,
    raid_center_text,
    social_center_text,
    world_center_text,
)
from .ui_v4 import (
    arena_menu as _arena_menu,
    battle_menu as _battle_menu,
    bijuu_menu as _bijuu_menu,
    cards_menu as _cards_menu,
    clan_menu as _clan_menu,
    craft_menu as _craft_menu,
    daily_menu as _daily_menu,
    economy_menu as _economy_menu,
    events_menu as _events_menu,
    government_menu as _government_menu,
    growth_menu as _growth_menu,
    hub_menu as _hub_menu,
    inventory_menu as _inventory_menu,
    main_menu as _main_menu,
    mission_menu as _mission_menu,
    mmo4_menu as _mmo4_menu,
    mmo_menu as _mmo_menu,
    newspaper_menu as _newspaper_menu,
    path_menu as _path_menu,
    profile_menu as _profile_menu,
    pulse_menu as _pulse_menu,
    recommend_menu as _recommend_menu,
    raid_menu as _raid_menu,
    social_menu as _social_menu,
    story_menu as _story_menu,
    techniques_menu as _techniques_menu,
    territory_menu as _territory_menu,
    world_menu as _world_menu,
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
    # MMO V4: compact root menu. Leaf screens use their own context keyboards.
    return _main_menu()


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
    rows.append([InlineKeyboardButton(text="⬅️ Развитие", callback_data="ng:hub:growth")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _profession_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=str(data["name"]), callback_data=f"ng:profession:{key}")]
        for key, data in PROFESSIONS.items()
    ]
    rows.append([InlineKeyboardButton(text="⬅️ Развитие", callback_data="ng:hub:growth")])
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
    rows = [
        [InlineKeyboardButton(text=name, callback_data=f"ng:mentor:{key}")]
        for key, name in mentors.items()
    ]
    rows.append([InlineKeyboardButton(text="⬅️ Развитие", callback_data="ng:hub:growth")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _summon_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=str(data), callback_data=f"ng:summon_contract:{key}")]
        for key, data in SUMMONS.items()
    ]
    rows.append([InlineKeyboardButton(text="⬅️ Развитие", callback_data="ng:hub:growth")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


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


def _screen_for_menu(section: str) -> str:
    mapping = {
        "home": "home",
        "profile": "profile",
        "daily": "profile",
        "mission": "mission",
        "story": "mission",
        "battle": "ready",
        "arena": "battle",
        "techniques": "growth",
        "cards": "cards",
        "inventory": "inventory",
        "world": "world",
        "clan": "clan",
        "raid": "raid",
        "social": "social",
        "mmo": "social",
        "path": "growth",
        "events": "world",
        "territory": "world",
        "recommend": "growth",
        "government": "world",
        "newspaper": "world",
        "mmo3": "world",
        "mmo4": "world",
    }
    return mapping.get(section, "home")


def _screen_for_hub(hub: str) -> str:
    mapping = {
        "shinobi": "profile",
        "activities": "mission",
        "world": "world",
        "social": "clan",
        "economy": "market",
        "growth": "growth",
    }
    return mapping.get(hub, "home")


def _screen_for_v4(domain: str, action: str) -> str:
    if domain == "profile":
        return "ready" if action == "ready" else "profile"
    if domain == "daily":
        return "profile"
    if domain in {"mission"}:
        return "mission"
    if domain in {"arena"}:
        return "battle"
    if domain in {"raid"}:
        return "raid"
    if domain in {"cards"}:
        return "cards"
    if domain in {"inventory"}:
        return "inventory"
    if domain in {"tech"}:
        return "growth"
    if domain in {"world", "event", "terr", "bijuu", "gov", "news", "mmo"}:
        return "world"
    if domain in {"clan", "social"}:
        return "clan"
    if domain in {"economy", "craft"}:
        return "market"
    if domain in {"growth", "path"}:
        return "growth"
    return "home"


async def _send_media_message(target: Message, user_id: int, text: str, markup: InlineKeyboardMarkup | None, screen: str) -> None:
    media_path, is_animation = await get_screen_media(user_id, screen)
    if is_animation:
        await target.answer_animation(FSInputFile(str(media_path)), caption=_safe(text), reply_markup=markup)
    else:
        await target.answer_photo(FSInputFile(str(media_path)), caption=_safe(text), reply_markup=markup)


async def _send_battle_message(
    target: Message,
    user_id: int,
    text: str,
    markup: InlineKeyboardMarkup | None,
    current_state: dict[str, Any],
    previous_state: dict[str, Any] | None = None,
    *,
    finished: bool = False,
) -> None:
    media_path = build_battle_animation(
        user_id,
        current_state=current_state,
        previous_state=previous_state,
        finished=finished,
    )
    if media_path.suffix.lower() == ".mp4":
        await target.answer_animation(FSInputFile(str(media_path)), caption=_safe(text), reply_markup=markup)
    else:
        await target.answer_photo(FSInputFile(str(media_path)), caption=_safe(text), reply_markup=markup)


async def _edit_battle_message(
    callback: CallbackQuery,
    text: str,
    markup: InlineKeyboardMarkup | None,
    current_state: dict[str, Any],
    previous_state: dict[str, Any] | None = None,
    *,
    finished: bool = False,
) -> None:
    if not callback.message or not callback.from_user:
        return
    try:
        await callback.message.delete()
    except Exception:
        pass
    await _send_battle_message(
        callback.message,
        callback.from_user.id,
        text,
        markup,
        current_state,
        previous_state,
        finished=finished,
    )


async def _answer_game(message: Message, operation: Callable[[Any], Awaitable[str]], *, markup: InlineKeyboardMarkup | None = None, screen: str | None = None) -> None:
    if not message.from_user:
        return
    try:
        text = await _with_session(message.from_user.id, operation)
    except GameError as exc:
        await message.answer(f"⚠️ {_safe(str(exc))}", reply_markup=markup)
        return
    if screen:
        await _send_media_message(message, message.from_user.id, text, markup, screen)
        return
    await message.answer(_safe(text), reply_markup=markup)


async def _edit_or_answer(callback: CallbackQuery, text: str, markup: InlineKeyboardMarkup | None = None, *, screen: str | None = None, user_id: int | None = None) -> None:
    if not callback.message:
        return
    if screen and user_id is not None:
        try:
            await callback.message.delete()
        except Exception:
            pass
        await _send_media_message(callback.message, user_id, text, markup, screen)
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
    async with SessionFactory() as session:
        text = await command_center_text(session, message.from_user.id)
    await _send_media_message(message, message.from_user.id, text, _menu_keyboard(), "home")


@router.message(Command("mmo4", "mmov4"))
async def mmo4_handler(message: Message) -> None:
    if not message.from_user:
        return
    await _answer_game(message, lambda s: mmo_v4_text(s, message.from_user.id), markup=_mmo4_menu(), screen="world")


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
        await _edit_or_answer(callback, profile_text(profile), _menu_keyboard(), screen="profile", user_id=callback.from_user.id)
        await callback.answer("Путь шиноби начался!")
    except GameError as exc:
        await callback.answer(str(exc), show_alert=True)


@router.message(Command("nprofile", "ninja_profile"))
async def profile_handler(message: Message) -> None:
    await _answer_game(message, lambda s: _profile_op(s, message.from_user.id), markup=_profile_menu(), screen="profile")


async def _profile_op(session: Any, user_id: int) -> str:
    return profile_text(await require_profile(session, user_id))


@router.message(Command("daily"))
async def daily_handler(message: Message) -> None:
    await _answer_game(message, lambda s: claim_daily(s, message.from_user.id), markup=_daily_menu(), screen="profile")


@router.message(Command("mission"))
async def mission_handler(message: Message, command: CommandObject) -> None:
    key = (command.args or "").strip().lower() or None
    await _answer_game(message, lambda s: run_mission(s, message.from_user.id, key), markup=_mission_menu(), screen="mission")


@router.message(Command("train"))
async def train_handler(message: Message, command: CommandObject) -> None:
    stat = (command.args or "").strip().lower()
    if not stat:
        await _send_media_message(message, message.from_user.id, "🥋 Выберите направление тренировки:", _training_keyboard(), "growth")
        return
    await _answer_game(message, lambda s: train(s, message.from_user.id, stat), markup=_training_keyboard(), screen="growth")


@router.callback_query(F.data.startswith("ng:train:"))
async def train_callback(callback: CallbackQuery) -> None:
    if not await _callback_allowed(callback) or not callback.from_user:
        return
    stat = (callback.data or "").split(":", 2)[-1]
    try:
        text = await _with_session(callback.from_user.id, lambda s: train(s, callback.from_user.id, stat))
        await _edit_or_answer(callback, text, _training_keyboard(), screen="growth", user_id=callback.from_user.id)
        await callback.answer()
    except GameError as exc:
        await callback.answer(str(exc), show_alert=True)


@router.message(Command("techniques", "jutsu"))
async def techniques_handler(message: Message) -> None:
    await _answer_game(message, lambda s: techniques_text(s, message.from_user.id), markup=_techniques_menu(), screen="growth")


@router.message(Command("learn"))
async def learn_handler(message: Message, command: CommandObject) -> None:
    key = (command.args or "").strip().lower()
    if not key:
        await message.answer("Использование: <code>/learn rasengan</code>")
        return
    await _answer_game(message, lambda s: learn_technique(s, message.from_user.id, key), markup=_techniques_menu(), screen="growth")


@router.message(Command("battle"))
async def battle_handler(message: Message, command: CommandObject) -> None:
    if not message.from_user:
        return
    boss_key = (command.args or "rogue").strip().lower()
    if boss_key not in BOSSES:
        await _send_media_message(message, message.from_user.id, "⚔️ Выберите противника:", _boss_keyboard(), "battle")
        return
    try:
        async with SessionFactory() as session:
            battle = await start_battle(session, message.from_user.id, boss_key)
            state = copy.deepcopy(battle.state)
            await session.commit()
        await _send_battle_message(
            message,
            message.from_user.id,
            active_battle_from_state(state),
            _battle_keyboard(state),
            state,
        )
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
            state = copy.deepcopy(battle.state)
            await session.commit()
        await _edit_battle_message(
            callback,
            active_battle_from_state(state),
            _battle_keyboard(state),
            state,
        )
        await callback.answer()
    except GameError as exc:
        await callback.answer(str(exc), show_alert=True)


@router.callback_query(F.data.startswith("ng:battle:"))
async def battle_callback(callback: CallbackQuery) -> None:
    if not await _callback_allowed(callback) or not callback.from_user:
        return
    raw = callback.data or ""
    action = raw.removeprefix("ng:battle:")
    uid = callback.from_user.id

    if action == "show":
        async with SessionFactory() as session:
            battle = await session.get(NinjaBattle, uid)
            state = copy.deepcopy(battle.state) if battle else None
        if state is None:
            await callback.answer("Активного боя нет.", show_alert=True)
            return
        await _edit_battle_message(
            callback,
            active_battle_from_state(state),
            _battle_keyboard(state),
            state,
        )
        await callback.answer()
        return

    if not (action.startswith("tech:") or action.startswith("item:")) and action not in {"attack", "defend", "focus"}:
        action = "attack"

    try:
        async with SessionFactory() as session:
            row = await session.get(NinjaBattle, uid)
            if row is None:
                raise GameError("Активного боя нет. /battle")
            previous_state = copy.deepcopy(row.state)
            text, finished = await battle_action(session, uid, action)
            # battle_action mutates the same ORM row. Even if it marks the row for deletion,
            # the final combat state remains available on this in-session object.
            current_state = copy.deepcopy(row.state)
            await session.commit()

        markup = _battle_menu() if finished else _battle_keyboard(current_state)
        await _edit_battle_message(
            callback,
            text,
            markup,
            current_state,
            previous_state,
            finished=finished,
        )
        await callback.answer("Бой завершён" if finished else "Ход выполнен")
    except GameError as exc:
        await callback.answer(str(exc), show_alert=True)


@router.message(Command("battle_status"))
async def battle_status_handler(message: Message) -> None:
    if not message.from_user:
        return
    async with SessionFactory() as session:
        battle = await session.get(NinjaBattle, message.from_user.id)
        state = copy.deepcopy(battle.state) if battle else None
    if state is None:
        await message.answer("Активного боя нет. Начните: <code>/battle rogue</code>")
        return
    await _send_battle_message(
        message,
        message.from_user.id,
        active_battle_from_state(state),
        _battle_keyboard(state),
        state,
    )


@router.message(Command("cards"))
async def cards_handler(message: Message) -> None:
    await _answer_game(message, lambda s: cards_text(s, message.from_user.id), markup=_cards_menu(), screen="cards")


@router.message(Command("summon_card", "gacha"))
async def draw_card_handler(message: Message) -> None:
    await _answer_game(message, lambda s: draw_card(s, message.from_user.id), markup=_cards_menu(), screen="cards")


@router.message(Command("inventory", "ninventory"))
async def inventory_handler(message: Message) -> None:
    await _answer_game(message, lambda s: inventory_text(s, message.from_user.id), markup=_inventory_menu(), screen="inventory")


@router.message(Command("craft"))
async def craft_handler(message: Message, command: CommandObject) -> None:
    key = (command.args or "").strip().lower()
    if not key:
        lines = ["🔨 Рецепты:"] + [f"• <code>{k}</code> — {html.escape(str(ITEMS.get(k, {'name': k})['name']))}" for k in CRAFT_RECIPES]
        await _send_media_message(message, message.from_user.id, "\n".join(lines), _craft_menu(), "craft")
        return
    await _answer_game(message, lambda s: craft(s, message.from_user.id, key), markup=_craft_menu(), screen="market")


@router.message(Command("item_upgrade"))
async def item_upgrade_handler(message: Message, command: CommandObject) -> None:
    key = (command.args or "").strip().lower()
    if not key:
        await message.answer("Использование: <code>/item_upgrade kunai</code>")
        return
    await _answer_game(message, lambda s: upgrade_item(s, message.from_user.id, key), markup=_inventory_menu(), screen="inventory")


@router.message(Command("equip"))
async def equip_handler(message: Message, command: CommandObject) -> None:
    key = (command.args or "").strip().lower()
    if not key:
        await message.answer("🎒 /equip ITEM_KEY · пример: /equip kunai")
        return
    await _answer_game(message, lambda s: equip_item(s, message.from_user.id, key), markup=_inventory_menu(), screen="inventory")


@router.message(Command("unequip"))
async def unequip_handler(message: Message, command: CommandObject) -> None:
    slot = (command.args or "").strip().lower()
    await _answer_game(message, lambda s: unequip_item(s, message.from_user.id, slot), markup=_inventory_menu(), screen="inventory")


@router.message(Command("exam"))
async def exam_handler(message: Message) -> None:
    await _answer_game(message, lambda s: exam(s, message.from_user.id), markup=_growth_menu(), screen="growth")


@router.message(Command("story"))
async def story_handler(message: Message, command: CommandObject) -> None:
    if (command.args or "").strip().lower() in {"start", "бой", "battle"}:
        if not message.from_user:
            return
        try:
            async with SessionFactory() as session:
                battle = await start_story(session, message.from_user.id)
                state = copy.deepcopy(battle.state)
                await session.commit()
            await _send_battle_message(
                message,
                message.from_user.id,
                active_battle_from_state(state),
                _battle_keyboard(state),
                state,
            )
        except GameError as exc:
            await message.answer(f"⚠️ {_safe(str(exc))}")
        return
    await _answer_game(message, lambda s: story_text(s, message.from_user.id), markup=_story_menu(), screen="mission")


@router.message(Command("explore"))
async def explore_handler(message: Message) -> None:
    await _answer_game(message, lambda s: explore(s, message.from_user.id), markup=_world_menu(), screen="world")


@router.message(Command("profession"))
async def profession_handler(message: Message, command: CommandObject) -> None:
    key = (command.args or "").strip().lower()
    if not key:
        await _send_media_message(message, message.from_user.id, "Выберите профессию:", _profession_keyboard(), "growth")
        return
    await _answer_game(message, lambda s: choose_profession(s, message.from_user.id, key), markup=_growth_menu(), screen="growth")


@router.callback_query(F.data.startswith("ng:profession:"))
async def profession_callback(callback: CallbackQuery) -> None:
    if not await _callback_allowed(callback) or not callback.from_user:
        return
    key = (callback.data or "").split(":", 2)[-1]
    try:
        text = await _with_session(callback.from_user.id, lambda s: choose_profession(s, callback.from_user.id, key))
        await _edit_or_answer(callback, text, _growth_menu(), screen="growth", user_id=callback.from_user.id)
        await callback.answer()
    except GameError as exc:
        await callback.answer(str(exc), show_alert=True)


@router.message(Command("mentor"))
async def mentor_handler(message: Message, command: CommandObject) -> None:
    key = (command.args or "").strip().lower()
    if not key:
        await _send_media_message(message, message.from_user.id, "Выберите наставника:", _mentor_keyboard(), "growth")
        return
    await _answer_game(message, lambda s: choose_mentor(s, message.from_user.id, key), markup=_growth_menu(), screen="growth")


@router.callback_query(F.data.startswith("ng:mentor:"))
async def mentor_callback(callback: CallbackQuery) -> None:
    if not await _callback_allowed(callback) or not callback.from_user:
        return
    key = (callback.data or "").split(":", 2)[-1]
    try:
        text = await _with_session(callback.from_user.id, lambda s: choose_mentor(s, callback.from_user.id, key))
        await _edit_or_answer(callback, text, _growth_menu(), screen="growth", user_id=callback.from_user.id)
        await callback.answer()
    except GameError as exc:
        await callback.answer(str(exc), show_alert=True)


@router.message(Command("mentor_train"))
async def mentor_train_handler(message: Message) -> None:
    await _answer_game(message, lambda s: mentor_training(s, message.from_user.id), markup=_growth_menu(), screen="growth")


@router.message(Command("summoning"))
async def summoning_handler(message: Message, command: CommandObject) -> None:
    key = (command.args or "").strip().lower()
    if not key:
        await _send_media_message(message, message.from_user.id, "Выберите контракт призыва:", _summon_keyboard(), "growth")
        return
    await _answer_game(message, lambda s: contract_summon(s, message.from_user.id, key), markup=_growth_menu(), screen="growth")


@router.callback_query(F.data.startswith("ng:summon_contract:"))
async def summoning_callback(callback: CallbackQuery) -> None:
    if not await _callback_allowed(callback) or not callback.from_user:
        return
    key = (callback.data or "").split(":", 2)[-1]
    try:
        text = await _with_session(callback.from_user.id, lambda s: contract_summon(s, callback.from_user.id, key))
        await _edit_or_answer(callback, text, _growth_menu(), screen="growth", user_id=callback.from_user.id)
        await callback.answer()
    except GameError as exc:
        await callback.answer(str(exc), show_alert=True)


@router.message(Command("home"))
async def home_handler(message: Message) -> None:
    await _answer_game(message, lambda s: home_upgrade(s, message.from_user.id), markup=_profile_menu(), screen="profile")


@router.message(Command("nukenin"))
async def nukenin_handler(message: Message) -> None:
    await _answer_game(message, lambda s: toggle_nukenin(s, message.from_user.id), markup=_path_menu(), screen="growth")


@router.message(Command("bingo"))
async def bingo_handler(message: Message) -> None:
    await _answer_game(message, lambda s: bingo_text(s), markup=_mission_menu(), screen="mission")


@router.message(Command("clan"))
async def clan_handler(message: Message, command: CommandObject) -> None:
    raw = (command.args or "").strip()
    head, _, tail = raw.partition(" ")
    action = head.lower()
    if action == "create" and tail:
        await _answer_game(message, lambda s: create_clan(s, message.from_user.id, tail.strip()), markup=_clan_menu(), screen="clan")
    elif action == "join" and tail:
        await _answer_game(message, lambda s: join_clan(s, message.from_user.id, tail.strip()), markup=_clan_menu(), screen="clan")
    elif action == "donate" and tail.isdigit():
        await _answer_game(message, lambda s: clan_donate(s, message.from_user.id, int(tail)), markup=_clan_menu(), screen="clan")
    else:
        await _answer_game(message, lambda s: clan_text(s, message.from_user.id), markup=_clan_menu(), screen="clan")


@router.message(Command("arena"))
async def arena_handler(message: Message) -> None:
    await _answer_game(message, lambda s: arena_match(s, message.from_user.id), markup=_arena_menu(), screen="battle")


@router.message(Command("world"))
async def world_handler(message: Message) -> None:
    await _answer_game(message, lambda s: world_text(s), markup=_world_menu(), screen="world")


@router.message(Command("raid"))
async def raid_handler(message: Message) -> None:
    await _answer_game(message, lambda s: raid_attack(s, message.from_user.id), markup=_raid_menu(), screen="raid")


@router.message(Command("market"))
async def market_handler(message: Message, command: CommandObject) -> None:
    raw = (command.args or "").strip()
    parts = raw.split()
    if parts and parts[0].lower() == "buy" and len(parts) >= 2 and parts[1].isdigit():
        await _answer_game(message, lambda s: market_buy(s, message.from_user.id, int(parts[1])), markup=_economy_menu(), screen="market")
        return
    if parts and parts[0].lower() == "sell" and len(parts) >= 4 and parts[2].isdigit() and parts[3].isdigit():
        key, qty, price = parts[1].lower(), int(parts[2]), int(parts[3])
        await _answer_game(message, lambda s: market_list(s, message.from_user.id, key, qty, price), markup=_economy_menu(), screen="market")
        return
    await _answer_game(message, lambda s: market_text(s), markup=_economy_menu(), screen="market")


@router.message(Command("ninja_top"))
async def top_handler(message: Message) -> None:
    await _answer_game(message, lambda s: top_text(s), markup=_mmo_menu(), screen="clan")


@router.message(Command("biju_train"))
async def biju_handler(message: Message) -> None:
    await _answer_game(message, lambda s: biju_train(s, message.from_user.id), markup=_world_menu(), screen="world")


@router.callback_query(F.data.startswith("ng:hub:"))
async def hub_callback(callback: CallbackQuery) -> None:
    if not await _callback_allowed(callback) or not callback.from_user:
        return
    hub = (callback.data or "").split(":", 2)[-1]
    try:
        text = await _with_session(callback.from_user.id, lambda s: hub_text(s, callback.from_user.id, hub))
        await _edit_or_answer(callback, text, _hub_menu(hub), screen=_screen_for_hub(hub), user_id=callback.from_user.id)
        await callback.answer()
    except GameError as exc:
        await callback.answer(str(exc), show_alert=True)


@router.callback_query(F.data.startswith("ng:menu:"))
async def menu_callback(callback: CallbackQuery) -> None:
    if not await _callback_allowed(callback) or not callback.from_user:
        return
    section = (callback.data or "").split(":", 2)[-1]
    user_id = callback.from_user.id
    try:
        if section == "home":
            text = await _with_session(user_id, lambda s: command_center_text(s, user_id))
            markup = _menu_keyboard()
        elif section == "profile":
            text = await _with_session(user_id, lambda s: _profile_op(s, user_id))
            markup = _profile_menu()
        elif section == "daily":
            text = await _with_session(user_id, lambda s: daily_center_text(s, user_id))
            markup = _daily_menu()
        elif section == "mission":
            text = await _with_session(user_id, lambda s: mission_center_text(s, user_id))
            markup = _mission_menu()
        elif section == "story":
            text = await _with_session(user_id, lambda s: story_text(s, user_id))
            markup = _story_menu()
        elif section == "battle":
            text = await _with_session(user_id, lambda s: combat_readiness_text(s, user_id))
            markup = _battle_menu()
        elif section == "arena":
            text = await _with_session(user_id, lambda s: arena_center_text(s, user_id))
            markup = _arena_menu()
        elif section == "techniques":
            text = await _with_session(user_id, lambda s: techniques_text(s, user_id))
            markup = _techniques_menu()
        elif section == "cards":
            text = await _with_session(user_id, lambda s: cards_center_text(s, user_id))
            markup = _cards_menu()
        elif section == "inventory":
            text = await _with_session(user_id, lambda s: inventory_center_text(s, user_id))
            markup = _inventory_menu()
        elif section == "world":
            text = await _with_session(user_id, lambda s: world_center_text(s, user_id))
            markup = _world_menu()
        elif section == "clan":
            text = await _with_session(user_id, lambda s: clan_text(s, user_id))
            markup = _clan_menu()
        elif section == "raid":
            text = await _with_session(user_id, lambda s: raid_center_text(s, user_id))
            markup = _raid_menu()
        elif section == "social":
            text = await _with_session(user_id, lambda s: social_center_text(s, user_id))
            markup = _social_menu()
        elif section == "mmo":
            text = await _with_session(user_id, lambda s: mmo_top_text(s))
            markup = _mmo_menu()
        elif section == "path":
            text = await _with_session(user_id, lambda s: path_text(s, user_id))
            markup = _path_menu()
        elif section == "events":
            text = await _with_session(user_id, lambda s: world_events_text(s))
            markup = _events_menu()
        elif section == "territory":
            text = await _with_session(user_id, lambda s: territory_map_text(s))
            markup = _territory_menu()
        elif section == "recommend":
            text = await _with_session(user_id, lambda s: recommendations_text(s, user_id))
            markup = _recommend_menu()
        elif section == "government":
            text = await _with_session(user_id, lambda s: government_text(s, user_id))
            markup = _government_menu()
        elif section == "newspaper":
            text = await _with_session(user_id, lambda s: newspaper_text(s))
            markup = _newspaper_menu()
        elif section == "mmo3":
            text = await _with_session(user_id, lambda s: world_pulse_text(s, user_id))
            markup = _pulse_menu()
        elif section == "mmo4":
            text = await _with_session(user_id, lambda s: mmo_v4_text(s, user_id))
            markup = _mmo4_menu()
        else:
            text = await _with_session(user_id, lambda s: command_center_text(s, user_id))
            markup = _menu_keyboard()
        await _edit_or_answer(callback, text, markup, screen=_screen_for_menu(section), user_id=callback.from_user.id)
        await callback.answer()
    except GameError as exc:
        await callback.answer(str(exc), show_alert=True)


@router.callback_query(F.data.startswith("ng4:"))
async def v4_action_callback(callback: CallbackQuery) -> None:
    if not await _callback_allowed(callback) or not callback.from_user:
        return
    raw = callback.data or ""
    parts = raw.split(":")
    if len(parts) < 3:
        await callback.answer("Некорректное действие.", show_alert=True)
        return
    domain, action = parts[1], parts[2]
    uid = callback.from_user.id

    try:
        # Profile and daily actions
        if domain == "profile" and action == "ready":
            text = await _with_session(uid, lambda s: combat_readiness_text(s, uid))
            markup = _profile_menu()
        elif domain == "daily" and action == "claim":
            text = await _with_session(uid, lambda s: claim_daily(s, uid))
            markup = _daily_menu()
        elif domain == "daily" and action == "return":
            text = await _with_session(uid, lambda s: return_summary_text(s, uid))
            markup = _daily_menu()

        # Missions
        elif domain == "mission" and action == "classic":
            text = await _with_session(uid, lambda s: run_mission(s, uid, None))
            markup = _mission_menu()
        elif domain == "mission" and action == "create":
            text = await _with_session(uid, lambda s: dynamic_mission_create(s, uid))
            markup = _mission_menu()
        elif domain == "mission" and action == "status":
            text = await _with_session(uid, lambda s: dynamic_mission_text(s, uid))
            markup = _mission_menu()
        elif domain == "mission" and action == "prepare":
            text = await _with_session(uid, lambda s: dynamic_mission_prepare(s, uid))
            markup = _mission_menu()
        elif domain == "mission" and action in {"stealth", "assault", "negotiate"}:
            choice = {"stealth": "investigate", "assault": "direct", "negotiate": "negotiate"}[action]
            text = await _with_session(uid, lambda s: dynamic_mission_choose(s, uid, choice))
            markup = _mission_menu()

        # Arena / raid
        elif domain == "arena" and action == "match":
            text = await _with_session(uid, lambda s: arena_match(s, uid))
            markup = _arena_menu()
        elif domain == "arena" and action == "duel":
            text = (
                "🤺 <b>Дуэли игроков</b>\n\n"
                "В группе ответьте на сообщение соперника командой <code>/duel</code> или используйте существующую систему вызова. "
                "Ставки проходят через безопасный эскроу."
            )
            markup = _arena_menu()
        elif domain == "raid" and action == "attack":
            text = await _with_session(uid, lambda s: raid_attack(s, uid))
            markup = _raid_menu()

        # Cards
        elif domain == "cards" and action == "list":
            text = await _with_session(uid, lambda s: cards_text(s, uid))
            markup = _cards_menu()
        elif domain == "cards" and action == "team":
            text = await _with_session(uid, lambda s: card_team_text(s, uid))
            markup = _cards_menu()
        elif domain == "cards" and action == "arena":
            text = await _with_session(uid, lambda s: card_arena(s, uid))
            markup = _cards_menu()

        # Inventory / techniques
        elif domain == "inventory" and action == "list":
            text = await _with_session(uid, lambda s: inventory_text(s, uid))
            markup = _inventory_menu()
        elif domain == "tech" and action == "list":
            text = await _with_session(uid, lambda s: techniques_text(s, uid))
            markup = _techniques_menu()

        # World
        elif domain == "world" and action == "state":
            text = await _with_session(uid, lambda s: world_text(s))
            markup = _world_menu()
        elif domain == "event" and action == "join":
            async def _join_event(session: Any) -> str:
                row = await ensure_world_event(session)
                return await world_event_participate(session, uid, int(row.id))
            text = await _with_session(uid, _join_event)
            markup = _events_menu()
        elif domain == "terr" and action:
            text = await _with_session(uid, lambda s: territory_expedition(s, uid, action))
            markup = _territory_menu()
        elif domain == "bijuu" and action == "status":
            text = await _with_session(uid, lambda s: bijuu_text(s, uid))
            markup = _bijuu_menu()
        elif domain == "bijuu" and action == "train":
            text = await _with_session(uid, lambda s: biju_train(s, uid))
            markup = _bijuu_menu()

        # Clan / social
        elif domain == "clan" and action == "status":
            text = await _with_session(uid, lambda s: clan_text(s, uid))
            markup = _clan_menu()
        elif domain == "clan" and action == "base":
            text = await _with_session(uid, lambda s: clan_base_text(s, uid))
            markup = _clan_menu()
        elif domain == "clan" and action == "roles":
            text = (
                "🎖 <b>Роли клана</b>\n\n"
                "👑 Клан-лидер\n⚔️ Джонин-командир\n🕵 АНБУ-разведчик\n"
                "💰 Хранитель свитков и казны\n🥷 Элитный шиноби\n🎓 Генин\n\n"
                "Назначение ролей выполняется через <code>/clanroles</code>."
            )
            markup = _clan_menu()
        elif domain == "clan" and action == "alliance":
            text = await _with_session(uid, lambda s: alliance_text(s, uid))
            markup = _clan_menu()
        elif domain == "clan" and action == "history":
            text = await _with_session(uid, lambda s: clan_history_text(s, uid))
            markup = _clan_menu()
        elif domain == "social" and action == "friends":
            text = await _with_session(uid, lambda s: friends_text(s, uid))
            markup = _social_menu()
        elif domain == "social" and action == "mentor":
            text = await _with_session(uid, lambda s: mentorship_text(s, uid))
            markup = _social_menu()
        elif domain == "social" and action == "mail":
            text = await _with_session(uid, lambda s: mail_inbox(s, uid))
            markup = _social_menu()
        elif domain == "social" and action == "privacy":
            text = (
                "🔒 <b>Приватность профиля</b>\n\n"
                "Управляйте видимостью профиля и социальными запросами через <code>/privacy</code>. "
                "Игровая почта и разведка не раскрывают реальные личные данные Telegram."
            )
            markup = _social_menu()
        elif domain == "social" and action == "settlement":
            text = await _with_session(uid, lambda s: settlement_top(s))
            markup = _social_menu()

        # Economy
        elif domain == "economy" and action == "overview":
            text = await _with_session(uid, lambda s: economy_center_text(s, uid))
            markup = _economy_menu()
        elif domain == "economy" and action == "market":
            text = await _with_session(uid, lambda s: market_text(s))
            markup = _economy_menu()
        elif domain == "economy" and action == "craft":
            text = "🔨 <b>Крафт</b>\n\nВыберите рецепт. Материалы будут списаны только после выбора конкретного рецепта."
            markup = _craft_menu()
        elif domain == "economy" and action == "contracts":
            text = await _with_session(uid, lambda s: contracts_text(s))
            markup = _economy_menu()
        elif domain == "economy" and action == "bank":
            text = await _with_session(uid, lambda s: bank_text(s, uid))
            markup = _economy_menu()
        elif domain == "economy" and action == "black":
            text = await _with_session(uid, lambda s: black_market_text(s, uid))
            markup = _economy_menu()
        elif domain == "craft" and action:
            text = await _with_session(uid, lambda s: craft(s, uid, action))
            markup = _craft_menu()

        # Development
        elif domain == "growth" and action == "train":
            text = await _with_session(uid, lambda s: development_center_text(s, uid))
            markup = _training_keyboard()
        elif domain == "growth" and action == "exam":
            text = await _with_session(uid, lambda s: exam(s, uid))
            markup = _growth_menu()
        elif domain == "growth" and action == "mentor":
            text = "👤 <b>Выбор наставника</b>\n\nНаставник влияет на тренировки и персональные ветки. Выберите персонажа ниже."
            markup = _mentor_keyboard()
        elif domain == "growth" and action == "profession":
            text = "🛠 <b>Профессия шиноби</b>\n\nПрофессия открывает отдельное экономическое и ремесленное развитие."
            markup = _profession_keyboard()
        elif domain == "growth" and action == "research":
            text = await _with_session(uid, lambda s: research_text(s, uid))
            markup = _growth_menu()
        elif domain == "growth" and action == "legacy":
            text = await _with_session(uid, lambda s: legacy_text(s, uid))
            markup = _growth_menu()

        # Government/news/MMO
        elif domain == "gov" and action == "election":
            text = await _with_session(uid, lambda s: election_status(s, uid))
            markup = _government_menu()
        elif domain == "gov" and action == "project":
            text = await _with_session(uid, lambda s: project_status(s, uid))
            markup = _government_menu()
        elif domain == "gov" and action == "treasury":
            text = await _with_session(uid, lambda s: government_text(s, uid))
            markup = _government_menu()
        elif domain == "news" and action == "chronicle":
            text = await _with_session(uid, lambda s: world_chronicle_text(s))
            markup = _newspaper_menu()
        elif domain == "mmo" and action == "ranking":
            text = await _with_session(uid, lambda s: mmo_top_text(s))
            markup = _mmo_menu()
        elif domain == "mmo" and action == "digest":
            text = await _with_session(uid, lambda s: activity_digest_text(s, uid))
            markup = _mmo4_menu()
        elif domain == "path" and action == "status":
            text = await _with_session(uid, lambda s: path_text(s, uid))
            markup = _path_menu()
        else:
            text = await _with_session(uid, lambda s: command_center_text(s, uid))
            markup = _menu_keyboard()

        await _edit_or_answer(callback, text, markup, screen=_screen_for_v4(domain, action), user_id=callback.from_user.id)
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
            state = copy.deepcopy(battle.state)
            await session.commit()
        await _edit_battle_message(
            callback,
            active_battle_from_state(state),
            _battle_keyboard(state),
            state,
        )
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
            [InlineKeyboardButton(text="⬅️ Карточки", callback_data="ng:menu:cards")],
            [InlineKeyboardButton(text="🏠 Центр", callback_data="ng:menu:home")],
        ])
        await _edit_or_answer(callback, text, markup, screen="cards", user_id=callback.from_user.id)
        await callback.answer()
    except GameError as exc:
        await callback.answer(str(exc), show_alert=True)
