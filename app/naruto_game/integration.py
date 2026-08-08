from __future__ import annotations

import logging

from aiogram.types import BotCommand, BotCommandScopeChat

# Import models before init_db(): AniGuard's init_db() runs Base.metadata.create_all,
# therefore all Naruto RPG tables are created without a separate migration package.
from . import models as _models  # noqa: F401
from . import v3_models as _v3_models  # noqa: F401
from .router import router as naruto_router
from .extended_router import router as extended_router
from .advanced_router import router as advanced_router
from .v3_router import router as v3_router
from .social_router import SocialPresenceMiddleware, router as social_router

logger = logging.getLogger(__name__)
_installed = False

_GAME_COMMANDS = [
    BotCommand(command="ninja", description="Naruto RPG — главное меню"),
    BotCommand(command="ninja_create", description="Создать шиноби"),
    BotCommand(command="daily", description="Ежедневная награда"),
    BotCommand(command="mission", description="Миссия шиноби"),
    BotCommand(command="train", description="Тренировка"),
    BotCommand(command="battle", description="Бой с противником"),
    BotCommand(command="story", description="Сюжетная кампания"),
    BotCommand(command="techniques", description="Техники персонажа"),
    BotCommand(command="cards", description="Коллекция карточек"),
    BotCommand(command="arena", description="PvP-арена"),
    BotCommand(command="clan", description="Клан шиноби"),
    BotCommand(command="world", description="Состояние мира"),
    BotCommand(command="raid", description="Мировой рейд"),
    BotCommand(command="market", description="Рынок игроков"),
    BotCommand(command="settlement", description="Telegram-поселение шиноби"),
    BotCommand(command="friend", description="Друзья и связь шиноби"),
    BotCommand(command="duel", description="Дуэль с игроком"),
    BotCommand(command="student", description="Наставник и ученики"),
    BotCommand(command="chatwar", description="Война Telegram-поселений"),
    BotCommand(command="tournament", description="Турнир в группе"),
    BotCommand(command="alliance", description="Альянс кланов"),
    BotCommand(command="nmail", description="Почта шиноби"),
    BotCommand(command="mmo_top", description="MMO-рейтинг мира"),
    BotCommand(command="clanroles", description="Роли клана в стиле Naruto"),
    BotCommand(command="clanbase", description="База и казна клана"),
    BotCommand(command="chatmission", description="Групповая миссия поселения"),
    BotCommand(command="deal", description="Безопасная сделка reply/ID"),
    BotCommand(command="marry", description="Семейный союз персонажей"),
    BotCommand(command="privacy", description="Приватность игрового профиля"),
    BotCommand(command="territory", description="Карта и захват территорий"),
    BotCommand(command="worldwar", description="Большая война деревень"),
    BotCommand(command="mobilize", description="Мобилизация на фронт"),
    BotCommand(command="front", description="Фронт и военные действия"),
    BotCommand(command="peace", description="Мирные переговоры Каге"),
    BotCommand(command="npc", description="Живые NPC и отношения"),
    BotCommand(command="promise", description="Обещания персонажам"),
    BotCommand(command="livemission", description="Динамическая живая миссия"),
    BotCommand(command="events", description="Текущие события мира"),
    BotCommand(command="recommend", description="Что делать дальше"),
    BotCommand(command="returnlog", description="Сводка за время отсутствия"),
    BotCommand(command="path", description="Путь и специализация шиноби"),
    BotCommand(command="research", description="Создание своей техники"),
    BotCommand(command="legend", description="Путь легендарного шиноби"),
    BotCommand(command="epithet", description="Прозвище шиноби"),
    BotCommand(command="legacy", description="Летопись и наследие"),
    BotCommand(command="mmo3", description="MMO V3 — пульс живого мира"),
    BotCommand(command="mmo4", description="MMO V4 — командный центр и новая навигация"),
    BotCommand(command="government", description="Правительство вашей деревни"),
    BotCommand(command="election", description="Выборы Каге"),
    BotCommand(command="project", description="Проекты и развитие деревни"),
    BotCommand(command="criminalorg", description="Преступные организации нукенинов"),
    BotCommand(command="crime", description="Операции преступной организации"),
    BotCommand(command="bijuu", description="Уникальные биджу сервера"),
    BotCommand(command="newspaper", description="Газета мира шиноби"),
    BotCommand(command="worldchronicle", description="Мировая летопись MMO"),
]


async def _register_game_commands(*_: object, **__: object) -> None:
    """Append RPG commands after AniGuard.configure_bot() installs its core menu."""
    from app.bot import bot, settings

    async def merge(scope: BotCommandScopeChat | None = None) -> None:
        current = await bot.get_my_commands(scope=scope) if scope else await bot.get_my_commands()
        known = {item.command for item in current}
        merged = list(current) + [item for item in _GAME_COMMANDS if item.command not in known]
        if scope:
            await bot.set_my_commands(merged, scope=scope)
        else:
            await bot.set_my_commands(merged)

    try:
        await merge()
    except Exception:
        logger.exception("Could not append Naruto RPG commands to default bot menu")

    for admin_id in settings.admin_ids:
        try:
            await merge(BotCommandScopeChat(chat_id=admin_id))
        except Exception:
            logger.exception("Could not append Naruto RPG commands for admin %s", admin_id)


def install_naruto_game() -> None:
    """Attach Naruto RPG before AniGuard's broad group message pipeline."""
    global _installed
    if _installed:
        return

    from app.bot import AccessMiddleware, dp

    # Extended systems are nested under the same RPG router so they share access controls.
    if extended_router.parent_router is None:
        naruto_router.include_router(extended_router)
    # Advanced natural-language aliases must be evaluated before the broad
    # social "Наруто, ..." handler.
    if advanced_router.parent_router is None:
        naruto_router.include_router(advanced_router)
    # MMO V3 politics/underworld/bijuu aliases are also specific and must run
    # before the broad social natural-language handler.
    if v3_router.parent_router is None:
        naruto_router.include_router(v3_router)
    if social_router.parent_router is None:
        naruto_router.include_router(social_router)

    # Reuse AniGuard's maintenance/block/penalty/command-restriction checks.
    naruto_router.message.outer_middleware(AccessMiddleware())
    # Count only real Naruto-RPG actions inside registered Telegram settlements.
    naruto_router.message.outer_middleware(SocialPresenceMiddleware())
    naruto_router.edited_message.outer_middleware(AccessMiddleware())
    naruto_router.callback_query.outer_middleware(AccessMiddleware())
    naruto_router.startup.register(_register_game_commands)

    dp.include_router(naruto_router)

    # bot.py contains a broad group message pipeline in the already attached
    # `aniguard` router. The RPG router must be evaluated first, otherwise
    # valid /ninja, /mission, /battle, ... messages can reach that catch-all.
    try:
        subrouters = dp.sub_routers
        if naruto_router in subrouters:
            subrouters.remove(naruto_router)
            subrouters.insert(0, naruto_router)
    except Exception:
        logger.exception("Could not prioritize Naruto RPG router")

    _installed = True
