from __future__ import annotations

import re
import random
from datetime import timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .content import CARDS, ELEMENTS, ITEMS, SUMMONS, VILLAGES
from .engine import player_power
from .models import (
    NinjaBond,
    NinjaCard,
    NinjaCaravan,
    NinjaClan,
    NinjaClanMember,
    NinjaCustomTechnique,
    NinjaDynasty,
    NinjaEventLog,
    NinjaItem,
    NinjaPlayerContract,
    NinjaProfile,
    NinjaTrade,
    NinjaWorldState,
    utcnow,
)
from .service import GameError, _energy_refresh, add_item, require_profile


def _copy_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _copy_list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _slug(value: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9а-яА-ЯёЁ_-]+", "_", value.strip()).strip("_").lower()
    return value[:32]


def _flag(profile: NinjaProfile, key: str, default: Any = None) -> Any:
    flags = _copy_dict(profile.flags)
    return flags.get(key, default)


def _set_flag(profile: NinjaProfile, key: str, value: Any) -> None:
    flags = _copy_dict(profile.flags)
    flags[key] = value
    profile.flags = flags


def _effective_village(profile: NinjaProfile) -> str:
    custom = _flag(profile, "custom_village", None)
    return str(custom) if custom else str(profile.village)


# ---------------------------------------------------------------------------
# Village politics, treasury, elections, diplomacy and territorial wars
# ---------------------------------------------------------------------------


async def _village_row(session: AsyncSession, key: str) -> NinjaWorldState:
    if key not in VILLAGES and not key.startswith("custom:"):
        raise GameError("Неизвестная деревня.")
    state_key = f"village:{key}"
    row = await session.get(NinjaWorldState, state_key)
    if row is None:
        row = NinjaWorldState(
            key=state_key,
            value={
                "key": key,
                "treasury": 0,
                "tax_rate": 2,
                "satisfaction": 75,
                "kage_id": None,
                "upgrades": {"academy": 1, "hospital": 1, "forge": 1, "intelligence": 1, "walls": 1},
                "diplomacy": {},
                "election": None,
                "territory_points": 0,
            },
        )
        session.add(row)
        await session.flush()
    return row


async def village_status(session: AsyncSession, user_id: int) -> str:
    profile = await require_profile(session, user_id)
    village_key = _effective_village(profile)
    row = await _village_row(session, village_key)
    value = _copy_dict(row.value)
    upgrades = _copy_dict(value.get("upgrades"))
    election = value.get("election") or {}
    village_name = VILLAGES.get(village_key, {"name": value.get("name", village_key)}).get("name", value.get("name", village_key))
    lines = [
        f"🏯 <b>{village_name}</b>",
        f"👑 Каге: {value.get('kage_id') or 'не выбран'}",
        f"💰 Казна: {int(value.get('treasury', 0)):,} рё",
        f"📈 Налог: {int(value.get('tax_rate', 2))}%",
        f"😊 Довольство: {int(value.get('satisfaction', 75))}/100",
        f"🗺 Очки территории: {int(value.get('territory_points', 0)):,}",
        "",
        "🏗 Проекты:",
        f"• Академия ур.{int(upgrades.get('academy', 1))}",
        f"• Госпиталь ур.{int(upgrades.get('hospital', 1))}",
        f"• Кузница ур.{int(upgrades.get('forge', 1))}",
        f"• Разведцентр ур.{int(upgrades.get('intelligence', 1))}",
        f"• Стены ур.{int(upgrades.get('walls', 1))}",
    ]
    if election and election.get("active"):
        lines += ["", f"🗳 Выборы: кандидатов {len(election.get('candidates') or [])}, голосов {len(election.get('votes') or {})}"]
    return "\n".join(lines)


async def village_donate(session: AsyncSession, user_id: int, amount: int) -> str:
    profile = await require_profile(session, user_id)
    amount = max(100, int(amount))
    if profile.ryo < amount:
        raise GameError("Недостаточно рё.")
    village_key = _effective_village(profile)
    row = await _village_row(session, village_key)
    value = _copy_dict(row.value)
    profile.ryo -= amount
    value["treasury"] = int(value.get("treasury", 0)) + amount
    value["satisfaction"] = min(100, int(value.get("satisfaction", 75)) + max(1, amount // 100_000))
    row.value = value
    profile.village_points += max(1, amount // 1000)
    session.add(NinjaEventLog(event_type="village_donation", user_id=user_id, payload={"village": village_key, "amount": amount}))
    return f"🏯 В казну внесено {amount:,} рё. +{max(1, amount // 1000)} очков деревни."


async def village_nominate(session: AsyncSession, user_id: int) -> str:
    profile = await require_profile(session, user_id)
    if profile.level < 40 or profile.reputation < 5000 or profile.nukenin:
        raise GameError("Кандидат в Каге: уровень 40+, репутация 5000+, не нукенин.")
    village_key = _effective_village(profile)
    row = await _village_row(session, village_key)
    value = _copy_dict(row.value)
    election = _copy_dict(value.get("election"))
    now = utcnow()
    if not election.get("active"):
        election = {
            "active": True,
            "started_at": now.isoformat(),
            "ends_at": (now + timedelta(days=3)).isoformat(),
            "candidates": [],
            "votes": {},
        }
    candidates = [int(x) for x in election.get("candidates") or []]
    if user_id not in candidates:
        candidates.append(user_id)
    election["candidates"] = candidates
    value["election"] = election
    row.value = value
    return f"🗳 Вы зарегистрированы кандидатом в Каге. Кандидатов: {len(candidates)}."


async def village_vote(session: AsyncSession, user_id: int, candidate_id: int) -> str:
    profile = await require_profile(session, user_id)
    village_key = _effective_village(profile)
    row = await _village_row(session, village_key)
    value = _copy_dict(row.value)
    election = _copy_dict(value.get("election"))
    if not election.get("active"):
        raise GameError("Сейчас нет активных выборов.")
    candidates = [int(x) for x in election.get("candidates") or []]
    if int(candidate_id) not in candidates:
        raise GameError("Такого кандидата нет.")
    votes = {str(k): int(v) for k, v in _copy_dict(election.get("votes")).items()}
    votes[str(user_id)] = int(candidate_id)
    election["votes"] = votes
    value["election"] = election
    row.value = value
    return f"🗳 Голос принят за кандидата {candidate_id}."


async def village_resolve_election(session: AsyncSession, user_id: int) -> str:
    profile = await require_profile(session, user_id)
    village_key = _effective_village(profile)
    row = await _village_row(session, village_key)
    value = _copy_dict(row.value)
    election = _copy_dict(value.get("election"))
    if not election.get("active"):
        raise GameError("Выборы не идут.")
    ends_at = utcnow()
    try:
        from datetime import datetime
        ends_at = datetime.fromisoformat(str(election.get("ends_at")))
        if ends_at.tzinfo is None:
            ends_at = ends_at.replace(tzinfo=utcnow().tzinfo)
    except Exception:
        pass
    if utcnow() < ends_at and len(election.get("votes") or {}) < 10:
        raise GameError("Выборы ещё не завершены (либо нужно минимум 10 голосов для досрочного подсчёта).")
    candidates = [int(x) for x in election.get("candidates") or []]
    if not candidates:
        raise GameError("Нет кандидатов.")
    counts = {candidate: 0 for candidate in candidates}
    for candidate in (election.get("votes") or {}).values():
        candidate = int(candidate)
        if candidate in counts:
            counts[candidate] += 1
    winner = max(candidates, key=lambda c: (counts[c], -c))
    value["kage_id"] = winner
    election["active"] = False
    election["winner"] = winner
    value["election"] = election
    row.value = value
    session.add(NinjaEventLog(event_type="kage_elected", user_id=winner, payload={"village": village_key, "votes": counts[winner]}))
    return f"👑 Новый Каге: {winner}. Голосов: {counts[winner]}."


async def village_project(session: AsyncSession, user_id: int, project: str) -> str:
    profile = await require_profile(session, user_id)
    village_key = _effective_village(profile)
    row = await _village_row(session, village_key)
    value = _copy_dict(row.value)
    if int(value.get("kage_id") or 0) != user_id:
        raise GameError("Проекты казны запускает действующий Каге.")
    upgrades = _copy_dict(value.get("upgrades"))
    aliases = {"academy", "hospital", "forge", "intelligence", "walls"}
    if project not in aliases:
        raise GameError("Проекты: academy, hospital, forge, intelligence, walls.")
    level = int(upgrades.get(project, 1))
    if level >= 10:
        raise GameError("Проект максимального уровня.")
    cost = int(250_000 * (level ** 1.65))
    if int(value.get("treasury", 0)) < cost:
        raise GameError(f"В казне нужно {cost:,} рё.")
    value["treasury"] = int(value.get("treasury", 0)) - cost
    upgrades[project] = level + 1
    value["upgrades"] = upgrades
    value["satisfaction"] = min(100, int(value.get("satisfaction", 75)) + 2)
    row.value = value
    return f"🏗 {project} улучшен до уровня {level + 1}. Потрачено {cost:,} рё."


async def village_tax(session: AsyncSession, user_id: int, percent: int) -> str:
    profile = await require_profile(session, user_id)
    village_key = _effective_village(profile)
    row = await _village_row(session, village_key)
    value = _copy_dict(row.value)
    if int(value.get("kage_id") or 0) != user_id:
        raise GameError("Налог меняет только Каге.")
    percent = max(0, min(5, int(percent)))
    old = int(value.get("tax_rate", 2))
    value["tax_rate"] = percent
    if percent > old:
        value["satisfaction"] = max(0, int(value.get("satisfaction", 75)) - (percent - old) * 2)
    row.value = value
    return f"📈 Налог деревни установлен: {percent}%."


async def village_diplomacy(session: AsyncSession, user_id: int, target: str, relation: str) -> str:
    profile = await require_profile(session, user_id)
    village_key = _effective_village(profile)
    if target not in VILLAGES or target == village_key:
        raise GameError("Укажите другую великую деревню.")
    if relation not in {"neutral", "alliance", "war"}:
        raise GameError("Статус: neutral, alliance или war.")
    village_key = _effective_village(profile)
    row = await _village_row(session, village_key)
    value = _copy_dict(row.value)
    if int(value.get("kage_id") or 0) != user_id:
        raise GameError("Дипломатию определяет Каге.")
    diplomacy = _copy_dict(value.get("diplomacy"))
    diplomacy[target] = relation
    value["diplomacy"] = diplomacy
    row.value = value
    session.add(NinjaEventLog(event_type="diplomacy", user_id=user_id, payload={"from": village_key, "to": target, "relation": relation}))
    return f"🤝 Статус {village_key} → {target}: {relation}."


async def village_war_action(session: AsyncSession, user_id: int) -> str:
    profile = await require_profile(session, user_id)
    if profile.energy < 15:
        raise GameError("Для военной операции нужно 15 энергии.")
    profile.energy -= 15
    profile.energy_updated_at = utcnow()
    village_key = _effective_village(profile)
    row = await _village_row(session, village_key)
    value = _copy_dict(row.value)
    gained = max(50, int(player_power(profile) * random.uniform(0.8, 1.3)))
    value["territory_points"] = int(value.get("territory_points", 0)) + gained
    row.value = value
    profile.village_points += max(1, gained // 100)
    return f"⚔️ Военная операция завершена: +{gained:,} очков территории."


async def create_hidden_village(session: AsyncSession, user_id: int, name: str) -> str:
    profile = await require_profile(session, user_id)
    member = await session.scalar(select(NinjaClanMember).where(NinjaClanMember.user_id == user_id))
    if not member or member.role != "leader":
        raise GameError("Скрытую деревню может основать глава клана.")
    clan = await session.get(NinjaClan, member.clan_id)
    if not clan or clan.level < 10:
        raise GameError("Нужен клан 10 уровня.")
    members_count = int(await session.scalar(select(func.count()).select_from(NinjaClanMember).where(NinjaClanMember.clan_id == clan.id)) or 0)
    if members_count < 5:
        raise GameError("Для основания деревни нужно минимум 5 участников клана.")
    cost = 500_000
    if clan.treasury < cost:
        raise GameError(f"В казне клана нужно {cost:,} рё.")
    key = f"custom:{_slug(name)}"
    if len(key) <= len("custom:"):
        raise GameError("Некорректное название.")
    existing = await session.get(NinjaWorldState, f"village:{key}")
    if existing:
        raise GameError("Такая деревня уже существует.")
    clan.treasury -= cost
    row = NinjaWorldState(
        key=f"village:{key}",
        value={
            "key": key,
            "name": name[:64],
            "treasury": 0,
            "tax_rate": 2,
            "satisfaction": 80,
            "kage_id": user_id,
            "founder_clan_id": clan.id,
            "upgrades": {"academy": 1, "hospital": 1, "forge": 1, "intelligence": 1, "walls": 1},
            "diplomacy": {},
            "election": None,
            "territory_points": 0,
        },
    )
    session.add(row)
    upgrades = _copy_dict(clan.upgrades)
    upgrades["hidden_village"] = key
    clan.upgrades = upgrades
    member_ids = (await session.scalars(select(NinjaClanMember.user_id).where(NinjaClanMember.clan_id == clan.id))).all()
    for member_id in member_ids:
        member_profile = await session.get(NinjaProfile, int(member_id))
        if member_profile is not None:
            _set_flag(member_profile, "custom_village", key)
    session.add(NinjaEventLog(event_type="hidden_village_created", user_id=user_id, payload={"name": name, "clan": clan.id}))
    return f"🏯 Основана скрытая деревня «{name[:64]}». Вы — первый Каге."


# ---------------------------------------------------------------------------
# Bank, black market, direct P2P trades and player contracts
# ---------------------------------------------------------------------------


async def bank_text(session: AsyncSession, user_id: int) -> str:
    profile = await require_profile(session, user_id)
    balance = int(_flag(profile, "bank_ryo", 0))
    return f"🏦 Банк шиноби\nКошелёк: {profile.ryo:,} рё\nСчёт: {balance:,} рё"


async def bank_move(session: AsyncSession, user_id: int, amount: int, *, deposit: bool) -> str:
    profile = await require_profile(session, user_id)
    amount = max(1, int(amount))
    bank = int(_flag(profile, "bank_ryo", 0))
    if deposit:
        if profile.ryo < amount:
            raise GameError("Недостаточно рё в кошельке.")
        profile.ryo -= amount
        bank += amount
        action = "внесено"
    else:
        if bank < amount:
            raise GameError("Недостаточно рё на счёте.")
        bank -= amount
        profile.ryo += amount
        action = "снято"
    _set_flag(profile, "bank_ryo", bank)
    return f"🏦 {action.capitalize()} {amount:,} рё. На счёте: {bank:,}."


async def black_market_text(session: AsyncSession, user_id: int) -> str:
    profile = await require_profile(session, user_id)
    day_key = utcnow().date().isoformat()
    stock = _copy_dict(_flag(profile, "black_market", {}))
    if stock.get("day") != day_key:
        pool = ["akatsuki_cloak", "chakra_crystal", "poison_gland", "explosive_tag", "chakra_pill"]
        picks = random.sample(pool, k=min(3, len(pool)))
        stock = {"day": day_key, "items": {key: int(ITEMS[key]["price"] * random.uniform(1.2, 2.2)) for key in picks}}
        _set_flag(profile, "black_market", stock)
    lines = ["🌑 Чёрный рынок"]
    for key, price in (stock.get("items") or {}).items():
        lines.append(f"• {key} — {ITEMS[key]['name']} — {int(price):,} рё")
    lines.append("Покупка: /blackmarket buy <key>")
    return "\n".join(lines)


async def black_market_buy(session: AsyncSession, user_id: int, item_key: str) -> str:
    profile = await require_profile(session, user_id)
    await black_market_text(session, user_id)
    stock = _copy_dict(_flag(profile, "black_market", {}))
    prices = _copy_dict(stock.get("items"))
    if item_key not in prices:
        raise GameError("Сегодня такого товара нет.")
    price = int(prices[item_key])
    if profile.ryo < price:
        raise GameError("Недостаточно рё.")
    profile.ryo -= price
    await add_item(session, user_id, item_key, 1)
    if random.random() < 0.08:
        profile.reputation -= 10
        suffix = " АНБУ заметили след сделки: репутация -10."
    else:
        suffix = ""
    return f"🌑 Куплено: {ITEMS[item_key]['name']} за {price:,} рё.{suffix}"


async def _take_items(session: AsyncSession, user_id: int, items: dict[str, int]) -> None:
    for key, qty in items.items():
        qty = max(1, int(qty))
        row = await session.scalar(select(NinjaItem).where(NinjaItem.user_id == user_id, NinjaItem.item_key == key))
        if not row or row.quantity < qty:
            raise GameError(f"Не хватает {ITEMS.get(key, {'name': key})['name']} ×{qty}.")
    for key, qty in items.items():
        row = await session.scalar(select(NinjaItem).where(NinjaItem.user_id == user_id, NinjaItem.item_key == key))
        row.quantity -= int(qty)


async def trade_create(
    session: AsyncSession,
    user_id: int,
    partner_id: int,
    give_item: str | None,
    give_qty: int,
    give_ryo: int,
    take_item: str | None,
    take_qty: int,
    take_ryo: int,
) -> str:
    profile = await require_profile(session, user_id)
    await require_profile(session, partner_id)
    if user_id == partner_id:
        raise GameError("Нельзя торговать с собой.")
    give_ryo, take_ryo = max(0, int(give_ryo)), max(0, int(take_ryo))
    give_items = {give_item: max(1, int(give_qty))} if give_item and give_item != "-" else {}
    take_items = {take_item: max(1, int(take_qty))} if take_item and take_item != "-" else {}
    if not give_items and not take_items and not give_ryo and not take_ryo:
        raise GameError("Пустая сделка.")
    if profile.ryo < give_ryo:
        raise GameError("Недостаточно рё для предложения.")
    await _take_items(session, user_id, give_items)
    profile.ryo -= give_ryo
    row = NinjaTrade(
        initiator_id=user_id,
        partner_id=int(partner_id),
        give_ryo=give_ryo,
        take_ryo=take_ryo,
        give_items=give_items,
        take_items=take_items,
        expires_at=utcnow() + timedelta(hours=24),
    )
    session.add(row)
    await session.flush()
    return f"🤝 Сделка #{row.id} создана для {partner_id}. Ценности инициатора помещены в эскроу на 24 часа."


async def trade_accept(session: AsyncSession, user_id: int, trade_id: int) -> str:
    partner = await require_profile(session, user_id)
    row = await session.get(NinjaTrade, int(trade_id))
    if not row or row.status != "pending" or row.partner_id != user_id:
        raise GameError("Сделка недоступна.")
    if row.expires_at and utcnow() > row.expires_at:
        raise GameError("Сделка истекла. Инициатор может вернуть эскроу отменой.")
    if partner.ryo < row.take_ryo:
        raise GameError("Недостаточно рё для принятия.")
    take_items = {str(k): int(v) for k, v in _copy_dict(row.take_items).items()}
    await _take_items(session, user_id, take_items)
    partner.ryo -= row.take_ryo
    initiator = await require_profile(session, row.initiator_id)
    initiator.ryo += row.take_ryo
    partner.ryo += row.give_ryo
    for key, qty in _copy_dict(row.give_items).items():
        await add_item(session, user_id, str(key), int(qty))
    for key, qty in take_items.items():
        await add_item(session, row.initiator_id, key, qty)
    row.status = "completed"
    row.completed_at = utcnow()
    return f"✅ Сделка #{row.id} завершена через эскроу."


async def trade_cancel(session: AsyncSession, user_id: int, trade_id: int) -> str:
    row = await session.get(NinjaTrade, int(trade_id))
    if not row or row.status != "pending" or row.initiator_id != user_id:
        raise GameError("Сделка недоступна для отмены.")
    profile = await require_profile(session, user_id)
    profile.ryo += row.give_ryo
    for key, qty in _copy_dict(row.give_items).items():
        await add_item(session, user_id, str(key), int(qty))
    row.status = "cancelled"
    return f"↩️ Сделка #{row.id} отменена, эскроу возвращён."


async def contracts_text(session: AsyncSession) -> str:
    rows = (await session.scalars(select(NinjaPlayerContract).where(NinjaPlayerContract.status == "open").order_by(NinjaPlayerContract.id.desc()).limit(15))).all()
    if not rows:
        return "📋 Доска контрактов пуста."
    lines = ["📋 Контракты игроков"]
    for row in rows:
        lines.append(f"#{row.id} · {row.contract_type} ×{row.amount} · {row.reward_ryo:,} рё")
    return "\n".join(lines)


async def contract_create(session: AsyncSession, user_id: int, kind: str, amount: int, reward: int) -> str:
    profile = await require_profile(session, user_id)
    if kind not in {"missions", "pvp", "craft"}:
        raise GameError("Типы контрактов: missions, pvp, craft.")
    amount = max(1, min(100, int(amount)))
    reward = max(100, int(reward))
    if profile.ryo < reward:
        raise GameError("Недостаточно рё для эскроу награды.")
    profile.ryo -= reward
    row = NinjaPlayerContract(creator_id=user_id, contract_type=kind, amount=amount, reward_ryo=reward)
    session.add(row)
    await session.flush()
    return f"📋 Контракт #{row.id} создан. Награда {reward:,} рё помещена в эскроу."


async def contract_take(session: AsyncSession, user_id: int, contract_id: int) -> str:
    profile = await require_profile(session, user_id)
    row = await session.get(NinjaPlayerContract, int(contract_id))
    if not row or row.status != "open":
        raise GameError("Контракт уже недоступен.")
    if row.creator_id == user_id:
        raise GameError("Нельзя выполнить свой контракт.")
    counters = _copy_dict(profile.counters)
    baseline = {
        "missions": profile.missions_completed,
        "pvp": profile.pvp_wins,
        "craft": int(counters.get("crafts", 0)),
    }[row.contract_type]
    row.assignee_id = user_id
    row.status = "active"
    row.accepted_at = utcnow()
    row.payload = {"baseline": int(baseline)}
    return f"🎯 Контракт #{row.id} принят. Требуется {row.contract_type} ×{row.amount}."


async def contract_complete(session: AsyncSession, user_id: int, contract_id: int) -> str:
    profile = await require_profile(session, user_id)
    row = await session.get(NinjaPlayerContract, int(contract_id))
    if not row or row.status != "active" or row.assignee_id != user_id:
        raise GameError("Активный контракт не найден.")
    counters = _copy_dict(profile.counters)
    current = {
        "missions": profile.missions_completed,
        "pvp": profile.pvp_wins,
        "craft": int(counters.get("crafts", 0)),
    }[row.contract_type]
    baseline = int(_copy_dict(row.payload).get("baseline", 0))
    progress = current - baseline
    if progress < row.amount:
        raise GameError(f"Прогресс: {progress}/{row.amount}.")
    profile.ryo += row.reward_ryo
    row.status = "completed"
    row.completed_at = utcnow()
    return f"✅ Контракт #{row.id} выполнен. +{row.reward_ryo:,} рё."


# ---------------------------------------------------------------------------
# Caravans and resource logistics
# ---------------------------------------------------------------------------


async def caravan_create(session: AsyncSession, user_id: int, destination: str, item_key: str, qty: int, insured: bool) -> str:
    profile = await require_profile(session, user_id)
    origin = _effective_village(profile)
    if destination not in VILLAGES or destination == origin:
        raise GameError("Укажите другую великую деревню.")
    if item_key not in ITEMS or ITEMS[item_key].get("unique"):
        raise GameError("Этот груз нельзя отправить караваном.")
    qty = max(1, min(100, int(qty)))
    await _take_items(session, user_id, {item_key: qty})
    value = int(ITEMS[item_key].get("price", 100)) * qty
    route_fee = max(100, int(value * 0.03))
    insurance_fee = int(value * 0.05) if insured else 0
    if profile.ryo < route_fee + insurance_fee:
        # return cargo immediately because transaction may still be committed after GameError in callers not rolling back
        await add_item(session, user_id, item_key, qty)
        raise GameError(f"Нужно {route_fee + insurance_fee:,} рё на маршрут и страховку.")
    profile.ryo -= route_fee + insurance_fee
    row = NinjaCaravan(
        owner_id=user_id,
        origin=origin,
        destination=destination,
        cargo={item_key: qty},
        cargo_value=value,
        insured=bool(insured),
        arrives_at=utcnow() + timedelta(hours=2),
    )
    session.add(row)
    await session.flush()
    return f"🚚 Караван #{row.id} отправлен в {VILLAGES[destination]['name']}. Прибытие через 2 часа."


async def caravan_claim(session: AsyncSession, user_id: int, caravan_id: int) -> str:
    profile = await require_profile(session, user_id)
    row = await session.get(NinjaCaravan, int(caravan_id))
    if not row or row.owner_id != user_id or row.status != "traveling":
        raise GameError("Караван недоступен.")
    if utcnow() < row.arrives_at:
        remain = int((row.arrives_at - utcnow()).total_seconds() // 60) + 1
        raise GameError(f"Караван ещё в пути: ~{remain} мин.")
    attack_chance = 0.20
    if row.escort_user_id:
        attack_chance -= 0.08
    if random.random() < attack_chance:
        row.status = "lost"
        row.outcome = {"attacked": True}
        refund = int(row.cargo_value * 0.70) if row.insured else 0
        profile.ryo += refund
        row.claimed_at = utcnow()
        return f"💥 Караван #{row.id} разграблен." + (f" Страховка вернула {refund:,} рё." if refund else "")
    profit = int(row.cargo_value * random.uniform(1.10, 1.35))
    profile.ryo += profit
    row.status = "delivered"
    row.outcome = {"attacked": False, "profit": profit}
    row.claimed_at = utcnow()
    return f"✅ Караван #{row.id} прибыл. Продажа груза принесла {profit:,} рё."


# ---------------------------------------------------------------------------
# Card teams, synergies and card arena
# ---------------------------------------------------------------------------


def _card_power(card: Any, level: int, stars: int) -> int:
    base = int(card.hp * 0.25 + card.attack * 2 + card.defense * 1.4 + card.speed + card.chakra * 0.15)
    return int(base * (1 + max(0, level - 1) * 0.012) * (1 + max(0, stars - 1) * 0.10))


async def card_team_set(session: AsyncSession, user_id: int, keys: list[str]) -> str:
    profile = await require_profile(session, user_id)
    keys = list(dict.fromkeys(keys))[:5]
    if not 1 <= len(keys) <= 5:
        raise GameError("Команда: от 1 до 5 карточек.")
    owned = set((await session.scalars(select(NinjaCard.card_key).where(NinjaCard.user_id == user_id, NinjaCard.card_key.in_(keys)))).all())
    missing = [key for key in keys if key not in owned]
    if missing:
        raise GameError("Нет карточек: " + ", ".join(missing))
    _set_flag(profile, "card_team", keys)
    return "🎴 Команда сохранена: " + ", ".join(CARDS[key].name for key in keys if key in CARDS)


async def card_team_text(session: AsyncSession, user_id: int) -> str:
    profile = await require_profile(session, user_id)
    keys = [str(x) for x in _copy_list(_flag(profile, "card_team", []))]
    if not keys:
        return "🎴 Команда карточек не собрана. /cardteam set key1 key2 ..."
    rows = (await session.scalars(select(NinjaCard).where(NinjaCard.user_id == user_id, NinjaCard.card_key.in_(keys)))).all()
    by_key = {r.card_key: r for r in rows}
    total = 0
    tags: list[str] = []
    lines = ["🎴 Боевая команда"]
    for key in keys:
        card, row = CARDS.get(key), by_key.get(key)
        if not card or not row:
            continue
        power = _card_power(card, row.level, row.stars)
        total += power
        tags.extend(card.tags)
        lines.append(f"• {card.name} · ⭐{row.stars} · сила {power:,}")
    bonus = 0.0
    if sum(1 for tag in tags if tag == "akatsuki") >= 3: bonus += 0.15
    if sum(1 for tag in tags if tag == "uchiha") >= 3: bonus += 0.15
    if all(key in keys for key in ("naruto_genin", "sakura", "kakashi")): bonus += 0.10
    total = int(total * (1 + bonus))
    lines += [f"Синергия: +{int(bonus * 100)}%", f"Общая сила: {total:,}"]
    return "\n".join(lines)


async def card_arena(session: AsyncSession, user_id: int) -> str:
    profile = await require_profile(session, user_id)
    own_text = await card_team_text(session, user_id)
    keys = [str(x) for x in _copy_list(_flag(profile, "card_team", []))]
    if not keys:
        raise GameError("Сначала соберите /cardteam set ...")
    rows = (await session.scalars(select(NinjaCard).where(NinjaCard.user_id == user_id, NinjaCard.card_key.in_(keys)))).all()
    own_power = sum(_card_power(CARDS[r.card_key], r.level, r.stars) for r in rows if r.card_key in CARDS)
    foe_power = int(own_power * random.uniform(0.82, 1.20))
    roll = own_power * random.uniform(0.90, 1.10)
    if roll >= foe_power:
        profile.arena_tokens += 8
        profile.ryo += 350
        return own_text + f"\n\n🏆 Карточная арена: победа ({own_power:,} vs {foe_power:,}). +8 жетонов, +350 рё."
    return own_text + f"\n\n💀 Карточная арена: поражение ({own_power:,} vs {foe_power:,})."


# ---------------------------------------------------------------------------
# Custom techniques, prestige, morality, injuries and titles
# ---------------------------------------------------------------------------


async def custom_technique_create(session: AsyncSession, user_id: int, name: str, element: str, kind: str = "ninjutsu") -> str:
    profile = await require_profile(session, user_id)
    if profile.level < 60:
        raise GameError("Собственную технику можно создать с 60 уровня.")
    if element not in ELEMENTS and element != "none":
        raise GameError("Стихия: fire, water, lightning, wind, earth или none.")
    known_elements = set(_known_elements(profile)) if "_known_elements" in globals() else {profile.primary_element, profile.secondary_element}
    if element != "none" and element not in known_elements:
        raise GameError("Нельзя создать технику чужой стихии.")
    kind = kind if kind in {"ninjutsu", "taijutsu", "genjutsu"} else "ninjutsu"
    slug = "custom_" + _slug(name)
    if slug == "custom_":
        raise GameError("Некорректное название.")
    existing = await session.scalar(select(NinjaCustomTechnique).where(NinjaCustomTechnique.user_id == user_id, NinjaCustomTechnique.slug == slug))
    if existing:
        raise GameError("Техника с таким именем уже создана.")
    count = int(await session.scalar(select(func.count()).select_from(NinjaCustomTechnique).where(NinjaCustomTechnique.user_id == user_id)) or 0)
    if count >= 3:
        raise GameError("Максимум 3 персональные техники.")
    cost = 75_000 + count * 50_000
    if profile.ryo < cost:
        raise GameError(f"Создание техники стоит {cost:,} рё.")
    profile.ryo -= cost
    power = min(500, 250 + profile.level * 3)
    chakra = max(100, int(power * 0.48))
    row = NinjaCustomTechnique(
        user_id=user_id,
        slug=slug,
        name=name[:64],
        element=None if element == "none" else element,
        kind=kind,
        chakra_cost=chakra,
        power=power,
        accuracy=0.90,
        cooldown=3,
    )
    session.add(row)
    session.add(NinjaEventLog(event_type="custom_technique", user_id=user_id, payload={"slug": slug, "name": name[:64]}))
    return f"✨ Создана техника «{name[:64]}» ({slug}): сила {power}, чакра {chakra}, откат 3."


async def custom_techniques_text(session: AsyncSession, user_id: int) -> str:
    await require_profile(session, user_id)
    rows = (await session.scalars(select(NinjaCustomTechnique).where(NinjaCustomTechnique.user_id == user_id))).all()
    if not rows:
        return "✨ Персональных техник пока нет."
    return "\n".join(["✨ Персональные техники"] + [f"• {r.slug}: {r.name} · сила {r.power} · чакра {r.chakra_cost}" for r in rows])


async def prestige(session: AsyncSession, user_id: int) -> str:
    profile = await require_profile(session, user_id)
    if profile.level < 100 or profile.story_chapter <= 22:
        raise GameError("Престиж открывается на 100 уровне после завершения 22 главы.")
    current = int(_flag(profile, "prestige", 0))
    if current >= 10:
        raise GameError("Достигнут максимальный престиж.")
    _set_flag(profile, "prestige", current + 1)
    profile.level = 1
    profile.xp = 0
    profile.max_hp += 25
    profile.max_chakra += 25
    profile.hp = profile.max_hp
    profile.chakra = profile.max_chakra
    profile.ninjutsu += 3
    profile.taijutsu += 3
    profile.genjutsu += 3
    profile.defense += 3
    profile.speed += 3
    return f"🌟 Престиж {current + 1}! Уровень сброшен, техники/карточки/предметы сохранены, базовые характеристики усилены."


async def morality_choice(session: AsyncSession, user_id: int, choice: str) -> str:
    profile = await require_profile(session, user_id)
    mapping = {
        "mercy": {"compassion": 8, "loyalty": 3, "cruelty": -3},
        "execute": {"cruelty": 8, "compassion": -5, "ambition": 2},
        "deceive": {"cunning": 7, "loyalty": -2},
        "loyal": {"loyalty": 8, "ambition": -1},
        "ambition": {"ambition": 8, "loyalty": -2},
    }
    if choice not in mapping:
        raise GameError("Выбор: mercy, execute, deceive, loyal, ambition.")
    morality = _copy_dict(profile.morality)
    for key, delta in mapping[choice].items():
        morality[key] = max(0, min(100, int(morality.get(key, 0)) + delta))
    profile.morality = morality
    session.add(NinjaEventLog(event_type="story_choice", user_id=user_id, payload={"choice": choice, "morality": morality}))
    return "🧭 Выбор сохранён. " + " · ".join(f"{k}: {v}" for k, v in morality.items())


async def treat_injuries(session: AsyncSession, user_id: int) -> str:
    profile = await require_profile(session, user_id)
    injuries = _copy_list(profile.injuries)
    if not injuries:
        return "🏥 Травм нет."
    cost = 800 * len(injuries)
    if profile.ryo < cost:
        raise GameError(f"Лечение стоит {cost:,} рё.")
    profile.ryo -= cost
    profile.injuries = []
    profile.hp = profile.max_hp
    return f"🏥 Вылечено травм: {len(injuries)}. Стоимость: {cost:,} рё."


async def set_title(session: AsyncSession, user_id: int, key: str) -> str:
    profile = await require_profile(session, user_id)
    titles = [str(x) for x in _copy_list(profile.titles)]
    if key not in titles:
        raise GameError("Этот титул ещё не открыт.")
    profile.active_title = key
    return f"🏷 Активный титул установлен: {key}."


# ---------------------------------------------------------------------------
# Season progress, notification preferences, chronicle and dynasty/legacy
# ---------------------------------------------------------------------------


def grant_season_xp(profile: NinjaProfile, amount: int) -> None:
    season_key = utcnow().strftime("%Y-%m")
    season = _copy_dict(_flag(profile, "season", {}))
    if season.get("key") != season_key:
        season = {"key": season_key, "xp": 0, "claimed": [], "premium": False}
    season["xp"] = int(season.get("xp", 0)) + max(0, int(amount))
    _set_flag(profile, "season", season)


async def season_text(session: AsyncSession, user_id: int) -> str:
    profile = await require_profile(session, user_id)
    grant_season_xp(profile, 0)
    season = _copy_dict(_flag(profile, "season", {}))
    counters = _copy_dict(profile.counters)
    earned = (
        int(profile.missions_completed) * 80
        + int(profile.pvp_wins) * 120
        + int(counters.get("crafts", 0)) * 50
        + max(0, int(profile.story_chapter) - 1) * 300
        + max(0, int(profile.level) - 1) * 25
    )
    if earned > int(season.get("xp", 0)):
        season["xp"] = earned
        _set_flag(profile, "season", season)
    level = min(50, int(season.get("xp", 0)) // 500 + 1)
    claimed = {int(x) for x in season.get("claimed") or []}
    claimable = [milestone for milestone in range(5, level + 1, 5) if milestone not in claimed]
    if claimable:
        reward = 0
        crystals = 0
        for milestone in claimable:
            reward += milestone * 150
            if milestone % 10 == 0:
                crystals += 1
        profile.ryo += reward
        profile.chakra_crystals += crystals
        claimed.update(claimable)
        season["claimed"] = sorted(claimed)
        _set_flag(profile, "season", season)
        suffix = f"\n🎁 Получено за уровни {claimable}: {reward:,} рё" + (f", {crystals} крист." if crystals else "")
    else:
        suffix = ""
    return f"🎟 Сезон {season.get('key')}\nУровень пропуска: {level}/50\nXP: {int(season.get('xp', 0)):,}\nPremium: {'да' if season.get('premium') else 'нет'}{suffix}"


async def notification_toggle(session: AsyncSession, user_id: int, key: str) -> str:
    profile = await require_profile(session, user_id)
    allowed = {"raids", "tournaments", "clan", "village", "market", "story"}
    if key not in allowed:
        raise GameError("Категории: raids, tournaments, clan, village, market, story.")
    prefs = _copy_dict(_flag(profile, "notifications", {k: True for k in allowed}))
    prefs[key] = not bool(prefs.get(key, True))
    _set_flag(profile, "notifications", prefs)
    return f"🔔 {key}: {'включено' if prefs[key] else 'выключено'}."


async def chronicle_text(session: AsyncSession, user_id: int | None = None) -> str:
    stmt = select(NinjaEventLog).order_by(NinjaEventLog.id.desc()).limit(20)
    if user_id is not None:
        stmt = stmt.where(NinjaEventLog.user_id == user_id)
    rows = (await session.scalars(stmt)).all()
    if not rows:
        return "📚 Хроника пока пуста."
    lines = ["📚 Хроника мира" if user_id is None else "📖 Личная хроника"]
    for row in reversed(rows):
        lines.append(f"• #{row.id} {row.event_type}: {row.payload}")
    return "\n".join(lines)


async def dynasty_create(session: AsyncSession, user_id: int, name: str) -> str:
    profile = await require_profile(session, user_id)
    if profile.level < 75 and int(_flag(profile, "prestige", 0)) < 1:
        raise GameError("Династия открывается с 75 уровня или после первого престижа.")
    if await session.scalar(select(NinjaDynasty).where(NinjaDynasty.name == name[:64])):
        raise GameError("Такая династия уже существует.")
    if _flag(profile, "dynasty_id"):
        raise GameError("Вы уже состоите в династии.")
    row = NinjaDynasty(
        name=name[:64],
        founder_id=user_id,
        members=[user_id],
        legacy={"bloodline": profile.bloodline, "element": profile.primary_element, "founder": profile.name},
    )
    session.add(row)
    await session.flush()
    _set_flag(profile, "dynasty_id", row.id)
    return f"🏛 Основана династия «{row.name}». Наследие: {profile.bloodline}/{profile.primary_element}."


async def dynasty_join(session: AsyncSession, user_id: int, name: str) -> str:
    profile = await require_profile(session, user_id)
    if _flag(profile, "dynasty_id"):
        raise GameError("Вы уже состоите в династии.")
    row = await session.scalar(select(NinjaDynasty).where(NinjaDynasty.name == name[:64]))
    if not row:
        raise GameError("Династия не найдена.")
    members = [int(x) for x in _copy_list(row.members)]
    if user_id not in members:
        members.append(user_id)
    row.members = members
    _set_flag(profile, "dynasty_id", row.id)
    return f"🏛 Вы присоединились к династии «{row.name}»."


async def dynasty_text(session: AsyncSession, user_id: int) -> str:
    profile = await require_profile(session, user_id)
    dynasty_id = int(_flag(profile, "dynasty_id", 0) or 0)
    if not dynasty_id:
        return "🏛 Династии нет. /dynasty create <название>"
    row = await session.get(NinjaDynasty, dynasty_id)
    if not row:
        _set_flag(profile, "dynasty_id", None)
        return "🏛 Династия не найдена."
    return f"🏛 {row.name}\nОснователь: {row.founder_id}\nУчастников: {len(row.members or [])}\nПрестиж: {row.prestige}\nНаследие: {row.legacy}"


# ---------------------------------------------------------------------------
# Social bond and personal rival
# ---------------------------------------------------------------------------


async def bond_propose(session: AsyncSession, user_id: int, partner_id: int) -> str:
    await require_profile(session, user_id)
    await require_profile(session, partner_id)
    if user_id == partner_id:
        raise GameError("Нельзя создать союз с собой.")
    existing = await session.scalar(
        select(NinjaBond).where(
            NinjaBond.status.in_(["pending", "active"]),
            ((NinjaBond.proposer_id == user_id) | (NinjaBond.partner_id == user_id)),
        )
    )
    if existing:
        raise GameError("У вас уже есть активный или ожидающий союз.")
    partner_existing = await session.scalar(
        select(NinjaBond).where(
            NinjaBond.status == "active",
            ((NinjaBond.proposer_id == partner_id) | (NinjaBond.partner_id == partner_id)),
        )
    )
    if partner_existing:
        raise GameError("У этого шиноби уже есть семейный союз.")
    row = NinjaBond(proposer_id=user_id, partner_id=partner_id)
    session.add(row)
    await session.flush()
    return f"💍 Предложение союза #{row.id} отправлено шиноби {partner_id}. Принятие: /bond accept {row.id}"


async def bond_accept(session: AsyncSession, user_id: int, bond_id: int) -> str:
    row = await session.get(NinjaBond, int(bond_id))
    if not row or row.status != "pending" or row.partner_id != user_id:
        raise GameError("Предложение союза недоступно.")
    row.status = "active"
    row.accepted_at = utcnow()
    a = await require_profile(session, row.proposer_id)
    b = await require_profile(session, row.partner_id)
    _set_flag(a, "bond_id", row.id)
    _set_flag(b, "bond_id", row.id)
    session.add(NinjaEventLog(event_type="family_bond", user_id=user_id, payload={"bond": row.id, "partner": row.proposer_id}))
    return f"💍 Семейный союз #{row.id} заключён. Он даёт общий социальный статус, но не боевое преимущество."


async def bond_text(session: AsyncSession, user_id: int) -> str:
    profile = await require_profile(session, user_id)
    bond_id = int(_flag(profile, "bond_id", 0) or 0)
    if not bond_id:
        pending = await session.scalar(
            select(NinjaBond).where(NinjaBond.partner_id == user_id, NinjaBond.status == "pending").order_by(NinjaBond.id.desc())
        )
        if pending:
            return f"💍 Ожидает предложение #{pending.id} от {pending.proposer_id}. /bond accept {pending.id}"
        return "💍 Семейного союза нет. /bond propose USER_ID"
    row = await session.get(NinjaBond, bond_id)
    if not row or row.status != "active":
        _set_flag(profile, "bond_id", None)
        return "💍 Семейного союза нет."
    partner = row.partner_id if row.proposer_id == user_id else row.proposer_id
    return f"💍 Семейный союз #{row.id}\nПартнёр: {partner}\nОбщий дом: ур.{row.shared_home_level}"


async def rival_text(session: AsyncSession, user_id: int) -> str:
    profile = await require_profile(session, user_id)
    rival = _copy_dict(_flag(profile, "rival", {}))
    if not rival:
        seed_names = ["Кайто", "Рен", "Хиро", "Акира", "Сора", "Юки"]
        rival = {
            "name": random.choice(seed_names),
            "level": max(1, profile.level),
            "relation": -10,
            "wins": 0,
            "losses": 0,
        }
        _set_flag(profile, "rival", rival)
    rival["level"] = max(int(rival.get("level", 1)), max(1, profile.level - 2))
    _set_flag(profile, "rival", rival)
    return (
        f"🥷 Личный соперник: {rival['name']}\n"
        f"Уровень: {rival['level']} · отношение {rival.get('relation', -10)}\n"
        f"Ваши победы: {rival.get('wins', 0)} · поражения: {rival.get('losses', 0)}"
    )


async def rival_duel(session: AsyncSession, user_id: int) -> str:
    profile = await require_profile(session, user_id)
    await rival_text(session, user_id)
    rival = _copy_dict(_flag(profile, "rival", {}))
    own = player_power(profile) * random.uniform(0.88, 1.12)
    foe = (220 + int(rival.get("level", 1)) * 55) * random.uniform(0.90, 1.10)
    if own >= foe:
        rival["wins"] = int(rival.get("wins", 0)) + 1
        rival["relation"] = min(100, int(rival.get("relation", -10)) + 2)
        profile.xp += 180 + profile.level * 10
        profile.ryo += 350
        result = "🏆 Вы победили соперника. +350 рё."
    else:
        rival["losses"] = int(rival.get("losses", 0)) + 1
        rival["relation"] = max(-100, int(rival.get("relation", -10)) - 1)
        result = "💥 Соперник оказался сильнее."
    rival["level"] = max(int(rival.get("level", 1)), profile.level)
    _set_flag(profile, "rival", rival)
    return (await rival_text(session, user_id)) + "\n\n" + result

# ---------------------------------------------------------------------------
# Dojutsu, transformations, ANBU missions, bounty hunts, clan wars and squads
# ---------------------------------------------------------------------------


async def _learn_free_technique(session: AsyncSession, user_id: int, technique_key: str) -> bool:
    from .models import NinjaTechnique

    existing = await session.scalar(
        select(NinjaTechnique).where(
            NinjaTechnique.user_id == user_id,
            NinjaTechnique.technique_key == technique_key,
        )
    )
    if existing:
        return False
    session.add(NinjaTechnique(user_id=user_id, technique_key=technique_key, level=1, mastery=0, equipped=True))
    return True


async def _consume_item_checked(session: AsyncSession, user_id: int, item_key: str, quantity: int) -> None:
    quantity = max(1, int(quantity))
    row = await session.scalar(
        select(NinjaItem).where(
            NinjaItem.user_id == user_id,
            NinjaItem.item_key == item_key,
        )
    )
    if not row or int(row.quantity) < quantity:
        item_name = ITEMS.get(item_key, {"name": item_key}).get("name", item_key)
        raise GameError(f"Нужно: {item_name} ×{quantity}.")
    row.quantity -= quantity


async def dojutsu_status(session: AsyncSession, user_id: int) -> str:
    profile = await require_profile(session, user_id)
    flags = _copy_dict(profile.flags)
    dojutsu = str(flags.get("dojutsu") or "none")
    labels = {
        "none": "—",
        "byakugan": "⚪ Бьякуган",
        "sharingan": "🔴 Шаринган I томоэ",
        "sharingan_2": "🔴 Шаринган II томоэ",
        "sharingan_3": "🔴 Шаринган III томоэ",
        "mangekyo": "🌑 Мангекё Шаринган",
        "eternal_mangekyo": "♾ Вечный Мангекё Шаринган",
        "rinnegan": "🟣 Риннеган",
    }
    stress = int(flags.get("emotional_stress", 0))
    sage = "открыт" if flags.get("sage_unlocked") else "не открыт"
    max_gate = max(1, min(8, 1 + profile.level // 12))
    lines = [
        "👁 <b>Додзюцу и формы</b>",
        f"Додзюцу: {labels.get(dojutsu, dojutsu)}",
        f"Эмоциональный стресс: {stress}",
        f"🐸 Режим Мудреца: {sage}",
        f"🔥 Доступные Врата: до {max_gate}",
    ]
    if (profile.biju or {}).get("key"):
        lines.append(f"🐾 Биджу: доверие {int((profile.biju or {}).get('trust', 0))}/100")
    lines.append("Пробуждение/эволюция: <code>/dojutsu awaken</code>")
    lines.append("Форма на следующий бой: <code>/form sharingan|sage|gates 5|biju</code>")
    return "\n".join(lines)


async def dojutsu_awaken(session: AsyncSession, user_id: int) -> str:
    profile = await require_profile(session, user_id)
    flags = _copy_dict(profile.flags)
    current = str(flags.get("dojutsu") or "none")
    stress = int(flags.get("emotional_stress", 0))
    bloodline = str(profile.bloodline or "none")

    # Hyuga and Otsutsuki obtain Byakugan through their bloodline rather than emotional stress.
    if bloodline in {"hyuga", "otsutsuki"} and current == "none":
        if profile.level < 10:
            raise GameError("Бьякуган можно пробудить с 10 уровня.")
        flags["dojutsu"] = "byakugan"
        profile.flags = flags
        session.add(NinjaEventLog(event_type="dojutsu_awakened", user_id=user_id, payload={"dojutsu": "byakugan"}))
        return "⚪ Ваш Бьякуган пробудился. Точность и чтение чакры усилены."

    if bloodline not in {"uchiha", "otsutsuki", "senju"} and current not in {"byakugan"}:
        raise GameError("У вашей родословной нет естественного пути к этому додзюцу.")

    if bloodline == "uchiha":
        progression = [
            ("none", "sharingan", 12, 0, "🔴 Пробуждён Шаринган I томоэ."),
            ("sharingan", "sharingan_2", 20, 5, "🔴 Шаринган развился до II томоэ."),
            ("sharingan_2", "sharingan_3", 30, 10, "🔴 Шаринган развился до III томоэ."),
            ("sharingan_3", "mangekyo", 45, 20, "🌑 Пробуждён Мангекё Шаринган."),
        ]
        for expected, new_value, level_req, stress_req, text in progression:
            if current == expected:
                if profile.level < level_req or stress < stress_req:
                    raise GameError(f"Нужно: уровень {level_req}+ и эмоциональный стресс {stress_req}+.")
                flags["dojutsu"] = new_value
                profile.flags = flags
                unlocked: list[str] = []
                if new_value == "mangekyo":
                    ability = random.choice(["amaterasu", "kamui", "tsukuyomi"])
                    for key in (ability, "susanoo"):
                        if await _learn_free_technique(session, user_id, key):
                            unlocked.append(key)
                    flags = _copy_dict(profile.flags)
                    flags["mangekyo_ability"] = ability
                    profile.flags = flags
                session.add(NinjaEventLog(event_type="dojutsu_awakened", user_id=user_id, payload={"dojutsu": new_value, "unlocked": unlocked}))
                if unlocked:
                    return text + "\n📜 Открыты техники: " + ", ".join(unlocked)
                return text

        if current == "mangekyo":
            if profile.level < 70:
                raise GameError("Вечный Мангекё требует 70 уровня.")
            await _consume_item_checked(session, user_id, "chakra_crystal", 8)
            flags["dojutsu"] = "eternal_mangekyo"
            profile.flags = flags
            session.add(NinjaEventLog(event_type="dojutsu_awakened", user_id=user_id, payload={"dojutsu": "eternal_mangekyo"}))
            return "♾ Мангекё стабилизирован. Получен Вечный Мангекё Шаринган."

    # End-game Rinnegan is intentionally extremely gated and expensive.
    if current in {"eternal_mangekyo", "byakugan", "none"} and bloodline in {"uchiha", "senju", "otsutsuki"}:
        if profile.level < 85 or profile.story_chapter < 17:
            raise GameError("Риннеган требует 85 уровень и прохождение как минимум 17-й главы.")
        await _consume_item_checked(session, user_id, "chakra_crystal", 12)
        flags["dojutsu"] = "rinnegan"
        profile.flags = flags
        for key in ("shinra_tensei", "chibaku_tensei"):
            await _learn_free_technique(session, user_id, key)
        session.add(NinjaEventLog(event_type="dojutsu_awakened", user_id=user_id, payload={"dojutsu": "rinnegan"}))
        return "🟣 Пробуждён Риннеган. Открыты Шинра Тенсей и Чибаку Тенсей."

    raise GameError("Следующая ступень додзюцу пока недоступна.")


async def sage_training(session: AsyncSession, user_id: int) -> str:
    profile = await require_profile(session, user_id)
    flags = _copy_dict(profile.flags)
    if flags.get("sage_unlocked"):
        return "🐸 Вы уже владеете Режимом Мудреца. /form sage"
    if profile.level < 45:
        raise GameError("Обучение сендзюцу доступно с 45 уровня.")
    summons = {str(x) for x in _copy_list(profile.summons)}
    if profile.mentor != "jiraiya" and not ({"toads", "snakes"} & summons):
        raise GameError("Нужен наставник Джирайя или контракт с жабами/змеями.")
    trust = int(flags.get("mentor_trust", 0))
    if profile.mentor == "jiraiya" and trust < 60:
        raise GameError("Для обучения у Джирайи нужно доверие наставника 60+.")
    if profile.ryo < 20_000:
        raise GameError("Подготовка к обучению стоит 20 000 рё.")
    profile.ryo -= 20_000
    flags["sage_unlocked"] = True
    profile.flags = flags
    await _learn_free_technique(session, user_id, "sage_art")
    session.add(NinjaEventLog(event_type="sage_mode_unlocked", user_id=user_id, payload={"mentor": profile.mentor}))
    return "🐸 Испытание пройдено. Режим Мудреца открыт, техника «Искусство Мудреца» изучена."


async def activate_form(session: AsyncSession, user_id: int, form: str, gate: int | None = None) -> str:
    profile = await require_profile(session, user_id)
    flags = _copy_dict(profile.flags)
    value = form.strip().lower()
    dojutsu = str(flags.get("dojutsu") or "none")

    aliases = {
        "sharingan": "sharingan",
        "mangekyo": "mangekyo",
        "ems": "eternal_mangekyo",
        "eternal": "eternal_mangekyo",
        "rinnegan": "rinnegan",
        "byakugan": "byakugan",
        "sage": "sage",
        "biju": "biju",
        "off": "",
        "none": "",
    }
    if value == "gates":
        gate = int(gate or 1)
        max_gate = max(1, min(8, 1 + profile.level // 12))
        if gate < 1 or gate > max_gate:
            raise GameError(f"Сейчас вы можете открыть Врата только 1–{max_gate}.")
        if gate == 8 and (profile.level < 85 or profile.taijutsu < 120):
            raise GameError("Восьмые Врата требуют 85 уровень и тайдзюцу 120+.")
        selected = f"gates_{gate}"
    else:
        selected = aliases.get(value)
        if selected is None:
            raise GameError("Форма: byakugan, sharingan, mangekyo, rinnegan, sage, gates N, biju или off.")

    if selected == "byakugan" and dojutsu not in {"byakugan", "rinnegan"}:
        raise GameError("Бьякуган ещё не пробуждён. /dojutsu awaken")
    if selected in {"sharingan", "mangekyo", "eternal_mangekyo", "rinnegan"}:
        hierarchy = {"none": 0, "byakugan": 0, "sharingan": 1, "sharingan_2": 2, "sharingan_3": 3, "mangekyo": 4, "eternal_mangekyo": 5, "rinnegan": 6}
        need = hierarchy[selected]
        if hierarchy.get(dojutsu, 0) < need:
            raise GameError("Эта форма додзюцу ещё не пробуждена. /dojutsu awaken")
    if selected == "sage" and not flags.get("sage_unlocked"):
        raise GameError("Сначала пройдите /sage_train.")
    if selected == "biju":
        data = _copy_dict(profile.biju)
        if not data.get("key"):
            raise GameError("У вас нет хвостатого зверя.")
        if int(data.get("trust", 0)) < 25:
            raise GameError("Для режима биджу нужно доверие 25+. Используйте /biju_train.")

    flags["battle_form"] = selected or None
    profile.flags = flags
    if not selected:
        return "🧘 Боевая форма отключена."
    return f"🔥 Для следующего боя подготовлена форма: {selected}."


async def anbu_mission(session: AsyncSession, user_id: int) -> str:
    profile = await require_profile(session, user_id)
    if profile.ninja_rank not in {"anbu", "kage"}:
        raise GameError("Секретные миссии доступны только АНБУ и Каге.")
    if profile.energy < 20:
        raise GameError("Для миссии АНБУ нужно 20 энергии.")
    profile.energy -= 20
    missions = [
        ("ликвидация опасного нукенина", 1.08),
        ("проникновение в вражеский разведцентр", 1.00),
        ("перехват секретного свитка", 0.96),
        ("защита агента под прикрытием", 1.02),
        ("контрразведывательная операция", 0.94),
    ]
    title, difficulty = random.choice(missions)
    own = player_power(profile) * random.uniform(0.90, 1.15)
    target = (profile.level * 58 + 520) * difficulty * random.uniform(0.90, 1.12)
    counters = _copy_dict(profile.counters)
    if own >= target:
        ryo = 8_000 + profile.level * 180
        xp = 650 + profile.level * 18
        profile.ryo += ryo
        profile.xp += xp
        profile.reputation += 18
        profile.village_points += 20
        counters["anbu_missions"] = int(counters.get("anbu_missions", 0)) + 1
        profile.counters = counters
        session.add(NinjaEventLog(event_type="anbu_mission", user_id=user_id, payload={"mission": title, "result": "success"}))
        return f"🎭 Секретная миссия: {title}\n✅ Выполнено. +{ryo:,} рё · +{xp} опыта · +20 очков деревни."
    flags = _copy_dict(profile.flags)
    flags["emotional_stress"] = int(flags.get("emotional_stress", 0)) + 2
    profile.flags = flags
    return f"🎭 Секретная миссия: {title}\n❌ Операция сорвана. Цель оказалась сильнее; эмоциональный стресс +2."


async def hunt_nukenin(session: AsyncSession, user_id: int, target_id: int) -> str:
    hunter = await require_profile(session, user_id)
    target = await require_profile(session, int(target_id))
    if hunter.user_id == target.user_id:
        raise GameError("Нельзя охотиться на самого себя.")
    if not target.nukenin:
        raise GameError("Цель не находится в Bingo Book.")
    if hunter.level + 18 < target.level:
        raise GameError("Цель слишком опасна для вашего текущего уровня.")
    h_power = player_power(hunter) * random.uniform(0.86, 1.14)
    t_power = player_power(target) * random.uniform(0.86, 1.14)
    if h_power >= t_power:
        bounty = max(500, min(int(target.wanted_reward or 0), 75_000))
        payout = max(500, int(bounty * 0.18))
        hunter.ryo += payout
        hunter.reputation += 10
        hunter.village_points += 8
        hunter.pvp_wins += 1
        target.pvp_losses += 1
        target.ryo = max(0, target.ryo - min(target.ryo, payout // 4))
        target.wanted_reward = max(1_000, int(target.wanted_reward * 0.92))
        session.add(NinjaEventLog(event_type="bounty_hunt", user_id=user_id, payload={"target": target_id, "result": "win", "payout": payout}))
        return f"🎯 {hunter.name} выследил {target.name}.\n🏆 Победа! Выплачено {payout:,} рё из контракта Bingo Book."
    hunter.pvp_losses += 1
    target.pvp_wins += 1
    target.wanted_reward = min(10_000_000, int(target.wanted_reward or 1_000) + 2_000)
    session.add(NinjaEventLog(event_type="bounty_hunt", user_id=user_id, payload={"target": target_id, "result": "loss"}))
    return f"🎯 Вы нашли {target.name}, но проиграли бой. Его награда выросла до {target.wanted_reward:,} рё."


async def clan_war(session: AsyncSession, user_id: int, target_name: str) -> str:
    own_member = await session.scalar(select(NinjaClanMember).where(NinjaClanMember.user_id == user_id))
    if not own_member:
        raise GameError("Вы не состоите в клане.")
    own_clan = await session.get(NinjaClan, own_member.clan_id)
    if not own_clan or own_clan.leader_id != user_id:
        raise GameError("Объявлять клановую войну может только глава клана.")
    target = await session.scalar(select(NinjaClan).where(func.lower(NinjaClan.name) == target_name.strip().casefold()))
    if not target or target.id == own_clan.id:
        raise GameError("Клан-противник не найден.")

    a, b = sorted((own_clan.id, target.id))
    state_key = f"clanwar:{a}:{b}"
    state = await session.get(NinjaWorldState, state_key)
    now = utcnow()
    if state:
        until_raw = _copy_dict(state.value).get("cooldown_until")
        try:
            from datetime import datetime
            until = datetime.fromisoformat(str(until_raw)) if until_raw else None
        except Exception:
            until = None
        if until and until > now:
            raise GameError("Между этими кланами ещё действует перерыв после прошлой войны.")

    async def team_score(clan_id: int) -> tuple[float, list[NinjaProfile]]:
        members = (await session.scalars(select(NinjaClanMember).where(NinjaClanMember.clan_id == clan_id))).all()
        ids = [m.user_id for m in members]
        if not ids:
            return 0.0, []
        profiles = (await session.scalars(select(NinjaProfile).where(NinjaProfile.user_id.in_(ids)))).all()
        top = sorted(profiles, key=player_power, reverse=True)[:10]
        return sum(player_power(p) for p in top) * random.uniform(0.92, 1.08), top

    own_score, own_team = await team_score(own_clan.id)
    foe_score, foe_team = await team_score(target.id)
    if not own_team or not foe_team:
        raise GameError("Для войны в обоих кланах должен быть хотя бы один активный шиноби.")

    winner, loser = (own_clan, target) if own_score >= foe_score else (target, own_clan)
    winner.rating += 28
    loser.rating = max(0, loser.rating - 18)
    prize = 20_000 + min(len(own_team), len(foe_team)) * 5_000
    winner.treasury += prize
    winner.xp += 750
    if state is None:
        state = NinjaWorldState(key=state_key, value={})
        session.add(state)
    state.value = {
        "last_winner": winner.id,
        "last_loser": loser.id,
        "score_a": round(own_score),
        "score_b": round(foe_score),
        "cooldown_until": (now + timedelta(hours=12)).isoformat(),
    }
    session.add(NinjaEventLog(event_type="clan_war", user_id=user_id, payload={"clan_a": own_clan.id, "clan_b": target.id, "winner": winner.id}))
    return (
        f"⚔️ <b>Клановая война</b>\n{own_clan.name}: {round(own_score):,}\n{target.name}: {round(foe_score):,}\n\n"
        f"🏆 Победитель: {winner.name}\n💰 В казну победителя: +{prize:,} рё · рейтинг +28."
    )


async def _find_squad(session: AsyncSession, user_id: int):
    from .models import NinjaSquad

    rows = (await session.scalars(select(NinjaSquad).order_by(NinjaSquad.id.desc()).limit(1000))).all()
    for row in rows:
        if int(user_id) in {int(x) for x in _copy_list(row.members)}:
            return row
    return None


async def squad_create(session: AsyncSession, user_id: int, purpose: str = "missions") -> str:
    from .models import NinjaSquad

    await require_profile(session, user_id)
    if await _find_squad(session, user_id):
        raise GameError("Вы уже состоите в отряде.")
    purpose = _slug(purpose or "missions") or "missions"
    row = NinjaSquad(leader_id=user_id, members=[user_id], purpose=purpose)
    session.add(row)
    await session.flush()
    return f"👥 Отряд #{row.id} создан. Код приглашения: {row.id}. Друг: /squad join {row.id}"


async def squad_join(session: AsyncSession, user_id: int, squad_id: int) -> str:
    from .models import NinjaSquad

    await require_profile(session, user_id)
    if await _find_squad(session, user_id):
        raise GameError("Сначала покиньте текущий отряд.")
    row = await session.get(NinjaSquad, int(squad_id))
    if not row:
        raise GameError("Отряд не найден.")
    members = [int(x) for x in _copy_list(row.members)]
    if len(members) >= 4:
        raise GameError("В отряде уже 4 шиноби.")
    members.append(user_id)
    row.members = members
    return f"👥 Вы вступили в отряд #{row.id}. Участников: {len(members)}/4."


async def squad_leave(session: AsyncSession, user_id: int) -> str:
    row = await _find_squad(session, user_id)
    if not row:
        raise GameError("Вы не состоите в отряде.")
    members = [int(x) for x in _copy_list(row.members) if int(x) != int(user_id)]
    if not members:
        await session.delete(row)
        return "👥 Отряд расформирован."
    row.members = members
    if row.leader_id == user_id:
        row.leader_id = members[0]
    return f"👥 Вы покинули отряд #{row.id}. Новый лидер: {row.leader_id}."


async def squad_text(session: AsyncSession, user_id: int) -> str:
    row = await _find_squad(session, user_id)
    if not row:
        return "👥 Отряда нет. /squad create missions · /squad join ID"
    members = [int(x) for x in _copy_list(row.members)]
    profiles = (await session.scalars(select(NinjaProfile).where(NinjaProfile.user_id.in_(members)))).all()
    by_id = {p.user_id: p for p in profiles}
    lines = [f"👥 <b>Отряд #{row.id}</b> · цель: {row.purpose}"]
    for uid in members:
        p = by_id.get(uid)
        marker = "👑" if uid == row.leader_id else "🥷"
        lines.append(f"{marker} {p.name if p else uid} · ур.{p.level if p else '?'}")
    lines.append(f"Суммарная сила: {sum(player_power(p) for p in profiles):,}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Element mastery, kekkei genkai, equipment-adjacent activities and summons
# ---------------------------------------------------------------------------


def _known_elements(profile: NinjaProfile) -> list[str]:
    result = [str(profile.primary_element)]
    if profile.secondary_element and profile.secondary_element not in result:
        result.append(str(profile.secondary_element))
    for key in _copy_list(_flag(profile, "extra_elements", [])):
        key = str(key)
        if key in ELEMENTS and key not in result:
            result.append(key)
    return result


def _detect_kekkei(profile: NinjaProfile) -> tuple[str | None, str | None]:
    elements = set(_known_elements(profile))
    combos = [
        ({"water", "wind"}, "ice", "❄️ Хьётон / Лёд"),
        ({"fire", "earth"}, "lava", "🌋 Йотон / Лава"),
        ({"water", "lightning"}, "storm", "⛈ Рантон / Шторм"),
        ({"fire", "wind"}, "scorch", "☀️ Сякутон / Жар"),
        ({"fire", "water"}, "boil", "♨️ Футтон / Кипение"),
    ]
    if {"water", "earth"}.issubset(elements) and profile.bloodline in {"senju", "otsutsuki"}:
        return "wood", "🌳 Мокутон / Дерево"
    for required, key, name in combos:
        if required.issubset(elements):
            return key, name
    return None, None


async def element_mastery_text(session: AsyncSession, user_id: int) -> str:
    profile = await require_profile(session, user_id)
    known = _known_elements(profile)
    _, kekkei_name = _detect_kekkei(profile)
    lines = [
        "🌈 <b>Стихии чакры</b>",
        "Освоено: " + ", ".join(ELEMENTS.get(key, key) for key in known),
        f"Кеккей Генкай: {kekkei_name or '—'}",
        "",
        "Новая стихия: <code>/element learn fire|water|lightning|wind|earth</code>",
    ]
    if len(known) >= 5:
        lines.append("☯️ Вы владеете всеми пятью базовыми стихиями.")
    return "\n".join(lines)


async def learn_element(session: AsyncSession, user_id: int, element: str) -> str:
    profile = await require_profile(session, user_id)
    element = element.lower().strip()
    if element not in ELEMENTS:
        raise GameError("Стихии: fire, water, lightning, wind, earth.")
    known = _known_elements(profile)
    if element in known:
        return f"🌈 {ELEMENTS[element]} уже освоена."
    if profile.level < 35:
        raise GameError("Вторая стихия открывается с 35 уровня.")

    target_count = len(known) + 1
    thresholds = {2: 35, 3: 60, 4: 75, 5: 90}
    required_level = thresholds.get(target_count, 999)
    if profile.level < required_level:
        raise GameError(f"Для {target_count}-й стихии нужен уровень {required_level}.")
    costs = {2: 30_000, 3: 90_000, 4: 180_000, 5: 350_000}
    cost = costs[target_count]
    if profile.ryo < cost:
        raise GameError(f"Освоение новой стихии стоит {cost:,} рё.")
    profile.ryo -= cost
    if not profile.secondary_element:
        profile.secondary_element = element
    else:
        extra = [str(x) for x in _copy_list(_flag(profile, "extra_elements", [])) if str(x) in ELEMENTS]
        extra.append(element)
        _set_flag(profile, "extra_elements", list(dict.fromkeys(extra)))

    kekkei_key, kekkei_name = _detect_kekkei(profile)
    if kekkei_key:
        _set_flag(profile, "kekkei", kekkei_key)
        _set_flag(profile, "kekkei_name", kekkei_name)
    session.add(
        NinjaEventLog(
            event_type="element_mastered",
            user_id=user_id,
            payload={"element": element, "count": target_count, "kekkei": kekkei_key},
        )
    )
    suffix = f"\n🧬 Открыт Кеккей Генкай: {kekkei_name}" if kekkei_name else ""
    return f"🌈 Освоена новая стихия: {ELEMENTS[element]}.{suffix}"


async def gather_activity(session: AsyncSession, user_id: int) -> str:
    profile = await require_profile(session, user_id)
    _energy_refresh(profile)
    if profile.energy < 5:
        raise GameError("Для сбора ресурсов нужно 5 энергии.")
    profile.energy -= 5
    profile.energy_updated_at = utcnow()
    pool = ["herb", "metal", "cloth", "seal_paper"]
    item_key = random.choice(pool)
    qty = random.randint(1, 4)
    if profile.profession == "herbalist" and item_key == "herb":
        qty += 3
    elif profile.profession in {"smith", "sealer"}:
        qty += 1
    await add_item(session, user_id, item_key, qty)
    counters = _copy_dict(profile.counters)
    counters["gather"] = int(counters.get("gather", 0)) + 1
    profile.counters = counters
    return f"🌿 Сбор завершён: {ITEMS[item_key]['name']} ×{qty}."


async def fishing_activity(session: AsyncSession, user_id: int) -> str:
    profile = await require_profile(session, user_id)
    _energy_refresh(profile)
    if profile.energy < 5:
        raise GameError("Для рыбалки нужно 5 энергии.")
    profile.energy -= 5
    profile.energy_updated_at = utcnow()
    rare_chance = 0.08 + (0.04 if _effective_village(profile) == "kiri" else 0.0)
    if random.random() < rare_chance:
        key, qty = "rare_fish", 1
    else:
        key, qty = "river_fish", random.randint(1, 3)
    await add_item(session, user_id, key, qty)
    counters = _copy_dict(profile.counters)
    counters["fishing"] = int(counters.get("fishing", 0)) + 1
    profile.counters = counters
    return f"🎣 Улов: {ITEMS[key]['name']} ×{qty}."


async def cook_ramen(session: AsyncSession, user_id: int) -> str:
    profile = await require_profile(session, user_id)
    fish = await session.scalar(select(NinjaItem).where(NinjaItem.user_id == user_id, NinjaItem.item_key == "river_fish"))
    herb = await session.scalar(select(NinjaItem).where(NinjaItem.user_id == user_id, NinjaItem.item_key == "herb"))
    if not fish or fish.quantity < 1 or not herb or herb.quantity < 2:
        raise GameError("Для рамена нужно: 🐟 рыба ×1 и 🌿 лечебная трава ×2.")
    fish.quantity -= 1
    herb.quantity -= 2
    qty = 2 if profile.profession == "cook" and random.random() < 0.35 else 1
    await add_item(session, user_id, "ramen", qty)
    return f"🍜 Приготовлен рамен ×{qty}. Использование: /eat ramen"


async def eat_food(session: AsyncSession, user_id: int, key: str) -> str:
    profile = await require_profile(session, user_id)
    if key != "ramen":
        raise GameError("Сейчас доступно блюдо: ramen.")
    row = await session.scalar(select(NinjaItem).where(NinjaItem.user_id == user_id, NinjaItem.item_key == "ramen", NinjaItem.quantity > 0))
    if not row:
        raise GameError("Рамена нет в инвентаре.")
    row.quantity -= 1
    _energy_refresh(profile)
    profile.energy = min(100, profile.energy + 20)
    flags = _copy_dict(profile.flags)
    flags["ramen_buff"] = max(int(flags.get("ramen_buff", 0)), 3)
    profile.flags = flags
    return "🍜 Рамен съеден: +20 энергии и +8% максимальной чакры на следующие 3 боя."


async def summon_for_battle(session: AsyncSession, user_id: int, key: str | None = None) -> str:
    profile = await require_profile(session, user_id)
    owned = [str(x) for x in _copy_list(profile.summons)]
    if not key:
        if not owned:
            return "✨ Контрактов призыва нет. Заключите: /summoning KEY"
        return "✨ Контракты: " + ", ".join(f"{k}={SUMMONS.get(k, k)}" for k in owned) + "\nВыбор: /summon_battle KEY"
    key = key.lower()
    if key not in owned:
        raise GameError("Такого контракта призыва у вас нет.")
    flags = _copy_dict(profile.flags)
    flags["battle_summon"] = key
    profile.flags = flags
    return f"✨ В следующем бою поможет призыв: {SUMMONS.get(key, key)}."
