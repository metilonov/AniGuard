from __future__ import annotations

import html

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from app.db import SessionFactory

from .extended import (
    bank_move,
    bank_text,
    bond_accept,
    bond_propose,
    bond_text,
    black_market_buy,
    black_market_text,
    card_arena,
    card_team_set,
    card_team_text,
    caravan_claim,
    caravan_create,
    chronicle_text,
    contract_complete,
    contract_create,
    contract_take,
    contracts_text,
    create_hidden_village,
    custom_technique_create,
    custom_techniques_text,
    dynasty_create,
    dynasty_join,
    dynasty_text,
    morality_choice,
    notification_toggle,
    prestige,
    rival_duel,
    rival_text,
    season_text,
    set_title,
    trade_accept,
    trade_cancel,
    trade_create,
    treat_injuries,
    village_diplomacy,
    village_donate,
    village_nominate,
    village_project,
    village_resolve_election,
    village_status,
    village_tax,
    village_vote,
    village_war_action,
    activate_form,
    anbu_mission,
    clan_war,
    dojutsu_awaken,
    dojutsu_status,
    hunt_nukenin,
    sage_training,
    squad_create,
    squad_join,
    squad_leave,
    squad_text,
    cook_ramen,
    eat_food,
    element_mastery_text,
    fishing_activity,
    gather_activity,
    learn_element,
    summon_for_battle,
)
from .service import GameError

router = Router(name="naruto-rpg-extended")


def _safe(text: str) -> str:
    return (
        html.escape(text, quote=False)
        .replace("&lt;b&gt;", "<b>")
        .replace("&lt;/b&gt;", "</b>")
        .replace("&lt;code&gt;", "<code>")
        .replace("&lt;/code&gt;", "</code>")
    )


async def _run(message: Message, operation) -> None:
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


@router.message(Command("village"))
async def village_handler(message: Message, command: CommandObject) -> None:
    raw = (command.args or "").strip()
    parts = raw.split()
    user_id = message.from_user.id
    if not parts:
        await _run(message, lambda s: village_status(s, user_id))
        return
    action = parts[0].lower()
    if action == "donate" and len(parts) >= 2 and parts[1].isdigit():
        await _run(message, lambda s: village_donate(s, user_id, int(parts[1])))
    elif action == "nominate":
        await _run(message, lambda s: village_nominate(s, user_id))
    elif action == "vote" and len(parts) >= 2 and parts[1].lstrip("-").isdigit():
        await _run(message, lambda s: village_vote(s, user_id, int(parts[1])))
    elif action == "resolve":
        await _run(message, lambda s: village_resolve_election(s, user_id))
    elif action == "project" and len(parts) >= 2:
        await _run(message, lambda s: village_project(s, user_id, parts[1].lower()))
    elif action == "tax" and len(parts) >= 2 and parts[1].isdigit():
        await _run(message, lambda s: village_tax(s, user_id, int(parts[1])))
    elif action == "diplomacy" and len(parts) >= 3:
        await _run(message, lambda s: village_diplomacy(s, user_id, parts[1].lower(), parts[2].lower()))
    elif action == "war":
        await _run(message, lambda s: village_war_action(s, user_id))
    elif action == "create" and len(parts) >= 2:
        name = raw.split(None, 1)[1]
        await _run(message, lambda s: create_hidden_village(s, user_id, name))
    else:
        await message.answer(
            "🏯 <b>Деревня</b>\n"
            "/village — статус\n"
            "/village donate 10000\n"
            "/village nominate\n"
            "/village vote USER_ID\n"
            "/village resolve\n"
            "/village project academy|hospital|forge|intelligence|walls\n"
            "/village tax 0..5\n"
            "/village diplomacy kiri alliance|war|neutral\n"
            "/village war\n"
            "/village create Название — своя скрытая деревня для развитого клана"
        )


@router.message(Command("bank"))
async def bank_handler(message: Message, command: CommandObject) -> None:
    parts = (command.args or "").split()
    uid = message.from_user.id
    if not parts:
        await _run(message, lambda s: bank_text(s, uid))
    elif len(parts) >= 2 and parts[1].isdigit() and parts[0].lower() in {"deposit", "withdraw"}:
        await _run(message, lambda s: bank_move(s, uid, int(parts[1]), deposit=parts[0].lower() == "deposit"))
    else:
        await message.answer("🏦 /bank deposit 10000 или /bank withdraw 10000")


@router.message(Command("blackmarket"))
async def black_market_handler(message: Message, command: CommandObject) -> None:
    parts = (command.args or "").split()
    uid = message.from_user.id
    if len(parts) >= 2 and parts[0].lower() == "buy":
        await _run(message, lambda s: black_market_buy(s, uid, parts[1].lower()))
    else:
        await _run(message, lambda s: black_market_text(s, uid))


@router.message(Command("trade"))
async def trade_handler(message: Message, command: CommandObject) -> None:
    parts = (command.args or "").split()
    uid = message.from_user.id
    if len(parts) >= 2 and parts[0].lower() == "accept" and parts[1].isdigit():
        await _run(message, lambda s: trade_accept(s, uid, int(parts[1])))
        return
    if len(parts) >= 2 and parts[0].lower() == "cancel" and parts[1].isdigit():
        await _run(message, lambda s: trade_cancel(s, uid, int(parts[1])))
        return
    if (
        len(parts) == 8
        and parts[0].lower() == "create"
        and parts[1].lstrip("-").isdigit()
        and parts[3].isdigit()
        and parts[4].isdigit()
        and parts[6].isdigit()
        and parts[7].isdigit()
    ):
        partner_id = int(parts[1])
        give_item, give_qty, give_ryo = parts[2], int(parts[3]), int(parts[4])
        take_item, take_qty, take_ryo = parts[5], int(parts[6]), int(parts[7])
        await _run(
            message,
            lambda s: trade_create(s, uid, partner_id, give_item, give_qty, give_ryo, take_item, take_qty, take_ryo),
        )
        return
    await message.answer(
        "🤝 <b>P2P-сделки через эскроу</b>\n"
        "Создать:\n<code>/trade create USER_ID give_item qty give_ryo take_item qty take_ryo</code>\n"
        "Вместо предмета используйте <code>-</code>.\n"
        "Пример: <code>/trade create 123 kunai 5 0 chakra_crystal 1 500</code>\n"
        "Принять: <code>/trade accept ID</code> · отменить: <code>/trade cancel ID</code>"
    )


@router.message(Command("contracts"))
async def contracts_handler(message: Message, command: CommandObject) -> None:
    parts = (command.args or "").split()
    uid = message.from_user.id
    if len(parts) == 4 and parts[0].lower() == "create" and parts[2].isdigit() and parts[3].isdigit():
        await _run(message, lambda s: contract_create(s, uid, parts[1].lower(), int(parts[2]), int(parts[3])))
    elif len(parts) >= 2 and parts[0].lower() == "take" and parts[1].isdigit():
        await _run(message, lambda s: contract_take(s, uid, int(parts[1])))
    elif len(parts) >= 2 and parts[0].lower() == "complete" and parts[1].isdigit():
        await _run(message, lambda s: contract_complete(s, uid, int(parts[1])))
    else:
        await _run(message, lambda s: contracts_text(s))


@router.message(Command("caravan"))
async def caravan_handler(message: Message, command: CommandObject) -> None:
    parts = (command.args or "").split()
    uid = message.from_user.id
    if len(parts) >= 2 and parts[0].lower() == "claim" and parts[1].isdigit():
        await _run(message, lambda s: caravan_claim(s, uid, int(parts[1])))
    elif len(parts) >= 4 and parts[0].lower() == "send" and parts[3].isdigit():
        insured = len(parts) >= 5 and parts[4].lower() in {"1", "yes", "да", "insured"}
        await _run(message, lambda s: caravan_create(s, uid, parts[1].lower(), parts[2].lower(), int(parts[3]), insured))
    else:
        await message.answer("🚚 /caravan send kiri herb 10 yes · /caravan claim ID")


@router.message(Command("cardteam"))
async def card_team_handler(message: Message, command: CommandObject) -> None:
    parts = (command.args or "").split()
    uid = message.from_user.id
    if parts and parts[0].lower() == "set" and len(parts) >= 2:
        await _run(message, lambda s: card_team_set(s, uid, [x.lower() for x in parts[1:]]))
    else:
        await _run(message, lambda s: card_team_text(s, uid))


@router.message(Command("cardarena"))
async def card_arena_handler(message: Message) -> None:
    await _run(message, lambda s: card_arena(s, message.from_user.id))


@router.message(Command("customjutsu"))
async def custom_jutsu_handler(message: Message, command: CommandObject) -> None:
    raw = (command.args or "").strip()
    parts = raw.split()
    uid = message.from_user.id
    if len(parts) >= 4 and parts[0].lower() == "create":
        element = parts[1].lower()
        kind = parts[2].lower()
        name = " ".join(parts[3:])
        await _run(message, lambda s: custom_technique_create(s, uid, name, element, kind))
    else:
        await _run(message, lambda s: custom_techniques_text(s, uid))


@router.message(Command("prestige"))
async def prestige_handler(message: Message) -> None:
    await _run(message, lambda s: prestige(s, message.from_user.id))


@router.message(Command("choice"))
async def choice_handler(message: Message, command: CommandObject) -> None:
    choice = (command.args or "").strip().lower()
    await _run(message, lambda s: morality_choice(s, message.from_user.id, choice))


@router.message(Command("hospital"))
async def hospital_handler(message: Message) -> None:
    await _run(message, lambda s: treat_injuries(s, message.from_user.id))


@router.message(Command("title_set"))
async def title_handler(message: Message, command: CommandObject) -> None:
    key = (command.args or "").strip().lower()
    await _run(message, lambda s: set_title(s, message.from_user.id, key))


@router.message(Command("season"))
async def season_handler(message: Message) -> None:
    await _run(message, lambda s: season_text(s, message.from_user.id))


@router.message(Command("notify_game"))
async def notify_handler(message: Message, command: CommandObject) -> None:
    key = (command.args or "").strip().lower()
    await _run(message, lambda s: notification_toggle(s, message.from_user.id, key))


@router.message(Command("chronicle"))
async def chronicle_handler(message: Message, command: CommandObject) -> None:
    own = (command.args or "").strip().lower() in {"me", "my", "я", "моя"}
    uid = message.from_user.id if own else None
    await _run(message, lambda s: chronicle_text(s, uid))


@router.message(Command("dynasty"))
async def dynasty_handler(message: Message, command: CommandObject) -> None:
    raw = (command.args or "").strip()
    head, _, tail = raw.partition(" ")
    uid = message.from_user.id
    if head.lower() == "create" and tail:
        await _run(message, lambda s: dynasty_create(s, uid, tail.strip()))
    elif head.lower() == "join" and tail:
        await _run(message, lambda s: dynasty_join(s, uid, tail.strip()))
    else:
        await _run(message, lambda s: dynasty_text(s, uid))


@router.message(Command("bond"))
async def bond_handler(message: Message, command: CommandObject) -> None:
    parts = (command.args or "").split()
    uid = message.from_user.id
    if len(parts) >= 2 and parts[0].lower() == "propose" and parts[1].lstrip("-").isdigit():
        await _run(message, lambda s: bond_propose(s, uid, int(parts[1])))
    elif len(parts) >= 2 and parts[0].lower() == "accept" and parts[1].isdigit():
        await _run(message, lambda s: bond_accept(s, uid, int(parts[1])))
    else:
        await _run(message, lambda s: bond_text(s, uid))


@router.message(Command("rival"))
async def rival_handler(message: Message, command: CommandObject) -> None:
    uid = message.from_user.id
    if (command.args or "").strip().lower() in {"duel", "бой", "battle"}:
        await _run(message, lambda s: rival_duel(s, uid))
    else:
        await _run(message, lambda s: rival_text(s, uid))


@router.message(Command("dojutsu"))
async def dojutsu_handler(message: Message, command: CommandObject) -> None:
    uid = message.from_user.id
    if (command.args or "").strip().lower() in {"awaken", "пробудить", "evolve"}:
        await _run(message, lambda s: dojutsu_awaken(s, uid))
    else:
        await _run(message, lambda s: dojutsu_status(s, uid))


@router.message(Command("sage_train"))
async def sage_train_handler(message: Message) -> None:
    await _run(message, lambda s: sage_training(s, message.from_user.id))


@router.message(Command("form"))
async def form_handler(message: Message, command: CommandObject) -> None:
    parts = (command.args or "").split()
    if not parts:
        await message.answer("🔥 /form sharingan|mangekyo|rinnegan|sage|biju|off или /form gates 1..8")
        return
    gate = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else None
    await _run(message, lambda s: activate_form(s, message.from_user.id, parts[0], gate))


@router.message(Command("anbu_mission"))
async def anbu_mission_handler(message: Message) -> None:
    await _run(message, lambda s: anbu_mission(s, message.from_user.id))


@router.message(Command("hunt"))
async def hunt_handler(message: Message, command: CommandObject) -> None:
    raw = (command.args or "").strip()
    if not raw.lstrip("-").isdigit():
        await message.answer("🎯 /hunt USER_ID — охота на нукенина из Bingo Book")
        return
    await _run(message, lambda s: hunt_nukenin(s, message.from_user.id, int(raw)))


@router.message(Command("clanwar"))
async def clanwar_handler(message: Message, command: CommandObject) -> None:
    name = (command.args or "").strip()
    if not name:
        await message.answer("⚔️ /clanwar Название клана")
        return
    await _run(message, lambda s: clan_war(s, message.from_user.id, name))


@router.message(Command("squad"))
async def squad_handler(message: Message, command: CommandObject) -> None:
    parts = (command.args or "").split()
    uid = message.from_user.id
    if parts and parts[0].lower() == "create":
        purpose = parts[1] if len(parts) > 1 else "missions"
        await _run(message, lambda s: squad_create(s, uid, purpose))
    elif len(parts) >= 2 and parts[0].lower() == "join" and parts[1].isdigit():
        await _run(message, lambda s: squad_join(s, uid, int(parts[1])))
    elif parts and parts[0].lower() == "leave":
        await _run(message, lambda s: squad_leave(s, uid))
    else:
        await _run(message, lambda s: squad_text(s, uid))


@router.message(Command("element"))
async def element_handler(message: Message, command: CommandObject) -> None:
    parts = (command.args or "").split()
    uid = message.from_user.id
    if len(parts) >= 2 and parts[0].lower() == "learn":
        await _run(message, lambda s: learn_element(s, uid, parts[1].lower()))
    else:
        await _run(message, lambda s: element_mastery_text(s, uid))


@router.message(Command("gather"))
async def gather_handler(message: Message) -> None:
    await _run(message, lambda s: gather_activity(s, message.from_user.id))


@router.message(Command("fish"))
async def fish_handler(message: Message) -> None:
    await _run(message, lambda s: fishing_activity(s, message.from_user.id))


@router.message(Command("cook"))
async def cook_handler(message: Message, command: CommandObject) -> None:
    if (command.args or "ramen").strip().lower() != "ramen":
        await message.answer("🍜 /cook ramen")
        return
    await _run(message, lambda s: cook_ramen(s, message.from_user.id))


@router.message(Command("eat"))
async def eat_handler(message: Message, command: CommandObject) -> None:
    await _run(message, lambda s: eat_food(s, message.from_user.id, (command.args or "ramen").strip().lower()))


@router.message(Command("summon_battle"))
async def summon_battle_handler(message: Message, command: CommandObject) -> None:
    key = (command.args or "").strip().lower() or None
    await _run(message, lambda s: summon_for_battle(s, message.from_user.id, key))
