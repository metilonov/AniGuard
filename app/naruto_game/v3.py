from __future__ import annotations

import random
from datetime import timedelta, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .content import VILLAGES
from .engine import player_power
from .models import NinjaEventLog, NinjaProfile, NinjaWorldEvent, NinjaWorldState, utcnow
from .service import GameError, require_profile
from .v3_models import (
    NinjaBijuuState,
    NinjaCriminalInvite,
    NinjaCriminalMember,
    NinjaCriminalOrg,
    NinjaVillageGovernment,
    NinjaVillageVote,
    NinjaWorldChronicle,
)


IDEOLOGIES: dict[str, str] = {
    "balanced": "⚖️ Сбалансированный путь",
    "peaceful": "🕊 Мирный курс",
    "military": "⚔️ Военный курс",
    "trade": "💰 Торговый курс",
    "isolation": "🌑 Изоляционизм",
    "scientific": "🧪 Научный курс",
}

PROJECTS: dict[str, dict[str, Any]] = {
    "hospital": {"name": "🏥 Госпиталь", "cost": 80_000, "stat": "medicine"},
    "walls": {"name": "🛡 Оборонительные стены", "cost": 100_000, "stat": "military"},
    "academy": {"name": "🎓 Академия шиноби", "cost": 75_000, "stat": "population"},
    "market": {"name": "🏪 Торговый квартал", "cost": 90_000, "stat": "economy"},
    "intel": {"name": "🕵 Разведцентр", "cost": 95_000, "stat": "intelligence"},
    "lab": {"name": "🧪 Исследовательский корпус", "cost": 110_000, "stat": "technology"},
}

CRIMINAL_ROLES: dict[str, str] = {
    "leader": "👑 Лидер",
    "right_hand": "🩸 Правая рука",
    "enforcer": "⚔️ S-ранговый боец",
    "spy": "🕵 Шпион",
    "researcher": "🧪 Исследователь",
    "initiate": "🌑 Посвящённый",
}

CRIMINAL_ROLE_ALIASES: dict[str, str] = {
    "leader": "leader", "лидер": "leader",
    "right_hand": "right_hand", "правая": "right_hand", "рука": "right_hand",
    "enforcer": "enforcer", "боец": "enforcer",
    "spy": "spy", "шпион": "spy",
    "researcher": "researcher", "исследователь": "researcher",
    "initiate": "initiate", "новичок": "initiate", "посвященный": "initiate", "посвящённый": "initiate",
}

CRIME_ACTIONS: dict[str, dict[str, Any]] = {
    "robbery": {"name": "💰 Ограбление каравана", "energy": 18, "reward": (8_000, 24_000), "heat": 8, "difficulty": 900},
    "sabotage": {"name": "💥 Диверсия", "energy": 22, "reward": (12_000, 32_000), "heat": 12, "difficulty": 1250},
    "intel": {"name": "🕵 Кража разведданных", "energy": 16, "reward": (6_000, 20_000), "heat": 6, "difficulty": 1050},
}

BIJUU: dict[str, dict[str, Any]] = {
    "shukaku": {"name": "一尾 Шукаку", "region": "Страна Ветра", "hp": 70_000},
    "matatabi": {"name": "二尾 Мататаби", "region": "Страна Молнии", "hp": 85_000},
    "isobu": {"name": "三尾 Исобу", "region": "Страна Воды", "hp": 95_000},
    "songoku": {"name": "四尾 Сон Гоку", "region": "Страна Земли", "hp": 110_000},
    "kokuo": {"name": "五尾 Кокуо", "region": "Граница Страны Земли", "hp": 120_000},
    "saiken": {"name": "六尾 Сайкен", "region": "Страна Воды", "hp": 130_000},
    "chomei": {"name": "七尾 Чомей", "region": "Скрытые леса", "hp": 145_000},
    "gyuki": {"name": "八尾 Гьюки", "region": "Страна Молнии", "hp": 165_000},
    "kurama": {"name": "九尾 Курама", "region": "Страна Огня", "hp": 200_000},
}

NEWS_LABELS: dict[str, str] = {
    "v3_election_open": "🗳 Открыты выборы Каге",
    "v3_kage_elected": "👑 Избран новый Каге",
    "v3_project_complete": "🏗 Завершён проект деревни",
    "v3_criminal_org_created": "🌑 Появилась преступная организация",
    "v3_crime_success": "☠️ Зафиксирована активность нукенинов",
    "v3_bijuu_awakened": "🐾 Биджу появился в мире",
    "v3_bijuu_defeated": "⚡ Биджу подавлен",
    "v3_bijuu_sealed": "🔒 Биджу запечатан",
    "village_war_declared": "⚔️ Объявлена война деревень",
    "world_event": "🌍 Мировое событие",
}


def _dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _aware(value):
    if value is None:
        return None
    if getattr(value, "tzinfo", None) is None:
        return value.replace(tzinfo=timezone.utc)
    return value


async def _log(session: AsyncSession, event_type: str, user_id: int | None, payload: dict[str, Any]) -> None:
    session.add(NinjaEventLog(event_type=event_type, user_id=user_id, payload=payload))


async def _chronicle(
    session: AsyncSession,
    category: str,
    title: str,
    text: str,
    *,
    actor_user_id: int | None = None,
    village: str | None = None,
    payload: dict[str, Any] | None = None,
) -> None:
    session.add(
        NinjaWorldChronicle(
            category=category,
            title=title[:128],
            text=text,
            actor_user_id=actor_user_id,
            village=village,
            payload=dict(payload or {}),
        )
    )


async def ensure_governments(session: AsyncSession) -> None:
    existing = set((await session.scalars(select(NinjaVillageGovernment.village))).all())
    for village in VILLAGES:
        if village not in existing:
            session.add(NinjaVillageGovernment(village=village, upgrades={key: 0 for key in PROJECTS}, history=[]))
    await session.flush()


async def get_government(session: AsyncSession, village: str) -> NinjaVillageGovernment:
    await ensure_governments(session)
    row = await session.get(NinjaVillageGovernment, village)
    if row is None:
        raise GameError("Правительство деревни не найдено.")
    # Preserve compatibility with V2 village state if it already has a Kage.
    if not row.kage_user_id:
        legacy = await session.get(NinjaWorldState, f"village:{village}")
        legacy_kage = int(_dict(legacy.value).get("kage_id") or 0) if legacy else 0
        if legacy_kage:
            row.kage_user_id = legacy_kage
    return row


async def is_kage(session: AsyncSession, profile: NinjaProfile) -> bool:
    if profile.ninja_rank == "kage":
        return True
    gov = await get_government(session, profile.village)
    return int(gov.kage_user_id or 0) == int(profile.user_id)


async def is_council(session: AsyncSession, profile: NinjaProfile) -> bool:
    gov = await get_government(session, profile.village)
    return int(profile.user_id) in {int(x) for x in _list(gov.council_member_ids)}


def _government_stats(gov: NinjaVillageGovernment) -> dict[str, int]:
    upgrades = _dict(gov.upgrades)
    stats = {
        "military": 50,
        "economy": 50,
        "population": 50,
        "intelligence": 50,
        "medicine": 50,
        "technology": 50,
    }
    for key, data in PROJECTS.items():
        level = int(upgrades.get(key, 0))
        stats[data["stat"]] += level * 8
    ideology = gov.ideology or "balanced"
    if ideology == "military":
        stats["military"] += 10
    elif ideology == "trade":
        stats["economy"] += 10
    elif ideology == "scientific":
        stats["technology"] += 10
    elif ideology == "peaceful":
        stats["medicine"] += 5
        stats["population"] += 5
    elif ideology == "isolation":
        stats["intelligence"] += 8
        stats["military"] += 4
    return stats


async def government_text(session: AsyncSession, user_id: int) -> str:
    p = await require_profile(session, user_id)
    gov = await get_government(session, p.village)
    stats = _government_stats(gov)
    village_name = VILLAGES.get(p.village, {}).get("name", p.village)
    kage_name = "вакантно"
    if gov.kage_user_id:
        kp = await session.get(NinjaProfile, int(gov.kage_user_id))
        kage_name = f"{kp.name} ({gov.kage_user_id})" if kp else str(gov.kage_user_id)
    council = ", ".join(str(x) for x in _list(gov.council_member_ids)) or "не сформирован"
    project = "—"
    if gov.project_key:
        info = PROJECTS.get(gov.project_key, {"name": gov.project_key})
        project = f"{info['name']} · {gov.project_progress:,}/{gov.project_target:,}"
    return "\n".join([
        f"🏯 <b>{village_name} · правительство MMO V3</b>",
        f"👑 Каге: {kage_name}",
        f"🏛 Совет: {council}",
        f"🤝 Доверие: {gov.trust}/100",
        f"🧭 Курс: {IDEOLOGIES.get(gov.ideology, gov.ideology)}",
        f"💰 Казна: {gov.treasury:,} рё · налог {gov.tax_rate}%",
        f"🏗 Проект: {project}",
        "",
        f"⚔️ Военная мощь: {stats['military']}",
        f"💰 Экономика: {stats['economy']}",
        f"👥 Население: {stats['population']}",
        f"🕵 Разведка: {stats['intelligence']}",
        f"🏥 Медицина: {stats['medicine']}",
        f"🧪 Технологии: {stats['technology']}",
    ])


async def election_open(session: AsyncSession, user_id: int) -> str:
    p = await require_profile(session, user_id)
    if p.nukenin:
        raise GameError("Нукенин не может открыть выборы Каге.")
    if p.level < 10 or p.reputation < 50:
        raise GameError("Для запуска выборов нужен 10 уровень и 50 репутации.")
    await get_government(session, p.village)
    active = await session.scalar(
        select(NinjaVillageVote).where(
            NinjaVillageVote.village == p.village,
            NinjaVillageVote.kind == "kage_election",
            NinjaVillageVote.status == "open",
        ).order_by(NinjaVillageVote.id.desc())
    )
    if active:
        return await election_status(session, user_id)
    vote = NinjaVillageVote(
        village=p.village,
        kind="kage_election",
        created_by=user_id,
        candidates=[],
        votes={},
        payload={},
        closes_at=utcnow() + timedelta(hours=24),
    )
    session.add(vote)
    await _log(session, "v3_election_open", user_id, {"village": p.village})
    await _chronicle(session, "politics", "Открыты выборы Каге", f"В {p.village} начались выборы нового Каге.", actor_user_id=user_id, village=p.village)
    return "🗳 <b>Выборы Каге открыты на 24 часа.</b>\n/election nominate — выдвинуть себя\n/election vote USER_ID — голосовать"


async def _active_election(session: AsyncSession, village: str) -> NinjaVillageVote | None:
    return await session.scalar(
        select(NinjaVillageVote).where(
            NinjaVillageVote.village == village,
            NinjaVillageVote.kind == "kage_election",
            NinjaVillageVote.status == "open",
        ).order_by(NinjaVillageVote.id.desc())
    )


async def election_nominate(session: AsyncSession, user_id: int) -> str:
    p = await require_profile(session, user_id)
    if p.nukenin or p.level < 20 or p.reputation < 250:
        raise GameError("Кандидату нужен 20 уровень, 250 репутации и статус шиноби деревни.")
    vote = await _active_election(session, p.village)
    if vote is None:
        raise GameError("Сейчас нет открытых выборов. /election start")
    candidates = [int(x) for x in _list(vote.candidates)]
    if user_id not in candidates:
        candidates.append(user_id)
        vote.candidates = candidates
    return f"🗳 {p.name} выдвинут кандидатом в Каге."


async def election_vote(session: AsyncSession, user_id: int, candidate_id: int) -> str:
    p = await require_profile(session, user_id)
    if p.nukenin or p.level < 5:
        raise GameError("Для голосования нужен минимум 5 уровень и принадлежность деревне.")
    vote = await _active_election(session, p.village)
    if vote is None:
        raise GameError("Сейчас нет открытых выборов.")
    candidates = {int(x) for x in _list(vote.candidates)}
    if candidate_id not in candidates:
        raise GameError("Такого кандидата нет в бюллетене.")
    cp = await require_profile(session, candidate_id)
    if cp.village != p.village:
        raise GameError("Кандидат относится к другой деревне.")
    votes = _dict(vote.votes)
    votes[str(user_id)] = int(candidate_id)
    vote.votes = votes
    return f"✅ Голос принят за {cp.name}. До закрытия можно изменить выбор."


async def _finalize_election(session: AsyncSession, vote: NinjaVillageVote) -> str | None:
    if vote.status != "open" or (_aware(vote.closes_at) and _aware(vote.closes_at) > utcnow()):
        return None
    votes = {str(k): int(v) for k, v in _dict(vote.votes).items()}
    counts: dict[int, int] = {}
    for candidate in votes.values():
        counts[candidate] = counts.get(candidate, 0) + 1
    if not counts:
        vote.status = "cancelled"
        vote.resolved_at = utcnow()
        return "🗳 Выборы завершены без результата: ни одного голоса."
    winner = sorted(counts.items(), key=lambda item: (-item[1], item[0]))[0][0]
    gov = await get_government(session, vote.village)
    gov.kage_user_id = winner
    gov.trust = max(45, min(100, int(gov.trust) + 5))
    # Council = up to three strongest non-winning candidates by votes.
    ranked = [cid for cid, _ in sorted(counts.items(), key=lambda item: (-item[1], item[0])) if cid != winner]
    gov.council_member_ids = ranked[:3]
    vote.status = "closed"
    vote.resolved_at = utcnow()
    winner_profile = await session.get(NinjaProfile, winner)
    if winner_profile:
        winner_profile.ninja_rank = "kage"
    legacy = await session.get(NinjaWorldState, f"village:{vote.village}")
    if legacy:
        value = _dict(legacy.value)
        value["kage_id"] = winner
        legacy.value = value
    else:
        session.add(NinjaWorldState(key=f"village:{vote.village}", value={"kage_id": winner}))
    await _log(session, "v3_kage_elected", winner, {"village": vote.village, "votes": counts.get(winner, 0)})
    await _chronicle(session, "politics", "Избран новый Каге", f"Игрок {winner} стал Каге деревни {vote.village}.", actor_user_id=winner, village=vote.village)
    return f"👑 Выборы завершены. Новый Каге: {winner_profile.name if winner_profile else winner} · голосов {counts.get(winner, 0)}."


async def election_status(session: AsyncSession, user_id: int) -> str:
    p = await require_profile(session, user_id)
    vote = await _active_election(session, p.village)
    if vote is None:
        latest = await session.scalar(
            select(NinjaVillageVote).where(
                NinjaVillageVote.village == p.village,
                NinjaVillageVote.kind == "kage_election",
            ).order_by(NinjaVillageVote.id.desc())
        )
        if latest and latest.status == "open":
            final = await _finalize_election(session, latest)
            if final:
                return final
        return "🗳 Активных выборов Каге нет. /election start"
    final = await _finalize_election(session, vote)
    if final:
        return final
    votes = {str(k): int(v) for k, v in _dict(vote.votes).items()}
    counts: dict[int, int] = {}
    for candidate in votes.values():
        counts[candidate] = counts.get(candidate, 0) + 1
    lines = ["🗳 <b>Выборы Каге</b>"]
    for cid in [int(x) for x in _list(vote.candidates)]:
        cp = await session.get(NinjaProfile, cid)
        lines.append(f"• {cp.name if cp else cid} [{cid}] — {counts.get(cid, 0)} голосов")
    if not _list(vote.candidates):
        lines.append("Кандидатов пока нет.")
    remaining = max(0, int((_aware(vote.closes_at) - utcnow()).total_seconds())) if _aware(vote.closes_at) else 0
    lines.append(f"⏳ До закрытия: {remaining // 3600}ч {(remaining % 3600) // 60}м")
    return "\n".join(lines)


async def ideology_set(session: AsyncSession, user_id: int, ideology: str) -> str:
    p = await require_profile(session, user_id)
    if ideology not in IDEOLOGIES:
        raise GameError("Курс: balanced, peaceful, military, trade, isolation, scientific.")
    if not await is_kage(session, p):
        raise GameError("Политический курс меняет только Каге.")
    gov = await get_government(session, p.village)
    gov.ideology = ideology
    history = _list(gov.history)
    history.append({"at": utcnow().isoformat(), "type": "ideology", "value": ideology, "actor": user_id})
    gov.history = history[-100:]
    return f"🧭 Новый курс деревни: {IDEOLOGIES[ideology]}"


async def tax_set(session: AsyncSession, user_id: int, rate: int) -> str:
    p = await require_profile(session, user_id)
    if not await is_kage(session, p):
        raise GameError("Налог меняет только Каге.")
    if rate < 0 or rate > 5:
        raise GameError("Налог деревни ограничен диапазоном 0–5%.")
    gov = await get_government(session, p.village)
    gov.tax_rate = rate
    gov.trust = max(0, min(100, int(gov.trust) - max(0, rate - 2)))
    return f"💰 Налог деревни установлен: {rate}%."


async def collect_village_tax(session: AsyncSession, profile: NinjaProfile, gross_reward: int) -> int:
    """Move the configured 0–5% mission tax from a reward into the village treasury."""
    if gross_reward <= 0 or profile.nukenin:
        return 0
    gov = await get_government(session, profile.village)
    rate = max(0, min(5, int(gov.tax_rate or 0)))
    if rate <= 0:
        return 0
    tax = min(int(profile.ryo), max(0, int(gross_reward) * rate // 100))
    if tax:
        profile.ryo -= tax
        gov.treasury += tax
    return tax


async def village_donate(session: AsyncSession, user_id: int, amount: int) -> str:
    p = await require_profile(session, user_id)
    if amount <= 0:
        raise GameError("Укажите положительную сумму.")
    if p.ryo < amount:
        raise GameError("Недостаточно рё.")
    gov = await get_government(session, p.village)
    p.ryo -= amount
    gov.treasury += amount
    p.village_points += max(1, amount // 5000)
    return f"🏯 В казну деревни внесено {amount:,} рё. Казна: {gov.treasury:,}."


async def project_start(session: AsyncSession, user_id: int, key: str) -> str:
    p = await require_profile(session, user_id)
    gov = await get_government(session, p.village)
    if not (await is_kage(session, p) or await is_council(session, p)):
        raise GameError("Проект может запустить Каге или член Совета.")
    info = PROJECTS.get(key)
    if not info:
        raise GameError("Проекты: hospital, walls, academy, market, intel, lab.")
    if gov.project_key:
        raise GameError("Сначала завершите текущий проект.")
    upgrades = _dict(gov.upgrades)
    level = int(upgrades.get(key, 0))
    target = int(info["cost"] * (1 + level * 0.55))
    gov.project_key = key
    gov.project_progress = 0
    gov.project_target = target
    return f"🏗 Начат проект {info['name']} уровня {level + 1}. Требуется {target:,} рё/вклада."


async def project_contribute(session: AsyncSession, user_id: int, amount: int) -> str:
    p = await require_profile(session, user_id)
    gov = await get_government(session, p.village)
    if not gov.project_key:
        raise GameError("У деревни нет активного проекта.")
    if amount <= 0:
        raise GameError("Укажите положительный вклад.")
    if p.ryo < amount:
        raise GameError("Недостаточно рё.")
    p.ryo -= amount
    gov.project_progress += amount
    key = gov.project_key
    info = PROJECTS[key]
    if gov.project_progress < gov.project_target:
        return f"🏗 +{amount:,} в {info['name']}. Прогресс: {gov.project_progress:,}/{gov.project_target:,}."
    upgrades = _dict(gov.upgrades)
    upgrades[key] = int(upgrades.get(key, 0)) + 1
    gov.upgrades = upgrades
    gov.project_key = None
    gov.project_progress = 0
    gov.project_target = 0
    gov.trust = min(100, int(gov.trust) + 3)
    p.reputation = min(10_000, int(p.reputation) + 15)
    await _log(session, "v3_project_complete", user_id, {"village": p.village, "project": key, "level": upgrades[key]})
    await _chronicle(session, "village", "Завершён проект", f"{p.village}: {info['name']} достиг уровня {upgrades[key]}.", actor_user_id=user_id, village=p.village)
    return f"🎉 {info['name']} завершён! Новый уровень: {upgrades[key]}. Репутация +15."


async def project_fund_from_treasury(session: AsyncSession, user_id: int, amount: int) -> str:
    p = await require_profile(session, user_id)
    gov = await get_government(session, p.village)
    if not (await is_kage(session, p) or await is_council(session, p)):
        raise GameError("Казной проекта распоряжаются Каге и Совет.")
    if not gov.project_key:
        raise GameError("У деревни нет активного проекта.")
    if amount <= 0 or gov.treasury < amount:
        raise GameError("Недостаточно средств в казне или неверная сумма.")
    amount = min(amount, max(0, gov.project_target - gov.project_progress))
    if amount <= 0:
        raise GameError("Проект уже профинансирован.")
    gov.treasury -= amount
    gov.project_progress += amount
    key = gov.project_key
    info = PROJECTS[key]
    if gov.project_progress < gov.project_target:
        return f"🏛 Из казны направлено {amount:,} в {info['name']}. Прогресс: {gov.project_progress:,}/{gov.project_target:,}."
    upgrades = _dict(gov.upgrades)
    upgrades[key] = int(upgrades.get(key, 0)) + 1
    gov.upgrades = upgrades
    gov.project_key = None
    gov.project_progress = 0
    gov.project_target = 0
    gov.trust = min(100, int(gov.trust) + 3)
    await _log(session, "v3_project_complete", user_id, {"village": p.village, "project": key, "level": upgrades[key], "funded_by": "treasury"})
    await _chronicle(session, "village", "Завершён проект", f"{p.village}: {info['name']} достиг уровня {upgrades[key]} за счёт казны.", actor_user_id=user_id, village=p.village)
    return f"🎉 {info['name']} завершён из казны! Новый уровень: {upgrades[key]}."


async def project_status(session: AsyncSession, user_id: int) -> str:
    p = await require_profile(session, user_id)
    gov = await get_government(session, p.village)
    upgrades = _dict(gov.upgrades)
    lines = ["🏗 <b>Проекты деревни</b>"]
    if gov.project_key:
        info = PROJECTS[gov.project_key]
        lines.append(f"Активный: {info['name']} · {gov.project_progress:,}/{gov.project_target:,}")
    else:
        lines.append("Активный: —")
    lines.append("")
    for key, info in PROJECTS.items():
        lines.append(f"• {info['name']}: ур. {int(upgrades.get(key, 0))}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Criminal organizations
# ---------------------------------------------------------------------------


async def _criminal_member(session: AsyncSession, user_id: int) -> NinjaCriminalMember | None:
    return await session.scalar(select(NinjaCriminalMember).where(NinjaCriminalMember.user_id == user_id))


async def criminal_org_create(session: AsyncSession, user_id: int, name: str) -> str:
    p = await require_profile(session, user_id)
    if not p.nukenin:
        raise GameError("Создать скрытую преступную организацию может только нукенин.")
    if p.level < 25:
        raise GameError("Нужен минимум 25 уровень.")
    if await _criminal_member(session, user_id):
        raise GameError("Вы уже состоите в преступной организации.")
    name = " ".join(name.strip().split())[:64]
    if len(name) < 3:
        raise GameError("Название должно содержать минимум 3 символа.")
    if await session.scalar(select(NinjaCriminalOrg).where(func.lower(NinjaCriminalOrg.name) == name.lower())):
        raise GameError("Организация с таким названием уже существует.")
    cost = 100_000
    if p.ryo < cost:
        raise GameError(f"Для создания нужно {cost:,} рё.")
    p.ryo -= cost
    org = NinjaCriminalOrg(
        name=name,
        leader_user_id=user_id,
        treasury=20_000,
        secrecy=100,
        heat=0,
        base_region="неизвестно",
        upgrades={"base": 1, "intel": 0, "lab": 0, "defense": 0},
        history=[],
    )
    session.add(org)
    await session.flush()
    session.add(NinjaCriminalMember(org_id=org.id, user_id=user_id, role="leader", contribution=0))
    await _log(session, "v3_criminal_org_created", user_id, {"org_id": org.id, "name": name})
    await _chronicle(session, "underworld", "Новая тень", f"В мире появилась новая скрытая организация: {name}.", actor_user_id=user_id)
    return f"🌑 <b>{name}</b> создана. ID: {org.id}. Максимум участников: 12."


async def criminal_org_text(session: AsyncSession, user_id: int) -> str:
    await require_profile(session, user_id)
    member = await _criminal_member(session, user_id)
    if not member:
        invites = (await session.scalars(
            select(NinjaCriminalInvite).where(
                NinjaCriminalInvite.target_user_id == user_id,
                NinjaCriminalInvite.status == "pending",
                NinjaCriminalInvite.expires_at > utcnow(),
            ).order_by(NinjaCriminalInvite.id.desc())
        )).all()
        suffix = ""
        if invites:
            suffix = "\n\n📨 Приглашения: " + ", ".join(str(x.id) for x in invites[:5])
        return "🌑 Вы не состоите в преступной организации.\n/criminalorg create Название" + suffix
    org = await session.get(NinjaCriminalOrg, member.org_id)
    if not org:
        raise GameError("Организация не найдена.")
    members = (await session.scalars(select(NinjaCriminalMember).where(NinjaCriminalMember.org_id == org.id))).all()
    upgrades = _dict(org.upgrades)
    lines = [
        f"🌑 <b>{org.name}</b> · ур. {org.level}",
        f"🎭 Ваша роль: {CRIMINAL_ROLES.get(member.role, member.role)}",
        f"👥 Участники: {len(members)}/12",
        f"💰 Казна: {org.treasury:,}",
        f"🕶 Секретность: {org.secrecy}/100 · 🔥 Розыск: {org.heat}",
        f"🏚 База: {org.base_region}",
        f"🔧 Улучшения: база {upgrades.get('base', 0)} · разведка {upgrades.get('intel', 0)} · лаборатория {upgrades.get('lab', 0)} · защита {upgrades.get('defense', 0)}",
        "",
        "Состав:",
    ]
    for row in members[:12]:
        pp = await session.get(NinjaProfile, row.user_id)
        lines.append(f"• {pp.name if pp else row.user_id} [{row.user_id}] — {CRIMINAL_ROLES.get(row.role, row.role)}")
    return "\n".join(lines)


async def criminal_org_invite(session: AsyncSession, user_id: int, target_id: int) -> str:
    p = await require_profile(session, user_id)
    target = await require_profile(session, target_id)
    member = await _criminal_member(session, user_id)
    if not member or member.role not in {"leader", "right_hand"}:
        raise GameError("Приглашать могут лидер и правая рука.")
    if not target.nukenin:
        raise GameError("В преступную организацию можно приглашать только нукенина.")
    if await _criminal_member(session, target_id):
        raise GameError("Этот игрок уже состоит в организации.")
    count = int(await session.scalar(select(func.count(NinjaCriminalMember.id)).where(NinjaCriminalMember.org_id == member.org_id)) or 0)
    if count >= 12:
        raise GameError("Организация заполнена: 12/12.")
    old = await session.scalar(select(NinjaCriminalInvite).where(NinjaCriminalInvite.org_id == member.org_id, NinjaCriminalInvite.target_user_id == target_id))
    if old:
        old.status = "pending"
        old.inviter_user_id = user_id
        old.expires_at = utcnow() + timedelta(hours=48)
        invite = old
    else:
        invite = NinjaCriminalInvite(
            org_id=member.org_id,
            inviter_user_id=user_id,
            target_user_id=target_id,
            status="pending",
            expires_at=utcnow() + timedelta(hours=48),
        )
        session.add(invite)
        await session.flush()
    return f"📨 Приглашение #{invite.id} отправлено нукенину {target.name}."


async def criminal_org_accept(session: AsyncSession, user_id: int, invite_id: int) -> str:
    p = await require_profile(session, user_id)
    if not p.nukenin:
        raise GameError("Только нукенин может принять это приглашение.")
    if await _criminal_member(session, user_id):
        raise GameError("Вы уже состоите в организации.")
    invite = await session.get(NinjaCriminalInvite, invite_id)
    if not invite or invite.target_user_id != user_id or invite.status != "pending":
        raise GameError("Приглашение не найдено.")
    if _aware(invite.expires_at) <= utcnow():
        invite.status = "expired"
        raise GameError("Приглашение истекло.")
    count = int(await session.scalar(select(func.count(NinjaCriminalMember.id)).where(NinjaCriminalMember.org_id == invite.org_id)) or 0)
    if count >= 12:
        raise GameError("Организация уже заполнена.")
    session.add(NinjaCriminalMember(org_id=invite.org_id, user_id=user_id, role="initiate", contribution=0))
    invite.status = "accepted"
    org = await session.get(NinjaCriminalOrg, invite.org_id)
    return f"🌑 Вы вступили в {org.name if org else 'преступную организацию'}."


async def criminal_org_role(session: AsyncSession, user_id: int, target_id: int, role: str) -> str:
    role = CRIMINAL_ROLE_ALIASES.get(role.lower(), role.lower())
    if role not in CRIMINAL_ROLES or role == "leader":
        raise GameError("Роли: right_hand, enforcer, spy, researcher, initiate.")
    member = await _criminal_member(session, user_id)
    if not member or member.role != "leader":
        raise GameError("Роли назначает только лидер.")
    target = await session.scalar(select(NinjaCriminalMember).where(NinjaCriminalMember.org_id == member.org_id, NinjaCriminalMember.user_id == target_id))
    if not target:
        raise GameError("Игрок не состоит в вашей организации.")
    target.role = role
    return f"🎭 Игрок {target_id} получил роль {CRIMINAL_ROLES[role]}."


async def criminal_org_donate(session: AsyncSession, user_id: int, amount: int) -> str:
    p = await require_profile(session, user_id)
    member = await _criminal_member(session, user_id)
    if not member:
        raise GameError("Вы не состоите в организации.")
    if amount <= 0 or p.ryo < amount:
        raise GameError("Недостаточно рё или неверная сумма.")
    org = await session.get(NinjaCriminalOrg, member.org_id)
    if not org:
        raise GameError("Организация не найдена.")
    p.ryo -= amount
    org.treasury += amount
    member.contribution += amount
    return f"💰 В скрытую казну внесено {amount:,} рё. Казна: {org.treasury:,}."


async def criminal_org_upgrade(session: AsyncSession, user_id: int, key: str) -> str:
    member = await _criminal_member(session, user_id)
    if not member or member.role not in {"leader", "right_hand"}:
        raise GameError("Базу улучшают лидер или правая рука.")
    if key not in {"base", "intel", "lab", "defense"}:
        raise GameError("Улучшения: base, intel, lab, defense.")
    org = await session.get(NinjaCriminalOrg, member.org_id)
    if not org:
        raise GameError("Организация не найдена.")
    upgrades = _dict(org.upgrades)
    current = int(upgrades.get(key, 0))
    cost = 30_000 * (current + 1)
    if org.treasury < cost:
        raise GameError(f"Нужно {cost:,} рё в казне.")
    org.treasury -= cost
    upgrades[key] = current + 1
    org.upgrades = upgrades
    org.level = max(org.level, 1 + sum(int(v) for v in upgrades.values()) // 4)
    if key in {"base", "defense"}:
        org.secrecy = min(100, org.secrecy + 4)
    return f"🔧 {key} улучшено до уровня {current + 1}."


async def crime_action(session: AsyncSession, user_id: int, action: str) -> str:
    p = await require_profile(session, user_id)
    member = await _criminal_member(session, user_id)
    if not member:
        raise GameError("Сначала вступите в преступную организацию.")
    data = CRIME_ACTIONS.get(action)
    if not data:
        raise GameError("Действия: robbery, sabotage, intel.")
    org = await session.get(NinjaCriminalOrg, member.org_id)
    if not org:
        raise GameError("Организация не найдена.")
    energy = int(data["energy"])
    if p.energy < energy:
        raise GameError(f"Нужно {energy} энергии.")
    p.energy -= energy
    p.energy_updated_at = utcnow()
    role_bonus = {"leader": 0.04, "right_hand": 0.03, "enforcer": 0.05 if action != "intel" else 0, "spy": 0.08 if action == "intel" else 0.02, "researcher": 0.03, "initiate": 0}.get(member.role, 0)
    secrecy_bonus = min(0.12, int(org.secrecy) / 1000)
    chance = max(0.25, min(0.92, 0.52 + (player_power(p) - int(data["difficulty"])) / 3500 + role_bonus + secrecy_bonus))
    org.heat = min(100, int(org.heat) + int(data["heat"]))
    org.secrecy = max(0, int(org.secrecy) - max(1, int(data["heat"]) // 3))
    p.wanted_reward = max(0, int(p.wanted_reward) + int(data["heat"]) * 1200)
    if random.random() > chance:
        p.hp = max(1, p.hp - max(8, p.max_hp // 8))
        p.reputation = max(-10_000, p.reputation - 8)
        org.heat = min(100, org.heat + 5)
        return f"❌ {data['name']} провалено. Розыск организации: {org.heat}/100."
    reward = random.randint(*data["reward"])
    personal = int(reward * 0.70)
    treasury = reward - personal
    p.ryo += personal
    org.treasury += treasury
    member.contribution += reward
    p.reputation = max(-10_000, p.reputation - 3)
    await _log(session, "v3_crime_success", user_id, {"org_id": org.id, "action": action, "reward": reward})
    return f"✅ {data['name']} успешно.\n🪙 Вам +{personal:,} · казне +{treasury:,} · 🔥 розыск {org.heat}/100."


# ---------------------------------------------------------------------------
# Unique server Bijuu
# ---------------------------------------------------------------------------


async def ensure_bijuu(session: AsyncSession) -> None:
    existing = set((await session.scalars(select(NinjaBijuuState.bijuu_key))).all())
    for key, data in BIJUU.items():
        if key not in existing:
            session.add(
                NinjaBijuuState(
                    bijuu_key=key,
                    name=data["name"],
                    status="free",
                    region=data["region"],
                    hp=data["hp"],
                    max_hp=data["hp"],
                    approvals=[],
                    contributors={},
                    history=[],
                )
            )
    await session.flush()


async def bijuu_text(session: AsyncSession, user_id: int) -> str:
    await require_profile(session, user_id)
    await ensure_bijuu(session)
    rows = (await session.scalars(select(NinjaBijuuState).order_by(NinjaBijuuState.max_hp))).all()
    lines = ["🐾 <b>Биджу мира · MMO V3</b>"]
    for row in rows:
        if row.status == "sealed":
            status = f"🔒 джинчурики {row.host_user_id}"
        elif row.status == "awaiting_seal":
            status = f"⚡ подавлен · кандидат {row.candidate_user_id or 'не выбран'}"
        else:
            status = f"🌍 свободен · ❤️ {row.hp:,}/{row.max_hp:,}"
        lines.append(f"• {row.name}: {status} · {row.region}")
    return "\n".join(lines)


async def bijuu_hunt(session: AsyncSession, user_id: int, key: str) -> str:
    p = await require_profile(session, user_id)
    await ensure_bijuu(session)
    row = await session.get(NinjaBijuuState, key)
    if not row:
        raise GameError("Неизвестный биджу.")
    if row.status != "free":
        raise GameError("Сейчас этот биджу недоступен для охоты.")
    if p.level < 30:
        raise GameError("Для охоты на биджу нужен минимум 30 уровень.")
    if p.energy < 20:
        raise GameError("Нужно 20 энергии.")
    p.energy -= 20
    p.energy_updated_at = utcnow()
    damage = max(250, int(player_power(p) * random.uniform(0.70, 1.25)))
    row.hp = max(0, int(row.hp) - damage)
    contributors = _dict(row.contributors)
    contributors[str(user_id)] = int(contributors.get(str(user_id), 0)) + damage
    row.contributors = contributors
    p.village_points += max(1, damage // 2500)
    if row.hp > 0:
        return f"🐾 Вы атаковали {row.name}: −{damage:,} HP. Осталось {row.hp:,}/{row.max_hp:,}."
    row.status = "awaiting_seal"
    row.hp = 0
    row.candidate_user_id = None
    row.approvals = []
    await _log(session, "v3_bijuu_defeated", user_id, {"bijuu": key, "damage": damage})
    await _chronicle(session, "bijuu", "Биджу подавлен", f"{row.name} был подавлен объединёнными силами шиноби. Теперь деревни могут выбрать джинчурики.", actor_user_id=user_id)
    return f"⚡ {row.name} подавлен! Теперь Каге может выдвинуть кандидата: /bijuu nominate {key} USER_ID"


async def bijuu_nominate(session: AsyncSession, user_id: int, key: str, target_id: int) -> str:
    actor = await require_profile(session, user_id)
    target = await require_profile(session, target_id)
    if not await is_kage(session, actor):
        raise GameError("Кандидата в джинчурики выдвигает Каге.")
    if actor.village != target.village:
        raise GameError("Каге может выдвигать только шиноби своей деревни.")
    if target.nukenin or target.level < 35 or target.reputation < 200:
        raise GameError("Кандидату нужен 35 уровень, 200 репутации и статус шиноби деревни.")
    await ensure_bijuu(session)
    row = await session.get(NinjaBijuuState, key)
    if not row or row.status != "awaiting_seal":
        raise GameError("Этот биджу не ожидает запечатывания.")
    already = await session.scalar(select(NinjaBijuuState).where(NinjaBijuuState.host_user_id == target_id, NinjaBijuuState.status == "sealed"))
    if already:
        raise GameError("Этот игрок уже является джинчурики.")
    row.candidate_user_id = target_id
    row.approvals = [user_id]
    return f"🔒 {target.name} выдвинут кандидатом для {row.name}. Нужна поддержка члена Совета: /bijuu approve {key}"


async def bijuu_approve(session: AsyncSession, user_id: int, key: str) -> str:
    actor = await require_profile(session, user_id)
    await ensure_bijuu(session)
    row = await session.get(NinjaBijuuState, key)
    if not row or row.status != "awaiting_seal" or not row.candidate_user_id:
        raise GameError("Нет активной кандидатуры для этого биджу.")
    target = await require_profile(session, int(row.candidate_user_id))
    if actor.village != target.village:
        raise GameError("Голосовать может только руководство деревни кандидата.")
    if not (await is_kage(session, actor) or await is_council(session, actor)):
        raise GameError("Подтверждать кандидатуру могут Каге и Совет.")
    approvals = {int(x) for x in _list(row.approvals)}
    approvals.add(user_id)
    row.approvals = sorted(approvals)
    # One Kage nomination + a second independent approval is required.
    if len(approvals) < 2:
        return f"✅ Поддержка записана: {len(approvals)}/2."
    row.status = "sealed"
    row.host_user_id = target.user_id
    row.candidate_user_id = None
    row.hp = row.max_hp
    row.contributors = {}
    biju = _dict(target.biju)
    biju.update({"key": key, "trust": 0, "chakra": 0})
    target.biju = biju
    await _log(session, "v3_bijuu_sealed", target.user_id, {"bijuu": key, "village": target.village})
    await _chronicle(session, "bijuu", "Новый джинчурики", f"{target.name} стал носителем {row.name}.", actor_user_id=target.user_id, village=target.village)
    return f"🔒 {row.name} запечатан в {target.name}. Новый джинчурики появился в мире."


async def bijuu_release(session: AsyncSession, user_id: int, key: str) -> str:
    p = await require_profile(session, user_id)
    await ensure_bijuu(session)
    row = await session.get(NinjaBijuuState, key)
    if not row or row.status != "sealed" or row.host_user_id != user_id:
        raise GameError("Вы не являетесь носителем этого биджу.")
    row.status = "free"
    row.host_user_id = None
    row.candidate_user_id = None
    row.hp = row.max_hp
    row.approvals = []
    row.contributors = {}
    p.biju = {"key": None, "trust": 0, "chakra": 0}
    await _chronicle(session, "bijuu", "Биджу вернулся в мир", f"{row.name} снова свободен.", actor_user_id=user_id)
    return f"🌍 {row.name} освобождён и снова появился в мире."


# ---------------------------------------------------------------------------
# Newspaper / world pulse
# ---------------------------------------------------------------------------


async def newspaper_text(session: AsyncSession) -> str:
    chronicle = (await session.scalars(select(NinjaWorldChronicle).order_by(NinjaWorldChronicle.id.desc()).limit(8))).all()
    if chronicle:
        lines = ["📰 <b>Газета мира шиноби</b>"]
        for row in chronicle:
            lines.append(f"\n<b>{row.title}</b>\n{row.text}")
        return "\n".join(lines)
    events = (await session.scalars(select(NinjaEventLog).order_by(NinjaEventLog.id.desc()).limit(10))).all()
    if not events:
        return "📰 Мир пока не оставил важных записей."
    lines = ["📰 <b>Газета мира шиноби</b>"]
    for event in events:
        label = NEWS_LABELS.get(event.event_type, event.event_type)
        lines.append(f"• {label} · игрок {event.user_id or 'мир'}")
    return "\n".join(lines)


async def world_pulse_text(session: AsyncSession, user_id: int) -> str:
    p = await require_profile(session, user_id)
    await ensure_governments(session)
    await ensure_bijuu(session)
    governments = (await session.scalars(select(NinjaVillageGovernment))).all()
    criminal_count = int(await session.scalar(select(func.count(NinjaCriminalOrg.id))) or 0)
    free_bijuu = int(await session.scalar(select(func.count(NinjaBijuuState.bijuu_key)).where(NinjaBijuuState.status == "free")) or 0)
    sealed_bijuu = int(await session.scalar(select(func.count(NinjaBijuuState.bijuu_key)).where(NinjaBijuuState.status == "sealed")) or 0)
    active_events = int(await session.scalar(select(func.count(NinjaWorldEvent.id)).where(NinjaWorldEvent.status == "active")) or 0)
    treasury = sum(int(g.treasury) for g in governments)
    kages = sum(1 for g in governments if g.kage_user_id)
    return "\n".join([
        "🌍 <b>MMO V3 · Пульс мира</b>",
        f"👑 Действующих Каге: {kages}/{len(governments)}",
        f"💰 Казна пяти деревень: {treasury:,} рё",
        f"🌑 Преступных организаций: {criminal_count}",
        f"🐾 Свободных биджу: {free_bijuu} · запечатано: {sealed_bijuu}",
        f"🚨 Активных мировых событий: {active_events}",
        "",
        f"🥷 Ваш путь: {p.name} · {p.ninja_rank} · ур. {p.level}",
        "/government · /election · /project · /criminalorg · /bijuu · /newspaper",
    ])


async def world_chronicle_text(session: AsyncSession, limit: int = 15) -> str:
    rows = (await session.scalars(select(NinjaWorldChronicle).order_by(NinjaWorldChronicle.id.desc()).limit(max(1, min(limit, 30))))).all()
    if not rows:
        return "📚 Мировая летопись MMO V3 пока пуста."
    lines = ["📚 <b>Мировая летопись</b>"]
    for row in rows:
        lines.append(f"• {row.title} — {row.text}")
    return "\n".join(lines)
