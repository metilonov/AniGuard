from __future__ import annotations

import html
import re

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from app.db import SessionFactory

from .advanced import (
    NPCS,
    dynamic_mission_choose,
    dynamic_mission_create,
    dynamic_mission_prepare,
    dynamic_mission_text,
    epithet_set,
    legacy_text,
    legend_status,
    npc_interact,
    npc_memories_text,
    npc_personal_quest,
    npc_promise,
    npc_promise_resolve,
    npc_relation_text,
    path_text,
    recommendations_text,
    research_start,
    research_text,
    research_train,
    return_summary_text,
    specialize,
    successor_prepare,
    territory_expedition,
    territory_map_text,
    territory_outpost_upgrade,
    unique_legend_claim,
    village_war_declare,
    war_action,
    war_front_text,
    war_mobilize,
    war_peace_accept,
    war_peace_offer,
    war_spy,
    world_event_participate,
    world_events_text,
)
from .service import GameError

router = Router(name="naruto-rpg-advanced-world")


def _safe(text: str) -> str:
    return (
        html.escape(str(text), quote=False)
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
        except Exception:
            await session.rollback()
            raise
    await message.answer(_safe(text))


@router.message(Command("territory"))
async def territory_handler(message: Message, command: CommandObject) -> None:
    parts = (command.args or "").split()
    uid = message.from_user.id
    if not parts or parts[0].lower() == "map":
        await _run(message, territory_map_text)
    elif parts[0].lower() in {"expedition", "разведка"} and len(parts) >= 2:
        await _run(message, lambda s: territory_expedition(s, uid, parts[1].lower()))
    elif parts[0].lower() in {"outpost", "аванпост"} and len(parts) >= 2:
        await _run(message, lambda s: territory_outpost_upgrade(s, uid, parts[1].lower()))
    else:
        await message.answer(
            "🗺 <b>Территории</b>\n"
            "/territory map\n"
            "/territory expedition fire_mountain_pass\n"
            "/territory outpost fire_north_forest"
        )


@router.message(Command("worldwar"))
async def worldwar_handler(message: Message, command: CommandObject) -> None:
    parts = (command.args or "").split()
    uid = message.from_user.id
    if len(parts) >= 2 and parts[0].lower() in {"declare", "объявить"}:
        await _run(message, lambda s: village_war_declare(s, uid, parts[1].lower()))
    else:
        await message.answer("⚔️ /worldwar declare kiri — объявить войну от лица Каге.")


@router.message(Command("mobilize"))
async def mobilize_handler(message: Message, command: CommandObject) -> None:
    role = (command.args or "fighter").split()[0].lower()
    await _run(message, lambda s: war_mobilize(s, message.from_user.id, role))


@router.message(Command("front"))
async def front_handler(message: Message, command: CommandObject) -> None:
    parts = (command.args or "").split()
    uid = message.from_user.id
    if not parts or parts[0].lower() in {"status", "статус"}:
        await _run(message, lambda s: war_front_text(s, uid))
    elif parts[0].lower() in {"action", "действие"}:
        front = parts[1].lower() if len(parts) > 1 else "center"
        await _run(message, lambda s: war_action(s, uid, front))
    elif parts[0].lower() in {"spy", "разведка"}:
        await _run(message, lambda s: war_spy(s, uid))
    else:
        await message.answer("⚔️ /front status · /front action north|center|south · /front spy")


@router.message(Command("peace"))
async def peace_handler(message: Message, command: CommandObject) -> None:
    parts = (command.args or "").split()
    uid = message.from_user.id
    if parts and parts[0].lower() in {"accept", "принять"}:
        await _run(message, lambda s: war_peace_accept(s, uid))
    elif parts and parts[0].lower() in {"offer", "предложить"}:
        amount = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
        await _run(message, lambda s: war_peace_offer(s, uid, amount))
    else:
        await message.answer("🕊 /peace offer [компенсация] · /peace accept")


@router.message(Command("npc"))
async def npc_handler(message: Message, command: CommandObject) -> None:
    parts = (command.args or "").split()
    uid = message.from_user.id
    if not parts:
        await message.answer("👥 NPC: " + ", ".join(NPCS.keys()) + "\n/npc kakashi · /npc kakashi talk|train|help|lie|challenge")
        return
    key = parts[0].lower()
    if len(parts) == 1:
        await _run(message, lambda s: npc_relation_text(s, uid, key))
    elif parts[1].lower() in {"memory", "память"}:
        await _run(message, lambda s: npc_memories_text(s, uid, key))
    elif parts[1].lower() in {"quest", "квест"}:
        await _run(message, lambda s: npc_personal_quest(s, uid, key))
    else:
        await _run(message, lambda s: npc_interact(s, uid, key, parts[1]))


@router.message(Command("promise"))
async def promise_handler(message: Message, command: CommandObject) -> None:
    raw = (command.args or "").strip()
    parts = raw.split()
    uid = message.from_user.id
    if len(parts) >= 4 and parts[0].lower() in {"resolve", "решить"} and parts[2].isdigit():
        success = parts[3].lower() in {"yes", "success", "done", "да", "выполнено"}
        await _run(message, lambda s: npc_promise_resolve(s, uid, parts[1].lower(), int(parts[2]), success))
        return
    if len(parts) < 2:
        await message.answer("🤝 /promise kakashi Я вернусь за товарищем\n/promise resolve kakashi 1 yes|no")
        return
    key = parts[0].lower()
    text = raw.split(None, 1)[1]
    await _run(message, lambda s: npc_promise(s, uid, key, text))


@router.message(Command("livemission"))
async def live_mission_handler(message: Message, command: CommandObject) -> None:
    parts = (command.args or "").split()
    uid = message.from_user.id
    if not parts:
        await _run(message, lambda s: dynamic_mission_text(s, uid))
    elif parts[0].lower() in {"new", "новая"}:
        await _run(message, lambda s: dynamic_mission_create(s, uid))
    elif parts[0].lower() in {"prepare", "подготовка"}:
        await _run(message, lambda s: dynamic_mission_prepare(s, uid))
    elif parts[0].lower() in {"choose", "выбор"} and len(parts) >= 2:
        await _run(message, lambda s: dynamic_mission_choose(s, uid, parts[1].lower()))
    else:
        await message.answer("📜 /livemission new · /livemission prepare · /livemission choose direct|investigate|negotiate|retreat")


@router.message(Command("events"))
async def events_handler(message: Message, command: CommandObject) -> None:
    parts = (command.args or "").split()
    if len(parts) >= 2 and parts[0].lower() in {"join", "участвовать"} and parts[1].isdigit():
        await _run(message, lambda s: world_event_participate(s, message.from_user.id, int(parts[1])))
    else:
        await _run(message, world_events_text)


@router.message(Command("recommend"))
async def recommend_handler(message: Message) -> None:
    await _run(message, lambda s: recommendations_text(s, message.from_user.id))


@router.message(Command("returnlog"))
async def returnlog_handler(message: Message) -> None:
    await _run(message, lambda s: return_summary_text(s, message.from_user.id))


@router.message(Command("path"))
async def path_handler(message: Message, command: CommandObject) -> None:
    parts = (command.args or "").split()
    uid = message.from_user.id
    if not parts:
        await _run(message, lambda s: path_text(s, uid))
    elif parts[0].lower() in {"specialize", "специализация"} and len(parts) >= 2:
        await _run(message, lambda s: specialize(s, uid, parts[1].lower()))
    else:
        await message.answer("🥷 /path · /path specialize ninjutsu|taijutsu|genjutsu|kenjutsu|medic|fuinjutsu|sensor")


@router.message(Command("research"))
async def research_handler(message: Message, command: CommandObject) -> None:
    raw = (command.args or "").strip()
    parts = raw.split()
    uid = message.from_user.id
    if not parts:
        await _run(message, lambda s: research_text(s, uid))
    elif parts[0].lower() in {"train", "тренировка"}:
        await _run(message, lambda s: research_train(s, uid))
    elif parts[0].lower() in {"start", "создать"} and len(parts) >= 5:
        # /research start fire ninjutsu burn Название техники
        element, kind, effect = parts[1], parts[2], parts[3]
        name = " ".join(parts[4:])
        await _run(message, lambda s: research_start(s, uid, name, element, kind, effect))
    else:
        await message.answer("📚 /research start fire ninjutsu burn Клык Грозы\n/research train\n/research")


@router.message(Command("legend"))
async def legend_handler(message: Message, command: CommandObject) -> None:
    raw = (command.args or "").strip()
    parts = raw.split()
    uid = message.from_user.id
    if not parts:
        await _run(message, lambda s: legend_status(s, uid))
    elif parts[0].lower() in {"claim", "создать"} and len(parts) >= 3:
        title = parts[1]
        desc = " ".join(parts[2:])
        await _run(message, lambda s: unique_legend_claim(s, uid, title, desc))
    else:
        await message.answer("🏆 /legend · /legend claim Название Описание")


@router.message(Command("epithet"))
async def epithet_handler(message: Message, command: CommandObject) -> None:
    key = (command.args or "").strip().lower()
    if not key:
        await message.answer("📛 /epithet lightning|fire|shadow|biju|medic|commander")
        return
    await _run(message, lambda s: epithet_set(s, message.from_user.id, key))


@router.message(Command("legacy"))
async def legacy_handler(message: Message, command: CommandObject) -> None:
    raw = (command.args or "").strip()
    uid = message.from_user.id
    if raw.lower().startswith("successor "):
        await _run(message, lambda s: successor_prepare(s, uid, raw.split(None, 1)[1]))
    else:
        await _run(message, lambda s: legacy_text(s, uid))


# Specific natural-language aliases are handled before the broad social
# "Наруто, ..." router so unknown phrases still fall through to old behavior.
_NARUTO_ADVANCED = re.compile(
    r"^\s*наруто\s*[,：:]?\s*(карта мира|территории|события|что мне делать|мой путь|путь шиноби|"
    r"живая миссия|новая миссия|фронт|сводка|вернулся|отношения\s+[a-zа-яё_]+|память\s+[a-zа-яё_]+)\s*$",
    re.IGNORECASE,
)


@router.message(F.text.regexp(_NARUTO_ADVANCED))
async def naruto_advanced_address(message: Message) -> None:
    if not message.from_user:
        return
    match = _NARUTO_ADVANCED.match(message.text or "")
    if not match:
        return
    raw = match.group(1).strip()
    low = raw.casefold()
    uid = message.from_user.id
    if low in {"карта мира", "территории"}:
        await _run(message, territory_map_text)
    elif low == "события":
        await _run(message, world_events_text)
    elif low == "что мне делать":
        await _run(message, lambda s: recommendations_text(s, uid))
    elif low in {"мой путь", "путь шиноби"}:
        await _run(message, lambda s: path_text(s, uid))
    elif low in {"живая миссия", "новая миссия"}:
        await _run(message, lambda s: dynamic_mission_create(s, uid))
    elif low == "фронт":
        await _run(message, lambda s: war_front_text(s, uid))
    elif low in {"сводка", "вернулся"}:
        await _run(message, lambda s: return_summary_text(s, uid))
    elif low.startswith("отношения "):
        key = low.split(None, 1)[1]
        await _run(message, lambda s: npc_relation_text(s, uid, key))
    elif low.startswith("память "):
        key = low.split(None, 1)[1]
        await _run(message, lambda s: npc_memories_text(s, uid, key))
