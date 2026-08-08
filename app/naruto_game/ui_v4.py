from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from .advanced import TERRITORY_DEFS
from .content import BOSSES, CRAFT_RECIPES


def kb(rows: list[list[tuple[str, str]]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=text, callback_data=data) for text, data in row]
            for row in rows
        ]
    )


def main_menu() -> InlineKeyboardMarkup:
    return kb([
        [("🥷 Шиноби", "ng:hub:shinobi"), ("⚔️ Активности", "ng:hub:activities")],
        [("🌍 Мир", "ng:hub:world"), ("👥 Сообщество", "ng:hub:social")],
        [("💰 Экономика", "ng:hub:economy"), ("🌀 Развитие", "ng:hub:growth")],
        [("🌐 MMO V4", "ng:menu:mmo4")],
    ])


def hub_menu(name: str) -> InlineKeyboardMarkup:
    menus: dict[str, list[list[tuple[str, str]]]] = {
        "shinobi": [
            [("🥷 Профиль", "ng:menu:profile"), ("🎁 Ежедневная", "ng:menu:daily")],
            [("📚 Техники", "ng:menu:techniques"), ("🎴 Карточки", "ng:menu:cards")],
            [("🎒 Инвентарь", "ng:menu:inventory"), ("🥷 Мой путь", "ng:menu:path")],
        ],
        "activities": [
            [("📜 Миссии", "ng:menu:mission"), ("🗺 Сюжет", "ng:menu:story")],
            [("⚔️ Бой", "ng:menu:battle"), ("🏆 Арена", "ng:menu:arena")],
            [("👹 Рейд", "ng:menu:raid"), ("🧭 Что делать?", "ng:menu:recommend")],
        ],
        "world": [
            [("🌍 Состояние мира", "ng:menu:world"), ("🌪 События", "ng:menu:events")],
            [("🗺 Территории", "ng:menu:territory"), ("🏯 Правительство", "ng:menu:government")],
            [("📰 Газета", "ng:menu:newspaper"), ("🐾 Биджу", "ng4:bijuu:status")],
            [("📡 Пульс мира", "ng:menu:mmo3")],
        ],
        "social": [
            [("👥 Клан", "ng:menu:clan"), ("🤝 Друзья", "ng:menu:social")],
            [("🌍 MMO-рейтинг", "ng:menu:mmo"), ("🎓 Наставничество", "ng4:social:mentor")],
            [("📬 Почта", "ng4:social:mail"), ("🏘 Поселения", "ng4:social:settlement")],
        ],
        "economy": [
            [("💰 Обзор", "ng4:economy:overview"), ("🏪 Рынок", "ng4:economy:market")],
            [("🔨 Крафт", "ng4:economy:craft"), ("📜 Контракты", "ng4:economy:contracts")],
            [("🏦 Банк", "ng4:economy:bank"), ("🌑 Чёрный рынок", "ng4:economy:black")],
        ],
        "growth": [
            [("🥋 Тренировка", "ng4:growth:train"), ("🎖 Экзамен", "ng4:growth:exam")],
            [("👤 Наставник", "ng4:growth:mentor"), ("🛠 Профессия", "ng4:growth:profession")],
            [("🧪 Исследование", "ng4:growth:research"), ("📖 Наследие", "ng4:growth:legacy")],
        ],
    }
    rows = list(menus.get(name, []))
    rows.append([("⬅️ Главный центр", "ng:menu:home")])
    return kb(rows)


def leaf_back(hub: str) -> list[tuple[str, str]]:
    return [("⬅️ Назад", f"ng:hub:{hub}"), ("🏠 Центр", "ng:menu:home")]


def profile_menu() -> InlineKeyboardMarkup:
    return kb([
        [("🔄 Обновить", "ng:menu:profile"), ("⚔️ Готовность", "ng4:profile:ready")],
        [("🥋 Тренировка", "ng4:growth:train"), ("🎖 Экзамен", "ng4:growth:exam")],
        [("🧭 Мой путь", "ng:menu:path")],
        leaf_back("shinobi"),
    ])


def daily_menu() -> InlineKeyboardMarkup:
    return kb([
        [("🎁 Забрать награду", "ng4:daily:claim")],
        [("📜 Сводка отсутствия", "ng4:daily:return")],
        leaf_back("shinobi"),
    ])


def mission_menu() -> InlineKeyboardMarkup:
    return kb([
        [("⚡ Быстрая миссия", "ng4:mission:classic"), ("🌟 Создать живую", "ng4:mission:create")],
        [("📋 Живая миссия", "ng4:mission:status"), ("🎒 Подготовка", "ng4:mission:prepare")],
        [("🌑 Скрытно", "ng4:mission:stealth"), ("⚔️ Штурм", "ng4:mission:assault")],
        [("🤝 Переговоры", "ng4:mission:negotiate")],
        leaf_back("activities"),
    ])


def story_menu() -> InlineKeyboardMarkup:
    return kb([
        [("📖 Статус сюжета", "ng:menu:story"), ("⚔️ Начать главу", "ng:story:start")],
        leaf_back("activities"),
    ])


def battle_menu() -> InlineKeyboardMarkup:
    rows: list[list[tuple[str, str]]] = [[("📊 Боевая готовность", "ng4:profile:ready"), ("🔁 Активный бой", "ng:battle:show")]]
    preferred = ["rogue", "zabuza", "deidara", "sasori", "pain", "obito", "madara", "kaguya", "zero"]
    pairs: list[tuple[str, str]] = []
    for key in preferred:
        boss = BOSSES.get(key)
        if not boss:
            continue
        pairs.append((f"⚔️ {str(boss['name'])[:20]}", f"ng:start_boss:{key}"))
    for i in range(0, len(pairs), 2):
        rows.append(pairs[i:i + 2])
    rows.append(leaf_back("activities"))
    return kb(rows)


def arena_menu() -> InlineKeyboardMarkup:
    return kb([
        [("⚔️ Быстрый матч", "ng4:arena:match"), ("🔄 Статус", "ng:menu:arena")],
        [("🌍 MMO-рейтинг", "ng:menu:mmo"), ("🤺 Дуэли", "ng4:arena:duel")],
        leaf_back("activities"),
    ])


def techniques_menu() -> InlineKeyboardMarkup:
    return kb([
        [("📚 Мои техники", "ng4:tech:list"), ("🧪 Исследование", "ng4:growth:research")],
        [("🥋 Тренировка", "ng4:growth:train"), ("👤 Наставник", "ng4:growth:mentor")],
        leaf_back("shinobi"),
    ])


def cards_menu() -> InlineKeyboardMarkup:
    return kb([
        [("🎴 Коллекция", "ng4:cards:list"), ("✨ Призвать", "ng:card:draw")],
        [("🃏 Команда", "ng4:cards:team"), ("🏆 Карточная арена", "ng4:cards:arena")],
        leaf_back("shinobi"),
    ])


def inventory_menu() -> InlineKeyboardMarkup:
    return kb([
        [("🎒 Инвентарь", "ng4:inventory:list"), ("🔨 Крафт", "ng4:economy:craft")],
        [("🏪 Рынок", "ng4:economy:market"), ("💰 Экономика", "ng4:economy:overview")],
        leaf_back("shinobi"),
    ])


def world_menu() -> InlineKeyboardMarkup:
    return kb([
        [("🌍 Состояние мира", "ng4:world:state"), ("🌪 События", "ng:menu:events")],
        [("🗺 Территории", "ng:menu:territory"), ("🏯 Правительство", "ng:menu:government")],
        [("📰 Газета", "ng:menu:newspaper"), ("📡 Пульс", "ng:menu:mmo3")],
        leaf_back("world"),
    ])


def clan_menu() -> InlineKeyboardMarkup:
    return kb([
        [("👥 Мой клан", "ng4:clan:status"), ("🏰 База клана", "ng4:clan:base")],
        [("🎖 Роли", "ng4:clan:roles"), ("🤝 Альянс", "ng4:clan:alliance")],
        [("📜 История", "ng4:clan:history")],
        leaf_back("social"),
    ])


def raid_menu() -> InlineKeyboardMarkup:
    return kb([
        [("👹 Статус рейда", "ng:menu:raid"), ("⚔️ Атаковать", "ng4:raid:attack")],
        [("🌍 Состояние мира", "ng:menu:world")],
        leaf_back("activities"),
    ])


def social_menu() -> InlineKeyboardMarkup:
    return kb([
        [("🤝 Друзья", "ng4:social:friends"), ("🎓 Наставничество", "ng4:social:mentor")],
        [("📬 Почта", "ng4:social:mail"), ("🔒 Приватность", "ng4:social:privacy")],
        [("👥 Клан", "ng:menu:clan"), ("🌍 Рейтинг", "ng:menu:mmo")],
        leaf_back("social"),
    ])


def mmo_menu() -> InlineKeyboardMarkup:
    return kb([
        [("🌍 Рейтинг", "ng4:mmo:ranking"), ("📡 Пульс мира", "ng:menu:mmo3")],
        [("📰 Газета", "ng:menu:newspaper"), ("🧭 Сводка", "ng4:mmo:digest")],
        leaf_back("social"),
    ])


def path_menu() -> InlineKeyboardMarkup:
    return kb([
        [("🥷 Мой путь", "ng4:path:status"), ("🧭 Рекомендации", "ng:menu:recommend")],
        [("🧪 Исследование", "ng4:growth:research"), ("📖 Наследие", "ng4:growth:legacy")],
        leaf_back("shinobi"),
    ])


def events_menu() -> InlineKeyboardMarkup:
    return kb([
        [("🌪 Обновить события", "ng:menu:events"), ("⚔️ Участвовать", "ng4:event:join")],
        [("📰 Газета", "ng:menu:newspaper"), ("🧭 Сводка", "ng4:mmo:digest")],
        leaf_back("world"),
    ])


def territory_menu() -> InlineKeyboardMarkup:
    rows: list[list[tuple[str, str]]] = [[("🗺 Карта", "ng:menu:territory")]]
    pairs: list[tuple[str, str]] = []
    for key, data in TERRITORY_DEFS.items():
        pairs.append((f"🧭 {str(data['name'])[:18]}", f"ng4:terr:{key}"))
    for i in range(0, len(pairs), 2):
        rows.append(pairs[i:i + 2])
    rows.append(leaf_back("world"))
    return kb(rows)


def government_menu() -> InlineKeyboardMarkup:
    return kb([
        [("🏯 Статус", "ng:menu:government"), ("🗳 Выборы", "ng4:gov:election")],
        [("🏗 Проекты", "ng4:gov:project"), ("💰 Казна и налог", "ng4:gov:treasury")],
        leaf_back("world"),
    ])


def newspaper_menu() -> InlineKeyboardMarkup:
    return kb([
        [("📰 Обновить газету", "ng:menu:newspaper"), ("📜 Летопись", "ng4:news:chronicle")],
        [("📡 Пульс мира", "ng:menu:mmo3")],
        leaf_back("world"),
    ])


def mmo4_menu() -> InlineKeyboardMarkup:
    return kb([
        [("🧭 Оперативная сводка", "ng4:mmo:digest"), ("⚔️ Готовность", "ng4:profile:ready")],
        [("💰 Экономика", "ng4:economy:overview"), ("📡 Пульс мира", "ng:menu:mmo3")],
        [("🌍 Рейтинг", "ng4:mmo:ranking"), ("📰 Газета", "ng:menu:newspaper")],
        [("🏠 Главный центр", "ng:menu:home")],
    ])



def recommend_menu() -> InlineKeyboardMarkup:
    return kb([
        [("🔄 Обновить советы", "ng:menu:recommend"), ("📜 Центр миссий", "ng:menu:mission")],
        [("🥷 Мой путь", "ng:menu:path"), ("🧭 Полная сводка", "ng4:mmo:digest")],
        leaf_back("activities"),
    ])


def pulse_menu() -> InlineKeyboardMarkup:
    return kb([
        [("📡 Обновить пульс", "ng:menu:mmo3"), ("🌪 События", "ng:menu:events")],
        [("🏯 Правительство", "ng:menu:government"), ("📰 Газета", "ng:menu:newspaper")],
        [("🌐 MMO V4", "ng:menu:mmo4")],
        leaf_back("world"),
    ])


def bijuu_menu() -> InlineKeyboardMarkup:
    return kb([
        [("🐾 Обновить список", "ng4:bijuu:status"), ("🔵 Тренировка биджу", "ng4:bijuu:train")],
        [("📡 Пульс мира", "ng:menu:mmo3")],
        leaf_back("world"),
    ])

def economy_menu() -> InlineKeyboardMarkup:
    return kb([
        [("💰 Обзор", "ng4:economy:overview"), ("🏪 Рынок", "ng4:economy:market")],
        [("🔨 Крафт", "ng4:economy:craft"), ("📜 Контракты", "ng4:economy:contracts")],
        [("🏦 Банк", "ng4:economy:bank"), ("🌑 Чёрный рынок", "ng4:economy:black")],
        leaf_back("economy"),
    ])


def craft_menu() -> InlineKeyboardMarkup:
    rows: list[list[tuple[str, str]]] = []
    pairs = [(f"🔨 {key[:20]}", f"ng4:craft:{key}") for key in CRAFT_RECIPES]
    for i in range(0, len(pairs), 2):
        rows.append(pairs[i:i + 2])
    rows.append([("⬅️ Экономика", "ng:hub:economy"), ("🏠 Центр", "ng:menu:home")])
    return kb(rows)


def growth_menu() -> InlineKeyboardMarkup:
    return kb([
        [("🥋 Тренировка", "ng4:growth:train"), ("🎖 Экзамен", "ng4:growth:exam")],
        [("👤 Наставник", "ng4:growth:mentor"), ("🛠 Профессия", "ng4:growth:profession")],
        [("🧪 Исследование", "ng4:growth:research"), ("📖 Наследие", "ng4:growth:legacy")],
        leaf_back("growth"),
    ])
