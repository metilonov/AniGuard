from __future__ import annotations

import logging

from aiogram.types import BotCommand, BotCommandScopeChat

# Import models before init_db(): AniGuard's init_db() runs Base.metadata.create_all,
# therefore all Naruto RPG tables are created without a separate migration package.
from . import models as _models  # noqa: F401
from .router import router as naruto_router
from .extended_router import router as extended_router

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

    # Reuse AniGuard's maintenance/block/penalty/command-restriction checks.
    naruto_router.message.outer_middleware(AccessMiddleware())
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
