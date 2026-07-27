from __future__ import annotations

import random
from pathlib import Path
from typing import Any


CAPTCHA_DIR = Path(__file__).resolve().parent / "static" / "captcha"

CAPTCHA_TEMPLATES: tuple[dict[str, Any], ...] = (
    {"key": "sun", "answer": "☀️", "label": "солнце", "category": "nature", "options": ["🌙", "☀️", "❄️", "🌧️", "🌳", "🚗", "🍎", "☕", "🐱"]},
    {"key": "snow", "answer": "❄️", "label": "снег", "category": "nature", "options": ["🔥", "🌊", "❄️", "☀️", "☕", "🌙", "🌳", "🚲", "🍋"]},
    {"key": "rain", "answer": "🌧️", "label": "дождь", "category": "nature", "options": ["☀️", "🌧️", "❄️", "🌙", "🌊", "🌳", "☕", "🚗", "🐱"]},
    {"key": "tree", "answer": "🌳", "label": "дерево", "category": "nature", "options": ["🌳", "🌵", "🍎", "🌙", "🚗", "🐱", "☕", "❄️", "🏠"]},
    {"key": "moon", "answer": "🌙", "label": "луна", "category": "nature", "options": ["☀️", "🌙", "⭐", "🌧️", "🌳", "🚗", "🍎", "☕", "🐱"]},
    {"key": "coffee", "answer": "☕", "label": "кофе", "category": "food", "options": ["🍎", "☕", "🍕", "🥕", "🍋", "🍰", "🥛", "🍞", "🍇"]},
    {"key": "apple", "answer": "🍎", "label": "яблоко", "category": "food", "options": ["🍋", "🍎", "🍇", "🍞", "☕", "🥕", "🍕", "🥛", "🍰"]},
    {"key": "car", "answer": "🚗", "label": "автомобиль", "category": "objects", "options": ["🚲", "✈️", "🚗", "🚂", "🚢", "🛴", "🏠", "🌳", "🐕"]},
    {"key": "cat", "answer": "🐱", "label": "кошка", "category": "animals", "options": ["🐶", "🐱", "🐭", "🐰", "🦊", "🐻", "🐼", "🐸", "🐵"]},
)


def select_captcha(image_set: str = "random") -> dict[str, Any]:
    pool = list(CAPTCHA_TEMPLATES)
    if image_set and image_set != "random":
        filtered = [item for item in pool if item["category"] == image_set]
        if filtered:
            pool = filtered
    selected = dict(random.choice(pool))
    options = list(selected["options"])
    random.shuffle(options)
    selected["options"] = options
    selected["path"] = CAPTCHA_DIR / f"{selected['key']}.png"
    return selected


def captcha_by_key(key: str) -> dict[str, Any] | None:
    for item in CAPTCHA_TEMPLATES:
        if item["key"] == key:
            result = dict(item)
            result["path"] = CAPTCHA_DIR / f"{key}.png"
            return result
    return None
