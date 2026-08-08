from __future__ import annotations

import random
import re
from datetime import timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .content import BLOODLINES, ELEMENTS, RANK_REQUIREMENTS, RANKS, VILLAGES
from .engine import level_up_stats
from .models import (
    NinjaClan,
    NinjaClanMember,
    NinjaCustomTechnique,
    NinjaDynamicMission,
    NinjaEventLog,
    NinjaLegendRecord,
    NinjaNpcRelation,
    NinjaProfile,
    NinjaTechniqueResearch,
    NinjaTerritory,
    NinjaVillageWar,
    NinjaVillageWarContribution,
    NinjaWorldEvent,
    NinjaWorldState,
    utcnow,
)
from .service import GameError, require_profile


# ---------------------------------------------------------------------------
# Content: living world
# ---------------------------------------------------------------------------

TERRITORY_DEFS: dict[str, dict[str, Any]] = {
    "fire_north_forest": {
        "name": "🌲 Северный лес Страны Огня",
        "region": "Страна Огня",
        "controller": "konoha",
        "type": "forest",
        "resources": {"herb": 5, "wood": 8},
    },
    "fire_river_valley": {
        "name": "🌊 Долина реки",
        "region": "Страна Огня",
        "controller": "konoha",
        "type": "trade",
        "resources": {"river_fish": 6, "herb": 3},
    },
    "fire_mountain_pass": {
        "name": "⛰ Горный перевал",
        "region": "Страна Огня",
        "controller": None,
        "type": "fortress",
        "resources": {"metal": 5},
    },
    "wind_border": {
        "name": "🏜 Пограничные дюны",
        "region": "Страна Ветра",
        "controller": "suna",
        "type": "border",
        "resources": {"chakra_crystal": 1, "poison_gland": 3},
    },
    "water_mist_coast": {
        "name": "🌫 Побережье Тумана",
        "region": "Страна Воды",
        "controller": "kiri",
        "type": "port",
        "resources": {"rare_fish": 3, "metal": 3},
    },
    "lightning_highlands": {
        "name": "⚡ Высокогорье Молнии",
        "region": "Страна Молнии",
        "controller": "kumo",
        "type": "mountain",
        "resources": {"metal": 7, "chakra_crystal": 1},
    },
    "earth_mines": {
        "name": "🪨 Рудники Камня",
        "region": "Страна Земли",
        "controller": "iwa",
        "type": "mine",
        "resources": {"metal": 10},
    },
    "rain_crossroads": {
        "name": "🌧 Перекрёсток Амегакуре",
        "region": "Страна Дождя",
        "controller": None,
        "type": "intel",
        "resources": {"seal_paper": 5, "herb": 2},
    },
    "waves_trade_route": {
        "name": "🛣 Торговый путь Страны Волн",
        "region": "Страна Волн",
        "controller": None,
        "type": "trade",
        "resources": {"ryo": 8, "river_fish": 4},
    },
}

WAR_ROLE_LABELS = {
    "fighter": "🥷 Боевой шиноби",
    "medic": "💚 Медик",
    "scout": "🕵 Разведчик",
    "sealer": "📜 Мастер печатей",
    "crafter": "🔨 Ремесленник",
    "commander": "⚔️ Джонин-командир",
}

NPCS: dict[str, dict[str, Any]] = {
    "naruto": {"name": "🍥 Наруто Узумаки", "likes": "loyalty", "role": "союзник", "village": "konoha"},
    "kakashi": {"name": "⚡ Какаши Хатаке", "likes": "tactics", "role": "наставник", "village": "konoha"},
    "jiraiya": {"name": "🐸 Джирайя", "likes": "courage", "role": "наставник", "village": "konoha"},
    "tsunade": {"name": "💚 Цунаде", "likes": "medicine", "role": "наставник", "village": "konoha"},
    "sasuke": {"name": "⚡ Саске Учиха", "likes": "ambition", "role": "соперник", "village": "konoha"},
    "sakura": {"name": "🌸 Сакура Харуно", "likes": "compassion", "role": "союзник", "village": "konoha"},
    "shikamaru": {"name": "🧠 Шикамару Нара", "likes": "tactics", "role": "стратег", "village": "konoha"},
    "guy": {"name": "🔥 Майто Гай", "likes": "discipline", "role": "наставник", "village": "konoha"},
    "itachi": {"name": "🌑 Итачи Учиха", "likes": "understanding", "role": "неизвестно", "village": None},
    "orochimaru": {"name": "🐍 Орочимару", "likes": "ambition", "role": "опасный наставник", "village": None},
    "kaito": {"name": "🥷 Кайто Хьюга", "likes": "strength", "role": "личный соперник", "village": "konoha"},
    "aiko": {"name": "💚 Айко", "likes": "compassion", "role": "медик команды", "village": "konoha"},
    "miyu": {"name": "🎭 Мию", "likes": "loyalty", "role": "АНБУ-разведчик", "village": "konoha"},
    "daichi": {"name": "📜 Даичи", "likes": "discipline", "role": "мастер печатей", "village": "konoha"},
    "shin": {"name": "🌑 Шин", "likes": "freedom", "role": "загадочный нукенин", "village": None},
    "raizen": {"name": "🧬 Рейдзен", "likes": "ambition", "role": "антагонист", "village": None},
}

MISSION_OBJECTIVES = [
    ("escort", "🚚 Сопроводить важный груз"),
    ("investigation", "🕵 Расследовать исчезновение шиноби"),
    ("rescue", "💚 Спасти пропавший отряд"),
    ("capture", "🔒 Захватить цель живой"),
    ("defense", "🏯 Защитить объект"),
    ("infiltration", "🌑 Проникнуть на вражескую базу"),
    ("recovery", "📜 Вернуть секретный свиток"),
]

MISSION_REGIONS = [
    "Страна Огня",
    "Страна Волн",
    "Страна Дождя",
    "Страна Ветра",
    "Страна Воды",
    "Страна Молнии",
    "Страна Земли",
]

MISSION_TWISTS = [
    "Заказчик скрыл часть правды.",
    "Цель добровольно сотрудничает с противником.",
    "В отряде может быть предатель.",
    "На месте появился третий участник конфликта.",
    "Противник предлагает переговоры.",
    "Обстановка резко ухудшается и ранг задания повышается.",
    "Найдена улика, связывающая дело с преступной организацией.",
]

WORLD_EVENT_TEMPLATES = [
    ("flood", "🌊 Наводнение в Стране Волн", "Страна Волн"),
    ("fire", "🔥 Пожар возле Конохи", "Страна Огня"),
    ("prison_break", "☠️ Побег опасных преступников", "Страна Огня"),
    ("rain_conflict", "🌧 Конфликт в Амегакуре", "Страна Дождя"),
    ("beast", "🐍 Неизвестное существо замечено на границе", "Пограничные земли"),
    ("caravan", "💰 Караван с редкими ресурсами вышел в путь", "Мир шиноби"),
    ("shortage", "💊 Дефицит медицинских припасов", "Мир шиноби"),
]

SPECIALIZATIONS = {
    "ninjutsu": "🔥 Ниндзюцу-мастер",
    "taijutsu": "🥋 Мастер тайдзюцу",
    "genjutsu": "👁 Гендзюцу-специалист",
    "kenjutsu": "⚔️ Кендзюцу",
    "medic": "💚 Медик",
    "fuinjutsu": "📜 Фуиндзюцу",
    "sensor": "🕵 Сенсор",
}

EPITHETS = {
    "lightning": "⚡ Белая Молния",
    "fire": "🔥 Алый Демон",
    "shadow": "🌑 Безликая Тень",
    "biju": "🦊 Хранитель хвостатого",
    "medic": "💚 Рука Жизни",
    "commander": "⚔️ Клинок Деревни",
}


def _dict(value: Any) -> dict[str, Any]:
    return dict(value or {}) if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return list(value or []) if isinstance(value, list) else []


def _clamp(value: int, low: int = 0, high: int = 100) -> int:
    return max(low, min(high, int(value)))


def _is_expired(value: Any) -> bool:
    if value is None:
        return False
    now = utcnow()
    if getattr(value, "tzinfo", None) is None:
        now = now.replace(tzinfo=None)
    return value < now


def _village_name(key: str | None) -> str:
    if not key:
        return "⚪ Нейтрально"
    return VILLAGES.get(key, {}).get("name", key)


def _element_name(key: str | None) -> str:
    return ELEMENTS.get(str(key), str(key or "—"))


def _append_profile_history(profile: NinjaProfile, text: str, kind: str = "story") -> None:
    flags = _dict(profile.flags)
    history = _list(flags.get("path_history"))
    history.append({"at": utcnow().isoformat(), "kind": kind, "text": text})
    flags["path_history"] = history[-80:]
    profile.flags = flags


async def _event_log(session: AsyncSession, event_type: str, user_id: int | None, payload: dict[str, Any]) -> None:
    session.add(NinjaEventLog(event_type=event_type, user_id=user_id, payload=payload))


async def _is_kage(session: AsyncSession, profile: NinjaProfile) -> bool:
    if profile.ninja_rank == "kage":
        return True
    row = await session.get(NinjaWorldState, f"village:{profile.village}")
    return bool(row and int(_dict(row.value).get("kage_id") or 0) == int(profile.user_id))


# ---------------------------------------------------------------------------
# Territories and village war
# ---------------------------------------------------------------------------


async def ensure_territories(session: AsyncSession) -> None:
    rows = (await session.execute(select(NinjaTerritory.key))).scalars().all()
    known = set(rows)
    for key, data in TERRITORY_DEFS.items():
        if key in known:
            continue
        session.add(
            NinjaTerritory(
                key=key,
                name=data["name"],
                region=data["region"],
                controller_village=data["controller"],
                strategic_type=data["type"],
                defense=1200 if data["type"] == "fortress" else 800,
                resources=data["resources"],
                influence={data["controller"]: 100} if data["controller"] else {},
            )
        )
    await session.flush()


async def territory_map_text(session: AsyncSession) -> str:
    await ensure_territories(session)
    rows = (await session.execute(select(NinjaTerritory).order_by(NinjaTerritory.region, NinjaTerritory.name))).scalars().all()
    lines = ["🗺 <b>Политическая карта мира шиноби</b>"]
    for row in rows:
        marker = "⚔️" if row.contested else "•"
        lines.append(
            f"{marker} <b>{row.name}</b> — {_village_name(row.controller_village)} · "
            f"аванпост {row.outpost_level} · защита {row.defense:,}"
        )
    lines.append("\nТерритории дают ресурсы, оборону, торговые и разведывательные преимущества.")
    return "\n".join(lines)


async def territory_expedition(session: AsyncSession, user_id: int, key: str) -> str:
    profile = await require_profile(session, user_id)
    await ensure_territories(session)
    row = await session.get(NinjaTerritory, key)
    if not row:
        raise GameError("Неизвестная территория. Используй /territory map.")
    if row.controller_village == profile.village:
        raise GameError("Эта территория уже контролируется вашей деревней.")
    if profile.energy < 20:
        raise GameError("Нужно 20 энергии для экспедиции.")
    profile.energy -= 20
    influence = _dict(row.influence)
    power = max(10, profile.level * 8 + profile.ninjutsu + profile.taijutsu + profile.speed)
    gain = max(8, min(35, power // 35 + random.randint(2, 12)))
    influence[profile.village] = int(influence.get(profile.village, 0)) + gain
    row.influence = influence
    row.contested = len([v for v in influence.values() if int(v) >= 35]) > 1
    own = int(influence.get(profile.village, 0))
    best_enemy = max([int(v) for k, v in influence.items() if k != profile.village] or [0])
    captured = own >= 100 and own >= best_enemy + 25
    if captured:
        old = row.controller_village
        row.controller_village = profile.village
        row.contested = False
        row.outpost_level = max(1, row.outpost_level)
        row.defense = max(row.defense, 1500)
        row.influence = {profile.village: 100}
        hist = _list(row.history)
        hist.append({"at": utcnow().isoformat(), "event": "capture", "from": old, "to": profile.village, "user_id": user_id})
        row.history = hist[-50:]
        profile.village_points += 80
        profile.reputation += 25
        _append_profile_history(profile, f"Помог захватить территорию «{row.name}» для {_village_name(profile.village)}.", "war")
        await _event_log(session, "territory_capture", user_id, {"territory": key, "village": profile.village})
        return f"🏳 <b>Территория захвачена!</b>\n{row.name}\nНовый контроль: {_village_name(profile.village)}\n+80 очков деревни · +25 репутации"
    return f"🧭 Экспедиция завершена.\n{row.name}\nВлияние {_village_name(profile.village)}: {own}/100 (+{gain})\nСтатус: {'⚔️ спорная территория' if row.contested else 'разведка продолжается'}"


async def territory_outpost_upgrade(session: AsyncSession, user_id: int, key: str) -> str:
    profile = await require_profile(session, user_id)
    await ensure_territories(session)
    row = await session.get(NinjaTerritory, key)
    if not row or row.controller_village != profile.village:
        raise GameError("Можно укреплять только территорию своей деревни.")
    if row.outpost_level >= 10:
        raise GameError("Аванпост уже максимального уровня.")
    cost = 3000 * (row.outpost_level + 1)
    if profile.ryo < cost:
        raise GameError(f"Нужно {cost:,} рё.")
    profile.ryo -= cost
    row.outpost_level += 1
    row.defense += 900 + row.outpost_level * 120
    profile.village_points += 15
    return f"🏯 Аванпост улучшен до {row.outpost_level} уровня.\n🛡 Защита: {row.defense:,}\nСтоимость: {cost:,} рё"


async def _active_war_for_village(session: AsyncSession, village: str) -> NinjaVillageWar | None:
    return (
        await session.execute(
            select(NinjaVillageWar)
            .where(
                NinjaVillageWar.status == "active",
                (NinjaVillageWar.attacker_village == village) | (NinjaVillageWar.defender_village == village),
            )
            .order_by(NinjaVillageWar.id.desc())
        )
    ).scalars().first()


async def village_war_declare(session: AsyncSession, user_id: int, target: str) -> str:
    profile = await require_profile(session, user_id)
    target = target.lower()
    if target not in VILLAGES or target == profile.village:
        raise GameError("Укажи другую великую деревню: konoha|suna|kiri|kumo|iwa.")
    if not await _is_kage(session, profile):
        raise GameError("Объявление войны доступно только действующему Каге.")
    if await _active_war_for_village(session, profile.village):
        raise GameError("Ваша деревня уже участвует в активной войне.")
    war = NinjaVillageWar(
        attacker_village=profile.village,
        defender_village=target,
        status="active",
        attacker_score=0,
        defender_score=0,
        started_by=user_id,
        starts_at=utcnow(),
        ends_at=utcnow() + timedelta(days=7),
        front={"north": [0, 0], "center": [0, 0], "south": [0, 0], "phase": 1},
    )
    session.add(war)
    await session.flush()
    await _event_log(session, "village_war", user_id, {"war_id": war.id, "attacker": profile.village, "defender": target})
    return (
        "🚨 <b>ВОЙНА ОБЪЯВЛЕНА</b>\n"
        f"{_village_name(profile.village)} VS {_village_name(target)}\n"
        f"Война #{war.id} длится до 7 дней.\n"
        "Используй /mobilize fighter|medic|scout|sealer|crafter|commander и /front action."
    )


async def war_mobilize(session: AsyncSession, user_id: int, role: str) -> str:
    profile = await require_profile(session, user_id)
    role = role.lower()
    if role not in WAR_ROLE_LABELS:
        raise GameError("Роль: fighter|medic|scout|sealer|crafter|commander.")
    war = await _active_war_for_village(session, profile.village)
    if not war:
        raise GameError("Ваша деревня сейчас не участвует в большой войне.")
    row = (
        await session.execute(
            select(NinjaVillageWarContribution).where(
                NinjaVillageWarContribution.war_id == war.id,
                NinjaVillageWarContribution.user_id == user_id,
            )
        )
    ).scalars().first()
    if not row:
        row = NinjaVillageWarContribution(war_id=war.id, user_id=user_id, village=profile.village, role=role)
        session.add(row)
    else:
        row.role = role
    flags = _dict(profile.flags)
    flags["war_role"] = role
    profile.flags = flags
    return f"⚔️ Мобилизация подтверждена.\nВойна #{war.id}\nРоль: {WAR_ROLE_LABELS[role]}"


def _war_side(war: NinjaVillageWar, village: str) -> str:
    return "attacker" if war.attacker_village == village else "defender"


async def war_front_text(session: AsyncSession, user_id: int) -> str:
    profile = await require_profile(session, user_id)
    war = await _active_war_for_village(session, profile.village)
    if not war:
        raise GameError("Активной войны деревни нет.")
    front = _dict(war.front)
    lines = [
        f"⚔️ <b>Фронт войны #{war.id}</b>",
        f"{_village_name(war.attacker_village)} — {war.attacker_score:,}",
        f"{_village_name(war.defender_village)} — {war.defender_score:,}",
    ]
    for key, label in (("north", "Север"), ("center", "Центр"), ("south", "Юг")):
        pair = front.get(key, [0, 0])
        lines.append(f"• {label}: {int(pair[0]):,} : {int(pair[1]):,}")
    row = (
        await session.execute(
            select(NinjaVillageWarContribution).where(
                NinjaVillageWarContribution.war_id == war.id,
                NinjaVillageWarContribution.user_id == user_id,
            )
        )
    ).scalars().first()
    if row:
        lines.append(f"\nВаш вклад: {row.score:,} · {WAR_ROLE_LABELS.get(row.role, row.role)}")
    return "\n".join(lines)


async def war_action(session: AsyncSession, user_id: int, front_key: str = "center") -> str:
    profile = await require_profile(session, user_id)
    war = await _active_war_for_village(session, profile.village)
    if not war:
        raise GameError("Активной войны нет.")
    row = (
        await session.execute(
            select(NinjaVillageWarContribution).where(
                NinjaVillageWarContribution.war_id == war.id,
                NinjaVillageWarContribution.user_id == user_id,
            )
        )
    ).scalars().first()
    if not row:
        raise GameError("Сначала мобилизуйся: /mobilize fighter|medic|scout|sealer|crafter|commander")
    if profile.energy < 12:
        raise GameError("Для действия на фронте нужно 12 энергии.")
    profile.energy -= 12
    front_key = front_key if front_key in {"north", "center", "south"} else "center"
    role_mult = {"fighter": 1.15, "medic": 0.95, "scout": 0.90, "sealer": 1.0, "crafter": 0.85, "commander": 1.20}.get(row.role, 1.0)
    base = profile.level * 5 + profile.ninjutsu + profile.taijutsu + profile.chakra_control
    gained = max(40, int((base / 5 + random.randint(20, 90)) * role_mult))
    if row.role == "medic":
        gained += profile.chakra_control // 2
    elif row.role == "scout":
        gained += profile.speed // 2
    elif row.role == "commander":
        gained += int(_dict(profile.counters).get("commander_authority", 0)) // 3
    row.score += gained
    row.actions += 1
    front = _dict(war.front)
    pair = list(front.get(front_key, [0, 0]))
    side = _war_side(war, profile.village)
    idx = 0 if side == "attacker" else 1
    pair[idx] = int(pair[idx]) + gained
    front[front_key] = pair
    war.front = front
    if side == "attacker":
        war.attacker_score += gained
    else:
        war.defender_score += gained
    profile.village_points += max(1, gained // 40)
    counters = _dict(profile.counters)
    counters["war_actions"] = int(counters.get("war_actions", 0)) + 1
    if row.role == "commander":
        counters["commander_authority"] = int(counters.get("commander_authority", 0)) + random.randint(1, 3)
    profile.counters = counters
    return f"⚔️ Действие на фронте выполнено.\nНаправление: {front_key}\nВклад: +{gained:,}\nЛичный вклад: {row.score:,}"


async def war_spy(session: AsyncSession, user_id: int) -> str:
    profile = await require_profile(session, user_id)
    war = await _active_war_for_village(session, profile.village)
    if not war:
        raise GameError("Разведка фронта доступна во время войны.")
    row = (
        await session.execute(
            select(NinjaVillageWarContribution).where(
                NinjaVillageWarContribution.war_id == war.id,
                NinjaVillageWarContribution.user_id == user_id,
            )
        )
    ).scalars().first()
    if not row or row.role != "scout":
        raise GameError("Эта операция доступна мобилизованным разведчикам.")
    chance = min(0.9, 0.45 + profile.speed / 1500 + profile.accuracy / 2000)
    if random.random() <= chance:
        bonus = random.randint(90, 230)
        if _war_side(war, profile.village) == "attacker":
            war.attacker_score += bonus
        else:
            war.defender_score += bonus
        row.score += bonus
        return f"🕵 Разведка успешна.\nРаскрыты маршруты снабжения противника.\nВоенный эффект: +{bonus} очков фронта."
    row.actions += 1
    return "🕵 Разведгруппа была обнаружена. Информацию получить не удалось, но вы смогли отступить."


async def war_peace_offer(session: AsyncSession, user_id: int, compensation: int = 0) -> str:
    profile = await require_profile(session, user_id)
    war = await _active_war_for_village(session, profile.village)
    if not war:
        raise GameError("Нет активной войны.")
    if not await _is_kage(session, profile):
        raise GameError("Предложение мира отправляет действующий Каге.")
    terms = {"offered_by": profile.village, "compensation": max(0, compensation), "at": utcnow().isoformat(), "accepted": False}
    war.peace_terms = terms
    return f"🕊 Мирное предложение отправлено.\nКомпенсация: {compensation:,} рё\nКаге противоположной стороны может использовать /peace accept."


async def war_peace_accept(session: AsyncSession, user_id: int) -> str:
    profile = await require_profile(session, user_id)
    war = await _active_war_for_village(session, profile.village)
    if not war:
        raise GameError("Нет активной войны.")
    if not await _is_kage(session, profile):
        raise GameError("Принять мир может действующий Каге.")
    terms = _dict(war.peace_terms)
    if not terms or terms.get("offered_by") == profile.village:
        raise GameError("Нет мирного предложения от противника.")
    terms["accepted"] = True
    war.peace_terms = terms
    war.status = "peace"
    war.completed_at = utcnow()
    winner = war.attacker_village if war.attacker_score > war.defender_score else war.defender_village
    await _event_log(session, "war_peace", user_id, {"war_id": war.id, "winner_by_score": winner})
    return f"🕊 <b>Война завершена мирным договором.</b>\nИтоговый счёт: {war.attacker_score:,} : {war.defender_score:,}"


# ---------------------------------------------------------------------------
# Living NPCs, memory and promises
# ---------------------------------------------------------------------------


async def _npc_relation(session: AsyncSession, user_id: int, npc_key: str) -> NinjaNpcRelation:
    npc_key = npc_key.lower()
    if npc_key not in NPCS:
        raise GameError("Неизвестный NPC. Доступные: " + ", ".join(NPCS))
    row = (
        await session.execute(
            select(NinjaNpcRelation).where(NinjaNpcRelation.user_id == user_id, NinjaNpcRelation.npc_key == npc_key)
        )
    ).scalars().first()
    if row:
        return row
    base = 5 if npc_key in {"naruto", "sakura", "kaito", "aiko"} else 0
    row = NinjaNpcRelation(user_id=user_id, npc_key=npc_key, trust=base, respect=base)
    session.add(row)
    await session.flush()
    return row


async def npc_relation_text(session: AsyncSession, user_id: int, npc_key: str) -> str:
    profile = await require_profile(session, user_id)
    row = await _npc_relation(session, user_id, npc_key)
    npc = NPCS[row.npc_key]
    status = "Незнакомец"
    if row.trust >= 80:
        status = "Доверенное лицо"
    elif row.trust >= 55:
        status = "Близкий союзник"
    elif row.trust >= 30:
        status = "Союзник"
    elif row.trust >= 10:
        status = "Знакомый"
    if row.rivalry >= 60:
        status += " · ⚔️ Вечный соперник"
    return (
        f"{npc['name']}\n"
        f"Роль: {npc['role']}\nСтатус: <b>{status}</b>\n\n"
        f"🤝 Доверие: {row.trust}/100\n❤️ Привязанность: {row.affection}/100\n"
        f"🎖 Уважение: {row.respect}/100\n⚠️ Подозрение: {row.suspicion}/100\n"
        f"☠️ Страх: {row.fear}/100\n⚔️ Соперничество: {row.rivalry}/100\n"
        f"🌓 Понимание: {row.understanding}/100\n"
        f"📜 Важных воспоминаний: {len(_list(row.memories))}\n"
        f"🤝 Активных обещаний: {len([p for p in _list(row.promises) if p.get('status') == 'active'])}\n\n"
        f"Ваш путь: {profile.ninja_rank} · уровень {profile.level}"
    )


async def npc_memory_add(
    session: AsyncSession,
    user_id: int,
    npc_key: str,
    text: str,
    *,
    importance: int = 1,
    trust: int = 0,
    respect: int = 0,
    suspicion: int = 0,
) -> None:
    row = await _npc_relation(session, user_id, npc_key)
    memories = _list(row.memories)
    memories.append({"at": utcnow().isoformat(), "text": text[:300], "importance": int(importance)})
    memories.sort(key=lambda x: int(x.get("importance", 0)), reverse=True)
    row.memories = memories[:30]
    row.trust = _clamp(row.trust + trust)
    row.respect = _clamp(row.respect + respect)
    row.suspicion = _clamp(row.suspicion + suspicion)


async def npc_memories_text(session: AsyncSession, user_id: int, npc_key: str) -> str:
    row = await _npc_relation(session, user_id, npc_key)
    npc = NPCS[npc_key.lower()]
    memories = _list(row.memories)
    if not memories:
        return f"📖 {npc['name']} пока не связывает с вами важных событий."
    lines = [f"📖 <b>Память: {npc['name']}</b>"]
    for item in memories[:12]:
        lines.append(f"• {item.get('text', 'Событие')}" )
    return "\n".join(lines)


async def npc_interact(session: AsyncSession, user_id: int, npc_key: str, action: str) -> str:
    profile = await require_profile(session, user_id)
    row = await _npc_relation(session, user_id, npc_key)
    npc = NPCS[npc_key.lower()]
    action = action.lower()
    morality = _dict(profile.morality)
    flags = _dict(profile.flags)
    if action in {"talk", "поговорить"}:
        row.trust = _clamp(row.trust + random.randint(1, 3))
        if npc["likes"] == "tactics" and profile.chakra_control >= 80:
            row.respect = _clamp(row.respect + 2)
        if npc_key == "itachi" and profile.story_chapter >= 10:
            row.understanding = _clamp(row.understanding + 4)
        return f"💬 Вы поговорили с {npc['name']}.\n🤝 Доверие: {row.trust}/100 · 🎖 Уважение: {row.respect}/100"
    if action in {"train", "тренировка"}:
        if row.trust < 20 and npc_key not in {"guy", "kaito"}:
            raise GameError(f"{npc['name']} пока не готов тренировать вас. Нужно больше доверия.")
        gain = random.randint(2, 5)
        if npc_key in {"kakashi", "jiraiya", "orochimaru"}:
            profile.ninjutsu += gain
        elif npc_key == "guy":
            profile.taijutsu += gain
            profile.speed += 1
        elif npc_key == "tsunade":
            profile.chakra_control += gain
        else:
            profile.accuracy += max(1, gain // 2)
        row.respect = _clamp(row.respect + 3)
        return f"🥋 Тренировка с {npc['name']} завершена.\nПрогресс: +{gain} к профильной характеристике · 🎖 уважение +3"
    if action in {"help", "помочь"}:
        row.trust = _clamp(row.trust + 6)
        row.affection = _clamp(row.affection + 3)
        morality["compassion"] = int(morality.get("compassion", 0)) + 2
        profile.morality = morality
        await npc_memory_add(session, user_id, npc_key, "Игрок добровольно помог в личном деле.", importance=2, trust=2)
        return f"🤝 Вы помогли {npc['name']}. Это решение запомнится."
    if action in {"lie", "соврать"}:
        chance = min(0.85, 0.35 + int(morality.get("cunning", 0)) / 200 + profile.genjutsu / 1000)
        if random.random() <= chance:
            row.suspicion = _clamp(row.suspicion + 5)
            flags["lies_told"] = int(flags.get("lies_told", 0)) + 1
            profile.flags = flags
            return f"🌑 {npc['name']} пока поверил вам. ⚠️ Подозрение: {row.suspicion}/100"
        row.suspicion = _clamp(row.suspicion + 18)
        row.trust = _clamp(row.trust - 8)
        await npc_memory_add(session, user_id, npc_key, "Игрок был пойман на лжи.", importance=3)
        return f"⚠️ Ложь раскрыта. {npc['name']} теперь доверяет вам меньше."
    if action in {"challenge", "вызов"}:
        row.rivalry = _clamp(row.rivalry + 6)
        row.respect = _clamp(row.respect + (3 if profile.level >= 20 else 1))
        return f"⚔️ Вы бросили вызов {npc['name']}. Соперничество: {row.rivalry}/100"
    raise GameError("Действие NPC: talk|train|help|lie|challenge.")


async def npc_promise(session: AsyncSession, user_id: int, npc_key: str, text: str) -> str:
    if not text.strip():
        raise GameError("Напиши содержание обещания.")
    row = await _npc_relation(session, user_id, npc_key)
    promises = _list(row.promises)
    promises.append({"id": len(promises) + 1, "text": text[:180], "status": "active", "created_at": utcnow().isoformat()})
    row.promises = promises[-20:]
    return f"🤝 Обещание дано персонажу {NPCS[npc_key.lower()]['name']}:\n«{text[:180]}»\nМир запомнит, выполнили вы его или нарушили."


async def npc_promise_resolve(session: AsyncSession, user_id: int, npc_key: str, promise_id: int, success: bool) -> str:
    profile = await require_profile(session, user_id)
    row = await _npc_relation(session, user_id, npc_key)
    promises = _list(row.promises)
    target = next((p for p in promises if int(p.get("id", 0)) == promise_id and p.get("status") == "active"), None)
    if not target:
        raise GameError("Активное обещание не найдено.")
    target["status"] = "fulfilled" if success else "broken"
    target["resolved_at"] = utcnow().isoformat()
    row.promises = promises
    titles = _list(profile.titles)
    counters = _dict(profile.counters)
    if success:
        row.trust = _clamp(row.trust + 12)
        counters["promises_kept"] = int(counters.get("promises_kept", 0)) + 1
        if counters["promises_kept"] >= 10 and "Человек слова" not in titles:
            titles.append("Человек слова")
    else:
        row.trust = _clamp(row.trust - 18)
        row.suspicion = _clamp(row.suspicion + 10)
        counters["promises_broken"] = int(counters.get("promises_broken", 0)) + 1
        if counters["promises_broken"] >= 5 and "Ненадёжный" not in titles:
            titles.append("Ненадёжный")
    profile.counters = counters
    profile.titles = titles
    await npc_memory_add(session, user_id, npc_key, f"Обещание «{target['text']}» было {'выполнено' if success else 'нарушено'}.", importance=3)
    return f"{'✅ Обещание выполнено.' if success else '💔 Обещание нарушено.'}\nДоверие {NPCS[npc_key.lower()]['name']}: {row.trust}/100"


async def npc_personal_quest(session: AsyncSession, user_id: int, npc_key: str) -> str:
    profile = await require_profile(session, user_id)
    row = await _npc_relation(session, user_id, npc_key)
    npc = NPCS[npc_key.lower()]
    if row.trust < 30:
        raise GameError("Личное задание откроется при доверии 30+.")
    flags = _dict(row.flags)
    last_level = int(flags.get("quest_level", 0))
    tier = min(5, 1 + row.trust // 20)
    if last_level >= tier:
        raise GameError("Новых личных заданий у этого персонажа пока нет.")
    flags["quest_level"] = tier
    row.flags = flags
    reward = 500 * tier + profile.level * 30
    profile.ryo += reward
    profile.xp += 100 * tier
    level_up_stats(profile)
    row.trust = _clamp(row.trust + 7)
    row.respect = _clamp(row.respect + 5)
    await npc_memory_add(session, user_id, npc_key, f"Вместе выполнено личное задание уровня {tier}.", importance=3)
    _append_profile_history(profile, f"Выполнено личное задание: {npc['name']}.", "npc")
    return f"📜 <b>Личное задание: {npc['name']}</b>\nЦепочка уровня {tier} завершена.\nНаграда: {reward:,} рё · доверие +7 · уважение +5"


# ---------------------------------------------------------------------------
# Dynamic missions and world director
# ---------------------------------------------------------------------------


def _mission_rank(profile: NinjaProfile) -> str:
    if profile.level >= 58:
        return random.choices(["A", "S"], [70, 30])[0]
    if profile.level >= 34:
        return random.choices(["B", "A"], [65, 35])[0]
    if profile.level >= 16:
        return random.choices(["C", "B"], [60, 40])[0]
    return random.choices(["D", "C"], [65, 35])[0]


async def dynamic_mission_create(session: AsyncSession, user_id: int) -> str:
    profile = await require_profile(session, user_id)
    active = (
        await session.execute(
            select(NinjaDynamicMission).where(NinjaDynamicMission.user_id == user_id, NinjaDynamicMission.status == "active")
        )
    ).scalars().first()
    if active:
        return await dynamic_mission_text(session, user_id)
    mission_type, objective = random.choice(MISSION_OBJECTIVES)
    rank = _mission_rank(profile)
    region = random.choice(MISSION_REGIONS)
    twist = random.choice(MISSION_TWISTS)
    choices = [
        {"key": "direct", "name": "⚔️ Действовать напрямую"},
        {"key": "investigate", "name": "🕵 Собрать больше информации"},
        {"key": "negotiate", "name": "🗣 Попытаться договориться"},
        {"key": "retreat", "name": "🏃 Отступить"},
    ]
    time_limited = random.random() < 0.28
    row = NinjaDynamicMission(
        user_id=user_id,
        title=objective,
        rank=rank,
        mission_type=mission_type,
        status="active",
        context={"region": region, "twist": twist, "stage": 1, "prepared": False},
        choices=choices,
        expires_at=utcnow() + timedelta(hours=6) if time_limited else None,
    )
    session.add(row)
    await session.flush()
    return await dynamic_mission_text(session, user_id)


async def dynamic_mission_text(session: AsyncSession, user_id: int) -> str:
    row = (
        await session.execute(
            select(NinjaDynamicMission)
            .where(NinjaDynamicMission.user_id == user_id, NinjaDynamicMission.status == "active")
            .order_by(NinjaDynamicMission.id.desc())
        )
    ).scalars().first()
    if not row:
        return "📜 Активной живой миссии нет. Используй /livemission new."
    context = _dict(row.context)
    expiry = ""
    if row.expires_at:
        expiry = f"\n⏳ Срок: {row.expires_at.isoformat(timespec='minutes')}"
    lines = [
        f"📜 <b>Живая миссия #{row.id} — {row.rank}-ранг</b>",
        row.title,
        f"Регион: {context.get('region')}",
        f"Стадия: {context.get('stage', 1)}",
        f"⚠️ Неожиданность: {context.get('twist')}",
        expiry,
        "\nВыбор: /livemission choose direct|investigate|negotiate|retreat",
    ]
    return "\n".join(x for x in lines if x)


async def dynamic_mission_prepare(session: AsyncSession, user_id: int) -> str:
    profile = await require_profile(session, user_id)
    row = (
        await session.execute(
            select(NinjaDynamicMission).where(NinjaDynamicMission.user_id == user_id, NinjaDynamicMission.status == "active")
        )
    ).scalars().first()
    if not row:
        raise GameError("Сначала создай живую миссию.")
    if profile.ryo < 500:
        raise GameError("На подготовку нужно 500 рё.")
    profile.ryo -= 500
    context = _dict(row.context)
    context["prepared"] = True
    context["intel_bonus"] = 0.12
    row.context = context
    return "🎒 Подготовка завершена: расходники, маршрут и разведданные собраны. Шанс успеха повышен."


async def dynamic_mission_choose(session: AsyncSession, user_id: int, choice: str) -> str:
    profile = await require_profile(session, user_id)
    row = (
        await session.execute(
            select(NinjaDynamicMission).where(NinjaDynamicMission.user_id == user_id, NinjaDynamicMission.status == "active")
        )
    ).scalars().first()
    if not row:
        raise GameError("Активной живой миссии нет.")
    now = utcnow()
    if _is_expired(row.expires_at):
        row.status = "expired"
        profile.reputation -= 2
        raise GameError("Время миссии истекло. Мир продолжил жить без вашего участия.")
    choice = choice.lower()
    if choice not in {"direct", "investigate", "negotiate", "retreat"}:
        raise GameError("Выбор: direct|investigate|negotiate|retreat.")
    context = _dict(row.context)
    if choice == "retreat":
        row.status = "failed"
        row.completed_at = now
        penalty = 2 if row.rank in {"D", "C"} else 8
        profile.reputation -= penalty
        row.outcome = {"choice": choice, "success": False, "reason": "retreat"}
        _append_profile_history(profile, f"Отступил с живой миссии {row.rank}-ранга.", "mission")
        return f"🏃 Вы отступили. Репутация: -{penalty}. Основной сюжет не потерян."
    base = 0.48 + min(0.32, profile.level / 250)
    morality = _dict(profile.morality)
    if context.get("prepared"):
        base += 0.12
    if choice == "investigate":
        base += min(0.18, (profile.accuracy + profile.speed) / 1600)
    elif choice == "negotiate":
        base += min(0.16, (profile.reputation + int(morality.get("compassion", 0)) * 5) / 8000)
    elif choice == "direct":
        base += min(0.16, (profile.ninjutsu + profile.taijutsu) / 1800)
    success = random.random() <= min(0.92, base)
    rank_mult = {"D": 1, "C": 2, "B": 4, "A": 7, "S": 12}[row.rank]
    if success:
        reward = random.randint(350, 650) * rank_mult
        xp = random.randint(120, 220) * rank_mult
        profile.ryo += reward
        profile.xp += xp
        profile.reputation += rank_mult * 2
        profile.missions_completed += 1
        ups = level_up_stats(profile)
        row.status = "completed"
        row.completed_at = now
        row.outcome = {"choice": choice, "success": True, "reward": reward, "xp": xp}
        _append_profile_history(profile, f"Завершил живую миссию «{row.title}» ({row.rank}).", "mission")
        if profile.mentor and profile.mentor in NPCS:
            await npc_memory_add(session, user_id, profile.mentor, f"Игрок успешно завершил {row.rank}-миссию способом: {choice}.", importance=1, respect=2)
        note = f" · повышение уровня x{len(ups)}" if ups else ""
        return f"✅ <b>Миссия завершена</b>\nРешение: {choice}\nНаграда: {reward:,} рё · {xp:,} XP · репутация +{rank_mult*2}{note}"
    profile.hp = max(1, profile.hp - max(8, profile.max_hp // 10))
    row.status = "failed"
    row.completed_at = now
    row.outcome = {"choice": choice, "success": False, "twist": context.get("twist")}
    _append_profile_history(profile, f"Провалил живую миссию «{row.title}»; история продолжилась с последствиями.", "mission")
    return f"❌ Миссия провалена, но история не откатывается.\nПоследствие: {context.get('twist')}\n❤️ HP: {profile.hp}/{profile.max_hp}"


async def ensure_world_event(session: AsyncSession) -> NinjaWorldEvent:
    now = utcnow()
    active = (
        await session.execute(
            select(NinjaWorldEvent)
            .where(NinjaWorldEvent.status == "active", (NinjaWorldEvent.expires_at.is_(None)) | (NinjaWorldEvent.expires_at > now))
            .order_by(NinjaWorldEvent.id.desc())
        )
    ).scalars().first()
    if active:
        return active
    key, title, region = random.choice(WORLD_EVENT_TEMPLATES)
    intensity = random.randint(1, 5)
    active = NinjaWorldEvent(
        event_key=key,
        title=title,
        kind="dynamic",
        region=region,
        status="active",
        chain_key=f"{key}:{now.date().isoformat()}",
        payload={"intensity": intensity, "progress": 0, "goal": 800 * intensity, "participants": 0},
        starts_at=now,
        expires_at=now + timedelta(hours=random.randint(8, 24)),
    )
    session.add(active)
    await session.flush()
    return active


async def world_events_text(session: AsyncSession) -> str:
    await ensure_world_event(session)
    now = utcnow()
    rows = (
        await session.execute(
            select(NinjaWorldEvent)
            .where(NinjaWorldEvent.status == "active", (NinjaWorldEvent.expires_at.is_(None)) | (NinjaWorldEvent.expires_at > now))
            .order_by(NinjaWorldEvent.id.desc())
            .limit(8)
        )
    ).scalars().all()
    lines = ["🌍 <b>Что происходит сейчас</b>"]
    for row in rows:
        p = _dict(row.payload)
        lines.append(f"• #{row.id} {row.title} — {row.region} · {int(p.get('progress',0))}/{int(p.get('goal',1))}")
    war_count = int((await session.execute(select(func.count()).select_from(NinjaVillageWar).where(NinjaVillageWar.status == "active"))).scalar_one())
    if war_count:
        lines.append(f"⚔️ Активных войн деревень: {war_count}")
    return "\n".join(lines)


async def world_event_participate(session: AsyncSession, user_id: int, event_id: int) -> str:
    profile = await require_profile(session, user_id)
    row = await session.get(NinjaWorldEvent, event_id)
    if not row or row.status != "active":
        raise GameError("Событие уже завершено или не найдено.")
    if _is_expired(row.expires_at):
        row.status = "expired"
        raise GameError("Событие уже завершилось без вашего участия.")
    if profile.energy < 10:
        raise GameError("Для участия нужно 10 энергии.")
    profile.energy -= 10
    impact = max(30, profile.level * 4 + (profile.ninjutsu + profile.taijutsu + profile.chakra_control) // 8)
    p = _dict(row.payload)
    p["progress"] = int(p.get("progress", 0)) + impact
    p["participants"] = int(p.get("participants", 0)) + 1
    row.payload = p
    profile.village_points += max(1, impact // 50)
    if p["progress"] >= int(p.get("goal", 1)):
        row.status = "resolved"
        row.resolved_at = utcnow()
        reward = 1200 * int(p.get("intensity", 1))
        profile.ryo += reward
        await _event_log(session, "world_event_resolved", user_id, {"event_id": row.id, "event_key": row.event_key})
        return f"🌟 Вы помогли завершить мировое событие: {row.title}\nЛичная награда за финальный вклад: {reward:,} рё."
    return f"🌍 Вклад в событие: +{impact}\n{row.title}\nОбщий прогресс: {p['progress']}/{p['goal']}"


async def recommendations_text(session: AsyncSession, user_id: int) -> str:
    profile = await require_profile(session, user_id)
    suggestions: list[str] = []
    for key, req in RANK_REQUIREMENTS.items():
        ranks = [r[0] for r in RANKS]
        if ranks.index(key) > ranks.index(profile.ninja_rank):
            missing = []
            if profile.level < int(req.get("level", 0)):
                missing.append(f"уровень {profile.level}/{req['level']}")
            if profile.missions_completed < int(req.get("missions", 0)):
                missing.append(f"миссии {profile.missions_completed}/{req['missions']}")
            if profile.reputation < int(req.get("reputation", 0)):
                missing.append(f"репутация {profile.reputation}/{req['reputation']}")
            suggestions.append(f"🎖 До следующего ранга: {', '.join(missing) if missing else 'можно идти на экзамен'}.")
            break
    if profile.mentor and profile.mentor in NPCS:
        rel = await _npc_relation(session, user_id, profile.mentor)
        if rel.trust >= 30:
            suggestions.append(f"👤 {NPCS[profile.mentor]['name']} готов к личной сюжетной ветке.")
    war = await _active_war_for_village(session, profile.village)
    if war:
        suggestions.append(f"⚔️ Ваша деревня участвует в войне #{war.id}; мобилизация принесёт очки деревни.")
    event = await ensure_world_event(session)
    suggestions.append(f"🌍 Активно мировое событие #{event.id}: {event.title}.")
    if profile.level >= 50 and not _dict(profile.flags).get("specialization"):
        suggestions.append("🧭 Вы можете закрепить боевую специализацию: /path specialize ...")
    return "🧭 <b>Что мне делать?</b>\n" + "\n".join(f"{i+1}. {s}" for i, s in enumerate(suggestions[:6]))


async def return_summary_text(session: AsyncSession, user_id: int) -> str:
    profile = await require_profile(session, user_id)
    flags = _dict(profile.flags)
    last = flags.get("last_return_summary")
    now = utcnow()
    flags["last_return_summary"] = now.isoformat()
    profile.flags = flags
    events = (
        await session.execute(select(NinjaEventLog).order_by(NinjaEventLog.id.desc()).limit(8))
    ).scalars().all()
    lines = ["🌅 <b>С возвращением в мир шиноби</b>"]
    if last:
        lines.append(f"Последний обзор: {last[:16].replace('T', ' ')}")
    if events:
        lines.append("\nЗа последнее время:")
        for event in events[:5]:
            payload = _dict(event.payload)
            lines.append(f"• {event.event_type}: {payload.get('territory') or payload.get('event_key') or payload.get('war_id') or 'изменение мира'}")
    lines.append("\nИспользуй /recommend, чтобы увидеть актуальные цели персонажа.")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Progression, specialization, technique research, legends and legacy
# ---------------------------------------------------------------------------


def _combat_path(profile: NinjaProfile) -> str:
    stats = {
        "ninjutsu": profile.ninjutsu,
        "taijutsu": profile.taijutsu,
        "genjutsu": profile.genjutsu,
        "sensor": profile.accuracy + profile.speed // 2,
        "medic": profile.chakra_control + (30 if profile.profession == "medic" else 0),
    }
    return max(stats, key=stats.get)


async def path_text(session: AsyncSession, user_id: int) -> str:
    profile = await require_profile(session, user_id)
    flags = _dict(profile.flags)
    spec = flags.get("specialization") or _combat_path(profile)
    prestige_level = int(flags.get("prestige", 0))
    ep = flags.get("epithet")
    second = _element_name(profile.secondary_element) if profile.secondary_element else "не открыта"
    blood = BLOODLINES.get(profile.bloodline, {}).get("name", profile.bloodline)
    lines = [
        "🥷 <b>Путь шиноби</b>",
        f"Имя: {profile.name}",
        f"Ранг: {profile.ninja_rank} · уровень {profile.level}",
        f"Род: {blood}",
        f"Стихии: {_element_name(profile.primary_element)} / {second}",
        f"Боевой путь: {SPECIALIZATIONS.get(spec, spec)}",
        f"🌟 Престиж: {prestige_level}",
        f"🌍 Известность: {int(_dict(profile.counters).get('world_fame', 0)):,}",
    ]
    if ep:
        lines.append(f"📛 Прозвище: {ep}")
    lines.append("\nРазвитие — это не только уровень: ранг, техники, отношения, клан, войны и история мира учитываются отдельно.")
    return "\n".join(lines)


async def specialize(session: AsyncSession, user_id: int, key: str) -> str:
    profile = await require_profile(session, user_id)
    key = key.lower()
    if key not in SPECIALIZATIONS:
        raise GameError("Специализация: ninjutsu|taijutsu|genjutsu|kenjutsu|medic|fuinjutsu|sensor.")
    if profile.level < 20:
        raise GameError("Специализация открывается с 20 уровня.")
    flags = _dict(profile.flags)
    old = flags.get("specialization")
    if old and old != key and int(flags.get("respecs", 0)) >= 2:
        raise GameError("Лимит бесплатных смен специализации исчерпан.")
    if old and old != key:
        flags["respecs"] = int(flags.get("respecs", 0)) + 1
    flags["specialization"] = key
    profile.flags = flags
    _append_profile_history(profile, f"Боевой путь закреплён: {SPECIALIZATIONS[key]}.", "progression")
    return f"🧭 Боевой путь: <b>{SPECIALIZATIONS[key]}</b>\nЭто не жёсткий класс: гибридные билды остаются доступны."


def _tech_slug(name: str) -> str:
    slug = re.sub(r"[^a-z0-9а-яё]+", "_", name.casefold(), flags=re.IGNORECASE).strip("_")
    return slug[:40] or "custom"


async def research_start(session: AsyncSession, user_id: int, name: str, element: str, kind: str, effect: str) -> str:
    profile = await require_profile(session, user_id)
    if profile.level < 45 or profile.chakra_control < 80:
        raise GameError("Создание собственной техники требует 45 уровня и 80 контроля чакры.")
    element = element.lower()
    if element not in ELEMENTS and element != "none":
        raise GameError("Стихия: fire|water|wind|lightning|earth|none.")
    active = (
        await session.execute(
            select(NinjaTechniqueResearch).where(NinjaTechniqueResearch.user_id == user_id, NinjaTechniqueResearch.status == "research")
        )
    ).scalars().first()
    if active:
        raise GameError(f"У вас уже исследуется техника #{active.id}: {active.name}.")
    required = 100 + profile.level
    row = NinjaTechniqueResearch(
        user_id=user_id,
        name=name[:64],
        element=None if element == "none" else element,
        kind=kind[:24],
        effect=effect[:32],
        stage=1,
        progress=0,
        required_progress=required,
    )
    session.add(row)
    await session.flush()
    return f"📚 Исследование техники начато.\n#{row.id} {row.name}\nСтихия: {_element_name(row.element)} · тип: {row.kind} · эффект: {row.effect}\nПрогресс: 0/{required}"


async def research_train(session: AsyncSession, user_id: int) -> str:
    profile = await require_profile(session, user_id)
    row = (
        await session.execute(
            select(NinjaTechniqueResearch).where(NinjaTechniqueResearch.user_id == user_id, NinjaTechniqueResearch.status == "research")
        )
    ).scalars().first()
    if not row:
        raise GameError("Активного исследования техники нет.")
    if profile.energy < 15:
        raise GameError("Нужно 15 энергии.")
    profile.energy -= 15
    gain = random.randint(8, 16) + profile.chakra_control // 25
    row.progress += gain
    if row.progress < row.required_progress:
        return f"📚 Исследование: +{gain}\n{row.name}: {row.progress}/{row.required_progress}"
    slug = _tech_slug(row.name)
    duplicate = (
        await session.execute(
            select(NinjaCustomTechnique).where(NinjaCustomTechnique.user_id == user_id, NinjaCustomTechnique.slug == slug)
        )
    ).scalars().first()
    if duplicate:
        slug = f"{slug}_{row.id}"[:40]
    power = 180 + profile.level * 4 + profile.chakra_control
    cost = max(70, int(power * 0.38))
    accuracy = max(0.75, min(0.98, 0.82 + profile.accuracy / 2000))
    tech = NinjaCustomTechnique(
        user_id=user_id,
        slug=slug,
        name=row.name,
        element=row.element,
        kind=row.kind,
        chakra_cost=cost,
        power=power,
        accuracy=accuracy,
        cooldown=3 if power < 650 else 4,
        level=1,
    )
    session.add(tech)
    await session.flush()
    row.status = "completed"
    row.completed_at = utcnow()
    row.result_technique_id = tech.id
    fame = _dict(profile.counters)
    fame["world_fame"] = int(fame.get("world_fame", 0)) + 150
    profile.counters = fame
    _append_profile_history(profile, f"Создана собственная техника «{row.name}».", "technique")
    await _event_log(session, "custom_technique_created", user_id, {"name": row.name, "technique_id": tech.id})
    return f"🌟 <b>Новая техника создана!</b>\n{row.name}\nСила: {power} · чакра: {cost} · точность: {accuracy:.0%}\nСоздатель: {profile.name}"


async def research_text(session: AsyncSession, user_id: int) -> str:
    rows = (
        await session.execute(
            select(NinjaTechniqueResearch).where(NinjaTechniqueResearch.user_id == user_id).order_by(NinjaTechniqueResearch.id.desc()).limit(10)
        )
    ).scalars().all()
    if not rows:
        return "📚 Исследований техники пока нет."
    lines = ["📚 <b>Исследования техник</b>"]
    for row in rows:
        lines.append(f"• #{row.id} {row.name} — {row.status} · {row.progress}/{row.required_progress}")
    return "\n".join(lines)


async def epithet_set(session: AsyncSession, user_id: int, key: str) -> str:
    profile = await require_profile(session, user_id)
    key = key.lower()
    if key not in EPITHETS:
        raise GameError("Прозвище: lightning|fire|shadow|biju|medic|commander.")
    counters = _dict(profile.counters)
    eligible = {
        "lightning": profile.primary_element == "lightning" or profile.secondary_element == "lightning",
        "fire": profile.primary_element == "fire" or profile.secondary_element == "fire",
        "shadow": profile.nukenin or profile.profession == "scout",
        "biju": bool(_dict(profile.biju).get("key") or _dict(profile.biju).get("name")),
        "medic": profile.profession == "medic",
        "commander": int(counters.get("commander_authority", 0)) >= 25,
    }[key]
    if not eligible:
        raise GameError("Ваш путь ещё не соответствует этому прозвищу.")
    flags = _dict(profile.flags)
    flags["epithet"] = EPITHETS[key]
    profile.flags = flags
    counters["world_fame"] = int(counters.get("world_fame", 0)) + 100
    profile.counters = counters
    return f"📛 Мир начинает знать вас как: <b>{EPITHETS[key]}</b>."


async def legend_status(session: AsyncSession, user_id: int) -> str:
    profile = await require_profile(session, user_id)
    counters = _dict(profile.counters)
    fame = int(counters.get("world_fame", 0))
    score = (
        profile.level * 20
        + profile.missions_completed * 8
        + profile.pvp_wins * 15
        + max(0, profile.reputation)
        + fame
        + int(counters.get("war_actions", 0)) * 10
    )
    threshold = 12000
    flags = _dict(profile.flags)
    if score >= threshold and not flags.get("legendary_shinobi"):
        flags["legendary_shinobi"] = True
        profile.flags = flags
        profile.titles = _list(profile.titles) + (["Легендарный шиноби"] if "Легендарный шиноби" not in _list(profile.titles) else [])
        session.add(NinjaLegendRecord(user_id=user_id, category="status", title="Легендарный шиноби", description="Получено за совокупность силы, миссий, войн и известности."))
        await _event_log(session, "legend_created", user_id, {"title": "Легендарный шиноби"})
        return f"🏆 <b>ЛЕГЕНДА СОЗДАНА</b>\n{profile.name} признан легендарным шиноби.\nОчки пути: {score:,}"
    return f"🏆 Путь к легенде: {score:,}/{threshold:,}\nСтатус: {'Легендарный шиноби' if flags.get('legendary_shinobi') else 'путь продолжается'}"


async def unique_legend_claim(session: AsyncSession, user_id: int, title: str, description: str) -> str:
    profile = await require_profile(session, user_id)
    if profile.level < 80:
        raise GameError("Мировые легенды открываются с 80 уровня.")
    exists = (
        await session.execute(select(NinjaLegendRecord).where(NinjaLegendRecord.unique_world.is_(True), NinjaLegendRecord.title == title[:96]))
    ).scalars().first()
    if exists:
        raise GameError("Эта мировая легенда уже принадлежит другому шиноби.")
    row = NinjaLegendRecord(user_id=user_id, category="world", title=title[:96], description=description[:500], unique_world=True)
    session.add(row)
    counters = _dict(profile.counters)
    counters["world_fame"] = int(counters.get("world_fame", 0)) + 1000
    profile.counters = counters
    await _event_log(session, "unique_legend", user_id, {"title": title[:96]})
    return f"📜 <b>УНИКАЛЬНАЯ ЛЕГЕНДА СЕРВЕРА</b>\n{profile.name}: {title[:96]}\n{description[:500]}"


async def legacy_text(session: AsyncSession, user_id: int) -> str:
    profile = await require_profile(session, user_id)
    records = (
        await session.execute(select(NinjaLegendRecord).where(NinjaLegendRecord.user_id == user_id).order_by(NinjaLegendRecord.id.desc()).limit(15))
    ).scalars().all()
    history = _list(_dict(profile.flags).get("path_history"))
    lines = [f"📖 <b>Путь шиноби — {profile.name}</b>"]
    for item in history[-10:]:
        lines.append(f"• {item.get('text')}")
    if records:
        lines.append("\n🏛 Легендарные записи:")
        for r in records:
            lines.append(f"• {r.title}{' 🌍' if r.unique_world else ''}")
    if not history and not records:
        lines.append("История только начинается.")
    return "\n".join(lines)


async def successor_prepare(session: AsyncSession, user_id: int, successor_name: str) -> str:
    profile = await require_profile(session, user_id)
    flags = _dict(profile.flags)
    if profile.level < 100 or int(flags.get("prestige", 0)) < 1:
        raise GameError("Наследник открывается после 100 уровня и хотя бы одного престижа.")
    legacy = _dict(flags.get("legacy_successor"))
    if legacy:
        return f"👶 Наследник уже подготовлен: {legacy.get('name')}."
    legacy = {
        "name": successor_name[:64],
        "bloodline": profile.bloodline,
        "element": profile.primary_element,
        "founder": profile.name,
        "created_at": utcnow().isoformat(),
    }
    flags["legacy_successor"] = legacy
    profile.flags = flags
    _append_profile_history(profile, f"Подготовлено наследие для следующего поколения: {successor_name[:64]}.", "legacy")
    return (
        f"👶 <b>Наследие подготовлено</b>\n{successor_name[:64]} получит фамильную историю, базовую родовую принадлежность "
        "и одну стартовую стихию, но не унаследует весь уровень и эндгейм-силу."
    )
