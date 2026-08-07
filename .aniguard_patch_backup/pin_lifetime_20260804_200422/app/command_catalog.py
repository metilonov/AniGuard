from __future__ import annotations

import re
import unicodedata
from copy import deepcopy
from typing import Any, Iterable

from app.extra_moderation_commands import EXTRA_MODERATION_COMMANDS, EXTRA_MODE_PATCHES


def _cmd(
    key: str,
    name: str,
    trigger: str,
    description: str,
    action: str,
    *,
    aliases: Iterable[str] = (),
    category: str = "Обычные команды",
    premium: bool = False,
    duration: int | None = None,
    amount: int | None = None,
    special: str | None = None,
    target: bool | None = None,
    response: str = "✅ Команда выполнена.",
    ordinary_response: str | None = None,
    naruto_response: str | None = None,
    patch: dict[str, Any] | None = None,
) -> tuple[str, dict[str, Any]]:
    return key, {
        "name": name,
        "trigger": trigger,
        "aliases": list(dict.fromkeys([*aliases])),
        "description": description,
        "action": action,
        "category": category,
        "premium": premium,
        "fixed_duration_seconds": duration,
        "fixed_amount": amount,
        "special": special,
        "target_required": target,
        "response": response,
        "ordinary_response": ordinary_response,
        "naruto_response": naruto_response,
        "settings_patch": patch or {},
    }


# Telegram actions shared by ordinary and Naruto-styled aliases.
_ENTRIES: list[tuple[str, dict[str, Any]]] = [
    _cmd("warn", "Предупреждение", "варн", "Выдать предупреждение", "warn", aliases=("warn", "пред", "предупреждение"), target=True, response="Варн: {user}."),
    _cmd("unwarn", "Снять предупреждение", "снять варн", "Снять одно предупреждение", "unwarn", aliases=("unwarn", "анварн", "снять пред", "снять предупреждение"), target=True, response="Варн снят: {user}."),
    _cmd("mute", "Мут", "мут", "Запретить сообщения", "mute", aliases=("mute", "замутить"), target=True, response="Мут: {user} — {duration}."),
    _cmd("unmute", "Снять мут", "размут", "Вернуть возможность писать", "unmute", aliases=("unmute", "анмут", "снять мут"), target=True, response="Мут снят: {user}."),
    _cmd("ban", "Бан", "бан", "Заблокировать пользователя", "ban", aliases=("ban", "забанить"), target=True, response="Бан: {user}."),
    _cmd("unban", "Разбан", "разбан", "Снять блокировку", "unban", aliases=("unban", "анбан", "снять бан"), target=True, response="Разбан: {user}."),
    _cmd("kick", "Кик", "кик", "Удалить из беседы", "kick", aliases=("kick", "выгнать", "исключить"), target=True, response="Кик: {user}."),
    _cmd("quarantine", "Карантин", "карантин", "Безопасная изоляция пользователя", "quarantine", aliases=("изоляция",), premium=True, target=True, response="Карантин: {user} — {duration}."),
    _cmd("unquarantine", "Снять карантин", "снять карантин", "Вернуть обычные права", "unquarantine", aliases=("разкарантин",), target=True, response="Карантин снят: {user}."),
    _cmd("purge", "Очистка", "очистить", "Удалить последние сообщения", "purge", aliases=("purge", "чистка"), amount=10, target=False, response="Удалено: {reason}."),
    _cmd("slow", "Медленный режим", "медленный режим", "Установить задержку сообщений", "slow", aliases=("slow", "слоумо"), amount=15, target=False, response="Медленный режим включён."),
    _cmd("lock", "Закрыть чат", "закрыть чат", "Запретить сообщения участникам", "lock", aliases=("lock", "лок"), target=False, response="Чат закрыт."),
    _cmd("unlock", "Открыть чат", "открыть чат", "Вернуть отправку сообщений", "unlock", aliases=("unlock", "анлок"), target=False, response="Чат открыт."),
    _cmd("restrict_media", "Запрет медиа", "запретить медиа", "Запретить фото, видео и файлы", "restrict_media", aliases=("media ban", "медиа бан"), target=True, response="Медиа запрещены: {user}."),
    _cmd("unrestrict_media", "Разрешить медиа", "разрешить медиа", "Снять запрет медиа", "unrestrict_media", aliases=("unmedia",), target=True, response="Медиа разрешены: {user}."),
    _cmd("restrict_links", "Запрет ссылок", "запретить ссылки", "Запретить ссылки пользователя", "restrict_links", aliases=("linksban", "линк бан"), target=True, response="Ссылки запрещены: {user}."),
    _cmd("unrestrict_links", "Разрешить ссылки", "разрешить ссылки", "Снять запрет ссылок", "unrestrict_links", aliases=("unlinksban",), target=True, response="Ссылки разрешены: {user}."),
    _cmd("restrict_commands", "Запрет команд", "запретить команды", "Запретить команды AniGuard", "restrict_commands", aliases=("commandsban",), target=True, response="Команды запрещены: {user}."),
    _cmd("unrestrict_commands", "Разрешить команды", "разрешить команды", "Снять запрет команд", "unrestrict_commands", aliases=("uncommandsban",), target=True, response="Команды разрешены: {user}."),
]

_ACTION_ICONS = {
    "warn": ("⚠️", "📜"),
    "unwarn": ("✅", "✨"),
    "mute": ("🔇", "🤐"),
    "unmute": ("🔊", "🕊"),
    "ban": ("⛔", "🌑"),
    "unban": ("♻️", "🍃"),
    "kick": ("🚪", "💨"),
    "quarantine": ("🛡", "🌀"),
    "unquarantine": ("✅", "✨"),
    "purge": ("🧹", "🌪"),
    "slow": ("🐢", "⏳"),
    "lock": ("🔒", "🟣"),
    "unlock": ("🔓", "🍃"),
    "restrict_media": ("🖼", "🔏"),
    "unrestrict_media": ("✅", "✨"),
    "restrict_links": ("🔗", "🔏"),
    "unrestrict_links": ("✅", "✨"),
    "restrict_commands": ("⌨️", "🔏"),
    "unrestrict_commands": ("✅", "✨"),
}


def _expanded_command_responses(number: int, name: str, description: str, action: str) -> tuple[str, str]:
    ordinary_icon, naruto_icon = _ACTION_ICONS.get(action, ("✅", "🍥"))
    ordinary = (
        f"{ordinary_icon} <b>Команда «{name}» выполнена</b>\n\n"
        f"📌 Результат: {description}\n"
        "👤 Пользователь: {user}\n"
        "🛡 Модератор: {admin}\n"
        "⏳ Срок: {duration}\n"
        "📝 Причина: {reason}\n"
        "📁 Дело: {case_code}\n"
        "🕒 Время: {datetime}\n\n"
        f"Идентификатор команды: N-{number:03d}. Действие зарегистрировано в журнале AniGuard."
    )
    naruto = (
        f"{naruto_icon} <b>Техника «{name}» активирована!</b>\n\n"
        f"📜 Эффект дзюцу: {description}\n"
        "🥷 Цель техники: {user}\n"
        "⚔️ Технику применил: {admin}\n"
        "⏳ Действие печати: {duration}\n"
        "🗯 Причина: {reason}\n"
        "📁 Свиток дела: {case_code}\n"
        "🕒 Печать времени: {datetime}\n\n"
        f"🍃 Протокол техники N-{number:03d} сохранён советом деревни AniGuard."
    )
    return ordinary, naruto


# 101–180. Advanced or destructive tools are Premium. Unsupported Telegram API
# operations are represented by safe, explicit handlers instead of pretending
# that IP bans, cross-platform bans or chat recreation are possible.
_ANIME = [
    (101,"Расен-сюрикен","расен_сюрикен","Бан и удаление доступных сообщений","ban",True,None,100,"ban_purge",True),
    (102,"Камуи-пространство","камуи_пространство","Изоляция пользователя на 2 часа","quarantine",True,7200,None,None,True),
    (103,"Инра Тенсей","инра_тенсей","Очистить последние 100 сообщений","purge",True,None,100,None,False),
    (104,"Зеркала Хаку","хаку_зеркала","Заморозить пользователя на 2 часа","mute",False,7200,None,None,True),
    (105,"Теневое удушение","дзюцу_теневого_удушения","Мут пользователя на 2 часа","mute",True,7200,None,None,True),
    (106,"Сенпо Расенган","сенпо_расенган","Постоянный бан; IP недоступен Telegram API","ban",True,0,None,None,True),
    (107,"Биджу-дама","биджу_дама","Удалить последние 100 сообщений","purge",True,None,100,None,False),
    (108,"Гудам-дама","гудам_дама","Очистить доступные сообщения и медиа","purge",True,None,100,None,False),
    (109,"Суд Риннегана","риннеган_суд","Создать голосование модераторов о бане","warn",True,None,None,"vote_ban",True),
    (110,"Изанаги","изанаги","Отменить последнее действие модерации","unwarn",True,None,None,"undo_last",False),
    (111,"Режим Наруто","режим_наруто","Включить усиленную защиту от спама","lock",False,None,None,"settings_patch",False),
    (112,"Режим Саске","режим_саске","Тихий мониторинг без автодействий","lock",True,None,None,"settings_patch",False),
    (113,"Режим Какаши","режим_какаши","Модерация только по жалобам","lock",False,None,None,"settings_patch",False),
    (114,"Режим Итачи","режим_итачи","Скрытые ответы и журнал действий","lock",True,None,None,"settings_patch",False),
    (115,"Режим Гаары","режим_гаары","Жёсткий фильтр и защита чата","lock",True,None,None,"settings_patch",False),
    (116,"Режим Орочимару","режим_орочимару","Собирать расширенную статистику нарушений","lock",True,None,None,"settings_patch",False),
    (117,"Режим Джирайи","режим_джирайи","Наблюдение и логи без наказаний","lock",False,None,None,"settings_patch",False),
    (118,"Режим Цунаде","режим_цунаде","Снять все активные муты","unmute",True,None,None,"unmute_all",False),
    (119,"Режим Мадары","режим_мадары","Максимально строгая автомодерация","lock",True,None,None,"settings_patch",False),
    (120,"Режим Боруто","режим_боруто","Мягкая защита для новичков","lock",False,None,None,"settings_patch",False),
    (121,"Сунагакуре","отправить_в_сунагакуре","Поместить пользователя в карантин","quarantine",True,3600,None,None,True),
    (122,"Киригакуре","отправить_в_киригакуре","Скрыть активность пользователя мутом","mute",True,7200,None,None,True),
    (123,"Кумогакуре","отправить_в_кумогакуре","Закрепить сообщение с жалобой","warn",False,None,None,"pin_reply",False),
    (124,"Ивагакуре","отправить_в_ивагакуре","Постоянный бан без срока","ban",True,0,None,None,True),
    (125,"Долина завершения","долина_завершения","Финальное предупреждение","warn",False,None,None,None,True),
    (126,"Лес смерти","лес_смерти","Карантин нарушителя на сутки","quarantine",True,86400,None,None,True),
    (127,"Академия Листа","академия_лист","Сбросить статус пользователя до новичка","warn",True,None,None,"reset_newcomer",True),
    (128,"Гора Хокаге","гора_хокаге","Отправить объявление администрации","lock",False,None,None,"announce",False),
    (129,"Деревня дождя","деревня_дождя","Открыть журнал жалоб","lock",True,None,None,"reports_summary",False),
    (130,"Чистая земля","чистая_земля","Показать архив удалений и действий","lock",True,None,None,"logs_summary",False),
    (131,"Свиток запретных дзюцу","свиток_запретных_дзюцу","Добавить слова в чёрный список","lock",True,None,None,"add_blocked_words",False),
    (132,"Свиток печати","свиток_печати","Добавить одно слово в фильтр","lock",False,None,None,"add_blocked_words",False),
    (133,"Кунай предупреждения","кунай_предупреждения","Быстро выдать предупреждение","warn",False,None,None,None,True),
    (134,"Сюрикен удаления","сюрикен_удаления","Удалить сообщение, на которое ответили","purge",False,None,None,"delete_reply",False),
    (135,"Взрывная печать","взрывная_печать","Удалить сообщение и выдать предупреждение","warn",False,None,None,"delete_warn",True),
    (136,"Дымовая бомба","дымовая_бомба","Удалить последние 10 сообщений","purge",False,None,10,None,False),
    (137,"Нить чакры","нить_чакры","Запретить пользователю команды","restrict_commands",True,3600,None,None,True),
    (138,"Нить кукловода","кукловод_нить","Ограничить пользователя одной активностью","restrict_commands",True,7200,None,None,True),
    (139,"Веер Учиха","веер_учиха","Отклонить жалобу по ID","lock",True,None,None,"reject_report",False),
    (140,"Таблетка чакры","таблетка_чакры","Добавить пользователя в белый список","lock",True,3600,None,"add_whitelist",True),
    (141,"Сбор Акацуки","акуцуки_сбор","Позвать модераторов","lock",False,None,None,"call_moderators",False),
    (142,"Охота Акацуки","акуцуки_охота","Показать связанные данные пользователя","lock",True,None,None,"unsupported_alts",True),
    (143,"Операция АНБУ","анбу_операция","Тихо заблокировать пользователя","ban",True,0,None,None,True),
    (144,"Корень АНБУ","анбу_корень","Показать историю нарушений пользователя","lock",True,None,None,"user_history",True),
    (145,"Корень Данзо","корень_данзо","Глобально заблокировать в AniGuard","ban",True,0,None,"global_block",True),
    (146,"Семь мечников","семь_мечников","Показать модераторов смены","lock",True,None,None,"call_moderators",False),
    (147,"Братство чакры","братство_чакры","Добавить пользователя в белый список","lock",True,None,None,"add_whitelist",True),
    (148,"Охотники-ниндзя","охотники_ниндзя","Показать пользователей с нарушениями","lock",True,None,None,"top_offenders",False),
    (149,"Жизнь биджу","жизнь_биджу","Снять бан с пользователя","unban",False,None,None,None,True),
    (150,"Печать биджу","печать_биджу","Пометить пользователя как опасного","lock",True,None,None,"mark_dangerous",True),
    (151,"Свиток статистики","свиток_статистики","Общая статистика нарушений","lock",False,None,None,"violation_stats",False),
    (152,"Рейтинг ниндзя","рейтинг_ниндзя","Топ нарушителей беседы","lock",False,None,None,"top_offenders",False),
    (153,"Миссии выполнены","миссии_выполнены","Статистика работы модераторов","lock",True,None,None,"moderator_stats",False),
    (154,"Отчёт Хокаге","отчет_хокаге","Еженедельный отчёт по чату","lock",True,None,None,"weekly_report",False),
    (155,"Хроники войны","хроники_войны","История банов и мутов","lock",True,None,None,"logs_summary",False),
    (156,"Свиток активности","свиток_активности","Топ пользователей по сообщениям","lock",False,None,None,"activity_stats",False),
    (157,"Детектор чакры","детектор_чакры","Проверить пользователя на бота","lock",False,None,None,"bot_check",True),
    (158,"Анализ дзюцу","анализ_дзюцу","Разобрать нарушения пользователя","lock",True,None,None,"user_history",True),
    (159,"Протокол АНБУ","протокол_анбу","Скрытый журнал для администраторов","lock",True,None,None,"logs_summary",False),
    (160,"Архив свитков","архив_свитков","Поиск по старым логам","lock",True,None,None,"search_logs",False),
    (161,"Даттебайо-пинг","даттебайо_пинг","Позвать известных активных участников","lock",False,None,None,"ping_members",False),
    (162,"Расенган-рулетка","расенган_рулетка","Случайный мут на 5 минут","mute",False,300,None,"random_mute",False),
    (163,"Экзамен на выживание","экзамен_на_выживание","Создать викторину в чате","lock",False,None,None,"quiz_poll",False),
    (164,"Призыв жабы","призыв_жабы","Напомнить через 10 минут","lock",False,None,None,"reminder",False),
    (165,"Ичираку-пауза","ичираку_пауза","Закрыть чат на 15 минут","lock",False,900,None,"lock_timed",False),
    (166,"Бег по деревьям","бег_по_деревьям","Показать конкурс активности","lock",False,None,None,"activity_stats",False),
    (167,"Белый змей-оракул","белый_змей_оракул","Случайное решение модерации","lock",False,None,None,"random_decision",False),
    (168,"Теневой клон-опрос","теневой_клон_опрос","Создать анонимный опрос","lock",False,None,None,"anonymous_poll",False),
    (169,"Печать удачи","печать_удачи","Снять случайный активный мут","unmute",False,None,None,"random_unmute",False),
    (170,"Воля огня","воля_огня_речь","Отправить мотивационное сообщение","lock",False,None,None,"motivation",False),
    (171,"Красная тревога Акацуки","красная_тревога_акуцуки","Полностью закрыть чат","lock",True,None,None,None,False),
    (172,"Эвакуация деревни","эвакуация_деревни","Удалить обычных участников после подтверждения","kick",True,None,None,"kick_all",False),
    (173,"Печать Четвёртого","печать_четвертого","Закрыть чат на 1 час","lock",True,3600,None,"lock_timed",False),
    (174,"Режим мудреца","режим_мудреца","Автобан новых участников","lock",True,None,None,"auto_ban_newcomers",False),
    (175,"Восемь врат","восемь_врат_открыты","Снять ограничения и открыть чат","unlock",True,None,None,"reset_restrictions",False),
    (176,"Муген-протокол","муген_протокол","Пересоздание чата недоступно Telegram API","lock",True,None,None,"unsupported_recreate",False),
    (177,"Печать смерти","печать_смерти_реаппера","Бан и глобальный чёрный список AniGuard","ban",True,0,None,"global_block",True),
    (178,"Защита клонирования","дзюцу_клонирования_защита","Включить антирейд и лимит входов","lock",True,None,None,"settings_patch",False),
    (179,"Фиолетовый барьер","барьер_четырех_фиолетовых","Закрыть чат на время расследования","lock",True,3600,None,"lock_timed",False),
    (180,"Седьмой путь","седьмой_путь","Сбросить настройки модерации","unlock",True,None,None,"reset_moderation",False),
]

_ANIME = EXTRA_MODERATION_COMMANDS + _ANIME

_MODE_PATCHES: dict[int, dict[str, Any]] = {
    111: {"anti_flood_enabled": True, "smart_spam_enabled": True, "duplicate_filter_enabled": True},
    112: {"auto_warn_enabled": False, "auto_cleanup_enabled": False, "reports_enabled": True},
    113: {"anti_flood_enabled": False, "link_filter_enabled": False, "reports_enabled": True},
    114: {"show_moderation_reason": False, "show_moderation_duration": False},
    115: {"anti_flood_enabled": True, "link_filter_enabled": True, "word_filter_enabled": True, "captcha_enabled": True, "warn_threshold": 2},
    116: {"premium_stats": True, "reports_enabled": True},
    117: {"auto_warn_enabled": False, "auto_cleanup_enabled": False, "premium_stats": True},
    119: {"anti_flood_enabled": True, "link_filter_enabled": True, "word_filter_enabled": True, "warn_threshold": 1},
    120: {"captcha_enabled": False, "warn_threshold": 5, "links_newbie_hours": 1},
    178: {"raid_lockdown_enabled": True, "raid_join_limit": 4, "raid_window_seconds": 30, "captcha_enabled": True},
}

_MODE_PATCHES.update(EXTRA_MODE_PATCHES)

for number, name, trigger, description, action, premium, duration, amount, special, target in _ANIME:
    aliases = [trigger.replace("_", " ")]
    if number == 114:
        aliases.extend(["режим_ита́чи"])
    if number == 118:
        aliases.extend(["режим_цунадэ", "режим цунадэ"])
    if number == 120:
        aliases.extend(["режим_борuto", "режим boruto"])
    if number == 154:
        aliases.extend(["отчёт_хокаге", "отчёт хокаге"])
    if number == 173:
        aliases.extend(["печать_четвёртого", "печать четвёртого"])
    if number == 177:
        aliases.extend(["печать_смерти_ре́аппера", "печать смерти реаппера"])
    if number == 179:
        aliases.extend(["барьер_четырёх_фиолетовых", "барьер четырёх фиолетовых"])
    ordinary_response, naruto_response = _expanded_command_responses(number, name, description, action)
    _ENTRIES.append(_cmd(
        f"anime_{number}",
        name,
        trigger,
        description,
        action,
        aliases=aliases,
        category="Premium-команды" if premium else "Обычные команды",
        premium=premium,
        duration=duration,
        amount=amount,
        special=special,
        target=target,
        response=ordinary_response,
        ordinary_response=ordinary_response,
        naruto_response=naruto_response,
        patch=_MODE_PATCHES.get(number),
    ))


BUILTIN_COMMANDS: dict[str, dict[str, Any]] = dict(_ENTRIES)


def default_builtin_commands() -> dict[str, dict[str, Any]]:
    return deepcopy(BUILTIN_COMMANDS)


def _without_accents(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value)
    return "".join(char for char in decomposed if not unicodedata.combining(char)).replace("ё", "е").replace("Ё", "Е")


def _alias_pattern(alias: str) -> re.Pattern[str]:
    clean = _without_accents(alias).strip().lstrip("/")
    tokens = [token for token in re.split(r"[\s_-]+", clean) if token]
    body = r"[\s_-]+".join(re.escape(token) for token in tokens)
    return re.compile(rf"^\s*/?{body}(?:@[A-Za-z0-9_]+)?(?=$|\s)", re.IGNORECASE)


def command_aliases(command: dict[str, Any]) -> list[str]:
    values = [str(command.get("trigger") or "")]
    values.extend(str(item) for item in command.get("aliases") or [])
    return [value for value in dict.fromkeys(values) if value.strip()]


def match_builtin_command(text: str, commands: dict[str, dict[str, Any]] | None = None) -> tuple[str, dict[str, Any], str] | None:
    cleaned = _without_accents(text or "")
    catalog = commands or BUILTIN_COMMANDS
    candidates: list[tuple[int, str, dict[str, Any], re.Pattern[str]]] = []
    for key, command in catalog.items():
        for alias in command_aliases(command):
            pattern = _alias_pattern(alias)
            candidates.append((len(_without_accents(alias)), key, command, pattern))
    for _, key, command, pattern in sorted(candidates, key=lambda item: item[0], reverse=True):
        match = pattern.match(cleaned)
        if match:
            return key, command, cleaned[match.end():].strip()
    return None
