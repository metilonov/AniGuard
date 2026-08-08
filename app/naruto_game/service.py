from __future__ import annotations

import random
from datetime import timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .content import (
    ACHIEVEMENTS,
    BIJU,
    BLOODLINES,
    BOSSES,
    CARDS,
    CRAFT_RECIPES,
    ELEMENTS,
    ITEMS,
    MISSIONS,
    PROFESSIONS,
    RANK_REQUIREMENTS,
    RANKS,
    RARITY_WEIGHTS,
    STARTING_TECHNIQUES,
    STORY_CHAPTERS,
    SUMMONS,
    TECHNIQUE_UNLOCKS,
    TECHNIQUES,
    TITLES,
    VILLAGES,
    WORLD_EVENTS,
    WORLD_RAIDS,
)
from .engine import (
    apply_player_action,
    arena_league,
    battle_text,
    expected_market_price,
    level_up_stats,
    make_battle_state,
    player_power,
    training_gain,
    upgrade_success_chance,
    weighted_choice,
    xp_required,
)
from .models import (
    NinjaAuction,
    NinjaBattle,
    NinjaCard,
    NinjaClan,
    NinjaClanMember,
    NinjaCustomTechnique,
    NinjaEventLog,
    NinjaItem,
    NinjaProfile,
    NinjaTechnique,
    NinjaWorldState,
    utcnow,
)


class GameError(ValueError):
    pass


def _weighted_bloodline() -> str:
    weights = {key: float(data["weight"]) for key, data in BLOODLINES.items()}
    return weighted_choice(weights)


def _copy_json(value: Any, default: Any) -> Any:
    if value is None:
        if isinstance(default, dict):
            return dict(default)
        if isinstance(default, list):
            return list(default)
        return default
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, list):
        return list(value)
    return value


def _energy_refresh(profile: NinjaProfile) -> None:
    now = utcnow()
    updated = profile.energy_updated_at or now
    if updated.tzinfo is None:
        updated = updated.replace(tzinfo=now.tzinfo)
    elapsed = max(0, int((now - updated).total_seconds()))
    recovered = elapsed // 300
    if recovered:
        profile.energy = min(100, int(profile.energy) + recovered)
        profile.energy_updated_at = updated + timedelta(seconds=recovered * 300)


def _counter(profile: NinjaProfile, key: str, amount: int = 1) -> int:
    counters = _copy_json(profile.counters, {})
    counters[key] = int(counters.get(key, 0)) + amount
    profile.counters = counters
    return counters[key]


def _add_achievement(profile: NinjaProfile, key: str) -> bool:
    achievements = _copy_json(profile.achievements, [])
    if key in achievements:
        return False
    achievements.append(key)
    profile.achievements = achievements
    return True


def _add_title(profile: NinjaProfile, key: str) -> bool:
    titles = _copy_json(profile.titles, [])
    if key in titles:
        return False
    titles.append(key)
    profile.titles = titles
    if not profile.active_title:
        profile.active_title = key
    return True


def _apply_meta_achievements(profile: NinjaProfile) -> list[str]:
    unlocked: list[str] = []
    if profile.missions_completed >= 1 and _add_achievement(profile, "first_mission"):
        unlocked.append(ACHIEVEMENTS["first_mission"])
    if profile.ninja_rank != "academy" and _add_achievement(profile, "genin"):
        unlocked.append(ACHIEVEMENTS["genin"])
    if profile.pvp_wins >= 1 and _add_achievement(profile, "first_pvp"):
        unlocked.append(ACHIEVEMENTS["first_pvp"])
    if profile.ryo >= 1_000_000 and _add_achievement(profile, "millionaire"):
        unlocked.append(ACHIEVEMENTS["millionaire"])
    if profile.nukenin and _add_achievement(profile, "nukenin"):
        unlocked.append(ACHIEVEMENTS["nukenin"])
    if profile.story_chapter > 22 and _add_achievement(profile, "chapter_22"):
        unlocked.append(ACHIEVEMENTS["chapter_22"])
    if profile.reputation >= 1500:
        _add_title(profile, "hero")
    if profile.nukenin and profile.wanted_reward >= 250_000:
        _add_title(profile, "wanted")
    if profile.pvp_wins >= 100:
        _add_title(profile, "champion")
    if profile.story_chapter > 18:
        _add_title(profile, "legend")
    return unlocked


async def get_profile(session: AsyncSession, user_id: int) -> NinjaProfile | None:
    profile = await session.get(NinjaProfile, int(user_id))
    if profile:
        _energy_refresh(profile)
    return profile


async def require_profile(session: AsyncSession, user_id: int) -> NinjaProfile:
    profile = await get_profile(session, user_id)
    if profile is None:
        raise GameError("Сначала создайте шиноби: /shinobi")
    return profile


async def create_profile(session: AsyncSession, user_id: int, name: str, village: str) -> NinjaProfile:
    if village not in VILLAGES:
        raise GameError("Неизвестная деревня.")
    existing = await session.get(NinjaProfile, int(user_id))
    if existing:
        raise GameError("У вас уже есть шиноби.")
    bloodline = _weighted_bloodline()
    element = random.choice(list(ELEMENTS))
    max_hp = 100
    max_chakra = 100
    ninjutsu, taijutsu, genjutsu, defense, speed = 12, 12, 10, 10, 11
    if bloodline == "uzumaki":
        max_chakra += 45
    elif bloodline == "senju":
        max_hp += 35
        max_chakra += 20
    elif bloodline == "uchiha":
        ninjutsu += 4
        genjutsu += 4
    elif bloodline == "hyuga":
        taijutsu += 4
        defense += 2
    elif bloodline == "otsutsuki":
        max_hp += 40
        max_chakra += 55
        ninjutsu += 5
        defense += 3
    if village == "kumo":
        speed += 2
    elif village == "iwa":
        defense += 2
    profile = NinjaProfile(
        user_id=int(user_id),
        name=(name.strip() or "Шиноби")[:64],
        village=village,
        bloodline=bloodline,
        primary_element=element,
        max_hp=max_hp,
        hp=max_hp,
        max_chakra=max_chakra,
        chakra=max_chakra,
        ninjutsu=ninjutsu,
        taijutsu=taijutsu,
        genjutsu=genjutsu,
        defense=defense,
        speed=speed,
        crit_chance=0.10 if village == "kiri" else 0.05,
        morality={"compassion": 50, "cruelty": 0, "loyalty": 50, "ambition": 20, "cunning": 20},
        flags={"gacha_pity": 0, "soft_pity": 0, "mentor_trust": 0, "dojutsu": None},
        home={"level": 0, "storage": 0, "trophies": []},
        biju={"key": None, "trust": 0, "chakra": 0},
        relations={"rival": 0, "mentor": 0},
        summons=[],
        achievements=[],
        titles=[],
        injuries=[],
        counters={},
    )
    session.add(profile)
    for key in STARTING_TECHNIQUES:
        session.add(NinjaTechnique(user_id=int(user_id), technique_key=key, level=1, mastery=0))
    session.add(NinjaItem(user_id=int(user_id), item_key="kunai", quantity=5))
    session.add(NinjaItem(user_id=int(user_id), item_key="medkit", quantity=1))
    session.add(NinjaEventLog(event_type="profile_created", user_id=int(user_id), payload={"village": village, "bloodline": bloodline}))
    await session.flush()
    return profile


def rank_name(key: str) -> str:
    for rank_key, name, _ in RANKS:
        if rank_key == key:
            return name
    return key


def profile_text(profile: NinjaProfile) -> str:
    title = TITLES.get(profile.active_title or "", "—")
    village = VILLAGES.get(profile.village, {}).get("name", profile.village)
    bloodline = BLOODLINES.get(profile.bloodline, {}).get("name", profile.bloodline)
    element = ELEMENTS.get(profile.primary_element, profile.primary_element)
    secondary = ELEMENTS.get(profile.secondary_element, profile.secondary_element) if profile.secondary_element else "—"
    flags = _copy_json(profile.flags, {})
    extra_elements = [ELEMENTS.get(key, key) for key in (flags.get("extra_elements") or [])]
    element_line = f"{element} / {secondary}" + (" / " + " / ".join(extra_elements) if extra_elements else "")
    kekkei = str(flags.get("kekkei_name") or "—")
    dojutsu_labels = {
        "byakugan": "⚪ Бьякуган",
        "sharingan": "🔴 Шаринган I",
        "sharingan_2": "🔴 Шаринган II",
        "sharingan_3": "🔴 Шаринган III",
        "mangekyo": "🌑 Мангекё",
        "eternal_mangekyo": "♾ Вечный Мангекё",
        "rinnegan": "🟣 Риннеган",
    }
    dojutsu = dojutsu_labels.get(str(flags.get("dojutsu") or ""), "—")
    biju_key = (profile.biju or {}).get("key")
    biju_name = BIJU.get(biju_key, "—") if biju_key else "—"
    return (
        f"🥷 <b>{profile.name}</b>\n"
        f"{rank_name(profile.ninja_rank)} · уровень <b>{profile.level}</b>\n"
        f"{village} · 🧬 {bloodline}\n"
        f"{element_line}\n"
        f"🧬 Кеккей Генкай: {kekkei} · 👁 Додзюцу: {dojutsu}\n\n"
        f"❤️ HP: <b>{profile.hp}/{profile.max_hp}</b>\n"
        f"🔵 Чакра: <b>{profile.chakra}/{profile.max_chakra}</b>\n"
        f"⚔️ Ниндзюцу: {profile.ninjutsu} · 🥋 Тайдзюцу: {profile.taijutsu}\n"
        f"👁 Гендзюцу: {profile.genjutsu} · 🛡 Защита: {profile.defense}\n"
        f"💨 Скорость: {profile.speed} · 🎯 Точность: {profile.accuracy}\n"
        f"🧠 Контроль чакры: {profile.chakra_control}\n\n"
        f"⭐ XP: {profile.xp}/{xp_required(profile.level)}\n"
        f"⚡ Энергия: {profile.energy}/100\n"
        f"🪙 Рё: <b>{profile.ryo}</b> · 💎 {profile.chakra_crystals}\n"
        f"🏆 Арена: {arena_league(profile.arena_rating)} ({profile.arena_rating})\n"
        f"📜 Миссий: {profile.missions_completed} · ⚔️ PvP: {profile.pvp_wins}/{profile.pvp_losses}\n"
        f"📕 Репутация: {profile.reputation} · {'🌑 Нукенин' if profile.nukenin else '🏯 Верен деревне'}\n"
        f"🐾 Биджу: {biju_name}\n"
        f"🏷 Титул: {title}"
    )


async def claim_daily(session: AsyncSession, user_id: int) -> str:
    profile = await require_profile(session, user_id)
    now = utcnow()
    if profile.last_daily_at:
        last = profile.last_daily_at
        if last.tzinfo is None:
            last = last.replace(tzinfo=now.tzinfo)
        if now - last < timedelta(hours=20):
            remain = timedelta(hours=20) - (now - last)
            hours = int(remain.total_seconds() // 3600)
            minutes = int((remain.total_seconds() % 3600) // 60)
            raise GameError(f"Ежедневная награда уже получена. Осталось примерно {hours}ч {minutes}м.")
    streak = _counter(profile, "daily_streak", 1)
    if streak > 7:
        counters = _copy_json(profile.counters, {})
        counters["daily_streak"] = 1
        profile.counters = counters
        streak = 1
    rewards = {1: 500, 2: 1000, 3: 1400, 4: 2000, 5: 2800, 6: 4000, 7: 6500}
    ryo = rewards[streak]
    profile.ryo += ryo
    profile.last_daily_at = now
    if streak in {3, 5, 7}:
        await add_item(session, user_id, "chakra_crystal", 1)
    return f"🎁 День {streak}/7: +{ryo} рё" + (" и 💎 кристалл чакры" if streak in {3, 5, 7} else "")


async def add_item(session: AsyncSession, user_id: int, item_key: str, quantity: int = 1) -> NinjaItem:
    row = await session.scalar(select(NinjaItem).where(NinjaItem.user_id == user_id, NinjaItem.item_key == item_key))
    if row is None:
        row = NinjaItem(user_id=user_id, item_key=item_key, quantity=0)
        session.add(row)
    row.quantity = max(0, int(row.quantity) + int(quantity))
    return row


async def inventory_text(session: AsyncSession, user_id: int) -> str:
    await require_profile(session, user_id)
    rows = (await session.scalars(select(NinjaItem).where(NinjaItem.user_id == user_id, NinjaItem.quantity > 0).order_by(NinjaItem.item_key))).all()
    if not rows:
        return "🎒 Инвентарь пуст."
    lines = ["🎒 <b>Инвентарь</b>"]
    for row in rows[:30]:
        item = ITEMS.get(row.item_key, {"name": row.item_key})
        upgrade = f" +{row.upgrade_level}" if row.upgrade_level else ""
        equipped = f" · надето: {row.equipped_slot}" if row.equipped_slot else ""
        lines.append(f"• {item['name']}{upgrade} ×{row.quantity}{equipped}")
    return "\n".join(lines)


async def equip_item(session: AsyncSession, user_id: int, item_key: str) -> str:
    await require_profile(session, user_id)
    item = ITEMS.get(item_key)
    if not item or item.get("type") not in {"weapon", "armor"}:
        raise GameError("Можно экипировать только оружие или броню.")
    row = await session.scalar(
        select(NinjaItem).where(
            NinjaItem.user_id == user_id,
            NinjaItem.item_key == item_key,
            NinjaItem.quantity > 0,
        )
    )
    if not row:
        raise GameError("Этого предмета нет в инвентаре.")
    slot = "weapon" if item.get("type") == "weapon" else "body"
    others = (
        await session.scalars(
            select(NinjaItem).where(
                NinjaItem.user_id == user_id,
                NinjaItem.equipped_slot == slot,
            )
        )
    ).all()
    for other in others:
        other.equipped_slot = None
    row.equipped_slot = slot
    return f"🎒 Экипировано: {item['name']} · слот {slot}."


async def unequip_item(session: AsyncSession, user_id: int, slot: str) -> str:
    await require_profile(session, user_id)
    slot = slot.lower()
    if slot not in {"weapon", "body"}:
        raise GameError("Слоты: weapon или body.")
    rows = (
        await session.scalars(
            select(NinjaItem).where(
                NinjaItem.user_id == user_id,
                NinjaItem.equipped_slot == slot,
            )
        )
    ).all()
    for row in rows:
        row.equipped_slot = None
    return f"🎒 Слот {slot} очищен."


async def _apply_equipment_to_battle(
    session: AsyncSession,
    user_id: int,
    state: dict[str, Any],
    profile: NinjaProfile,
) -> None:
    player = state.get("player") or {}
    rows = (
        await session.scalars(
            select(NinjaItem).where(
                NinjaItem.user_id == user_id,
                NinjaItem.equipped_slot.is_not(None),
                NinjaItem.quantity > 0,
            )
        )
    ).all()
    labels: list[str] = []
    for row in rows:
        item = ITEMS.get(row.item_key, {})
        mult = 1.0 + max(0, int(row.upgrade_level)) * 0.05
        craft_mult = 1.0 + float((row.extra_stats or {}).get("craft_bonus", 0.0))
        if item.get("attack"):
            bonus = int(float(item["attack"]) * mult * craft_mult)
            player["ninjutsu"] += bonus
            player["taijutsu"] += bonus
            labels.append(f"{item.get('name', row.item_key)} +{bonus} атаки")
        if item.get("defense"):
            bonus = int(float(item["defense"]) * mult * craft_mult)
            player["defense"] += bonus
            labels.append(f"{item.get('name', row.item_key)} +{bonus} защиты")

    flags = _copy_json(profile.flags, {})
    if int(flags.get("ramen_buff", 0)) > 0:
        player["max_chakra"] = int(player["max_chakra"] * 1.08)
        player["chakra"] = player["max_chakra"]
        flags["ramen_buff"] = max(0, int(flags.get("ramen_buff", 0)) - 1)
        profile.flags = flags
        labels.append("🍜 Рамен: +8% максимальной чакры")
    if labels:
        state["equipment"] = labels


async def techniques_text(session: AsyncSession, user_id: int) -> str:
    await require_profile(session, user_id)
    rows = (await session.scalars(select(NinjaTechnique).where(NinjaTechnique.user_id == user_id).order_by(NinjaTechnique.technique_key))).all()
    lines = ["📜 <b>Техники</b>"]
    for row in rows:
        tech = TECHNIQUES.get(row.technique_key)
        if not tech:
            continue
        lines.append(f"• {tech.name} · ур. {row.level} · мастерство {row.mastery}% · {tech.chakra} чакры")
    return "\n".join(lines)


async def learn_technique(session: AsyncSession, user_id: int, key: str) -> str:
    profile = await require_profile(session, user_id)
    tech = TECHNIQUES.get(key)
    req = TECHNIQUE_UNLOCKS.get(key)
    if not tech or not req:
        raise GameError("Эту технику нельзя изучить обычным способом.")
    existing = await session.scalar(select(NinjaTechnique).where(NinjaTechnique.user_id == user_id, NinjaTechnique.technique_key == key))
    if existing:
        if existing.level >= 10:
            raise GameError("Техника уже максимального уровня.")
        cost = int(req.get("ryo", 1000) * (0.55 + existing.level * 0.25))
        if profile.ryo < cost:
            raise GameError(f"Нужно {cost} рё для улучшения.")
        profile.ryo -= cost
        existing.level += 1
        return f"📜 {tech.name} улучшена до уровня {existing.level}."
    if profile.level < int(req.get("level", 1)):
        raise GameError(f"Нужен уровень {req['level']}.")
    extra_elements = set((_copy_json(profile.flags, {}) or {}).get("extra_elements") or [])
    if req.get("element") and req["element"] not in ({profile.primary_element, profile.secondary_element} | extra_elements):
        raise GameError(f"Нужна стихия: {ELEMENTS.get(req['element'], req['element'])}.")
    if req.get("bloodline") and profile.bloodline != req["bloodline"]:
        raise GameError("Техника недоступна вашему клану крови.")
    if req.get("profession") and profile.profession != req["profession"]:
        raise GameError("Нужна соответствующая профессия.")
    if req.get("mentor") and profile.mentor != req["mentor"]:
        raise GameError("Нужен соответствующий наставник.")
    if req.get("requires"):
        required = await session.scalar(select(NinjaTechnique).where(NinjaTechnique.user_id == user_id, NinjaTechnique.technique_key == req["requires"]))
        if not required:
            raise GameError("Сначала изучите предыдущую технику ветки.")
    cost = int(req.get("ryo", 1000))
    if profile.ryo < cost:
        raise GameError(f"Нужно {cost} рё.")
    profile.ryo -= cost
    session.add(NinjaTechnique(user_id=user_id, technique_key=key))
    return f"✨ Изучена техника: {tech.name}."


async def train(session: AsyncSession, user_id: int, stat: str) -> str:
    profile = await require_profile(session, user_id)
    _energy_refresh(profile)
    mapping = {
        "ninjutsu": ("⚔️ Ниндзюцу", 12),
        "taijutsu": ("🥋 Тайдзюцу", 10),
        "genjutsu": ("👁 Гендзюцу", 12),
        "defense": ("🛡 Защита", 9),
        "speed": ("💨 Скорость", 10),
        "chakra_control": ("🔵 Контроль чакры", 12),
    }
    if stat not in mapping:
        raise GameError("Неизвестная тренировка.")
    label, energy = mapping[stat]
    if profile.energy < energy:
        raise GameError("Недостаточно энергии. Она восстанавливается по 1 каждые 5 минут.")
    profile.energy -= energy
    profile.energy_updated_at = utcnow()
    gain = training_gain(profile.level)
    setattr(profile, stat, int(getattr(profile, stat)) + gain)
    xp = 70 + profile.level * 6
    profile.xp += xp
    ups = level_up_stats(profile)
    return f"{label}: +{gain} к характеристике, +{xp} XP." + ("\n" + "\n".join(ups) if ups else "")


async def run_mission(session: AsyncSession, user_id: int, mission_key: str | None = None) -> str:
    profile = await require_profile(session, user_id)
    _energy_refresh(profile)
    available = [(key, data) for key, data in MISSIONS.items() if profile.level >= int(data["level"])]
    if not available:
        raise GameError("Пока нет доступных миссий.")
    if mission_key:
        mission = MISSIONS.get(mission_key)
        if not mission or profile.level < mission["level"]:
            raise GameError("Эта миссия пока недоступна.")
        key = mission_key
        data = mission
    else:
        key, data = random.choice(available)
    energy_cost = {"D": 5, "C": 9, "B": 13, "A": 18, "S": 25}[data["rank"]]
    if profile.energy < energy_cost:
        raise GameError(f"Нужно {energy_cost} энергии.")
    profile.energy -= energy_cost
    profile.energy_updated_at = utcnow()
    power = player_power(profile)
    difficulty = int(data["power"])
    chance = min(0.96, max(0.30, 0.62 + (power - difficulty) / max(450.0, difficulty * 2.5)))
    if random.random() > chance:
        profile.hp = max(1, profile.hp - max(5, int(profile.max_hp * 0.12)))
        profile.reputation = max(-10000, profile.reputation - 2)
        return f"❌ <b>{data['name']}</b> провалена. Отряд отступил."
    ryo = random.randint(*data["ryo"])
    xp = random.randint(*data["xp"])
    profile.ryo += ryo
    # MMO V3: village tax is capped at 5% and goes only to the public treasury.
    # The import is local to avoid a module-load cycle: v3 itself reuses service helpers.
    from .v3 import collect_village_tax
    mission_tax = await collect_village_tax(session, profile, ryo)
    profile.xp += xp
    profile.reputation = min(10000, profile.reputation + int(data["rep"]))
    profile.village_points += max(1, int(data["rep"]) // 2)
    profile.missions_completed += 1
    _counter(profile, f"mission_{data['rank']}")
    if random.random() < 0.32:
        await add_item(session, user_id, random.choice(["herb", "metal", "seal_paper"]), random.randint(1, 3))
    ups = level_up_stats(profile)
    achievements = _apply_meta_achievements(profile)
    extra = ""
    if achievements:
        extra += "\n🏆 " + ", ".join(achievements)
    if ups:
        extra += "\n" + "\n".join(ups)
    tax_text = f" · 🏯 налог −{mission_tax}" if mission_tax else ""
    return f"✅ <b>{data['name']}</b> выполнена.\n🪙 +{ryo - mission_tax}{tax_text} · ⭐ +{xp} XP · 📕 +{data['rep']} репутации{extra}"


async def _battle_techniques(
    session: AsyncSession, user_id: int
) -> tuple[list[tuple[str, int]], dict[str, dict[str, Any]]]:
    rows = (await session.scalars(select(NinjaTechnique).where(NinjaTechnique.user_id == user_id, NinjaTechnique.equipped.is_(True)))).all()
    rows = sorted(rows, key=lambda r: (TECHNIQUES.get(r.technique_key).rank if TECHNIQUES.get(r.technique_key) else "Z", -r.level))
    techniques = [(row.technique_key, row.level) for row in rows[:8]]
    custom_rows = (await session.scalars(
        select(NinjaCustomTechnique).where(NinjaCustomTechnique.user_id == user_id).order_by(NinjaCustomTechnique.id).limit(3)
    )).all()
    custom: dict[str, dict[str, Any]] = {}
    for row in custom_rows:
        if len(techniques) >= 8:
            break
        techniques.append((row.slug, row.level))
        custom[row.slug] = {
            "name": row.name,
            "element": row.element,
            "kind": row.kind,
            "chakra": row.chakra_cost,
            "power": row.power,
            "accuracy": row.accuracy,
            "cooldown": row.cooldown,
        }
    return techniques, custom


async def start_battle(session: AsyncSession, user_id: int, boss_key: str = "rogue", *, meta: dict[str, Any] | None = None) -> NinjaBattle:
    profile = await require_profile(session, user_id)
    boss = BOSSES.get(boss_key)
    if not boss:
        raise GameError("Неизвестный противник.")
    if profile.level + 8 < int(boss["level"]):
        raise GameError(f"Противник слишком силён. Рекомендуемый уровень: {boss['level']}.")
    techniques, custom_techniques = await _battle_techniques(session, user_id)
    state = make_battle_state(profile, boss, techniques, custom_techniques)
    await _apply_equipment_to_battle(session, user_id, state, profile)
    state["meta"] = dict(meta or {})
    row = await session.get(NinjaBattle, user_id)
    if row is None:
        row = NinjaBattle(user_id=user_id, opponent_key=boss_key, state=state)
        session.add(row)
    else:
        row.opponent_key = boss_key
        row.state = state
        row.battle_type = "pve"
    return row


def _finish_battle_form(profile: NinjaProfile, *, victory: bool) -> str:
    flags = _copy_json(profile.flags, {})
    form = str(flags.get("battle_form") or "")
    suffix = ""
    if not victory:
        flags["emotional_stress"] = int(flags.get("emotional_stress", 0)) + 5
    elif profile.hp <= max(1, profile.max_hp // 5):
        flags["emotional_stress"] = int(flags.get("emotional_stress", 0)) + 2
    if form.startswith("gates_"):
        try:
            gate = int(form.split("_", 1)[1])
        except Exception:
            gate = 1
        if gate >= 5:
            injuries = _copy_json(profile.injuries, [])
            injuries.append({"name": f"Последствия {gate}-х Врат", "penalty": "истощение", "created": utcnow().isoformat()})
            profile.injuries = injuries[-4:]
            suffix = f"\n🩹 Открытие {gate}-х Врат оставило тяжёлое истощение."
    if form in {"sage", "biju"} or form.startswith("gates_"):
        flags["battle_form"] = None
    flags["battle_summon"] = None
    profile.flags = flags
    return suffix


async def battle_action(session: AsyncSession, user_id: int, action: str) -> tuple[str, bool]:
    profile = await require_profile(session, user_id)
    row = await session.get(NinjaBattle, user_id)
    if row is None:
        raise GameError("Активного боя нет. /battle")
    item_row = None
    if action.startswith("item:"):
        item_key = action.removeprefix("item:")
        if item_key not in {"medkit", "chakra_pill", "explosive_tag"}:
            raise GameError("Этот предмет нельзя использовать в бою.")
        item_row = await session.scalar(
            select(NinjaItem).where(
                NinjaItem.user_id == user_id,
                NinjaItem.item_key == item_key,
                NinjaItem.quantity > 0,
            )
        )
        if not item_row:
            raise GameError("Такого расходника нет в инвентаре.")
    result = apply_player_action(dict(row.state), action)
    consumed = result.state.pop("consumed_item", None)
    if consumed and item_row is not None:
        item_row.quantity = max(0, int(item_row.quantity) - 1)
    row.state = result.state
    if not result.finished:
        return battle_text(result.state), False

    meta = dict((result.state.get("meta") or {}))
    if result.victory:
        rewards = result.state.get("rewards", {})
        xp = int(rewards.get("xp", 0))
        ryo = int(rewards.get("ryo", 0))
        profile.xp += xp
        profile.ryo += ryo
        profile.hp = max(1, int(profile.max_hp * 0.65))
        profile.chakra = max(1, int(profile.max_chakra * 0.45))
        _counter(profile, "boss_wins")
        level_notes = level_up_stats(profile)
        if meta.get("story_chapter") == profile.story_chapter:
            chapter = STORY_CHAPTERS.get(profile.story_chapter)
            if chapter:
                bonus = int(chapter["reward"])
                profile.ryo += bonus
                profile.story_chapter += 1
                _counter(profile, "story_completed")
        _apply_meta_achievements(profile)
        form_note = _finish_battle_form(profile, victory=True)
        await session.delete(row)
        text = battle_text(result.state) + f"\n\n🏆 <b>Победа!</b> +{xp} XP · +{ryo} рё" + form_note
        if level_notes:
            text += "\n" + "\n".join(level_notes)
        return text, True

    profile.hp = 1
    profile.chakra = max(1, profile.max_chakra // 5)
    form_note = _finish_battle_form(profile, victory=False)
    injuries = _copy_json(profile.injuries, [])
    if random.random() < 0.22 and len(injuries) < 3:
        injuries.append({"name": "Боевая травма", "penalty": "-5%", "created": utcnow().isoformat()})
        profile.injuries = injuries
    await session.delete(row)
    return battle_text(result.state) + "\n\n💀 <b>Поражение.</b> Вы доставлены в госпиталь." + form_note, True


async def active_battle_text(session: AsyncSession, user_id: int) -> str | None:
    row = await session.get(NinjaBattle, user_id)
    return battle_text(row.state) if row else None


async def draw_card(session: AsyncSession, user_id: int) -> str:
    profile = await require_profile(session, user_id)
    price = 1500
    if profile.ryo < price:
        raise GameError(f"Призыв стоит {price} рё.")
    profile.ryo -= price
    flags = _copy_json(profile.flags, {})
    pity = int(flags.get("gacha_pity", 0)) + 1
    weights = dict(RARITY_WEIGHTS)
    if pity >= 80:
        weights = {"SSS": 90.0, "Mythic": 10.0}
    elif pity >= 60:
        bonus = (pity - 59) * 1.25
        weights["SSS"] += bonus
        weights["Mythic"] += bonus * 0.15
        weights["B"] = max(1.0, weights["B"] - bonus)
    rarity = weighted_choice(weights)
    candidates = [card for card in CARDS.values() if card.rarity == rarity]
    if not candidates:
        candidates = list(CARDS.values())
    card = random.choice(candidates)
    row = await session.scalar(select(NinjaCard).where(NinjaCard.user_id == user_id, NinjaCard.card_key == card.key))
    duplicate = row is not None
    if row is None:
        row = NinjaCard(user_id=user_id, card_key=card.key, stars=1, copies=1)
        session.add(row)
    else:
        row.copies += 1
        row.fragments += 20 if rarity in {"B", "A", "S"} else 10
        if row.stars < 6 and row.copies >= row.stars + 1:
            row.stars += 1
    if rarity in {"SSS", "Mythic"}:
        pity = 0
    flags["gacha_pity"] = pity
    profile.flags = flags
    count = int(await session.scalar(select(func.count()).select_from(NinjaCard).where(NinjaCard.user_id == user_id)) or 0)
    if count >= 50:
        _add_achievement(profile, "collector")
    return (
        f"🎴 <b>{card.name}</b>\n"
        f"Редкость: <b>{card.rarity}</b>\n"
        f"⭐ {row.stars}/6 · ❤️ {card.hp} · ⚔️ {card.attack} · 🛡 {card.defense}\n"
        + ("♻️ Дубликат превращён во фрагменты/звёзды." if duplicate else "✨ Новая карточка в коллекции.")
        + f"\nГарант: {pity}/80"
    )


async def cards_text(session: AsyncSession, user_id: int) -> str:
    await require_profile(session, user_id)
    rows = (await session.scalars(select(NinjaCard).where(NinjaCard.user_id == user_id))).all()
    if not rows:
        return "🎴 Коллекция пуста. Используйте /summon_card."
    rarity_order = {"Mythic": 0, "SSS": 1, "SS": 2, "S": 3, "A": 4, "B": 5}
    rows.sort(key=lambda row: (rarity_order.get(CARDS.get(row.card_key).rarity if CARDS.get(row.card_key) else "B", 9), row.card_key))
    lines = [f"🎴 <b>Коллекция · {len(rows)} карточек</b>"]
    for row in rows[:30]:
        card = CARDS.get(row.card_key)
        if card:
            lines.append(f"• [{card.rarity}] {card.name} · ⭐{row.stars} · ур.{row.level} · фрагм. {row.fragments}")
    return "\n".join(lines)


async def craft(session: AsyncSession, user_id: int, recipe_key: str) -> str:
    profile = await require_profile(session, user_id)
    recipe = CRAFT_RECIPES.get(recipe_key)
    if not recipe:
        raise GameError("Неизвестный рецепт.")
    if profile.ryo < int(recipe["ryo"]):
        raise GameError("Недостаточно рё для работы мастерской.")
    rows = {
        row.item_key: row
        for row in (await session.scalars(select(NinjaItem).where(NinjaItem.user_id == user_id))).all()
    }
    for key, qty in recipe["needs"].items():
        if not rows.get(key) or rows[key].quantity < qty:
            raise GameError(f"Не хватает: {ITEMS.get(key, {'name': key})['name']} ×{qty}.")
    for key, qty in recipe["needs"].items():
        rows[key].quantity -= qty
    profile.ryo -= int(recipe["ryo"])
    result = await add_item(session, user_id, recipe_key, 1)
    if profile.profession == "smith" and random.random() < 0.20:
        result.quality = "excellent"
        result.extra_stats = {"craft_bonus": 0.08}
    _counter(profile, "crafts")
    return f"🔨 Создано: {ITEMS[recipe_key]['name']} ×1."


async def upgrade_item(session: AsyncSession, user_id: int, item_key: str) -> str:
    profile = await require_profile(session, user_id)
    row = await session.scalar(select(NinjaItem).where(NinjaItem.user_id == user_id, NinjaItem.item_key == item_key, NinjaItem.quantity > 0))
    if not row or ITEMS.get(item_key, {}).get("type") not in {"weapon", "armor"}:
        raise GameError("Такого снаряжения нет.")
    if row.upgrade_level >= 10:
        raise GameError("Уже +10.")
    cost = int(500 * (row.upgrade_level + 1) ** 1.55)
    if profile.ryo < cost:
        raise GameError(f"Нужно {cost} рё.")
    profile.ryo -= cost
    chance = upgrade_success_chance(row.upgrade_level)
    if random.random() <= chance:
        row.upgrade_level += 1
        return f"✨ {ITEMS[item_key]['name']} усилен до +{row.upgrade_level}."
    if row.upgrade_level >= 4:
        row.upgrade_level -= 1
        return f"💥 Неудача. Уровень снизился до +{row.upgrade_level}."
    return "💥 Усиление не удалось, уровень сохранён."


async def exam(session: AsyncSession, user_id: int) -> str:
    profile = await require_profile(session, user_id)
    keys = [entry[0] for entry in RANKS]
    try:
        current_index = keys.index(profile.ninja_rank)
    except ValueError:
        current_index = 0
    if current_index >= len(keys) - 1:
        raise GameError("Вы достигли высшего ранга.")
    next_key = keys[current_index + 1]
    req = RANK_REQUIREMENTS[next_key]
    failures = []
    if profile.level < req.get("level", 0): failures.append(f"уровень {req['level']}")
    if profile.missions_completed < req.get("missions", 0): failures.append(f"миссий {req['missions']}")
    if profile.reputation < req.get("reputation", -10000): failures.append(f"репутация {req['reputation']}")
    if profile.pvp_wins < req.get("pvp_wins", 0): failures.append(f"PvP-побед {req['pvp_wins']}")
    if failures:
        raise GameError("Для экзамена нужно: " + ", ".join(failures) + ".")
    # Tactical exam: a sufficiently prepared player still has a small fail chance.
    chance = 0.82 + min(0.14, profile.chakra_control / 5000)
    if random.random() > chance:
        return "📝 Экзамен не пройден. Совет отметил ошибки в тактике — попробуйте ещё раз после тренировки."
    profile.ninja_rank = next_key
    profile.ryo += 1500 * (current_index + 1)
    profile.reputation += 50 * (current_index + 1)
    _apply_meta_achievements(profile)
    return f"🎉 Новый ранг: {rank_name(next_key)}."


async def story_text(session: AsyncSession, user_id: int) -> str:
    profile = await require_profile(session, user_id)
    chapter = STORY_CHAPTERS.get(profile.story_chapter)
    if not chapter:
        return "🌅 Основная кампания завершена. Началась Эпоха Свободных Шиноби."
    status = "✅ доступна" if profile.level >= chapter["min_level"] else f"🔒 нужен уровень {chapter['min_level']}"
    return (
        f"📖 <b>Глава {profile.story_chapter}: {chapter['title']}</b>\n"
        f"Статус: {status}\n"
        f"Босс: {BOSSES[chapter['boss']]['name']}\n"
        f"Доп. награда за главу: {chapter['reward']} рё"
    )


async def start_story(session: AsyncSession, user_id: int) -> NinjaBattle:
    profile = await require_profile(session, user_id)
    chapter = STORY_CHAPTERS.get(profile.story_chapter)
    if not chapter:
        raise GameError("Основной сюжет уже завершён.")
    if profile.level < chapter["min_level"]:
        raise GameError(f"Для главы нужен уровень {chapter['min_level']}.")
    return await start_battle(session, user_id, chapter["boss"], meta={"story_chapter": profile.story_chapter})


async def explore(session: AsyncSession, user_id: int) -> str:
    profile = await require_profile(session, user_id)
    _energy_refresh(profile)
    if profile.energy < 8:
        raise GameError("Для исследования нужно 8 энергии.")
    profile.energy -= 8
    profile.energy_updated_at = utcnow()
    roll = random.random()
    if roll < 0.36:
        item_key = random.choice(["herb", "metal", "seal_paper", "cloth"])
        qty = random.randint(1, 5)
        if profile.profession == "scout": qty += 1
        await add_item(session, user_id, item_key, qty)
        return f"🗺 Экспедиция успешна: найдено {ITEMS[item_key]['name']} ×{qty}."
    if roll < 0.60:
        amount = random.randint(250, 950)
        profile.ryo += amount
        return f"💰 Найден забытый тайник: +{amount} рё."
    if roll < 0.76:
        profile.xp += 240
        level_up_stats(profile)
        return "🥷 Вы столкнулись с разведчиками противника и получили +240 XP."
    if roll < 0.90:
        summon = random.choice(list(SUMMONS))
        summons = _copy_json(profile.summons, [])
        if summon not in summons:
            summons.append(summon)
            profile.summons = summons
            return f"📜 Найден путь к контракту призыва: {SUMMONS[summon]}."
        return "🌲 Ничего редкого не найдено, но разведаны новые тропы."
    if profile.level >= 45 and not (profile.biju or {}).get("key") and random.random() < 0.025:
        key = random.choice(list(BIJU))
        profile.biju = {"key": key, "trust": 0, "chakra": 100}
        return f"🌌 <b>Легендарное событие!</b> Судьба связала вас с {BIJU[key]}."
    profile.chakra_crystals += 1
    return "💎 Вы нашли редкий кристалл чакры."


async def choose_profession(session: AsyncSession, user_id: int, key: str) -> str:
    profile = await require_profile(session, user_id)
    if key not in PROFESSIONS:
        raise GameError("Неизвестная профессия.")
    if profile.profession and profile.profession != key:
        raise GameError("Профессия уже выбрана. Смена потребует отдельного сюжетного предмета.")
    profile.profession = key
    return f"🎓 Вы выбрали профессию: {PROFESSIONS[key]['name']}."


async def choose_mentor(session: AsyncSession, user_id: int, key: str) -> str:
    profile = await require_profile(session, user_id)
    mentors = {"jiraiya": "🐸 Джирайя", "tsunade": "💚 Цунаде", "orochimaru": "🐍 Орочимару", "kakashi": "⚡ Какаши", "guy": "🥋 Гай"}
    if key not in mentors:
        raise GameError("Неизвестный наставник.")
    if profile.level < 15:
        raise GameError("Наставника можно выбрать с 15 уровня.")
    profile.mentor = key
    flags = _copy_json(profile.flags, {})
    flags["mentor_trust"] = max(0, int(flags.get("mentor_trust", 0)))
    profile.flags = flags
    if key == "orochimaru":
        profile.reputation -= 120
    return f"🤝 Ваш наставник: {mentors[key]}."


async def contract_summon(session: AsyncSession, user_id: int, key: str) -> str:
    profile = await require_profile(session, user_id)
    if key not in SUMMONS:
        raise GameError("Неизвестный контракт.")
    if profile.level < 20:
        raise GameError("Контракт призыва доступен с 20 уровня.")
    summons = _copy_json(profile.summons, [])
    if key in summons:
        return f"📜 Контракт {SUMMONS[key]} уже заключён."
    price = 9000
    if profile.ryo < price:
        raise GameError(f"Для ритуала нужно {price} рё.")
    profile.ryo -= price
    summons.append(key)
    profile.summons = summons
    return f"✨ Заключён контракт: {SUMMONS[key]}."


async def home_upgrade(session: AsyncSession, user_id: int) -> str:
    profile = await require_profile(session, user_id)
    home = _copy_json(profile.home, {"level": 0, "storage": 0, "trophies": []})
    level = int(home.get("level", 0))
    if level >= 5:
        raise GameError("Поместье максимального уровня.")
    costs = [2500, 9000, 25000, 65000, 160000]
    cost = costs[level]
    if profile.ryo < cost:
        raise GameError(f"Нужно {cost} рё.")
    profile.ryo -= cost
    home["level"] = level + 1
    home["storage"] = 50 + home["level"] * 50
    profile.home = home
    names = {1: "🏠 квартира", 2: "🏡 дом", 3: "🏘 большой дом", 4: "🏯 поместье", 5: "🏯 легендарное поместье"}
    return f"🏠 Жильё улучшено: {names[home['level']]}. Хранилище: {home['storage']}."


async def toggle_nukenin(session: AsyncSession, user_id: int) -> str:
    profile = await require_profile(session, user_id)
    if profile.nukenin:
        raise GameError("Вернуться в деревню можно только через будущую цепочку искупления.")
    if profile.level < 20:
        raise GameError("Покинуть деревню можно с 20 уровня.")
    profile.nukenin = True
    profile.reputation = min(profile.reputation, -500)
    profile.wanted_reward = max(25_000, player_power(profile) * 25)
    _add_achievement(profile, "nukenin")
    _add_title(profile, "wanted")
    session.add(NinjaEventLog(event_type="became_nukenin", user_id=user_id, payload={"wanted": profile.wanted_reward}))
    return f"🌑 Вы покинули деревню. Награда в Bingo Book: {profile.wanted_reward} рё."


async def bingo_text(session: AsyncSession) -> str:
    rows = (await session.scalars(select(NinjaProfile).where(NinjaProfile.nukenin.is_(True)).order_by(NinjaProfile.wanted_reward.desc()).limit(10))).all()
    if not rows:
        return "📕 Bingo Book пуст."
    lines = ["📕 <b>Bingo Book</b>"]
    for i, p in enumerate(rows, 1):
        lines.append(f"{i}. {p.name} · ур.{p.level} · {p.wanted_reward} рё")
    return "\n".join(lines)


async def create_clan(session: AsyncSession, user_id: int, name: str) -> str:
    profile = await require_profile(session, user_id)
    name = " ".join(name.split())[:64]
    if len(name) < 3:
        raise GameError("Название клана должно быть минимум 3 символа.")
    member = await session.scalar(select(NinjaClanMember).where(NinjaClanMember.user_id == user_id))
    if member:
        raise GameError("Вы уже состоите в клане.")
    exists = await session.scalar(select(NinjaClan).where(func.lower(NinjaClan.name) == name.casefold()))
    if exists:
        raise GameError("Такой клан уже существует.")
    cost = 50_000
    if profile.ryo < cost:
        raise GameError(f"Создание клана стоит {cost} рё.")
    profile.ryo -= cost
    clan = NinjaClan(name=name, leader_id=user_id, village=None if profile.nukenin else profile.village, upgrades={"hall": 1, "forge": 0, "hospital": 0, "intel": 0, "walls": 0})
    session.add(clan)
    await session.flush()
    session.add(NinjaClanMember(clan_id=clan.id, user_id=user_id, role="leader"))
    return f"👥 Клан <b>{name}</b> создан."


async def join_clan(session: AsyncSession, user_id: int, name: str) -> str:
    await require_profile(session, user_id)
    if await session.scalar(select(NinjaClanMember).where(NinjaClanMember.user_id == user_id)):
        raise GameError("Вы уже в клане.")
    clan = await session.scalar(select(NinjaClan).where(func.lower(NinjaClan.name) == name.strip().casefold()))
    if not clan:
        raise GameError("Клан не найден.")
    count = int(await session.scalar(select(func.count()).select_from(NinjaClanMember).where(NinjaClanMember.clan_id == clan.id)) or 0)
    if count >= 50 + clan.level * 5:
        raise GameError("В клане нет свободных мест.")
    session.add(NinjaClanMember(clan_id=clan.id, user_id=user_id, role="genin"))
    return f"👥 Вы вступили в клан <b>{clan.name}</b>."


async def clan_text(session: AsyncSession, user_id: int) -> str:
    await require_profile(session, user_id)
    member = await session.scalar(select(NinjaClanMember).where(NinjaClanMember.user_id == user_id))
    if not member:
        rows = (await session.scalars(select(NinjaClan).order_by(NinjaClan.rating.desc()).limit(10))).all()
        lines = ["👥 <b>Вы не состоите в клане.</b>", "Топ кланов:"]
        lines += [f"• {c.name} · ур.{c.level} · рейтинг {c.rating}" for c in rows]
        return "\n".join(lines)
    clan = await session.get(NinjaClan, member.clan_id)
    count = int(await session.scalar(select(func.count()).select_from(NinjaClanMember).where(NinjaClanMember.clan_id == member.clan_id)) or 0)
    return (
        f"👥 <b>{clan.name}</b>\n"
        f"Уровень: {clan.level} · рейтинг: {clan.rating}\n"
        f"Участников: {count}\n"
        f"💰 Казна: {clan.treasury}\n"
        f"Ваш ранг: { {"leader": "👑 Клан-лидер", "commander": "⚔️ Джонин-командир", "scout": "🕵 АНБУ-разведчик", "treasurer": "💰 Хранитель свитков и казны", "elite": "🥷 Элитный шиноби", "genin": "🎓 Генин", "shinobi": "🎓 Генин"}.get(member.role, "🎓 Генин") } · вклад: {member.contribution}"
    )


async def clan_donate(session: AsyncSession, user_id: int, amount: int) -> str:
    profile = await require_profile(session, user_id)
    member = await session.scalar(select(NinjaClanMember).where(NinjaClanMember.user_id == user_id))
    if not member:
        raise GameError("Вы не состоите в клане.")
    amount = max(1, min(int(amount), 1_000_000))
    if profile.ryo < amount:
        raise GameError("Недостаточно рё.")
    clan = await session.get(NinjaClan, member.clan_id)
    profile.ryo -= amount
    clan.treasury += amount
    member.contribution += amount
    member.tokens += max(1, amount // 1000)
    clan.xp += max(1, amount // 500)
    while clan.xp >= clan.level * 5000 and clan.level < 50:
        clan.xp -= clan.level * 5000
        clan.level += 1
    return f"🏯 В казну внесено {amount} рё. Жетоны клана: +{max(1, amount // 1000)}."


async def arena_match(session: AsyncSession, user_id: int) -> str:
    profile = await require_profile(session, user_id)
    candidates = (await session.scalars(select(NinjaProfile).where(NinjaProfile.user_id != user_id, NinjaProfile.level.between(max(1, profile.level - 12), min(100, profile.level + 12))).order_by(func.abs(NinjaProfile.arena_rating - profile.arena_rating)).limit(20))).all()
    if not candidates:
        # NPC fallback avoids dead arena on a new server.
        opponent_name = "Тренировочный шиноби"
        opponent_power = int(player_power(profile) * random.uniform(0.82, 1.08))
        opponent = None
    else:
        opponent = random.choice(candidates[: min(6, len(candidates))])
        opponent_name = opponent.name
        opponent_power = player_power(opponent)
    own_power = player_power(profile)
    win_chance = min(0.88, max(0.18, 0.50 + (own_power - opponent_power) / max(500.0, opponent_power * 2.8)))
    won = random.random() < win_chance
    delta = random.randint(17, 25)
    if won:
        profile.pvp_wins += 1
        profile.arena_rating += delta
        profile.arena_tokens += 12
        profile.ryo += 500
        if opponent:
            opponent.pvp_losses += 1
            opponent.arena_rating = max(700, opponent.arena_rating - max(8, delta // 2))
        _apply_meta_achievements(profile)
        return f"🏆 Победа над <b>{opponent_name}</b>! Рейтинг +{delta}, жетоны +12, рё +500.\nЛига: {arena_league(profile.arena_rating)}"
    profile.pvp_losses += 1
    loss = max(8, delta // 2)
    profile.arena_rating = max(700, profile.arena_rating - loss)
    if opponent:
        opponent.pvp_wins += 1
        opponent.arena_rating += max(8, delta // 2)
    return f"💀 Поражение от <b>{opponent_name}</b>. Рейтинг -{loss}.\nЛига: {arena_league(profile.arena_rating)}"


async def world_text(session: AsyncSession) -> str:
    row = await session.get(NinjaWorldState, "world")
    now = utcnow()
    if row is None:
        row = NinjaWorldState(key="world", value={})
        session.add(row)
    value = _copy_json(row.value, {})
    last_raw = value.get("event_at")
    rotate = True
    if last_raw:
        try:
            last = __import__("datetime").datetime.fromisoformat(last_raw)
            if last.tzinfo is None: last = last.replace(tzinfo=now.tzinfo)
            rotate = now - last >= timedelta(hours=6)
        except Exception:
            rotate = True
    if rotate:
        value["event"] = random.choice(WORLD_EVENTS)
        value["event_at"] = now.isoformat()
        row.value = value
    raid = await _ensure_raid(session)
    return (
        "🌍 <b>Мир шиноби</b>\n\n"
        f"{value.get('event', WORLD_EVENTS[0])}\n\n"
        f"👹 Рейд: {raid['name']}\n"
        f"❤️ {raid['hp']:,}/{raid['max_hp']:,}\n"
        f"Фаза: {raid['phase']}"
    )


async def _ensure_raid(session: AsyncSession) -> dict[str, Any]:
    row = await session.get(NinjaWorldState, "raid")
    now = utcnow()
    if row is None:
        key = "shukaku"
        data = WORLD_RAIDS[key]
        value = {"key": key, "name": data["name"], "hp": data["max_hp"], "max_hp": data["max_hp"], "phase": 1, "started_at": now.isoformat(), "contributors": {}}
        row = NinjaWorldState(key="raid", value=value)
        session.add(row)
        return value
    value = _copy_json(row.value, {})
    if int(value.get("hp", 0)) <= 0:
        keys = list(WORLD_RAIDS)
        old = value.get("key")
        key = random.choice([k for k in keys if k != old] or keys)
        data = WORLD_RAIDS[key]
        value = {"key": key, "name": data["name"], "hp": data["max_hp"], "max_hp": data["max_hp"], "phase": 1, "started_at": now.isoformat(), "contributors": {}}
        row.value = value
    return value


async def raid_attack(session: AsyncSession, user_id: int) -> str:
    profile = await require_profile(session, user_id)
    now = utcnow()
    if profile.last_raid_at:
        last = profile.last_raid_at
        if last.tzinfo is None: last = last.replace(tzinfo=now.tzinfo)
        if now - last < timedelta(hours=1):
            remain = 3600 - int((now - last).total_seconds())
            raise GameError(f"Повторная рейдовая атака через {remain // 60 + 1} мин.")
    raid = await _ensure_raid(session)
    row = await session.get(NinjaWorldState, "raid")
    power = player_power(profile)
    damage = max(100, int(power * random.uniform(7.5, 10.5)))
    if profile.biju and profile.biju.get("key"):
        damage = int(damage * 1.12)
    raid["hp"] = max(0, int(raid["hp"]) - damage)
    ratio = raid["hp"] / max(1, raid["max_hp"])
    raid["phase"] = 3 if ratio <= 0.2 else 2 if ratio <= 0.5 else 1
    contributors = dict(raid.get("contributors") or {})
    contributors[str(user_id)] = int(contributors.get(str(user_id), 0)) + damage
    raid["contributors"] = contributors
    row.value = raid
    profile.last_raid_at = now
    profile.raid_seals += max(1, damage // 15000)
    if raid["hp"] <= 0:
        reward = int(WORLD_RAIDS[raid["key"]]["reward"])
        profile.ryo += reward
        profile.raid_seals += 30
        return f"💥 Вы нанесли {damage:,} урона и добили {raid['name']}!\n🎁 +{reward} рё и +30 печатей рейда."
    return f"👹 {raid['name']}: нанесено <b>{damage:,}</b> урона.\n❤️ Осталось {raid['hp']:,}/{raid['max_hp']:,} · фаза {raid['phase']}"


async def market_list(session: AsyncSession, user_id: int, item_key: str, quantity: int, price: int) -> str:
    await require_profile(session, user_id)
    item = ITEMS.get(item_key)
    if not item or item.get("unique"):
        raise GameError("Этот предмет нельзя выставить обычным лотом.")
    row = await session.scalar(select(NinjaItem).where(NinjaItem.user_id == user_id, NinjaItem.item_key == item_key))
    quantity = max(1, min(int(quantity), 999))
    if not row or row.quantity < quantity:
        raise GameError("Недостаточно предметов.")
    price = max(1, int(price))
    low, high = expected_market_price(int(item.get("price", 100)) * quantity, row.upgrade_level)
    if not low <= price <= high:
        raise GameError(f"Допустимый диапазон цены: {low}–{high} рё.")
    fee = max(1, price // 100)
    profile = await require_profile(session, user_id)
    if profile.ryo < fee:
        raise GameError(f"Нужна комиссия размещения {fee} рё.")
    profile.ryo -= fee
    row.quantity -= quantity
    lot = NinjaAuction(seller_id=user_id, item_key=item_key, quantity=quantity, price=price)
    session.add(lot)
    await session.flush()
    return f"🏷 Лот #{lot.id}: {item['name']} ×{quantity} за {price} рё. Комиссия {fee}."


async def market_text(session: AsyncSession) -> str:
    rows = (await session.scalars(select(NinjaAuction).where(NinjaAuction.active.is_(True)).order_by(NinjaAuction.id.desc()).limit(20))).all()
    if not rows:
        return "🏪 На рынке нет активных лотов."
    lines = ["🏪 <b>Рынок игроков</b>"]
    for lot in rows:
        name = ITEMS.get(lot.item_key, {"name": lot.item_key})["name"]
        lines.append(f"#{lot.id} · {name} ×{lot.quantity} · {lot.price} рё")
    return "\n".join(lines)


async def market_buy(session: AsyncSession, user_id: int, lot_id: int) -> str:
    buyer = await require_profile(session, user_id)
    lot = await session.get(NinjaAuction, int(lot_id))
    if not lot or not lot.active:
        raise GameError("Лот недоступен.")
    if lot.seller_id == user_id:
        raise GameError("Нельзя купить собственный лот.")
    seller = await session.get(NinjaProfile, lot.seller_id)
    if not seller:
        raise GameError("Продавец больше не существует.")
    if buyer.ryo < lot.price:
        raise GameError("Недостаточно рё.")
    buyer.ryo -= lot.price
    commission = max(1, int(lot.price * 0.05))
    seller.ryo += lot.price - commission
    await add_item(session, user_id, lot.item_key, lot.quantity)
    lot.active = False
    lot.buyer_id = user_id
    lot.sold_at = utcnow()
    return f"✅ Куплено {ITEMS.get(lot.item_key, {'name': lot.item_key})['name']} ×{lot.quantity}. Комиссия рынка: {commission} рё."


async def top_text(session: AsyncSession) -> str:
    rows = (await session.scalars(select(NinjaProfile).order_by(NinjaProfile.level.desc(), NinjaProfile.xp.desc()).limit(10))).all()
    if not rows:
        return "🏆 Рейтинг пока пуст."
    lines = ["🏆 <b>Сильнейшие шиноби</b>"]
    for i, p in enumerate(rows, 1):
        lines.append(f"{i}. {p.name} · ур.{p.level} · {rank_name(p.ninja_rank)} · сила {player_power(p)}")
    return "\n".join(lines)


async def mentor_training(session: AsyncSession, user_id: int) -> str:
    profile = await require_profile(session, user_id)
    if not profile.mentor:
        raise GameError("Сначала выберите наставника: /mentor.")
    flags = _copy_json(profile.flags, {})
    trust = min(100, int(flags.get("mentor_trust", 0)) + random.randint(3, 8))
    flags["mentor_trust"] = trust
    profile.flags = flags
    profile.xp += 250 + profile.level * 4
    profile.chakra_control += 1
    level_up_stats(profile)
    return f"🤝 Тренировка с наставником завершена. Доверие: {trust}/100, контроль чакры +1."


async def biju_train(session: AsyncSession, user_id: int) -> str:
    profile = await require_profile(session, user_id)
    data = _copy_json(profile.biju, {})
    key = data.get("key")
    if not key:
        raise GameError("У вас нет хвостатого зверя.")
    trust = min(100, int(data.get("trust", 0)) + random.randint(2, 6))
    data["trust"] = trust
    data["chakra"] = min(1000, int(data.get("chakra", 0)) + 25)
    profile.biju = data
    return f"🐾 Связь с {BIJU[key]} стала сильнее. Доверие: {trust}/100."


async def world_event_log(session: AsyncSession, event_type: str, user_id: int | None, payload: dict[str, Any]) -> None:
    session.add(NinjaEventLog(event_type=event_type, user_id=user_id, payload=payload))
