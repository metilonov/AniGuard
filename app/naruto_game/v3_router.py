from __future__ import annotations

import html
import re

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from app.db import SessionFactory

from .service import GameError
from .v3 import (
    BIJUU,
    criminal_org_accept,
    criminal_org_create,
    criminal_org_donate,
    criminal_org_invite,
    criminal_org_role,
    criminal_org_text,
    criminal_org_upgrade,
    crime_action,
    bijuu_approve,
    bijuu_hunt,
    bijuu_nominate,
    bijuu_release,
    bijuu_text,
    election_nominate,
    election_open,
    election_status,
    election_vote,
    government_text,
    ideology_set,
    newspaper_text,
    project_contribute,
    project_fund_from_treasury,
    project_start,
    project_status,
    tax_set,
    village_donate,
    world_chronicle_text,
    world_pulse_text,
)

router = Router(name="naruto-mmo-v3")


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


@router.message(Command("mmo3", "worldpulse"))
async def mmo3_handler(message: Message) -> None:
    await _run(message, lambda s: world_pulse_text(s, message.from_user.id))


@router.message(Command("government", "villagegov"))
async def government_handler(message: Message) -> None:
    await _run(message, lambda s: government_text(s, message.from_user.id))


@router.message(Command("election"))
async def election_handler(message: Message, command: CommandObject) -> None:
    parts = (command.args or "").split()
    uid = message.from_user.id
    if not parts or parts[0].lower() in {"status", "статус"}:
        await _run(message, lambda s: election_status(s, uid))
    elif parts[0].lower() in {"start", "начать", "open"}:
        await _run(message, lambda s: election_open(s, uid))
    elif parts[0].lower() in {"nominate", "выдвинуть", "кандидат"}:
        await _run(message, lambda s: election_nominate(s, uid))
    elif parts[0].lower() in {"vote", "голос"} and len(parts) >= 2 and parts[1].lstrip("-").isdigit():
        await _run(message, lambda s: election_vote(s, uid, int(parts[1])))
    else:
        await message.answer("🗳 /election start · /election nominate · /election vote USER_ID · /election status")


@router.message(Command("ideology"))
async def ideology_handler(message: Message, command: CommandObject) -> None:
    key = (command.args or "").strip().lower()
    if not key:
        await message.answer("🧭 /ideology balanced|peaceful|military|trade|isolation|scientific")
        return
    await _run(message, lambda s: ideology_set(s, message.from_user.id, key))


@router.message(Command("tax"))
async def tax_handler(message: Message, command: CommandObject) -> None:
    raw = (command.args or "").strip()
    if not raw.isdigit():
        await message.answer("💰 /tax 0..5 — налог деревни, доступен Каге")
        return
    await _run(message, lambda s: tax_set(s, message.from_user.id, int(raw)))


@router.message(Command("villagefund"))
async def village_fund_handler(message: Message, command: CommandObject) -> None:
    raw = (command.args or "").replace("_", "").strip()
    if not raw.isdigit():
        await message.answer("🏯 /villagefund 25000 — пожертвовать рё в казну деревни")
        return
    await _run(message, lambda s: village_donate(s, message.from_user.id, int(raw)))


@router.message(Command("project", "villageproject"))
async def project_handler(message: Message, command: CommandObject) -> None:
    parts = (command.args or "").split()
    uid = message.from_user.id
    if not parts or parts[0].lower() in {"status", "статус"}:
        await _run(message, lambda s: project_status(s, uid))
    elif parts[0].lower() in {"start", "начать"} and len(parts) >= 2:
        await _run(message, lambda s: project_start(s, uid, parts[1].lower()))
    elif parts[0].lower() in {"contribute", "вклад", "donate"} and len(parts) >= 2 and parts[1].replace("_", "").isdigit():
        amount = int(parts[1].replace("_", ""))
        await _run(message, lambda s: project_contribute(s, uid, amount))
    elif parts[0].lower() in {"treasury", "казна"} and len(parts) >= 2 and parts[1].replace("_", "").isdigit():
        amount = int(parts[1].replace("_", ""))
        await _run(message, lambda s: project_fund_from_treasury(s, uid, amount))
    else:
        await message.answer(
            "🏗 /project status\n"
            "/project start hospital|walls|academy|market|intel|lab\n"
            "/project contribute 25000\n"
            "/project treasury 50000"
        )


@router.message(Command("criminalorg", "shadoworg"))
async def criminal_org_handler(message: Message, command: CommandObject) -> None:
    raw = (command.args or "").strip()
    parts = raw.split()
    uid = message.from_user.id
    if not parts or parts[0].lower() in {"status", "статус"}:
        await _run(message, lambda s: criminal_org_text(s, uid))
    elif parts[0].lower() in {"create", "создать"} and len(parts) >= 2:
        name = raw.split(None, 1)[1]
        await _run(message, lambda s: criminal_org_create(s, uid, name))
    elif parts[0].lower() in {"invite", "пригласить"} and len(parts) >= 2 and parts[1].lstrip("-").isdigit():
        await _run(message, lambda s: criminal_org_invite(s, uid, int(parts[1])))
    elif parts[0].lower() in {"accept", "принять"} and len(parts) >= 2 and parts[1].isdigit():
        await _run(message, lambda s: criminal_org_accept(s, uid, int(parts[1])))
    elif parts[0].lower() in {"role", "роль"} and len(parts) >= 3 and parts[1].lstrip("-").isdigit():
        await _run(message, lambda s: criminal_org_role(s, uid, int(parts[1]), parts[2]))
    elif parts[0].lower() in {"donate", "казна", "вклад"} and len(parts) >= 2 and parts[1].replace("_", "").isdigit():
        await _run(message, lambda s: criminal_org_donate(s, uid, int(parts[1].replace("_", ""))))
    elif parts[0].lower() in {"upgrade", "улучшить"} and len(parts) >= 2:
        await _run(message, lambda s: criminal_org_upgrade(s, uid, parts[1].lower()))
    else:
        await message.answer(
            "🌑 <b>Преступная организация</b>\n"
            "/criminalorg\n"
            "/criminalorg create Название\n"
            "/criminalorg invite USER_ID\n"
            "/criminalorg accept INVITE_ID\n"
            "/criminalorg role USER_ID right_hand|enforcer|spy|researcher|initiate\n"
            "/criminalorg donate 25000\n"
            "/criminalorg upgrade base|intel|lab|defense"
        )


@router.message(Command("crime"))
async def crime_handler(message: Message, command: CommandObject) -> None:
    action = (command.args or "").strip().lower()
    if not action:
        await message.answer("☠️ /crime robbery|sabotage|intel")
        return
    await _run(message, lambda s: crime_action(s, message.from_user.id, action))


@router.message(Command("bijuu", "bijuuworld"))
async def bijuu_handler(message: Message, command: CommandObject) -> None:
    parts = (command.args or "").split()
    uid = message.from_user.id
    if not parts or parts[0].lower() in {"status", "list", "список"}:
        await _run(message, lambda s: bijuu_text(s, uid))
    elif parts[0].lower() in {"hunt", "охота", "attack"} and len(parts) >= 2:
        await _run(message, lambda s: bijuu_hunt(s, uid, parts[1].lower()))
    elif parts[0].lower() in {"nominate", "кандидат"} and len(parts) >= 3 and parts[2].lstrip("-").isdigit():
        await _run(message, lambda s: bijuu_nominate(s, uid, parts[1].lower(), int(parts[2])))
    elif parts[0].lower() in {"approve", "поддержать"} and len(parts) >= 2:
        await _run(message, lambda s: bijuu_approve(s, uid, parts[1].lower()))
    elif parts[0].lower() in {"release", "освободить"} and len(parts) >= 2:
        await _run(message, lambda s: bijuu_release(s, uid, parts[1].lower()))
    else:
        keys = "|".join(BIJUU.keys())
        await message.answer(
            "🐾 <b>Биджу</b>\n"
            f"/bijuu hunt {keys}\n"
            "/bijuu nominate KEY USER_ID\n"
            "/bijuu approve KEY\n"
            "/bijuu release KEY"
        )


@router.message(Command("newspaper", "shinobinews"))
async def newspaper_handler(message: Message) -> None:
    await _run(message, newspaper_text)


@router.message(Command("worldchronicle", "serverchronicle"))
async def chronicle_handler(message: Message, command: CommandObject) -> None:
    raw = (command.args or "").strip()
    limit = int(raw) if raw.isdigit() else 15
    await _run(message, lambda s: world_chronicle_text(s, limit))


_NARUTO_V3 = re.compile(
    r"^\s*наруто\s*[,：:]?\s*(пульс мира|правительство|выборы|проекты деревни|"
    r"преступная организация|подполье|биджу|газета|мировая летопись)\s*$",
    re.IGNORECASE,
)


@router.message(F.text.regexp(_NARUTO_V3))
async def v3_natural_aliases(message: Message) -> None:
    if not message.from_user:
        return
    match = _NARUTO_V3.match(message.text or "")
    if not match:
        return
    key = match.group(1).casefold()
    uid = message.from_user.id
    if key == "пульс мира":
        await _run(message, lambda s: world_pulse_text(s, uid))
    elif key == "правительство":
        await _run(message, lambda s: government_text(s, uid))
    elif key == "выборы":
        await _run(message, lambda s: election_status(s, uid))
    elif key == "проекты деревни":
        await _run(message, lambda s: project_status(s, uid))
    elif key in {"преступная организация", "подполье"}:
        await _run(message, lambda s: criminal_org_text(s, uid))
    elif key == "биджу":
        await _run(message, lambda s: bijuu_text(s, uid))
    elif key == "газета":
        await _run(message, newspaper_text)
    elif key == "мировая летопись":
        await _run(message, world_chronicle_text)
