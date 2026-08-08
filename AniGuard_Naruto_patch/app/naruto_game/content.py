from __future__ import annotations

from dataclasses import dataclass
from typing import Final


@dataclass(frozen=True, slots=True)
class TechniqueDef:
    key: str
    name: str
    element: str | None
    kind: str
    rank: str
    chakra: int
    power: int
    accuracy: float = 0.95
    cooldown: int = 0
    status: str | None = None
    status_chance: float = 0.0
    description: str = ""


@dataclass(frozen=True, slots=True)
class CardDef:
    key: str
    name: str
    rarity: str
    hp: int
    attack: int
    defense: int
    speed: int
    chakra: int
    tags: tuple[str, ...] = ()


VILLAGES: Final = {
    "konoha": {"name": "🍃 Коноха", "bonus": "technique_xp", "value": 0.05},
    "suna": {"name": "🏜 Сунагакуре", "bonus": "evasion", "value": 0.05},
    "kiri": {"name": "🌊 Киригакуре", "bonus": "crit", "value": 0.05},
    "kumo": {"name": "⚡ Кумогакуре", "bonus": "speed", "value": 0.05},
    "iwa": {"name": "🪨 Ивагакуре", "bonus": "defense", "value": 0.05},
}

ELEMENTS: Final = {
    "fire": "🔥 Огонь",
    "water": "💧 Вода",
    "lightning": "⚡ Молния",
    "wind": "🌪 Ветер",
    "earth": "🪨 Земля",
}

ELEMENT_ADVANTAGE: Final = {
    "fire": "wind",
    "wind": "lightning",
    "lightning": "earth",
    "earth": "water",
    "water": "fire",
}

BLOODLINES: Final = {
    "none": {"name": "Без великого клана", "rarity": "common", "weight": 38},
    "inuzuka": {"name": "Инузука", "rarity": "common", "weight": 9},
    "aburame": {"name": "Абураме", "rarity": "common", "weight": 8},
    "akimichi": {"name": "Акимичи", "rarity": "common", "weight": 8},
    "nara": {"name": "Нара", "rarity": "common", "weight": 7},
    "yamanaka": {"name": "Яманака", "rarity": "common", "weight": 6},
    "hyuga": {"name": "Хьюга", "rarity": "rare", "weight": 5},
    "uzumaki": {"name": "Узумаки", "rarity": "rare", "weight": 5},
    "hozuki": {"name": "Хозуки", "rarity": "rare", "weight": 4},
    "uchiha": {"name": "Учиха", "rarity": "epic", "weight": 3},
    "kaguya": {"name": "Кагуя", "rarity": "epic", "weight": 2},
    "senju": {"name": "Сенджу", "rarity": "legendary", "weight": 1.5},
    "otsutsuki": {"name": "Ооцуцуки", "rarity": "mythic", "weight": 0.5},
}

RANKS: Final = [
    ("academy", "🎓 Ученик Академии", 1),
    ("genin", "🥷 Генин", 5),
    ("chunin", "🎖 Чунин", 20),
    ("tokubetsu", "🛡 Токубецу Джонин", 32),
    ("jonin", "⚔️ Джонин", 40),
    ("anbu", "🎭 АНБУ", 55),
    ("kage", "👑 Каге", 75),
]

RANK_REQUIREMENTS: Final = {
    "genin": {"level": 5, "missions": 5, "reputation": 0},
    "chunin": {"level": 20, "missions": 20, "reputation": 250},
    "tokubetsu": {"level": 32, "missions": 45, "reputation": 700},
    "jonin": {"level": 40, "missions": 70, "reputation": 1000, "pvp_wins": 20},
    "anbu": {"level": 55, "missions": 100, "reputation": 1800, "pvp_wins": 40},
    "kage": {"level": 75, "missions": 180, "reputation": 5000, "pvp_wins": 75},
}

TECHNIQUES: Final[dict[str, TechniqueDef]] = {
    "basic_strike": TechniqueDef("basic_strike", "🥋 Тайдзюцу: удар", None, "taijutsu", "E", 0, 52, 0.98),
    "substitution": TechniqueDef("substitution", "🪵 Техника замены", None, "utility", "E", 25, 0, 1.0, 3, "dodge", 1.0),
    "clone": TechniqueDef("clone", "👥 Техника клонирования", None, "utility", "D", 35, 0, 1.0, 2, "evasion", 1.0),
    "fireball": TechniqueDef("fireball", "🔥 Катон: Огненный шар", "fire", "ninjutsu", "C", 55, 115, 0.92, 0, "burn", 0.22),
    "great_fireball": TechniqueDef("great_fireball", "🔥 Катон: Великий огненный шар", "fire", "ninjutsu", "B", 95, 205, 0.90, 1, "burn", 0.30),
    "water_dragon": TechniqueDef("water_dragon", "💧 Суйтон: Водяной дракон", "water", "ninjutsu", "B", 100, 210, 0.90, 1, "slow", 0.20),
    "lightning_spear": TechniqueDef("lightning_spear", "⚡ Райтон: Копьё молнии", "lightning", "ninjutsu", "B", 100, 220, 0.88, 1, "paralyze", 0.18),
    "wind_blade": TechniqueDef("wind_blade", "🌪 Футон: Лезвие ветра", "wind", "ninjutsu", "B", 90, 200, 0.94, 0, "bleed", 0.18),
    "earth_wall": TechniqueDef("earth_wall", "🪨 Дотон: Земляная стена", "earth", "defense", "C", 65, 0, 1.0, 2, "guard", 1.0),
    "rasengan": TechniqueDef("rasengan", "🌀 Расенган", None, "ninjutsu", "A", 120, 310, 0.95, 2, "stun", 0.14),
    "chidori": TechniqueDef("chidori", "⚡ Чидори", "lightning", "ninjutsu", "A", 130, 330, 0.90, 2, "paralyze", 0.22),
    "shadow_bind": TechniqueDef("shadow_bind", "🌑 Техника теневого подражания", None, "control", "B", 80, 70, 0.90, 2, "stun", 0.45),
    "mystic_palm": TechniqueDef("mystic_palm", "💚 Мистическая ладонь", None, "medical", "B", 90, -240, 1.0, 2),
    "gentle_fist": TechniqueDef("gentle_fist", "☯️ Мягкий кулак", None, "taijutsu", "B", 55, 170, 0.96, 0, "chakra_lock", 0.22),
    "sixty_four_palms": TechniqueDef("sixty_four_palms", "☯️ 64 ладони", None, "taijutsu", "A", 125, 305, 0.94, 3, "chakra_lock", 0.48),
    "amaterasu": TechniqueDef("amaterasu", "🔥 Аматерасу", "fire", "dojutsu", "S", 235, 410, 0.96, 4, "black_flame", 0.75),
    "kamui": TechniqueDef("kamui", "🌑 Камуи", None, "dojutsu", "S", 210, 360, 0.90, 4, "phase", 0.80),
    "tsukuyomi": TechniqueDef("tsukuyomi", "👁 Цукуёми", None, "genjutsu", "S", 220, 210, 0.92, 5, "stun", 0.80),
    "susanoo": TechniqueDef("susanoo", "🟣 Сусаноо", None, "dojutsu", "S", 190, 180, 1.0, 3, "susanoo", 1.0),
    "rasenshuriken": TechniqueDef("rasenshuriken", "🌪 Расенсюрикен", "wind", "ninjutsu", "S", 300, 540, 0.86, 4, "bleed", 0.60),
    "sage_art": TechniqueDef("sage_art", "🐸 Искусство Мудреца", None, "senjutsu", "S", 180, 260, 0.98, 4, "sage", 1.0),
    "shinra_tensei": TechniqueDef("shinra_tensei", "🌑 Шинра Тенсей", None, "dojutsu", "S", 260, 470, 0.98, 4, "stun", 0.38),
    "chibaku_tensei": TechniqueDef("chibaku_tensei", "🌒 Чибаку Тенсей", None, "dojutsu", "SS", 420, 720, 0.88, 7, "stun", 0.70),
}

STARTING_TECHNIQUES: Final = ("basic_strike", "substitution", "clone")

TECHNIQUE_UNLOCKS: Final = {
    "fireball": {"level": 6, "element": "fire", "ryo": 1200},
    "water_dragon": {"level": 14, "element": "water", "ryo": 3500},
    "lightning_spear": {"level": 14, "element": "lightning", "ryo": 3500},
    "wind_blade": {"level": 14, "element": "wind", "ryo": 3500},
    "earth_wall": {"level": 10, "element": "earth", "ryo": 2200},
    "rasengan": {"level": 24, "mentor": "jiraiya", "ryo": 9000},
    "chidori": {"level": 24, "element": "lightning", "ryo": 9000},
    "shadow_bind": {"level": 18, "bloodline": "nara", "ryo": 5000},
    "mystic_palm": {"level": 18, "profession": "medic", "ryo": 5000},
    "gentle_fist": {"level": 12, "bloodline": "hyuga", "ryo": 3200},
    "sixty_four_palms": {"level": 32, "bloodline": "hyuga", "ryo": 11000},
    "rasenshuriken": {"level": 48, "element": "wind", "requires": "rasengan", "ryo": 30000},
}

CARDS: Final[dict[str, CardDef]] = {
    "iruka": CardDef("iruka", "Ирука", "B", 300, 58, 60, 55, 240, ("konoha", "teacher")),
    "kiba": CardDef("kiba", "Киба", "B", 360, 72, 62, 82, 250, ("konoha", "inuzuka")),
    "shikamaru": CardDef("shikamaru", "Шикамару", "A", 390, 78, 70, 70, 330, ("konoha", "nara", "team10")),
    "sakura": CardDef("sakura", "Сакура", "A", 430, 90, 78, 75, 360, ("konoha", "team7", "medical")),
    "naruto_genin": CardDef("naruto_genin", "Наруто — Генин", "A", 470, 92, 75, 88, 500, ("konoha", "team7", "uzumaki")),
    "zabuza": CardDef("zabuza", "Забуза", "S", 620, 135, 115, 100, 520, ("kiri", "nukenin")),
    "kakashi": CardDef("kakashi", "Какаши", "S", 650, 145, 120, 135, 620, ("konoha", "team7", "sharingan")),
    "deidara": CardDef("deidara", "Дейдара", "SS", 760, 185, 125, 150, 740, ("akatsuki", "iwa")),
    "sasori": CardDef("sasori", "Сасори", "SS", 800, 180, 150, 125, 700, ("akatsuki", "suna")),
    "itachi": CardDef("itachi", "Итачи", "SSS", 920, 235, 185, 210, 980, ("akatsuki", "uchiha", "sharingan")),
    "pain": CardDef("pain", "Пейн", "SSS", 1050, 255, 210, 185, 1150, ("akatsuki", "rinnegan")),
    "naruto_sage": CardDef("naruto_sage", "Наруто — Режим Мудреца", "SSS", 1080, 260, 205, 225, 1200, ("konoha", "uzumaki", "sage")),
    "obito": CardDef("obito", "Обито", "SSS", 1100, 270, 220, 235, 1250, ("uchiha", "akatsuki", "sharingan")),
    "madara": CardDef("madara", "Мадара", "Mythic", 1450, 360, 300, 285, 1750, ("uchiha", "rinnegan", "legend")),
    "hashirama": CardDef("hashirama", "Хаширама", "Mythic", 1520, 345, 330, 260, 1800, ("senju", "mokuton", "legend")),
    "kaguya": CardDef("kaguya", "Кагуя", "Mythic", 1700, 390, 345, 300, 2100, ("otsutsuki", "divine")),
}

RARITY_WEIGHTS: Final = {"B": 45.0, "A": 30.0, "S": 17.0, "SS": 6.0, "SSS": 1.8, "Mythic": 0.2}

ITEMS: Final = {
    "kunai": {"name": "🗡 Кунай", "type": "weapon", "price": 120, "attack": 8},
    "shuriken": {"name": "🪃 Сюрикен", "type": "weapon", "price": 100, "attack": 6},
    "explosive_tag": {"name": "💥 Взрывная печать", "type": "consumable", "price": 350, "power": 95},
    "medkit": {"name": "💊 Медицинский набор", "type": "consumable", "price": 500, "heal": 250},
    "chakra_pill": {"name": "🔵 Пилюля чакры", "type": "consumable", "price": 650, "chakra": 180},
    "anbu_mask": {"name": "🎭 Маска АНБУ", "type": "cosmetic", "price": 15000},
    "akatsuki_cloak": {"name": "☁️ Плащ Акацуки", "type": "armor", "price": 28000, "defense": 45},
    "gunbai": {"name": "🌑 Гунбай", "type": "weapon", "price": 250000, "attack": 165, "unique": True},
    "samehada": {"name": "🦈 Самехада", "type": "weapon", "price": 300000, "attack": 180, "chakra_steal": 0.08, "unique": True},
    "chakra_crystal": {"name": "💎 Кристалл чакры", "type": "material", "price": 8000},
    "metal": {"name": "⛓ Металл", "type": "material", "price": 70},
    "cloth": {"name": "🧵 Ткань", "type": "material", "price": 45},
    "seal_paper": {"name": "📄 Бумага для печатей", "type": "material", "price": 55},
    "herb": {"name": "🌿 Лечебная трава", "type": "material", "price": 35},
    "poison_gland": {"name": "☠️ Ядовитая железа", "type": "material", "price": 900},
    "river_fish": {"name": "🐟 Речная рыба", "type": "material", "price": 90},
    "rare_fish": {"name": "🐠 Редкая рыба", "type": "material", "price": 650},
    "ramen": {"name": "🍜 Рамен", "type": "food", "price": 850, "energy": 20},
}

CRAFT_RECIPES: Final = {
    "explosive_tag": {"needs": {"seal_paper": 2, "metal": 1}, "ryo": 80},
    "medkit": {"needs": {"herb": 5, "cloth": 1}, "ryo": 120},
    "kunai": {"needs": {"metal": 3, "cloth": 1}, "ryo": 60},
    "chakra_pill": {"needs": {"herb": 3, "chakra_crystal": 1}, "ryo": 300},
}

MISSIONS: Final = {
    "cat": {"name": "🐈 Найти пропавшую кошку", "rank": "D", "level": 1, "power": 80, "ryo": (120, 300), "xp": (80, 150), "rep": 3},
    "herbs": {"name": "🌿 Сбор лечебных трав", "rank": "D", "level": 2, "power": 110, "ryo": (150, 360), "xp": (90, 180), "rep": 4},
    "escort": {"name": "🚚 Сопровождение торговца", "rank": "C", "level": 6, "power": 320, "ryo": (500, 1400), "xp": (250, 480), "rep": 12},
    "bandits": {"name": "⚔️ Лагерь разбойников", "rank": "C", "level": 8, "power": 450, "ryo": (700, 1800), "xp": (330, 600), "rep": 16},
    "spy": {"name": "🕵️ Найти вражеского разведчика", "rank": "B", "level": 16, "power": 900, "ryo": (1800, 4800), "xp": (650, 1100), "rep": 35},
    "nukenin": {"name": "📕 Охота на нукенина", "rank": "B", "level": 20, "power": 1200, "ryo": (2500, 6200), "xp": (850, 1450), "rep": 50},
    "secret_scroll": {"name": "📜 Вернуть секретный свиток", "rank": "A", "level": 34, "power": 2400, "ryo": (6500, 14000), "xp": (1600, 2800), "rep": 90},
    "akatsuki_trace": {"name": "☁️ След Акацуки", "rank": "A", "level": 42, "power": 3300, "ryo": (8500, 18000), "xp": (2200, 3600), "rep": 120},
    "village_defense": {"name": "🏯 Защита деревни", "rank": "S", "level": 58, "power": 5600, "ryo": (18000, 52000), "xp": (4200, 7500), "rep": 220},
}

BOSSES: Final = {
    "rogue": {"name": "🥷 Нукенин B-ранга", "level": 8, "hp": 950, "chakra": 380, "attack": 92, "defense": 60, "speed": 62, "element": "wind", "xp": 420, "ryo": 750},
    "zabuza": {"name": "🌫 Забуза", "level": 18, "hp": 2200, "chakra": 900, "attack": 155, "defense": 115, "speed": 105, "element": "water", "xp": 1600, "ryo": 4200},
    "deidara": {"name": "💣 Дейдара", "level": 40, "hp": 5200, "chakra": 2100, "attack": 285, "defense": 180, "speed": 225, "element": "earth", "xp": 5200, "ryo": 15000},
    "sasori": {"name": "🦂 Сасори", "level": 42, "hp": 5800, "chakra": 1800, "attack": 270, "defense": 225, "speed": 190, "element": None, "xp": 5600, "ryo": 16000},
    "pain": {"name": "☁️ Пейн", "level": 58, "hp": 10500, "chakra": 5200, "attack": 410, "defense": 315, "speed": 300, "element": None, "xp": 12500, "ryo": 42000},
    "obito": {"name": "🌑 Обито", "level": 68, "hp": 14500, "chakra": 6800, "attack": 510, "defense": 390, "speed": 410, "element": "fire", "xp": 19000, "ryo": 65000},
    "madara": {"name": "🔥 Мадара", "level": 82, "hp": 23500, "chakra": 10500, "attack": 700, "defense": 530, "speed": 520, "element": "fire", "xp": 35000, "ryo": 120000},
    "kaguya": {"name": "🌌 Кагуя", "level": 95, "hp": 34000, "chakra": 18000, "attack": 900, "defense": 690, "speed": 640, "element": None, "xp": 60000, "ryo": 220000},
    "zero": {"name": "🧬 Проект ZERO", "level": 100, "hp": 40000, "chakra": 22000, "attack": 980, "defense": 760, "speed": 700, "element": None, "xp": 80000, "ryo": 300000},
}

WORLD_RAIDS: Final = {
    "shukaku": {"name": "🐾 Шукаку", "max_hp": 25_000_000, "reward": 4000},
    "pain": {"name": "☁️ Вторжение Пейна", "max_hp": 50_000_000, "reward": 9000},
    "juubi": {"name": "👹 Десятихвостый", "max_hp": 250_000_000, "reward": 22000},
    "madara": {"name": "🔥 Мадара — Джинчурики", "max_hp": 1_000_000_000, "reward": 50000},
}

STORY_CHAPTERS: Final = {
    1: {"title": "Тень над деревней", "boss": "rogue", "min_level": 5, "reward": 700},
    2: {"title": "Запрещённая лаборатория", "boss": "zabuza", "min_level": 16, "reward": 1800},
    3: {"title": "Экзамен на Чунина", "boss": "rogue", "min_level": 20, "reward": 2600},
    4: {"title": "Падение деревни", "boss": "zabuza", "min_level": 25, "reward": 3800},
    5: {"title": "Исчезновение товарища", "boss": "rogue", "min_level": 30, "reward": 5000},
    6: {"title": "Годы тренировок", "boss": "zabuza", "min_level": 34, "reward": 6500},
    7: {"title": "Возвращение", "boss": "deidara", "min_level": 40, "reward": 8200},
    8: {"title": "Охота на джинчурики", "boss": "deidara", "min_level": 42, "reward": 10000},
    9: {"title": "Бессмертные", "boss": "sasori", "min_level": 45, "reward": 12000},
    10: {"title": "Правда об Учиха", "boss": "sasori", "min_level": 48, "reward": 14500},
    11: {"title": "Нападение Пейна", "boss": "pain", "min_level": 58, "reward": 22000},
    12: {"title": "Совет Пяти Каге", "boss": "pain", "min_level": 62, "reward": 25000},
    13: {"title": "Подготовка к войне", "boss": "obito", "min_level": 66, "reward": 30000},
    14: {"title": "Эдо Тенсей", "boss": "obito", "min_level": 70, "reward": 34000},
    15: {"title": "Обито", "boss": "obito", "min_level": 72, "reward": 40000},
    16: {"title": "Мадара", "boss": "madara", "min_level": 82, "reward": 60000},
    17: {"title": "Бесконечное Цукуёми", "boss": "madara", "min_level": 86, "reward": 70000},
    18: {"title": "Кагуя", "boss": "kaguya", "min_level": 92, "reward": 90000},
    19: {"title": "Охота на кровь", "boss": "zero", "min_level": 94, "reward": 100000},
    20: {"title": "Падение Пяти Деревень", "boss": "zero", "min_level": 96, "reward": 120000},
    21: {"title": "Искусственный бог", "boss": "zero", "min_level": 98, "reward": 150000},
    22: {"title": "Раскол Чёрной Печати", "boss": "zero", "min_level": 100, "reward": 200000},
}

PROFESSIONS: Final = {
    "medic": {"name": "💚 Медик", "bonus": "healing"},
    "smith": {"name": "🔨 Кузнец", "bonus": "craft"},
    "sealer": {"name": "📜 Мастер печатей", "bonus": "sealing"},
    "hunter": {"name": "🎯 Охотник", "bonus": "bingo"},
    "scout": {"name": "🕵️ Разведчик", "bonus": "explore"},
    "herbalist": {"name": "🌿 Травник", "bonus": "gather"},
    "researcher": {"name": "🧪 Исследователь", "bonus": "rare_find"},
    "cook": {"name": "🍜 Повар", "bonus": "food"},
}

SUMMONS: Final = {
    "toads": "🐸 Жабы",
    "snakes": "🐍 Змеи",
    "slugs": "🐌 Слизни",
    "ninken": "🐕 Нинкены",
    "hawks": "🦅 Ястребы",
}

BIJU: Final = {
    "shukaku": "🐾 Шукаку",
    "matatabi": "🔥 Мататаби",
    "isobu": "🐢 Исобу",
    "son_goku": "🐒 Сон Гоку",
    "kokuo": "🐎 Кокуо",
    "saiken": "🐌 Сайкен",
    "chomei": "🪲 Чомей",
    "gyuki": "🐙 Гьюки",
    "kurama": "🦊 Курама",
}

ACHIEVEMENTS: Final = {
    "first_mission": "Первый шаг",
    "genin": "Настоящий шиноби",
    "first_pvp": "Первая дуэль",
    "collector": "Коллекционер",
    "boss_hunter": "Охотник на легенды",
    "nukenin": "Путь тьмы",
    "millionaire": "Миллионер рё",
    "chapter_22": "Легенда новой эпохи",
}

TITLES: Final = {
    "hero": "🔥 Герой деревни",
    "shadow": "🌑 Тень деревни",
    "champion": "🏆 Непобеждённый",
    "akatsuki_hunter": "☁️ Охотник на Акацуки",
    "wanted": "☠️ Преступник S-класса",
    "legend": "⚔️ Легенда войны",
}

WORLD_EVENTS: Final = (
    "☁️ Разведчики заметили след Акацуки — награды за A-ранг увеличены.",
    "🌿 В Стране Огня богатый урожай трав — лечение стало доступнее.",
    "⚔️ На границе растёт напряжение между великими деревнями.",
    "📕 Кири объявила охоту на нукенинов.",
    "💎 В Стране Земли найдено новое месторождение кристаллов чакры.",
    "🌧 В Амегакуре замечена аномально мощная чакра.",
    "🏯 Совет Каге просит деревни готовить ресурсы к возможной войне.",
)
