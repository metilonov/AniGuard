from __future__ import annotations

from datetime import timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .advanced import recommendations_text, world_events_text
from .models import (
    NinjaAuction,
    NinjaBattle,
    NinjaCard,
    NinjaClan,
    NinjaClanMember,
    NinjaCustomTechnique,
    NinjaDynamicMission,
    NinjaItem,
    NinjaProfile,
    NinjaTechnique,
    NinjaWorldEvent,
    NinjaWorldState,
    utcnow,
)
from .service import GameError, profile_text, require_profile, world_text
from .social import friends_text, mmo_top_text
from .v3 import government_text, newspaper_text, world_pulse_text


def _aware(value):
    if value is None:
        return None
    now = utcnow()
    if getattr(value, "tzinfo", None) is None:
        return value.replace(tzinfo=now.tzinfo)
    return value


def _fmt_remaining(delta: timedelta) -> str:
    seconds = max(0, int(delta.total_seconds()))
    hours, seconds = divmod(seconds, 3600)
    minutes, _ = divmod(seconds, 60)
    if hours:
        return f"{hours}ч {minutes}м"
    return f"{max(1, minutes)}м"


async def command_center_text(session: AsyncSession, user_id: int) -> str:
    profile = await require_profile(session, user_id)
    battle = await session.get(NinjaBattle, user_id)
    mission = (
        await session.execute(
            select(NinjaDynamicMission)
            .where(NinjaDynamicMission.user_id == user_id, NinjaDynamicMission.status.in_(["active", "prepared"]))
            .order_by(NinjaDynamicMission.id.desc())
            .limit(1)
        )
    ).scalars().first()
    alerts: list[str] = []
    if battle:
        alerts.append("⚔️ есть активный бой")
    if mission:
        alerts.append("📜 есть активная живая миссия")
    if profile.energy <= 20:
        alerts.append("⚡ мало энергии")
    if profile.hp < max(1, profile.max_hp // 2):
        alerts.append("❤️ здоровье ниже 50%")
    alert_text = " · ".join(alerts) if alerts else "срочных угроз нет"
    return (
        "🌐 <b>Командный центр MMO V4</b>\n\n"
        f"🥷 {profile.name} · уровень {profile.level} · {profile.ninja_rank}\n"
        f"❤️ {profile.hp}/{profile.max_hp} · 🔵 {profile.chakra}/{profile.max_chakra} · ⚡ {profile.energy}/100\n"
        f"🪙 {profile.ryo:,} рё · 🏆 {profile.arena_rating}\n\n"
        f"📡 Статус: {alert_text}.\n\n"
        "Разделы теперь независимы: каждая кнопка открывает свой экран и только связанные с ним действия."
    )


async def hub_text(session: AsyncSession, user_id: int, hub: str) -> str:
    profile = await require_profile(session, user_id)
    texts = {
        "shinobi": (
            "🥷 <b>Шиноби</b>\n\n"
            "Личный раздел персонажа: профиль, ежедневная награда, техники, карточки, инвентарь и путь развития."
        ),
        "activities": (
            "⚔️ <b>Активности</b>\n\n"
            "Миссии, сюжет, PvE-бои, арена и рейды. Открытие раздела больше не запускает действие автоматически."
        ),
        "world": (
            "🌍 <b>Мир шиноби</b>\n\n"
            "Глобальные события, территории, политика деревень, газета, биджу и пульс живого мира."
        ),
        "social": (
            "👥 <b>Сообщество</b>\n\n"
            "Кланы, друзья, наставничество, MMO-рейтинг, поселения и социальные системы."
        ),
        "economy": (
            "💰 <b>Экономика</b>\n\n"
            "Баланс, рынок, крафт, контракты, торговая репутация и экономика мира."
        ),
        "growth": (
            "🌀 <b>Развитие</b>\n\n"
            "Тренировки, экзамены, наставники, профессии, исследования техник и наследие."
        ),
    }
    base = texts.get(hub, "🌐 <b>MMO V4</b>")
    return f"{base}\n\n⭐ Уровень: {profile.level} · ⚡ Энергия: {profile.energy}/100"


async def daily_center_text(session: AsyncSession, user_id: int) -> str:
    profile = await require_profile(session, user_id)
    now = utcnow()
    streak = int((profile.counters or {}).get("daily_streak", 0))
    if profile.last_daily_at:
        last = _aware(profile.last_daily_at)
        wait = timedelta(hours=20) - (now - last)
        if wait.total_seconds() > 0:
            status = f"⏳ Следующая награда примерно через {_fmt_remaining(wait)}"
        else:
            status = "✅ Награда доступна"
    else:
        status = "✅ Первая награда доступна"
    return (
        "🎁 <b>Ежедневная награда</b>\n\n"
        f"🔥 Серия: {streak}/7\n"
        f"{status}\n\n"
        "Нажатие на раздел ничего не списывает и не начисляет — получение выполняется отдельной кнопкой."
    )


async def mission_center_text(session: AsyncSession, user_id: int) -> str:
    profile = await require_profile(session, user_id)
    active = (
        await session.execute(
            select(NinjaDynamicMission)
            .where(NinjaDynamicMission.user_id == user_id, NinjaDynamicMission.status.in_(["active", "prepared"]))
            .order_by(NinjaDynamicMission.id.desc())
            .limit(1)
        )
    ).scalars().first()
    state = f"📌 Активна: <b>{active.title}</b> · {active.rank}-ранг" if active else "📭 Активной живой миссии нет"
    return (
        "📜 <b>Центр миссий</b>\n\n"
        f"{state}\n"
        f"✅ Выполнено миссий: {profile.missions_completed}\n"
        f"⚡ Энергия: {profile.energy}/100\n\n"
        "Можно выбрать быструю классическую миссию или полноценную живую миссию с подготовкой и решениями."
    )


async def combat_readiness_text(session: AsyncSession, user_id: int) -> str:
    profile = await require_profile(session, user_id)
    battle = await session.get(NinjaBattle, user_id)
    base = profile.ninjutsu + profile.taijutsu + profile.genjutsu + profile.defense + profile.speed + profile.chakra_control
    hp_ratio = profile.hp / max(1, profile.max_hp)
    chakra_ratio = profile.chakra / max(1, profile.max_chakra)
    readiness = int(max(0, min(100, (hp_ratio * 40) + (chakra_ratio * 30) + (profile.energy / 100 * 30))))
    if base < 100:
        tier = "D–C"
    elif base < 180:
        tier = "C–B"
    elif base < 280:
        tier = "B–A"
    else:
        tier = "A–S"
    active = "⚔️ Активный бой уже идёт" if battle else "✅ Активного боя нет"
    return (
        "⚔️ <b>Боевая готовность</b>\n\n"
        f"❤️ HP: {profile.hp}/{profile.max_hp}\n"
        f"🔵 Чакра: {profile.chakra}/{profile.max_chakra}\n"
        f"⚡ Энергия: {profile.energy}/100\n"
        f"📊 Готовность: <b>{readiness}%</b>\n"
        f"🎯 Рекомендуемая сложность: {tier}\n"
        f"{active}"
    )


async def arena_center_text(session: AsyncSession, user_id: int) -> str:
    profile = await require_profile(session, user_id)
    total = profile.pvp_wins + profile.pvp_losses
    wr = int(profile.pvp_wins * 100 / total) if total else 0
    return (
        "🏆 <b>Арена</b>\n\n"
        f"Рейтинг: <b>{profile.arena_rating}</b>\n"
        f"⚔️ Победы/поражения: {profile.pvp_wins}/{profile.pvp_losses}\n"
        f"📈 Winrate: {wr}%\n"
        f"🎟 Жетоны: {profile.arena_tokens}\n\n"
        "Быстрый матч запускается только отдельной кнопкой."
    )


async def cards_center_text(session: AsyncSession, user_id: int) -> str:
    await require_profile(session, user_id)
    cards = int(await session.scalar(select(func.count()).select_from(NinjaCard).where(NinjaCard.user_id == user_id)) or 0)
    return (
        "🎴 <b>Карточный центр</b>\n\n"
        f"Уникальных карточек в коллекции: <b>{cards}</b>\n\n"
        "Здесь отдельно доступны коллекция, призыв и карточная арена."
    )


async def inventory_center_text(session: AsyncSession, user_id: int) -> str:
    profile = await require_profile(session, user_id)
    stacks = int(await session.scalar(select(func.count()).select_from(NinjaItem).where(NinjaItem.user_id == user_id, NinjaItem.quantity > 0)) or 0)
    equipped = int(await session.scalar(select(func.count()).select_from(NinjaItem).where(NinjaItem.user_id == user_id, NinjaItem.equipped_slot.is_not(None))) or 0)
    return (
        "🎒 <b>Снаряжение и инвентарь</b>\n\n"
        f"📦 Стаков предметов: {stacks}\n"
        f"🛡 Экипировано: {equipped}\n"
        f"🪙 Рё: {profile.ryo:,}\n\n"
        "Инвентарь, крафт и рынок теперь находятся на отдельных экранах."
    )


async def economy_center_text(session: AsyncSession, user_id: int) -> str:
    profile = await require_profile(session, user_id)
    lots = int(await session.scalar(select(func.count()).select_from(NinjaAuction).where(NinjaAuction.active.is_(True))) or 0)
    custom = int(await session.scalar(select(func.count()).select_from(NinjaCustomTechnique).where(NinjaCustomTechnique.user_id == user_id)) or 0)
    clan_treasury = 0
    member = await session.scalar(select(NinjaClanMember).where(NinjaClanMember.user_id == user_id))
    if member:
        clan = await session.get(NinjaClan, member.clan_id)
        clan_treasury = int(clan.treasury if clan else 0)
    return (
        "💰 <b>Экономический центр MMO V4</b>\n\n"
        f"🪙 Баланс: <b>{profile.ryo:,} рё</b>\n"
        f"💎 Кристаллы: {profile.chakra_crystals}\n"
        f"🏪 Активных лотов на рынке: {lots}\n"
        f"👥 Казна вашего клана: {clan_treasury:,}\n"
        f"📜 Собственных техник: {custom}\n\n"
        "Торговля и производство разделены от боевых экранов и не запускают операции при открытии."
    )


async def world_center_text(session: AsyncSession, user_id: int) -> str:
    profile = await require_profile(session, user_id)
    active_events = int(
        await session.scalar(select(func.count()).select_from(NinjaWorldEvent).where(NinjaWorldEvent.status == "active")) or 0
    )
    raid = await session.get(NinjaWorldState, "raid")
    raid_name = str((raid.value or {}).get("name") or "не определён") if raid else "ещё не появился"
    return (
        "🌍 <b>Глобальный мир</b>\n\n"
        f"🏯 Ваша деревня: {profile.village}\n"
        f"🌪 Активных мировых событий: {active_events}\n"
        f"👹 Текущий рейд: {raid_name}\n\n"
        "Откройте нужную подсистему: события, территории, политику, газету или биджу."
    )


async def raid_center_text(session: AsyncSession, user_id: int) -> str:
    profile = await require_profile(session, user_id)
    # world_text initializes the shared raid safely if it does not exist yet.
    world = await world_text(session)
    now = utcnow()
    cooldown = "✅ атака доступна"
    if profile.last_raid_at:
        last = _aware(profile.last_raid_at)
        wait = timedelta(hours=1) - (now - last)
        if wait.total_seconds() > 0:
            cooldown = f"⏳ следующая атака через {_fmt_remaining(wait)}"
    raid_part = world.split("👹 Рейд:", 1)[-1].strip() if "👹 Рейд:" in world else world
    return (
        "👹 <b>Рейдовый центр</b>\n\n"
        f"👹 Рейд: {raid_part}\n"
        f"🪬 Печати рейда: {profile.raid_seals}\n"
        f"{cooldown}\n\n"
        "Атака выполняется только после отдельного подтверждающего нажатия."
    )


async def social_center_text(session: AsyncSession, user_id: int) -> str:
    profile = await require_profile(session, user_id)
    text = await friends_text(session, user_id)
    return (
        "👥 <b>Социальный центр</b>\n\n"
        f"🥷 {profile.name}\n"
        f"{text}\n\n"
        "Дружба, наставничество, клан и MMO-рейтинг вынесены в отдельные ветки."
    )


async def development_center_text(session: AsyncSession, user_id: int) -> str:
    profile = await require_profile(session, user_id)
    techniques = int(await session.scalar(select(func.count()).select_from(NinjaTechnique).where(NinjaTechnique.user_id == user_id)) or 0)
    return (
        "🌀 <b>Центр развития</b>\n\n"
        f"⭐ Уровень: {profile.level}\n"
        f"🎖 Ранг: {profile.ninja_rank}\n"
        f"📚 Изучено техник: {techniques}\n"
        f"👤 Наставник: {profile.mentor or '—'}\n"
        f"🛠 Профессия: {profile.profession or '—'}\n\n"
        "Тренировки, экзамены и выбор наставника теперь не смешиваются с главным меню."
    )


async def mmo_v4_text(session: AsyncSession, user_id: int) -> str:
    profile = await require_profile(session, user_id)
    recommendations = await recommendations_text(session, user_id)
    return (
        "🌐 <b>MMO V4 — глобальное расширение</b>\n\n"
        "• новая многоуровневая навигация без повторяющихся клавиатур;\n"
        "• безопасные экраны перед ежедневками, миссиями, ареной и рейдом;\n"
        "• боевая готовность и экономический центр;\n"
        "• отдельные центры мира, сообщества и развития;\n"
        "• контекстный возврат в родительский раздел.\n\n"
        f"🥷 {profile.name} · уровень {profile.level}\n\n"
        f"{recommendations}"
    )


async def activity_digest_text(session: AsyncSession, user_id: int) -> str:
    profile = await require_profile(session, user_id)
    recommendations = await recommendations_text(session, user_id)
    events = await world_events_text(session)
    return (
        "🧭 <b>Оперативная сводка</b>\n\n"
        f"🥷 {profile.name} · ❤️ {profile.hp}/{profile.max_hp} · ⚡ {profile.energy}/100\n\n"
        f"{recommendations}\n\n"
        f"{events}"
    )


async def full_profile_text(session: AsyncSession, user_id: int) -> str:
    return profile_text(await require_profile(session, user_id))


async def mmo_ranking_text(session: AsyncSession, user_id: int) -> str:
    await require_profile(session, user_id)
    return await mmo_top_text(session)


async def government_center_text(session: AsyncSession, user_id: int) -> str:
    return await government_text(session, user_id)


async def newspaper_center_text(session: AsyncSession, user_id: int) -> str:
    await require_profile(session, user_id)
    return await newspaper_text(session)


async def pulse_center_text(session: AsyncSession, user_id: int) -> str:
    return await world_pulse_text(session, user_id)


async def ensure_profile(session: AsyncSession, user_id: int) -> NinjaProfile:
    profile = await session.get(NinjaProfile, user_id)
    if profile is None:
        raise GameError("Сначала создайте шиноби командой /ninja_create Имя.")
    return profile
