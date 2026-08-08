from __future__ import annotations

import random
from datetime import timedelta, timezone
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from .content import ITEMS, VILLAGES
from .engine import player_power
from .models import (
    NinjaBond,
    NinjaClan,
    NinjaClanAlliance,
    NinjaClanChat,
    NinjaClanMember,
    NinjaDuel,
    NinjaEventLog,
    NinjaFriendship,
    NinjaMail,
    NinjaMentorship,
    NinjaProfile,
    NinjaSettlement,
    NinjaSettlementMember,
    NinjaSettlementWar,
    NinjaSocialBlock,
    NinjaTournament,
    NinjaTrade,
    NinjaVillageChat,
    NinjaWarContribution,
    NinjaWorldState,
    utcnow,
)
from .service import GameError, add_item, require_profile


CLAN_ROLE_LABELS: dict[str, str] = {
    "leader": "👑 Клан-лидер",
    "commander": "⚔️ Джонин-командир",
    "scout": "🕵 АНБУ-разведчик",
    "treasurer": "💰 Хранитель свитков и казны",
    "elite": "🥷 Элитный шиноби",
    "genin": "🎓 Генин",
    # legacy rows from the first RPG patch
    "shinobi": "🎓 Генин",
}

CLAN_ROLE_ALIASES: dict[str, str] = {
    "leader": "leader",
    "лидер": "leader",
    "глава": "leader",
    "commander": "commander",
    "командир": "commander",
    "джонин": "commander",
    "scout": "scout",
    "разведчик": "scout",
    "anbu": "scout",
    "анбу": "scout",
    "treasurer": "treasurer",
    "казначей": "treasurer",
    "хранитель": "treasurer",
    "elite": "elite",
    "элита": "elite",
    "genin": "genin",
    "генин": "genin",
    "shinobi": "genin",
}

SETTLEMENT_BUILDINGS: dict[str, tuple[str, int]] = {
    "hall": ("🏯 Штаб", 10_000),
    "training": ("🥋 Тренировочное поле", 15_000),
    "hospital": ("🏥 Госпиталь", 18_000),
    "forge": ("🔨 Кузница", 20_000),
    "market": ("🏪 Рынок", 22_000),
    "library": ("📚 Библиотека", 24_000),
    "walls": ("🛡 Стены", 30_000),
    "intel": ("🕵 Разведцентр", 28_000),
}

SETTLEMENT_NOTIFICATION_KINDS = {
    "war": "⚔️ войны",
    "raid": "👹 рейды",
    "achievement": "🏆 достижения",
    "village": "🏯 события деревни",
    "world": "🌍 мировые события",
    "market": "💰 экономика",
}

SOCIAL_PERMISSION_KEYS = {
    "friends": "🤝 запросы дружбы",
    "duels": "⚔️ дуэли",
    "trades": "💰 сделки",
    "clans": "👥 приглашения клана",
    "marriage": "💍 семейные предложения",
    "mentor": "👨‍🏫 наставничество",
    "mail": "✉️ игровая почта",
}

PRIVACY_KEYS = {
    "ryo": "💰 баланс рё",
    "inventory": "🎒 инвентарь",
    "techniques": "📚 техники",
    "trades": "🤝 торговую статистику",
}


def _copy_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _copy_list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _aware(value):
    if value is None:
        return None
    if getattr(value, "tzinfo", None) is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def clan_role_label(role: str | None) -> str:
    return CLAN_ROLE_LABELS.get(str(role or "genin"), "🎓 Генин")


def clan_roles_text() -> str:
    return "\n".join(
        [
            "🥷 <b>Роли клана</b>",
            "👑 Клан-лидер",
            "⚔️ Джонин-командир",
            "🕵 АНБУ-разведчик",
            "💰 Хранитель свитков и казны",
            "🥷 Элитный шиноби",
            "🎓 Генин",
        ]
    )


def _social_flags(profile: NinjaProfile) -> dict[str, bool]:
    flags = _copy_dict(profile.flags)
    raw = _copy_dict(flags.get("social_permissions"))
    return {key: bool(raw.get(key, True)) for key in SOCIAL_PERMISSION_KEYS}


def _privacy_flags(profile: NinjaProfile) -> dict[str, bool]:
    flags = _copy_dict(profile.flags)
    raw = _copy_dict(flags.get("privacy"))
    # True means visible.
    return {key: bool(raw.get(key, key not in {"ryo", "inventory"})) for key in PRIVACY_KEYS}


def _set_nested_flag(profile: NinjaProfile, section: str, key: str, value: bool) -> None:
    flags = _copy_dict(profile.flags)
    nested = _copy_dict(flags.get(section))
    nested[key] = bool(value)
    flags[section] = nested
    profile.flags = flags


async def _blocked(session: AsyncSession, a: int, b: int) -> bool:
    row = await session.scalar(
        select(NinjaSocialBlock).where(
            or_(
                (NinjaSocialBlock.user_id == a) & (NinjaSocialBlock.blocked_user_id == b),
                (NinjaSocialBlock.user_id == b) & (NinjaSocialBlock.blocked_user_id == a),
            )
        )
    )
    return row is not None


async def _check_social_permission(session: AsyncSession, target_id: int, key: str) -> None:
    target = await require_profile(session, target_id)
    if not _social_flags(target).get(key, True):
        raise GameError("Этот шиноби отключил такие социальные запросы.")


async def public_profile_text(session: AsyncSession, viewer_id: int, target_id: int) -> str:
    await require_profile(session, viewer_id)
    p = await require_profile(session, target_id)
    privacy = _privacy_flags(p)
    village = VILLAGES.get(p.village, {}).get("name", p.village)
    clan_member = await session.scalar(select(NinjaClanMember).where(NinjaClanMember.user_id == target_id))
    clan_text = "—"
    if clan_member:
        clan = await session.get(NinjaClan, clan_member.clan_id)
        if clan:
            clan_text = f"{clan.name} · {clan_role_label(clan_member.role)}"
    lines = [
        f"🥷 <b>{p.name}</b>",
        f"🎖 {p.ninja_rank} · уровень {p.level}",
        f"{village}",
        f"👥 Клан: {clan_text}",
        f"🏆 PvP: {p.arena_rating} · {p.pvp_wins}/{p.pvp_losses}",
        f"📕 Репутация: {p.reputation}",
    ]
    if privacy.get("ryo"):
        lines.append(f"💰 Рё: {p.ryo:,}")
    if privacy.get("techniques"):
        lines.append(f"🔥 Ниндзюцу: {p.ninjutsu} · 🥋 Тайдзюцу: {p.taijutsu} · 👁 Гендзюцу: {p.genjutsu}")
    return "\n".join(lines)


async def privacy_toggle(session: AsyncSession, user_id: int, key: str, enabled: bool) -> str:
    p = await require_profile(session, user_id)
    key = key.lower()
    if key not in PRIVACY_KEYS:
        raise GameError("Доступно: ryo, inventory, techniques, trades.")
    _set_nested_flag(p, "privacy", key, enabled)
    return f"🔐 {PRIVACY_KEYS[key]}: {'показывать' if enabled else 'скрывать'}"


async def social_permission_toggle(session: AsyncSession, user_id: int, key: str, enabled: bool) -> str:
    p = await require_profile(session, user_id)
    key = key.lower()
    if key not in SOCIAL_PERMISSION_KEYS:
        raise GameError("Доступно: friends, duels, trades, clans, marriage, mentor, mail.")
    _set_nested_flag(p, "social_permissions", key, enabled)
    return f"🛡 {SOCIAL_PERMISSION_KEYS[key]}: {'разрешены' if enabled else 'запрещены'}"


async def block_add(session: AsyncSession, user_id: int, target_id: int) -> str:
    await require_profile(session, user_id)
    await require_profile(session, target_id)
    if user_id == target_id:
        raise GameError("Нельзя заблокировать себя.")
    row = await session.scalar(select(NinjaSocialBlock).where(NinjaSocialBlock.user_id == user_id, NinjaSocialBlock.blocked_user_id == target_id))
    if row:
        return "🚫 Этот шиноби уже в игровом чёрном списке."
    session.add(NinjaSocialBlock(user_id=user_id, blocked_user_id=target_id))
    return f"🚫 Шиноби {target_id} добавлен в игровой чёрный список."


async def block_remove(session: AsyncSession, user_id: int, target_id: int) -> str:
    row = await session.scalar(select(NinjaSocialBlock).where(NinjaSocialBlock.user_id == user_id, NinjaSocialBlock.blocked_user_id == target_id))
    if not row:
        raise GameError("Шиноби не найден в вашем игровом чёрном списке.")
    await session.delete(row)
    return f"✅ Шиноби {target_id} удалён из игрового чёрного списка."


async def block_list_text(session: AsyncSession, user_id: int) -> str:
    await require_profile(session, user_id)
    rows = (await session.scalars(select(NinjaSocialBlock).where(NinjaSocialBlock.user_id == user_id).order_by(NinjaSocialBlock.id.desc()))).all()
    if not rows:
        return "🚫 Игровой чёрный список пуст."
    return "🚫 <b>Игровой чёрный список</b>\n" + "\n".join(f"• {r.blocked_user_id}" for r in rows[:50])


# ---------------------------------------------------------------------------
# Telegram groups as settlements
# ---------------------------------------------------------------------------


def _settlement_defaults() -> tuple[dict[str, int], dict[str, bool]]:
    buildings = {key: 0 for key in SETTLEMENT_BUILDINGS}
    buildings["hall"] = 1
    notifications = {key: True for key in SETTLEMENT_NOTIFICATION_KINDS}
    notifications["market"] = False
    return buildings, notifications


async def settlement_register(
    session: AsyncSession,
    chat_id: int,
    title: str,
    username: str | None,
    owner_id: int,
    village: str | None = None,
) -> str:
    profile = await require_profile(session, owner_id)
    existing = await session.get(NinjaSettlement, int(chat_id))
    if existing:
        return "🏯 Этот Telegram-чат уже зарегистрирован как поселение шиноби."
    village_key = (village or profile.village).lower()
    if village_key not in VILLAGES:
        raise GameError("Неизвестная деревня. Доступно: konoha, suna, kiri, kumo, iwa.")
    if profile.nukenin:
        raise GameError("Нукенин не может зарегистрировать официальное поселение великой деревни.")
    buildings, notifications = _settlement_defaults()
    row = NinjaSettlement(
        chat_id=int(chat_id),
        title=(title or "Поселение шиноби")[:128],
        username=(username or None),
        owner_id=int(owner_id),
        village=village_key,
        buildings=buildings,
        notification_settings=notifications,
        achievements=["settlement_founded"],
        history=[{"at": utcnow().isoformat(), "event": "Основано поселение", "user_id": int(owner_id)}],
    )
    session.add(row)
    session.add(NinjaSettlementMember(chat_id=int(chat_id), user_id=int(owner_id), role="leader"))
    session.add(NinjaEventLog(event_type="settlement_created", user_id=owner_id, payload={"chat_id": chat_id, "village": village_key}))
    return f"🏯 <b>{row.title}</b> зарегистрирован как поселение шиноби.\n{VILLAGES[village_key]['name']} · уровень 1"


async def touch_settlement_member(session: AsyncSession, chat_id: int, user_id: int) -> None:
    settlement = await session.get(NinjaSettlement, int(chat_id))
    if not settlement:
        return
    profile = await session.get(NinjaProfile, int(user_id))
    if not profile:
        return
    member = await session.scalar(select(NinjaSettlementMember).where(NinjaSettlementMember.chat_id == chat_id, NinjaSettlementMember.user_id == user_id))
    now = utcnow()
    if member is None:
        member = NinjaSettlementMember(chat_id=int(chat_id), user_id=int(user_id), role="shinobi")
        session.add(member)
        settlement.xp += 20
    else:
        last_seen = _aware(member.last_seen_at)
        if last_seen is None or now - last_seen >= timedelta(minutes=30):
            settlement.xp += 2
    member.last_seen_at = now
    while settlement.level < 50 and settlement.xp >= settlement.level * 4000:
        settlement.xp -= settlement.level * 4000
        settlement.level += 1
        settlement.defense += 250


async def settlement_status(session: AsyncSession, chat_id: int) -> str:
    row = await session.get(NinjaSettlement, int(chat_id))
    if not row:
        raise GameError("Этот чат ещё не зарегистрирован. Создатель группы: /settlement register")
    count = int(await session.scalar(select(func.count()).select_from(NinjaSettlementMember).where(NinjaSettlementMember.chat_id == chat_id)) or 0)
    buildings = _copy_dict(row.buildings)
    village = VILLAGES.get(row.village, {}).get("name", row.village)
    lines = [
        f"🏯 <b>{row.title}</b>",
        f"{village} · уровень <b>{row.level}</b>",
        f"🥷 Шиноби: {count}",
        f"🏆 Рейтинг: {row.rating}",
        f"💰 Казна: {row.treasury:,} рё",
        f"🛡 Защита: {row.defense:,}",
        "",
        "🏗 <b>Постройки</b>",
    ]
    for key, (label, _) in SETTLEMENT_BUILDINGS.items():
        lines.append(f"• {label}: ур.{int(buildings.get(key, 0))}")
    return "\n".join(lines)


async def settlement_donate(session: AsyncSession, chat_id: int, user_id: int, amount: int) -> str:
    settlement = await session.get(NinjaSettlement, int(chat_id))
    if not settlement:
        raise GameError("Чат не зарегистрирован как поселение.")
    p = await require_profile(session, user_id)
    amount = max(100, min(int(amount), 2_000_000))
    if p.ryo < amount:
        raise GameError("Недостаточно рё.")
    p.ryo -= amount
    settlement.treasury += amount
    gain = max(1, amount // 200)
    settlement.xp += gain
    member = await session.scalar(select(NinjaSettlementMember).where(NinjaSettlementMember.chat_id == chat_id, NinjaSettlementMember.user_id == user_id))
    if member is None:
        member = NinjaSettlementMember(chat_id=chat_id, user_id=user_id)
        session.add(member)
    member.contribution += amount
    while settlement.level < 50 and settlement.xp >= settlement.level * 4000:
        settlement.xp -= settlement.level * 4000
        settlement.level += 1
        settlement.defense += 250
    achievements = _copy_list(settlement.achievements)
    if settlement.treasury >= 1_000_000 and "million_treasury" not in achievements:
        achievements.append("million_treasury")
        settlement.achievements = achievements
    return f"💰 В казну поселения внесено {amount:,} рё.\n🏯 Уровень: {settlement.level} · казна: {settlement.treasury:,}"


async def settlement_upgrade(session: AsyncSession, chat_id: int, user_id: int, building: str) -> str:
    settlement = await session.get(NinjaSettlement, int(chat_id))
    if not settlement:
        raise GameError("Чат не зарегистрирован как поселение.")
    if settlement.owner_id != user_id:
        raise GameError("Улучшать поселение может его создатель.")
    building = building.lower()
    if building not in SETTLEMENT_BUILDINGS:
        raise GameError("Доступно: " + ", ".join(SETTLEMENT_BUILDINGS))
    buildings = _copy_dict(settlement.buildings)
    current = int(buildings.get(building, 0))
    if current >= 10:
        raise GameError("Постройка максимального уровня.")
    label, base_cost = SETTLEMENT_BUILDINGS[building]
    cost = base_cost * (current + 1)
    if settlement.treasury < cost:
        raise GameError(f"В казне нужно {cost:,} рё.")
    settlement.treasury -= cost
    buildings[building] = current + 1
    settlement.buildings = buildings
    if building == "walls":
        settlement.defense += 1000 + current * 350
    settlement.rating += 5
    history = _copy_list(settlement.history)
    history.append({"at": utcnow().isoformat(), "event": f"{label} улучшен до ур.{current + 1}", "user_id": user_id})
    settlement.history = history[-100:]
    return f"🏗 {label} улучшен до уровня {current + 1}. Потрачено {cost:,} рё."


async def settlement_notify_toggle(session: AsyncSession, chat_id: int, user_id: int, kind: str, enabled: bool) -> str:
    row = await session.get(NinjaSettlement, int(chat_id))
    if not row:
        raise GameError("Чат не зарегистрирован как поселение.")
    if row.owner_id != user_id:
        raise GameError("Настройки поселения меняет его создатель.")
    kind = kind.lower()
    if kind not in SETTLEMENT_NOTIFICATION_KINDS:
        raise GameError("Доступно: " + ", ".join(SETTLEMENT_NOTIFICATION_KINDS))
    settings = _copy_dict(row.notification_settings)
    settings[kind] = bool(enabled)
    row.notification_settings = settings
    return f"🔔 {SETTLEMENT_NOTIFICATION_KINDS[kind]}: {'включены' if enabled else 'выключены'}"


async def settlement_top(session: AsyncSession) -> str:
    rows = (await session.scalars(select(NinjaSettlement).order_by(NinjaSettlement.rating.desc(), NinjaSettlement.level.desc()).limit(20))).all()
    if not rows:
        return "🏯 Зарегистрированных поселений пока нет."
    lines = ["🏆 <b>Лучшие поселения Telegram</b>"]
    for i, row in enumerate(rows, 1):
        lines.append(f"{i}. {row.title} · ур.{row.level} · {row.rating}")
    return "\n".join(lines)


async def settlement_history_text(session: AsyncSession, chat_id: int) -> str:
    row = await session.get(NinjaSettlement, int(chat_id))
    if not row:
        raise GameError("Чат не зарегистрирован как поселение.")
    history = _copy_list(row.history)[-15:]
    if not history:
        return "📜 Хроника поселения пуста."
    lines = [f"📜 <b>Хроника {row.title}</b>"]
    for item in reversed(history):
        stamp = str(item.get("at", ""))[:10]
        lines.append(f"• {stamp} — {item.get('event', 'Событие')}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Clan/village Telegram chats and Naruto-styled clan roles
# ---------------------------------------------------------------------------


async def clan_role_set(session: AsyncSession, actor_id: int, target_id: int, role: str) -> str:
    actor = await session.scalar(select(NinjaClanMember).where(NinjaClanMember.user_id == actor_id))
    target = await session.scalar(select(NinjaClanMember).where(NinjaClanMember.user_id == target_id))
    if not actor or not target or actor.clan_id != target.clan_id:
        raise GameError("Оба шиноби должны состоять в одном клане.")
    clan = await session.get(NinjaClan, actor.clan_id)
    if not clan or clan.leader_id != actor_id:
        raise GameError("Назначать роли может только 👑 Клан-лидер.")
    normalized = CLAN_ROLE_ALIASES.get(role.lower())
    if not normalized:
        raise GameError("Роль: leader, commander, scout, treasurer, elite, genin.")
    if normalized == "leader":
        if target_id == actor_id:
            return "👑 Вы уже Клан-лидер."
        actor.role = "commander"
        target.role = "leader"
        clan.leader_id = target_id
        return f"👑 {target_id} назначен новым Клан-лидером. Вы стали Джонином-командиром."
    if target_id == clan.leader_id:
        raise GameError("Сначала передайте должность Клан-лидера другому шиноби.")
    target.role = normalized
    return f"✅ Новая роль {target_id}: {clan_role_label(normalized)}"


async def clan_chat_link(session: AsyncSession, user_id: int, chat_id: int, title: str) -> str:
    member = await session.scalar(select(NinjaClanMember).where(NinjaClanMember.user_id == user_id))
    if not member:
        raise GameError("Вы не состоите в клане.")
    clan = await session.get(NinjaClan, member.clan_id)
    if not clan or clan.leader_id != user_id:
        raise GameError("Привязать чат может только 👑 Клан-лидер.")
    settlement = await session.get(NinjaSettlement, chat_id)
    if not settlement:
        raise GameError("Сначала зарегистрируйте группу: /settlement register")
    clash = await session.scalar(select(NinjaClanChat).where(NinjaClanChat.chat_id == chat_id, NinjaClanChat.clan_id != clan.id))
    if clash:
        raise GameError("Этот Telegram-чат уже связан с другим кланом.")
    row = await session.scalar(select(NinjaClanChat).where(NinjaClanChat.clan_id == clan.id))
    if row is None:
        row = NinjaClanChat(clan_id=clan.id, chat_id=chat_id, linked_by=user_id, title=title[:128])
        session.add(row)
    else:
        row.chat_id = chat_id
        row.linked_by = user_id
        row.title = title[:128]
    settlement.clan_id = clan.id
    return f"👥 Telegram-чат <b>{title}</b> связан с кланом <b>{clan.name}</b>."


async def village_chat_link(session: AsyncSession, user_id: int, chat_id: int, title: str, username: str | None) -> str:
    p = await require_profile(session, user_id)
    if p.nukenin:
        raise GameError("Нукенин не может создать официальный чат деревни.")
    village_state = await session.get(NinjaWorldState, f"village:{p.village}")
    state = _copy_dict(village_state.value) if village_state else {}
    if int(state.get("kage_id") or 0) != user_id:
        raise GameError("Официальный деревенский чат может привязать только действующий Каге.")
    settlement = await session.get(NinjaSettlement, chat_id)
    if not settlement:
        raise GameError("Сначала зарегистрируйте группу как поселение.")
    if settlement.village != p.village:
        raise GameError("Поселение относится к другой деревне.")
    clash = await session.scalar(select(NinjaVillageChat).where(NinjaVillageChat.chat_id == chat_id, NinjaVillageChat.village != p.village))
    if clash:
        raise GameError("Чат уже принадлежит другой деревне.")
    row = await session.scalar(select(NinjaVillageChat).where(NinjaVillageChat.village == p.village))
    if row is None:
        row = NinjaVillageChat(village=p.village, chat_id=chat_id, linked_by=user_id, title=title[:128], username=username)
        session.add(row)
    else:
        row.chat_id = chat_id
        row.linked_by = user_id
        row.title = title[:128]
        row.username = username
    return f"🏯 <b>{title}</b> стал официальным Telegram-чатом {VILLAGES[p.village]['name']}."


async def clan_intel(session: AsyncSession, user_id: int, target_name: str) -> str:
    p = await require_profile(session, user_id)
    member = await session.scalar(select(NinjaClanMember).where(NinjaClanMember.user_id == user_id))
    allowed = p.profession in {"scout", "hunter"} or (member and member.role == "scout")
    if not allowed:
        raise GameError("Разведка доступна профессии разведчика/охотника или 🕵 АНБУ-разведчику клана.")
    target = await session.scalar(select(NinjaClan).where(func.lower(NinjaClan.name) == target_name.strip().casefold()))
    if not target:
        raise GameError("Клан не найден.")
    members = (await session.scalars(select(NinjaClanMember).where(NinjaClanMember.clan_id == target.id))).all()
    ids = [m.user_id for m in members]
    profiles = (await session.scalars(select(NinjaProfile).where(NinjaProfile.user_id.in_(ids)))).all() if ids else []
    total = sum(player_power(x) for x in profiles)
    approx = int(round(total / 500.0) * 500)
    linked = await session.scalar(select(NinjaClanChat).where(NinjaClanChat.clan_id == target.id))
    return (
        f"🕵 <b>Разведданные: {target.name}</b>\n"
        f"Участников: {len(members)}\n"
        f"Примерная суммарная сила: ~{approx:,}\n"
        f"Рейтинг: {target.rating}\n"
        f"Уровень базы: {target.level}\n"
        f"Связанный игровой чат: {linked.title if linked else 'не обнаружен'}"
    )


# ---------------------------------------------------------------------------
# Friends and player mentorship
# ---------------------------------------------------------------------------


async def friend_request(session: AsyncSession, user_id: int, target_id: int) -> tuple[str, int]:
    await require_profile(session, user_id)
    await require_profile(session, target_id)
    if user_id == target_id:
        raise GameError("Нельзя добавить себя в друзья.")
    if await _blocked(session, user_id, target_id):
        raise GameError("Социальное взаимодействие между этими игроками заблокировано.")
    await _check_social_permission(session, target_id, "friends")
    existing = await session.scalar(
        select(NinjaFriendship).where(
            or_(
                (NinjaFriendship.requester_id == user_id) & (NinjaFriendship.addressee_id == target_id),
                (NinjaFriendship.requester_id == target_id) & (NinjaFriendship.addressee_id == user_id),
            ),
            NinjaFriendship.status.in_(["pending", "active"]),
        )
    )
    if existing:
        raise GameError("Запрос уже существует или вы уже друзья.")
    row = NinjaFriendship(requester_id=user_id, addressee_id=target_id)
    session.add(row)
    await session.flush()
    return f"🤝 Запрос дружбы #{row.id} отправлен шиноби {target_id}.", row.id


async def friend_accept(session: AsyncSession, user_id: int, request_id: int) -> tuple[str, int]:
    row = await session.get(NinjaFriendship, request_id)
    if not row or row.status != "pending" or row.addressee_id != user_id:
        raise GameError("Запрос дружбы недоступен.")
    row.status = "active"
    row.accepted_at = utcnow()
    row.bond_points = 10
    a = await require_profile(session, row.requester_id)
    b = await require_profile(session, row.addressee_id)
    return f"🤝 {a.name} и {b.name} теперь друзья. Связь: 10/100.", row.requester_id


async def friend_reject(session: AsyncSession, user_id: int, request_id: int) -> str:
    row = await session.get(NinjaFriendship, request_id)
    if not row or row.status != "pending" or row.addressee_id != user_id:
        raise GameError("Запрос дружбы недоступен.")
    row.status = "rejected"
    return "❌ Запрос дружбы отклонён."


async def friend_remove(session: AsyncSession, user_id: int, target_id: int) -> str:
    row = await session.scalar(
        select(NinjaFriendship).where(
            or_(
                (NinjaFriendship.requester_id == user_id) & (NinjaFriendship.addressee_id == target_id),
                (NinjaFriendship.requester_id == target_id) & (NinjaFriendship.addressee_id == user_id),
            ),
            NinjaFriendship.status == "active",
        )
    )
    if not row:
        raise GameError("Этот шиноби не находится в списке друзей.")
    row.status = "ended"
    return f"👋 Дружба с {target_id} завершена."


async def friends_text(session: AsyncSession, user_id: int) -> str:
    await require_profile(session, user_id)
    rows = (await session.scalars(
        select(NinjaFriendship).where(
            or_(NinjaFriendship.requester_id == user_id, NinjaFriendship.addressee_id == user_id),
            NinjaFriendship.status == "active",
        ).order_by(NinjaFriendship.bond_points.desc())
    )).all()
    pending = (await session.scalars(select(NinjaFriendship).where(NinjaFriendship.addressee_id == user_id, NinjaFriendship.status == "pending"))).all()
    lines = ["🤝 <b>Друзья</b>"]
    if rows:
        for row in rows[:30]:
            partner_id = row.addressee_id if row.requester_id == user_id else row.requester_id
            p = await session.get(NinjaProfile, partner_id)
            lines.append(f"• {p.name if p else partner_id} · связь {row.bond_points}/100")
    else:
        lines.append("Пока нет друзей.")
    if pending:
        lines += ["", "📨 Ожидают ответа:"] + [f"• #{r.id} от {r.requester_id}" for r in pending[:10]]
    return "\n".join(lines)


async def mentorship_request(session: AsyncSession, mentor_id: int, student_id: int) -> tuple[str, int]:
    mentor = await require_profile(session, mentor_id)
    student = await require_profile(session, student_id)
    if mentor_id == student_id:
        raise GameError("Нельзя быть наставником самому себе.")
    if await _blocked(session, mentor_id, student_id):
        raise GameError("Социальное взаимодействие заблокировано.")
    await _check_social_permission(session, student_id, "mentor")
    if mentor.level < 30 or mentor.level < student.level + 10:
        raise GameError("Наставник должен быть минимум 30 уровня и на 10 уровней выше ученика.")
    active_count = int(await session.scalar(select(func.count()).select_from(NinjaMentorship).where(NinjaMentorship.mentor_id == mentor_id, NinjaMentorship.status == "active")) or 0)
    if active_count >= 3:
        raise GameError("Одновременно можно обучать максимум трёх учеников.")
    existing = await session.scalar(select(NinjaMentorship).where(NinjaMentorship.student_id == student_id, NinjaMentorship.status.in_(["pending", "active"])))
    if existing:
        raise GameError("У этого шиноби уже есть наставник или ожидающий запрос.")
    row = NinjaMentorship(mentor_id=mentor_id, student_id=student_id)
    session.add(row)
    await session.flush()
    return f"👨‍🏫 Предложение наставничества #{row.id} отправлено.", row.id


async def mentorship_accept(session: AsyncSession, user_id: int, mentorship_id: int) -> tuple[str, int]:
    row = await session.get(NinjaMentorship, mentorship_id)
    if not row or row.status != "pending" or row.student_id != user_id:
        raise GameError("Предложение наставничества недоступно.")
    row.status = "active"
    row.accepted_at = utcnow()
    row.bond_points = 10
    mentor = await require_profile(session, row.mentor_id)
    student = await require_profile(session, row.student_id)
    return f"👨‍🏫 {mentor.name} стал наставником {student.name}.", row.mentor_id


async def mentorship_text(session: AsyncSession, user_id: int) -> str:
    await require_profile(session, user_id)
    as_student = await session.scalar(select(NinjaMentorship).where(NinjaMentorship.student_id == user_id, NinjaMentorship.status == "active"))
    students = (await session.scalars(select(NinjaMentorship).where(NinjaMentorship.mentor_id == user_id, NinjaMentorship.status == "active"))).all()
    pending = (await session.scalars(select(NinjaMentorship).where(NinjaMentorship.student_id == user_id, NinjaMentorship.status == "pending"))).all()
    lines = ["👨‍🏫 <b>Наставничество игроков</b>"]
    if as_student:
        p = await session.get(NinjaProfile, as_student.mentor_id)
        lines.append(f"Ваш наставник: {p.name if p else as_student.mentor_id} · связь {as_student.bond_points}/100")
    else:
        lines.append("Ваш наставник: —")
    if students:
        lines.append("Ученики:")
        for row in students:
            p = await session.get(NinjaProfile, row.student_id)
            lines.append(f"• {p.name if p else row.student_id} · связь {row.bond_points}/100")
    if pending:
        lines.append("Ожидают принятия: " + ", ".join(f"#{r.id}" for r in pending))
    return "\n".join(lines)


async def mentorship_claim(session: AsyncSession, mentor_id: int, student_id: int) -> str:
    mentor = await require_profile(session, mentor_id)
    student = await require_profile(session, student_id)
    row = await session.scalar(select(NinjaMentorship).where(NinjaMentorship.mentor_id == mentor_id, NinjaMentorship.student_id == student_id, NinjaMentorship.status == "active"))
    if not row:
        raise GameError("Этот шиноби не является вашим активным учеником.")
    claimed = {str(x) for x in _copy_list(row.claimed_milestones)}
    milestones = [(10, 2500), (20, 5000), (40, 12_000), (60, 25_000), (80, 50_000)]
    available = [(lvl, reward) for lvl, reward in milestones if student.level >= lvl and f"lvl{lvl}" not in claimed]
    if not available:
        raise GameError("Новых достижений ученика для награды пока нет.")
    total = 0
    for lvl, reward in available:
        claimed.add(f"lvl{lvl}")
        total += reward
        row.bond_points = min(100, row.bond_points + 8)
    row.claimed_milestones = sorted(claimed)
    mentor.ryo += total
    student.xp += min(3000, total // 5)
    return f"🎓 Ученик достиг новых рубежей. Наставнику +{total:,} рё, ученику +{min(3000, total // 5)} XP."


# ---------------------------------------------------------------------------
# Marriage/divorce on top of the existing NinjaBond model
# ---------------------------------------------------------------------------


async def marriage_divorce(session: AsyncSession, user_id: int) -> str:
    p = await require_profile(session, user_id)
    flags = _copy_dict(p.flags)
    bond_id = int(flags.get("bond_id") or 0)
    if not bond_id:
        raise GameError("У вас нет активного семейного союза.")
    row = await session.get(NinjaBond, bond_id)
    if not row or row.status != "active":
        flags["bond_id"] = None
        p.flags = flags
        raise GameError("Активный семейный союз не найден.")
    partner_id = row.partner_id if row.proposer_id == user_id else row.proposer_id
    partner = await require_profile(session, partner_id)
    partner_flags = _copy_dict(partner.flags)
    flags["bond_id"] = None
    partner_flags["bond_id"] = None
    p.flags = flags
    partner.flags = partner_flags
    row.status = "ended"
    session.add(NinjaEventLog(event_type="family_bond_ended", user_id=user_id, payload={"bond": bond_id, "partner": partner_id}))
    return f"💔 Семейный союз #{bond_id} расторгнут. Совместный социальный статус завершён."


# ---------------------------------------------------------------------------
# Duels with escrow wagers
# ---------------------------------------------------------------------------


async def duel_challenge(session: AsyncSession, challenger_id: int, opponent_id: int, chat_id: int | None, wager: int = 0) -> tuple[str, int]:
    challenger = await require_profile(session, challenger_id)
    await require_profile(session, opponent_id)
    if challenger_id == opponent_id:
        raise GameError("Нельзя вызвать на дуэль самого себя.")
    if await _blocked(session, challenger_id, opponent_id):
        raise GameError("Социальное взаимодействие заблокировано.")
    await _check_social_permission(session, opponent_id, "duels")
    wager = max(0, min(int(wager), 1_000_000))
    if challenger.ryo < wager:
        raise GameError("Недостаточно рё для ставки.")
    recent = await session.scalar(
        select(NinjaDuel).where(
            NinjaDuel.challenger_id == challenger_id,
            NinjaDuel.opponent_id == opponent_id,
            NinjaDuel.status == "pending",
            NinjaDuel.created_at >= utcnow() - timedelta(minutes=10),
        ).order_by(NinjaDuel.id.desc())
    )
    if recent:
        raise GameError("У этого соперника уже есть ваш ожидающий вызов.")
    challenger.ryo -= wager
    row = NinjaDuel(
        challenger_id=challenger_id,
        opponent_id=opponent_id,
        chat_id=chat_id,
        wager=wager,
        expires_at=utcnow() + timedelta(minutes=30),
    )
    session.add(row)
    await session.flush()
    return f"⚔️ Вызов на дуэль #{row.id} отправлен. Ставка: {wager:,} рё. Ценности инициатора в эскроу на 30 минут.", row.id


async def _friendship_between(session: AsyncSession, a: int, b: int) -> NinjaFriendship | None:
    return await session.scalar(
        select(NinjaFriendship).where(
            or_(
                (NinjaFriendship.requester_id == a) & (NinjaFriendship.addressee_id == b),
                (NinjaFriendship.requester_id == b) & (NinjaFriendship.addressee_id == a),
            ),
            NinjaFriendship.status == "active",
        )
    )


async def duel_accept(session: AsyncSession, user_id: int, duel_id: int) -> tuple[str, int | None]:
    row = await session.get(NinjaDuel, duel_id)
    if not row or row.status != "pending" or row.opponent_id != user_id:
        raise GameError("Дуэль недоступна.")
    challenger = await require_profile(session, row.challenger_id)
    opponent = await require_profile(session, row.opponent_id)
    if row.expires_at and utcnow() > _aware(row.expires_at):
        challenger.ryo += row.wager
        row.status = "expired"
        raise GameError("Вызов истёк. Ставка инициатора возвращена.")
    if opponent.ryo < row.wager:
        raise GameError("Недостаточно рё, чтобы принять ставку.")
    opponent.ryo -= row.wager
    a_power = player_power(challenger) * random.uniform(0.88, 1.12)
    b_power = player_power(opponent) * random.uniform(0.88, 1.12)
    winner, loser = (challenger, opponent) if a_power >= b_power else (opponent, challenger)
    pool = row.wager * 2
    fee = int(pool * 0.05) if pool else 0
    payout = pool - fee
    winner.ryo += payout
    winner.pvp_wins += 1
    loser.pvp_losses += 1
    row.status = "completed"
    row.winner_id = winner.user_id
    row.completed_at = utcnow()
    row.result = {"challenger_power": round(a_power), "opponent_power": round(b_power), "fee": fee, "payout": payout}
    friendship = await _friendship_between(session, challenger.user_id, opponent.user_id)
    combo = ""
    if friendship:
        friendship.bond_points = min(100, friendship.bond_points + 3)
        if friendship.bond_points >= 70:
            combo = "\n🤝 Высокая связь: разблокирован статус командной синергии."
    session.add(NinjaEventLog(event_type="social_duel", user_id=winner.user_id, payload={"duel": row.id, "loser": loser.user_id, "wager": row.wager}))
    return (
        f"⚔️ <b>Дуэль #{row.id}</b>\n{challenger.name}: {round(a_power):,}\n{opponent.name}: {round(b_power):,}\n\n"
        f"🏆 Победитель: <b>{winner.name}</b>\n💰 Выплата: {payout:,} рё" + (f" · комиссия {fee:,}" if fee else "") + combo,
        winner.user_id,
    )


async def duel_cancel(session: AsyncSession, user_id: int, duel_id: int) -> str:
    row = await session.get(NinjaDuel, duel_id)
    if not row or row.status != "pending" or row.challenger_id != user_id:
        raise GameError("Этот вызов нельзя отменить.")
    challenger = await require_profile(session, row.challenger_id)
    challenger.ryo += row.wager
    row.status = "cancelled"
    return f"↩️ Дуэль #{row.id} отменена. {row.wager:,} рё возвращены из эскроу."


async def duel_reject(session: AsyncSession, user_id: int, duel_id: int) -> tuple[str, int]:
    row = await session.get(NinjaDuel, duel_id)
    if not row or row.status != "pending" or row.opponent_id != user_id:
        raise GameError("Этот вызов нельзя отклонить.")
    challenger = await require_profile(session, row.challenger_id)
    challenger.ryo += row.wager
    row.status = "rejected"
    return f"❌ Дуэль #{row.id} отклонена. Ставка {row.wager:,} рё возвращена инициатору.", row.challenger_id


# ---------------------------------------------------------------------------
# Settlement wars directly in Telegram groups
# ---------------------------------------------------------------------------


async def _find_settlement_by_query(session: AsyncSession, query: str) -> NinjaSettlement | None:
    query = query.strip()
    if query.lstrip("-").isdigit():
        return await session.get(NinjaSettlement, int(query))
    rows = (await session.scalars(select(NinjaSettlement).where(func.lower(NinjaSettlement.title) == query.casefold()).limit(2))).all()
    return rows[0] if rows else None


async def settlement_war_challenge(session: AsyncSession, chat_id: int, user_id: int, target_query: str) -> tuple[str, int, int]:
    own = await session.get(NinjaSettlement, int(chat_id))
    if not own:
        raise GameError("Сначала зарегистрируйте текущий чат как поселение.")
    if own.owner_id != user_id:
        raise GameError("Объявить войну поселений может создатель текущего поселения.")
    target = await _find_settlement_by_query(session, target_query)
    if not target or target.chat_id == own.chat_id:
        raise GameError("Поселение-противник не найдено.")
    existing = await session.scalar(
        select(NinjaSettlementWar).where(
            NinjaSettlementWar.status.in_(["pending", "active"]),
            or_(
                (NinjaSettlementWar.attacker_chat_id == own.chat_id) & (NinjaSettlementWar.defender_chat_id == target.chat_id),
                (NinjaSettlementWar.attacker_chat_id == target.chat_id) & (NinjaSettlementWar.defender_chat_id == own.chat_id),
            ),
        )
    )
    if existing:
        raise GameError("Между этими поселениями уже есть ожидающая или активная война.")
    row = NinjaSettlementWar(attacker_chat_id=own.chat_id, defender_chat_id=target.chat_id, created_by=user_id)
    session.add(row)
    await session.flush()
    return f"⚔️ <b>{own.title}</b> вызывает <b>{target.title}</b> на войну.\nID войны: #{row.id}", row.id, target.chat_id


async def settlement_war_accept(session: AsyncSession, chat_id: int, user_id: int, war_id: int) -> tuple[str, NinjaSettlementWar]:
    row = await session.get(NinjaSettlementWar, war_id)
    if not row or row.status != "pending" or row.defender_chat_id != chat_id:
        raise GameError("Вызов на войну недоступен в этом чате.")
    defender = await session.get(NinjaSettlement, chat_id)
    attacker = await session.get(NinjaSettlement, row.attacker_chat_id)
    if not defender or not attacker:
        raise GameError("Одно из поселений больше не существует.")
    if defender.owner_id != user_id:
        raise GameError("Принять войну может создатель поселения-защитника.")
    row.status = "active"
    row.starts_at = utcnow()
    row.ends_at = utcnow() + timedelta(minutes=30)
    history_a = _copy_list(attacker.history)
    history_d = _copy_list(defender.history)
    text_event = f"Началась война против {defender.title}"
    history_a.append({"at": utcnow().isoformat(), "event": text_event})
    history_d.append({"at": utcnow().isoformat(), "event": f"Началась война против {attacker.title}"})
    attacker.history, defender.history = history_a[-100:], history_d[-100:]
    return war_board_text(row, attacker.title, defender.title), row


def war_board_text(row: NinjaSettlementWar, attacker_title: str, defender_title: str) -> str:
    left = int(row.attacker_score)
    right = int(row.defender_score)
    total = max(1, left + right)
    left_pct = int(left * 100 / total) if left + right else 50
    right_pct = 100 - left_pct
    status = {"pending": "ожидает", "active": "идёт", "finished": "завершена"}.get(row.status, row.status)
    ends = row.ends_at.strftime("%H:%M UTC") if row.ends_at else "—"
    return (
        f"🔥 <b>ВОЙНА ПОСЕЛЕНИЙ #{row.id}</b>\n"
        f"{attacker_title}\n{'█' * max(1, left_pct // 10)}{'░' * max(0, 10 - left_pct // 10)} {left} очков\n\n"
        f"VS\n\n{defender_title}\n{'█' * max(1, right_pct // 10)}{'░' * max(0, 10 - right_pct // 10)} {right} очков\n\n"
        f"Статус: {status} · конец: {ends}\n"
        f"🥷 /chatwar attack {row.id} — атаковать (до 3 раз)"
    )


async def settlement_war_attack(session: AsyncSession, chat_id: int, user_id: int, war_id: int) -> tuple[str, NinjaSettlementWar]:
    row = await session.get(NinjaSettlementWar, war_id)
    if not row or row.status != "active":
        raise GameError("Активная война не найдена.")
    if chat_id not in {row.attacker_chat_id, row.defender_chat_id}:
        raise GameError("Эта группа не участвует в указанной войне.")
    if row.ends_at and utcnow() > _aware(row.ends_at):
        raise GameError("Время войны закончилось. Используйте /chatwar finish ID.")
    p = await require_profile(session, user_id)
    member = await session.scalar(select(NinjaSettlementMember).where(NinjaSettlementMember.chat_id == chat_id, NinjaSettlementMember.user_id == user_id))
    if not member:
        member = NinjaSettlementMember(chat_id=chat_id, user_id=user_id)
        session.add(member)
    side = "attacker" if chat_id == row.attacker_chat_id else "defender"
    contribution = await session.scalar(select(NinjaWarContribution).where(NinjaWarContribution.war_id == row.id, NinjaWarContribution.user_id == user_id))
    if contribution is None:
        contribution = NinjaWarContribution(war_id=row.id, user_id=user_id, side=side)
        session.add(contribution)
        await session.flush()
    if contribution.attacks >= 3:
        raise GameError("Вы уже использовали 3 атаки в этой войне.")
    score = max(10, int(player_power(p) * random.uniform(0.035, 0.065)))
    contribution.attacks += 1
    contribution.score += score
    member.contribution += score * 10
    if side == "attacker":
        row.attacker_score += score
    else:
        row.defender_score += score
    return f"⚔️ {p.name} приносит поселению <b>+{score}</b> очков. Осталось атак: {3 - contribution.attacks}.", row


async def settlement_war_finish(session: AsyncSession, war_id: int, force: bool = False) -> tuple[str, NinjaSettlementWar, list[int]]:
    row = await session.get(NinjaSettlementWar, war_id)
    if not row or row.status != "active":
        raise GameError("Активная война не найдена.")
    if not force and row.ends_at and utcnow() < _aware(row.ends_at):
        remain = _aware(row.ends_at) - utcnow()
        raise GameError(f"Война ещё идёт примерно {int(remain.total_seconds() // 60) + 1} мин.")
    attacker = await session.get(NinjaSettlement, row.attacker_chat_id)
    defender = await session.get(NinjaSettlement, row.defender_chat_id)
    if not attacker or not defender:
        raise GameError("Поселение войны не найдено.")
    if row.attacker_score == row.defender_score:
        winner = attacker if random.random() < 0.5 else defender
        loser = defender if winner is attacker else attacker
    else:
        winner, loser = (attacker, defender) if row.attacker_score > row.defender_score else (defender, attacker)
    row.status = "finished"
    row.completed_at = utcnow()
    row.winner_chat_id = winner.chat_id
    winner.rating += 35
    loser.rating = max(0, loser.rating - 20)
    prize = 25_000 + min(row.attacker_score, row.defender_score) * 5
    winner.treasury += prize
    winner.xp += 700
    history = _copy_list(winner.history)
    history.append({"at": utcnow().isoformat(), "event": f"Победа в войне над {loser.title}"})
    winner.history = history[-100:]
    achievements = _copy_list(winner.achievements)
    if "first_war_win" not in achievements:
        achievements.append("first_war_win")
        winner.achievements = achievements
    session.add(NinjaEventLog(event_type="settlement_war", user_id=None, payload={"war": row.id, "winner_chat_id": winner.chat_id, "loser_chat_id": loser.chat_id}))
    return (
        f"🏆 <b>Война поселений #{row.id} завершена</b>\n"
        f"{attacker.title}: {row.attacker_score}\n{defender.title}: {row.defender_score}\n\n"
        f"Победитель: <b>{winner.title}</b>\n💰 В казну: +{prize:,} рё · рейтинг +35",
        row,
        [attacker.chat_id, defender.chat_id],
    )


async def settlement_war_status(session: AsyncSession, war_id: int) -> tuple[str, NinjaSettlementWar]:
    row = await session.get(NinjaSettlementWar, war_id)
    if not row:
        raise GameError("Война не найдена.")
    a = await session.get(NinjaSettlement, row.attacker_chat_id)
    b = await session.get(NinjaSettlement, row.defender_chat_id)
    return war_board_text(row, a.title if a else str(row.attacker_chat_id), b.title if b else str(row.defender_chat_id)), row


# ---------------------------------------------------------------------------
# In-game mail, alliances, tournaments and MMO rankings
# ---------------------------------------------------------------------------


async def mail_send(session: AsyncSession, sender_id: int, receiver_id: int, body: str, anonymous: bool = False) -> tuple[str, int]:
    sender = await require_profile(session, sender_id)
    await require_profile(session, receiver_id)
    if sender_id == receiver_id:
        raise GameError("Нельзя отправить письмо самому себе.")
    if await _blocked(session, sender_id, receiver_id):
        raise GameError("Игровая почта между этими шиноби заблокирована.")
    await _check_social_permission(session, receiver_id, "mail")
    recent = await session.scalar(
        select(NinjaMail).where(
            NinjaMail.sender_id == sender_id,
            NinjaMail.receiver_id == receiver_id,
            NinjaMail.created_at >= utcnow() - timedelta(seconds=45),
        ).order_by(NinjaMail.id.desc())
    )
    if recent:
        raise GameError("Не отправляйте одному игроку письма чаще одного раза в 45 секунд.")
    body = " ".join(body.split())[:1000]
    if len(body) < 2:
        raise GameError("Письмо пустое.")
    if anonymous:
        if not sender.nukenin and sender.profession not in {"scout", "hunter"}:
            raise GameError("Анонимные письма доступны нукенинам и разведчикам.")
        cost = 200
        if sender.ryo < cost:
            raise GameError("Для шифрования нужно 200 рё.")
        sender.ryo -= cost
    row = NinjaMail(sender_id=sender_id, receiver_id=receiver_id, body=body, anonymous=anonymous)
    session.add(row)
    await session.flush()
    return f"✉️ Письмо #{row.id} отправлено" + (" анонимно." if anonymous else "."), row.id


async def mail_inbox(session: AsyncSession, user_id: int) -> str:
    await require_profile(session, user_id)
    rows = (await session.scalars(select(NinjaMail).where(NinjaMail.receiver_id == user_id).order_by(NinjaMail.id.desc()).limit(20))).all()
    if not rows:
        return "📭 Игровая почта пуста."
    lines = ["✉️ <b>Почта шиноби</b>"]
    for row in rows:
        sender = "🌑 Неизвестный шиноби" if row.anonymous else str(row.sender_id)
        unread = "🆕" if row.read_at is None else ""
        lines.append(f"{unread} #{row.id} от {sender}: {row.body[:120]}")
        if row.read_at is None:
            row.read_at = utcnow()
    return "\n".join(lines)


async def alliance_create(session: AsyncSession, user_id: int, name: str) -> str:
    member = await session.scalar(select(NinjaClanMember).where(NinjaClanMember.user_id == user_id))
    if not member:
        raise GameError("Вы не состоите в клане.")
    clan = await session.get(NinjaClan, member.clan_id)
    if not clan or clan.leader_id != user_id:
        raise GameError("Создать альянс может только Клан-лидер.")
    existing = (await session.scalars(select(NinjaClanAlliance))).all()
    if any(clan.id in {int(x) for x in _copy_list(a.members)} for a in existing):
        raise GameError("Ваш клан уже состоит в альянсе.")
    name = " ".join(name.split())[:64]
    if len(name) < 3:
        raise GameError("Название альянса слишком короткое.")
    if await session.scalar(select(NinjaClanAlliance).where(func.lower(NinjaClanAlliance.name) == name.casefold())):
        raise GameError("Такой альянс уже существует.")
    cost = 100_000
    if clan.treasury < cost:
        raise GameError(f"В казне клана нужно {cost:,} рё.")
    clan.treasury -= cost
    row = NinjaClanAlliance(name=name, leader_clan_id=clan.id, members=[clan.id], pending_invites=[])
    session.add(row)
    return f"🏯 Альянс <b>{name}</b> создан кланом {clan.name}."


async def alliance_invite(session: AsyncSession, user_id: int, target_clan_name: str) -> str:
    member = await session.scalar(select(NinjaClanMember).where(NinjaClanMember.user_id == user_id))
    if not member:
        raise GameError("Вы не состоите в клане.")
    own = await session.get(NinjaClan, member.clan_id)
    if not own or own.leader_id != user_id:
        raise GameError("Приглашать может только Клан-лидер.")
    alliance = await session.scalar(select(NinjaClanAlliance).where(NinjaClanAlliance.leader_clan_id == own.id))
    if not alliance:
        raise GameError("Ваш клан не является лидером альянса.")
    target = await session.scalar(select(NinjaClan).where(func.lower(NinjaClan.name) == target_clan_name.strip().casefold()))
    if not target or target.id == own.id:
        raise GameError("Клан не найден.")
    members = {int(x) for x in _copy_list(alliance.members)}
    pending = {int(x) for x in _copy_list(alliance.pending_invites)}
    if target.id in members or target.id in pending:
        raise GameError("Этот клан уже в альянсе или приглашён.")
    pending.add(target.id)
    alliance.pending_invites = sorted(pending)
    return f"📨 Клан {target.name} приглашён в альянс #{alliance.id} {alliance.name}."


async def alliance_accept(session: AsyncSession, user_id: int, alliance_id: int) -> str:
    member = await session.scalar(select(NinjaClanMember).where(NinjaClanMember.user_id == user_id))
    if not member:
        raise GameError("Вы не состоите в клане.")
    clan = await session.get(NinjaClan, member.clan_id)
    if not clan or clan.leader_id != user_id:
        raise GameError("Принять приглашение может только Клан-лидер.")
    all_rows = (await session.scalars(select(NinjaClanAlliance))).all()
    if any(clan.id in {int(x) for x in _copy_list(x.members)} for x in all_rows):
        raise GameError("Ваш клан уже состоит в альянсе.")
    alliance = await session.get(NinjaClanAlliance, alliance_id)
    if not alliance:
        raise GameError("Альянс не найден.")
    pending = {int(x) for x in _copy_list(alliance.pending_invites)}
    if clan.id not in pending:
        raise GameError("Ваш клан не приглашён в этот альянс.")
    pending.remove(clan.id)
    members = {int(x) for x in _copy_list(alliance.members)}
    members.add(clan.id)
    alliance.pending_invites = sorted(pending)
    alliance.members = sorted(members)
    return f"🤝 Клан {clan.name} присоединился к альянсу <b>{alliance.name}</b>."


async def alliance_text(session: AsyncSession, user_id: int) -> str:
    member = await session.scalar(select(NinjaClanMember).where(NinjaClanMember.user_id == user_id))
    if not member:
        raise GameError("Вы не состоите в клане.")
    rows = (await session.scalars(select(NinjaClanAlliance).order_by(NinjaClanAlliance.rating.desc()))).all()
    own = next((a for a in rows if member.clan_id in {int(x) for x in _copy_list(a.members)}), None)
    if not own:
        return "🏯 Ваш клан не состоит в альянсе.\n/alliance create Название"
    names = []
    for clan_id in _copy_list(own.members):
        c = await session.get(NinjaClan, int(clan_id))
        if c:
            names.append(c.name)
    return f"🏯 <b>{own.name}</b> · рейтинг {own.rating}\nКланы: " + ", ".join(names)


async def tournament_create(session: AsyncSession, chat_id: int, creator_id: int, title: str, max_players: int = 16) -> tuple[str, int]:
    await require_profile(session, creator_id)
    if not await session.get(NinjaSettlement, chat_id):
        raise GameError("Турнир можно создать в зарегистрированном поселении Telegram.")
    active = await session.scalar(select(NinjaTournament).where(NinjaTournament.chat_id == chat_id, NinjaTournament.status.in_(["open", "running"])))
    if active:
        raise GameError("В этом чате уже есть активный турнир.")
    max_players = max(4, min(int(max_players), 64))
    row = NinjaTournament(chat_id=chat_id, creator_id=creator_id, title=(title or "Турнир шиноби")[:96], max_players=max_players, participants=[creator_id])
    session.add(row)
    await session.flush()
    return f"🏆 Турнир #{row.id} «{row.title}» создан. Участников: 1/{row.max_players}. /tournament join {row.id}", row.id


async def tournament_join(session: AsyncSession, chat_id: int, user_id: int, tournament_id: int) -> str:
    await require_profile(session, user_id)
    row = await session.get(NinjaTournament, tournament_id)
    if not row or row.chat_id != chat_id or row.status != "open":
        raise GameError("Турнир недоступен в этом чате.")
    participants = [int(x) for x in _copy_list(row.participants)]
    if user_id in participants:
        return "🏆 Вы уже зарегистрированы в турнире."
    if len(participants) >= row.max_players:
        raise GameError("Сетка турнира заполнена.")
    participants.append(user_id)
    row.participants = participants
    return f"🏆 Вы вступили в турнир #{row.id}. Участников: {len(participants)}/{row.max_players}."


async def tournament_start(session: AsyncSession, chat_id: int, user_id: int, tournament_id: int) -> str:
    row = await session.get(NinjaTournament, tournament_id)
    if not row or row.chat_id != chat_id or row.status != "open":
        raise GameError("Турнир недоступен.")
    if row.creator_id != user_id:
        raise GameError("Запустить турнир может его создатель.")
    participants = [int(x) for x in _copy_list(row.participants)]
    if len(participants) < 4:
        raise GameError("Для турнира нужно минимум 4 шиноби.")
    profiles = {p.user_id: p for p in (await session.scalars(select(NinjaProfile).where(NinjaProfile.user_id.in_(participants)))).all()}
    active = [uid for uid in participants if uid in profiles]
    random.shuffle(active)
    rounds: list[dict[str, Any]] = []
    while len(active) > 1:
        nxt: list[int] = []
        pairs = []
        if len(active) % 2:
            nxt.append(active.pop())
        for i in range(0, len(active), 2):
            a, b = active[i], active[i + 1]
            pa, pb = profiles[a], profiles[b]
            sa = player_power(pa) * random.uniform(0.85, 1.15)
            sb = player_power(pb) * random.uniform(0.85, 1.15)
            winner = a if sa >= sb else b
            nxt.append(winner)
            pairs.append({"a": a, "b": b, "winner": winner})
        rounds.append({"matches": pairs})
        active = nxt
    winner_id = active[0]
    winner = profiles[winner_id]
    winner.ryo += 15_000
    winner.arena_tokens += 30
    row.status = "finished"
    row.winner_id = winner_id
    row.bracket = rounds
    row.completed_at = utcnow()
    settlement = await session.get(NinjaSettlement, chat_id)
    if settlement:
        settlement.rating += 10
        settlement.xp += 300
    return f"🏆 <b>{row.title}</b> завершён.\nПобедитель: <b>{winner.name}</b>\nНаграда: 15 000 рё и 30 жетонов арены."


async def mmo_top_text(session: AsyncSession) -> str:
    players = (await session.scalars(select(NinjaProfile).order_by(NinjaProfile.arena_rating.desc()).limit(5))).all()
    clans = (await session.scalars(select(NinjaClan).order_by(NinjaClan.rating.desc()).limit(5))).all()
    settlements = (await session.scalars(select(NinjaSettlement).order_by(NinjaSettlement.rating.desc()).limit(5))).all()
    lines = ["🌍 <b>MMO-рейтинг мира шиноби</b>", "", "🥷 Игроки:"]
    lines += [f"{i}. {p.name} · {p.arena_rating}" for i, p in enumerate(players, 1)] or ["—"]
    lines += ["", "👥 Кланы:"]
    lines += [f"{i}. {c.name} · {c.rating}" for i, c in enumerate(clans, 1)] or ["—"]
    lines += ["", "🏯 Telegram-поселения:"]
    lines += [f"{i}. {s.title} · {s.rating}" for i, s in enumerate(settlements, 1)] or ["—"]
    return "\n".join(lines)


async def world_broadcast_targets(session: AsyncSession, kind: str) -> list[int]:
    kind = kind.lower()
    rows = (await session.scalars(select(NinjaSettlement))).all()
    targets: set[int] = set()
    for row in rows:
        settings = _copy_dict(row.notification_settings)
        if bool(settings.get(kind, True)):
            targets.add(int(row.chat_id))
    return sorted(targets)


async def trade_reputation_text(session: AsyncSession, user_id: int) -> str:
    await require_profile(session, user_id)
    completed = int(await session.scalar(select(func.count()).select_from(NinjaTrade).where(or_(NinjaTrade.initiator_id == user_id, NinjaTrade.partner_id == user_id), NinjaTrade.status == "completed")) or 0)
    cancelled = int(await session.scalar(select(func.count()).select_from(NinjaTrade).where(NinjaTrade.initiator_id == user_id, NinjaTrade.status == "cancelled")) or 0)
    return f"💰 <b>Торговая репутация</b>\n✅ Завершённых сделок: {completed}\n↩️ Отмен инициатором: {cancelled}"


# ---------------------------------------------------------------------------
# Additional social MMO systems: clan base, group missions, alliance wars,
# family home and controlled global notices.
# ---------------------------------------------------------------------------


async def clan_base_text(session: AsyncSession, user_id: int) -> str:
    member = await session.scalar(select(NinjaClanMember).where(NinjaClanMember.user_id == user_id))
    if not member:
        raise GameError("Вы не состоите в клане.")
    clan = await session.get(NinjaClan, member.clan_id)
    upgrades = _copy_dict(clan.upgrades if clan else {})
    return (
        f"🏯 <b>База клана {clan.name}</b>\n"
        f"Главный зал: ур.{int(upgrades.get('hall', 1))}\n"
        f"🔨 Кузница: ур.{int(upgrades.get('forge', 0))}\n"
        f"🏥 Госпиталь: ур.{int(upgrades.get('hospital', 0))}\n"
        f"🕵 Разведцентр: ур.{int(upgrades.get('intel', 0))}\n"
        f"🛡 Стены: ур.{int(upgrades.get('walls', 0))}\n"
        f"💰 Казна: {clan.treasury:,} рё\n"
        f"Ваша роль: {clan_role_label(member.role)}"
    )


async def clan_base_upgrade(session: AsyncSession, user_id: int, key: str) -> str:
    member = await session.scalar(select(NinjaClanMember).where(NinjaClanMember.user_id == user_id))
    if not member:
        raise GameError("Вы не состоите в клане.")
    clan = await session.get(NinjaClan, member.clan_id)
    if not clan:
        raise GameError("Клан не найден.")
    if clan.leader_id != user_id and member.role != "treasurer":
        raise GameError("Казной управляет 👑 Клан-лидер или 💰 Хранитель свитков и казны.")
    key = key.lower()
    labels = {
        "hall": "🏯 Главный зал",
        "forge": "🔨 Кузница",
        "hospital": "🏥 Госпиталь",
        "intel": "🕵 Разведцентр",
        "walls": "🛡 Стены",
    }
    if key not in labels:
        raise GameError("Доступно: hall, forge, hospital, intel, walls.")
    upgrades = _copy_dict(clan.upgrades)
    level = int(upgrades.get(key, 1 if key == "hall" else 0))
    if level >= 10:
        raise GameError("Улучшение уже максимального уровня.")
    base = {"hall": 30_000, "forge": 25_000, "hospital": 25_000, "intel": 30_000, "walls": 35_000}[key]
    cost = base * (level + 1)
    if clan.treasury < cost:
        raise GameError(f"В казне клана нужно {cost:,} рё.")
    clan.treasury -= cost
    upgrades[key] = level + 1
    clan.upgrades = upgrades
    clan.xp += 300 + 100 * level
    clan.rating += 3
    session.add(NinjaEventLog(event_type="clan_base_upgrade", user_id=user_id, payload={"clan_id": clan.id, "upgrade": key, "level": level + 1}))
    return f"🏗 {labels[key]} клана улучшен до уровня {level + 1}. Потрачено {cost:,} рё."


async def settlement_mission_start(session: AsyncSession, chat_id: int, user_id: int) -> str:
    settlement = await session.get(NinjaSettlement, chat_id)
    if not settlement:
        raise GameError("Чат не зарегистрирован как поселение.")
    if settlement.owner_id != user_id:
        raise GameError("Запускать миссию поселения может создатель группы.")
    key = f"settlement_mission:{chat_id}"
    row = await session.get(NinjaWorldState, key)
    value = _copy_dict(row.value) if row else {}
    if value.get("active"):
        try:
            from datetime import datetime
            ends = datetime.fromisoformat(str(value.get("ends_at")))
        except Exception:
            ends = None
        if ends and ends > utcnow():
            raise GameError("В поселении уже идёт групповая миссия.")
    target = 700 + settlement.level * 80
    value = {
        "active": True,
        "started_at": utcnow().isoformat(),
        "ends_at": (utcnow() + timedelta(minutes=20)).isoformat(),
        "target": target,
        "score": 0,
        "participants": {},
        "title": random.choice(["Засада нукенинов", "Защита каравана", "Охрана границы", "Поиск пропавшего отряда"]),
    }
    if row is None:
        row = NinjaWorldState(key=key, value=value)
        session.add(row)
    else:
        row.value = value
    return f"📜 <b>Миссия поселения: {value['title']}</b>\nЦель: {target} очков силы за 20 минут.\n🥷 /chatmission join"


async def settlement_mission_join(session: AsyncSession, chat_id: int, user_id: int) -> str:
    settlement = await session.get(NinjaSettlement, chat_id)
    if not settlement:
        raise GameError("Чат не зарегистрирован как поселение.")
    row = await session.get(NinjaWorldState, f"settlement_mission:{chat_id}")
    value = _copy_dict(row.value) if row else {}
    if not value.get("active"):
        raise GameError("Активной миссии поселения нет.")
    from datetime import datetime
    try:
        ends = datetime.fromisoformat(str(value.get("ends_at")))
    except Exception:
        ends = utcnow()
    if ends <= utcnow():
        value["active"] = False
        row.value = value
        raise GameError("Время групповой миссии закончилось.")
    p = await require_profile(session, user_id)
    participants = _copy_dict(value.get("participants"))
    if str(user_id) in participants:
        raise GameError("Вы уже внесли вклад в эту миссию.")
    contribution = max(10, int(player_power(p) * random.uniform(0.025, 0.05)))
    participants[str(user_id)] = contribution
    value["participants"] = participants
    value["score"] = int(value.get("score", 0)) + contribution
    completed = value["score"] >= int(value.get("target", 1))
    if completed:
        value["active"] = False
        reward_each = 1200 + settlement.level * 100
        for uid_raw in participants:
            player = await session.get(NinjaProfile, int(uid_raw))
            if player:
                player.ryo += reward_each
                player.xp += 180
                player.village_points += 3
        settlement.xp += 350
        settlement.treasury += 5000
        settlement.rating += 6
        history = _copy_list(settlement.history)
        history.append({"at": utcnow().isoformat(), "event": f"Выполнена групповая миссия: {value.get('title')}"})
        settlement.history = history[-100:]
    row.value = value
    text = f"🥷 {p.name} внёс +{contribution}. Прогресс: {value['score']}/{value['target']}."
    if completed:
        text += f"\n🏆 Миссия завершена! Каждый из {len(participants)} участников получает {reward_each:,} рё и 180 XP."
    return text


async def settlement_mission_status(session: AsyncSession, chat_id: int) -> str:
    row = await session.get(NinjaWorldState, f"settlement_mission:{chat_id}")
    value = _copy_dict(row.value) if row else {}
    if not value:
        return "📜 В этом поселении ещё не запускались групповые миссии."
    return (
        f"📜 <b>{value.get('title', 'Миссия поселения')}</b>\n"
        f"Статус: {'активна' if value.get('active') else 'завершена'}\n"
        f"Прогресс: {int(value.get('score', 0))}/{int(value.get('target', 0))}\n"
        f"Участников: {len(_copy_dict(value.get('participants')))}"
    )


async def marriage_propose(session: AsyncSession, user_id: int, partner_id: int) -> tuple[str, int]:
    await require_profile(session, user_id)
    await require_profile(session, partner_id)
    if user_id == partner_id:
        raise GameError("Нельзя заключить семейный союз с собой.")
    if await _blocked(session, user_id, partner_id):
        raise GameError("Социальное взаимодействие заблокировано.")
    await _check_social_permission(session, partner_id, "marriage")
    existing = await session.scalar(
        select(NinjaBond).where(
            NinjaBond.status.in_(["pending", "active"]),
            or_(NinjaBond.proposer_id == user_id, NinjaBond.partner_id == user_id),
        )
    )
    if existing:
        raise GameError("У вас уже есть активное или ожидающее семейное предложение.")
    partner_existing = await session.scalar(
        select(NinjaBond).where(
            NinjaBond.status.in_(["pending", "active"]),
            or_(NinjaBond.proposer_id == partner_id, NinjaBond.partner_id == partner_id),
        )
    )
    if partner_existing:
        raise GameError("У этого шиноби уже есть активный или ожидающий семейный союз.")
    row = NinjaBond(proposer_id=user_id, partner_id=partner_id)
    session.add(row)
    await session.flush()
    return f"💍 Предложение семейного союза #{row.id} отправлено шиноби {partner_id}.", row.id


async def marriage_accept(session: AsyncSession, user_id: int, bond_id: int) -> tuple[str, int]:
    row = await session.get(NinjaBond, bond_id)
    if not row or row.status != "pending" or row.partner_id != user_id:
        raise GameError("Предложение семейного союза недоступно.")
    row.status = "active"
    row.accepted_at = utcnow()
    a = await require_profile(session, row.proposer_id)
    b = await require_profile(session, row.partner_id)
    for p in (a, b):
        flags = _copy_dict(p.flags)
        flags["bond_id"] = row.id
        p.flags = flags
    session.add(NinjaEventLog(event_type="family_bond", user_id=user_id, payload={"bond": row.id, "partner": row.proposer_id}))
    return f"💍 {a.name} и {b.name} заключили семейный союз #{row.id}.", row.proposer_id


async def marriage_home_upgrade(session: AsyncSession, user_id: int) -> str:
    p = await require_profile(session, user_id)
    bond_id = int(_copy_dict(p.flags).get("bond_id") or 0)
    row = await session.get(NinjaBond, bond_id) if bond_id else None
    if not row or row.status != "active":
        raise GameError("Нужен активный семейный союз.")
    if row.shared_home_level >= 5:
        raise GameError("Общий дом максимального уровня.")
    cost = 15_000 * (row.shared_home_level + 1)
    if p.ryo < cost:
        raise GameError(f"Нужно {cost:,} рё.")
    p.ryo -= cost
    row.shared_home_level += 1
    return f"🏡 Общий семейный дом улучшен до уровня {row.shared_home_level}. Потрачено {cost:,} рё."


async def alliance_war(session: AsyncSession, user_id: int, target_name: str) -> str:
    member = await session.scalar(select(NinjaClanMember).where(NinjaClanMember.user_id == user_id))
    if not member:
        raise GameError("Вы не состоите в клане.")
    own_clan = await session.get(NinjaClan, member.clan_id)
    if not own_clan or own_clan.leader_id != user_id:
        raise GameError("Войну альянсов начинает Клан-лидер ведущего клана.")
    own = await session.scalar(select(NinjaClanAlliance).where(NinjaClanAlliance.leader_clan_id == own_clan.id))
    if not own:
        raise GameError("Ваш клан не является ведущим кланом альянса.")
    target = await session.scalar(select(NinjaClanAlliance).where(func.lower(NinjaClanAlliance.name) == target_name.strip().casefold()))
    if not target or target.id == own.id:
        raise GameError("Альянс-противник не найден.")

    async def power(alliance: NinjaClanAlliance) -> float:
        clan_ids = [int(x) for x in _copy_list(alliance.members)]
        clan_members = (await session.scalars(select(NinjaClanMember).where(NinjaClanMember.clan_id.in_(clan_ids)))).all()
        user_ids = [x.user_id for x in clan_members]
        profiles = (await session.scalars(select(NinjaProfile).where(NinjaProfile.user_id.in_(user_ids)))).all() if user_ids else []
        top = sorted(profiles, key=player_power, reverse=True)[:50]
        return sum(player_power(p) for p in top) * random.uniform(0.94, 1.06)

    a_score, b_score = await power(own), await power(target)
    winner, loser = (own, target) if a_score >= b_score else (target, own)
    winner.rating += 45
    loser.rating = max(0, loser.rating - 30)
    winner.treasury += 100_000
    session.add(NinjaEventLog(event_type="alliance_war", user_id=user_id, payload={"a": own.id, "b": target.id, "winner": winner.id}))
    return (
        f"🌍 <b>Война альянсов</b>\n{own.name}: {round(a_score):,}\n{target.name}: {round(b_score):,}\n\n"
        f"🏆 Победитель: {winner.name}\nРейтинг +45 · казна альянса +100 000 рё."
    )


async def clan_history_text(session: AsyncSession, user_id: int) -> str:
    member = await session.scalar(select(NinjaClanMember).where(NinjaClanMember.user_id == user_id))
    if not member:
        raise GameError("Вы не состоите в клане.")
    clan = await session.get(NinjaClan, member.clan_id)
    rows = (await session.scalars(
        select(NinjaEventLog).where(
            NinjaEventLog.event_type.in_(["clan_war", "clan_base_upgrade"])
        ).order_by(NinjaEventLog.id.desc()).limit(100)
    )).all()
    related = []
    for row in rows:
        payload = _copy_dict(row.payload)
        if clan.id in {int(payload.get("clan_a") or 0), int(payload.get("clan_b") or 0), int(payload.get("clan_id") or 0)}:
            related.append(row)
    lines = [f"📜 <b>Хроника клана {clan.name}</b>", f"Основан: {clan.created_at.date().isoformat()}"]
    for row in related[:15]:
        lines.append(f"• {row.created_at.date().isoformat()} — {row.event_type}")
    if len(lines) == 2:
        lines.append("Крупных событий пока нет.")
    return "\n".join(lines)


async def global_notice_text(session: AsyncSession, kind: str, text: str) -> tuple[str, list[int]]:
    kind = kind.lower()
    if kind not in SETTLEMENT_NOTIFICATION_KINDS:
        raise GameError("Тип: war, raid, achievement, village, world, market.")
    targets = await world_broadcast_targets(session, kind)
    clean = " ".join(text.split())[:1500]
    if not clean:
        raise GameError("Текст уведомления пуст.")
    return f"🌍 <b>Глобальное событие</b>\n{clean}", targets
