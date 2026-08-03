from __future__ import annotations

import html
import re
import secrets
from datetime import datetime, timezone
from typing import Any

from app.durations import format_duration_ru


STYLE_VARIABLES: list[dict[str, str]] = [
    {"name": "{admin}", "description": "Кликабельное имя администратора", "example": "Алексей"},
    {"name": "{admin_name}", "description": "Имя администратора без ссылки", "example": "Алексей"},
    {"name": "{admin_id}", "description": "Telegram ID администратора", "example": "123456789"},
    {"name": "{admin_username}", "description": "Username администратора", "example": "@alex"},
    {"name": "{admin_role}", "description": "Роль администратора в беседе", "example": "Модератор"},
    {"name": "{user}", "description": "Кликабельное имя пользователя", "example": "Дмитрий"},
    {"name": "{user_name}", "description": "Имя пользователя без ссылки", "example": "Дмитрий"},
    {"name": "{user_id}", "description": "Telegram ID пользователя", "example": "741852963"},
    {"name": "{username}", "description": "Username пользователя", "example": "@username"},
    {"name": "{user_role}", "description": "Роль пользователя", "example": "Участник"},
    {"name": "{command}", "description": "Название вызванной команды", "example": "/mute"},
    {"name": "{command_key}", "description": "Системный ключ конкретной команды", "example": "anime_101"},
    {"name": "{command_description}", "description": "Описание эффекта команды", "example": "Бан и удаление сообщений"},
    {"name": "{command_number}", "description": "Номер Naruto-команды", "example": "101"},
    {"name": "{action}", "description": "Системное название действия", "example": "mute"},
    {"name": "{action_title}", "description": "Название действия на русском", "example": "Ограничение сообщений"},
    {"name": "{duration}", "description": "Срок ограничения", "example": "1 день"},
    {"name": "{duration_seconds}", "description": "Срок в секундах", "example": "86400"},
    {"name": "{reason}", "description": "Причина действия", "example": "Оскорбление участников"},
    {"name": "{chat}", "description": "Название беседы", "example": "AniGuard Community"},
    {"name": "{chat_id}", "description": "ID беседы", "example": "-1001234567890"},
    {"name": "{warnings}", "description": "Текущее число предупреждений", "example": "2"},
    {"name": "{warning_limit}", "description": "Лимит предупреждений", "example": "3"},
    {"name": "{case_id}", "description": "Номер дела", "example": "1842"},
    {"name": "{case_code}", "description": "Код дела", "example": "AG-1842"},
    {"name": "{report_id}", "description": "Номер жалобы", "example": "AG-81"},
    {"name": "{appeal_id}", "description": "Номер апелляции", "example": "17"},
    {"name": "{message_id}", "description": "ID исходного сообщения", "example": "4821"},
    {"name": "{deleted_count}", "description": "Количество удалённых сообщений", "example": "25"},
    {"name": "{slow_seconds}", "description": "Задержка медленного режима", "example": "15"},
    {"name": "{status}", "description": "Итоговый статус", "example": "Активно"},
    {"name": "{date}", "description": "Дата выполнения", "example": "03.08.2026"},
    {"name": "{time}", "description": "Время выполнения", "example": "20:45"},
    {"name": "{datetime}", "description": "Дата и время", "example": "03.08.2026 20:45"},
    {"name": "{xp}", "description": "Начисленный игровой опыт", "example": "100"},
    {"name": "{coins}", "description": "Начисленные AniCoin", "example": "50"},
    {"name": "{text}", "description": "Аргументы или текст после команды", "example": "сообщение пользователя"},
    {"name": "{target}", "description": "Цель RP-команды", "example": "Мария"},
    {"name": "{actor}", "description": "Автор RP-команды", "example": "Алексей"},
    {"name": "{newline}", "description": "Перенос строки", "example": "↵"},
]

ACTION_TITLES: dict[str, str] = {
    "warn": "Предупреждение",
    "unwarn": "Снятие предупреждения",
    "mute": "Ограничение сообщений",
    "unmute": "Снятие ограничения сообщений",
    "ban": "Блокировка пользователя",
    "unban": "Снятие блокировки",
    "kick": "Исключение из беседы",
    "quarantine": "Карантин",
    "unquarantine": "Снятие карантина",
    "restrict_media": "Ограничение медиа",
    "unrestrict_media": "Снятие ограничения медиа",
    "restrict_links": "Ограничение ссылок",
    "unrestrict_links": "Снятие ограничения ссылок",
    "restrict_commands": "Ограничение команд",
    "unrestrict_commands": "Снятие ограничения команд",
    "purge": "Очистка сообщений",
    "slow": "Медленный режим",
    "lock": "Закрытие беседы",
    "unlock": "Открытие беседы",
    "report": "Регистрация жалобы",
    "appeal": "Регистрация апелляции",
    "case": "Открытие дела",
}


def _ordinary(action: str) -> str:
    title = ACTION_TITLES.get(action, action)
    if action == "warn":
        return "⚠️ <b>Предупреждение выдано</b>\n\n👤 Пользователь: {user}\n🛡 Модератор: {admin}\n📊 Предупреждения: {warnings} из {warning_limit}\n📝 Причина: {reason}\n📁 Дело: {case_code}\n🕒 Время: {datetime}\n\nСледующее нарушение может привести к автоматическому ограничению."
    if action == "unwarn":
        return "✅ <b>Предупреждение снято</b>\n\n👤 Пользователь: {user}\n🛡 Решение принял: {admin}\n📊 Осталось предупреждений: {warnings} из {warning_limit}\n📝 Основание: {reason}\n🕒 Время: {datetime}"
    if action in {"mute", "quarantine", "restrict_media", "restrict_links", "restrict_commands"}:
        labels = {
            "mute": ("🔇", "отправка сообщений"), "quarantine": ("🛡", "режим карантина"),
            "restrict_media": ("🖼", "отправка медиа и файлов"), "restrict_links": ("🔗", "отправка ссылок"),
            "restrict_commands": ("⌨️", "использование команд"),
        }
        emoji, scope = labels[action]
        return f"{emoji} <b>{title} применено</b>\n\n👤 Пользователь: {{user}}\n🛡 Модератор: {{admin}}\n⏳ Срок: {{duration}}\n🚫 Ограничено: {scope}\n📝 Причина: {{reason}}\n📁 Дело: {{case_code}}\n🕒 Начало: {{datetime}}\n\nОграничение будет снято автоматически после окончания срока."
    if action in {"unmute", "unquarantine", "unrestrict_media", "unrestrict_links", "unrestrict_commands", "unban"}:
        return f"✅ <b>{title} выполнено</b>\n\n👤 Пользователь: {{user}}\n🛡 Решение принял: {{admin}}\n📝 Основание: {{reason}}\n🕒 Время: {{datetime}}\n\nПрава пользователя восстановлены в пределах его текущей роли."
    if action == "ban":
        return "⛔ <b>Пользователь заблокирован</b>\n\n👤 Пользователь: {user}\n🛡 Администратор: {admin}\n⏳ Срок: {duration}\n📝 Причина: {reason}\n📁 Дело: {case_code}\n🕒 Время: {datetime}\n\nДо окончания блокировки пользователь не сможет вернуться в беседу."
    if action == "kick":
        return "🚪 <b>Пользователь исключён из беседы</b>\n\n👤 Пользователь: {user}\n🛡 Администратор: {admin}\n📝 Причина: {reason}\n📁 Дело: {case_code}\n🕒 Время: {datetime}\n\nПользователь сможет вернуться только по действующей ссылке или приглашению."
    if action == "purge":
        return "🧹 <b>Очистка завершена</b>\n\n🗑 Удалено сообщений: {deleted_count}\n🛡 Действие выполнил: {admin}\n💬 Беседа: {chat}\n🕒 Время: {datetime}\n\nОперация записана в журнал модерации."
    if action == "slow":
        return "⏱ <b>Медленный режим обновлён</b>\n\n💬 Беседа: {chat}\n⌛ Интервал: {slow_seconds} сек.\n🛡 Настройку изменил: {admin}\n🕒 Время: {datetime}\n\nУчастники смогут отправлять сообщения с указанной задержкой."
    if action == "lock":
        return "🔒 <b>Беседа временно закрыта</b>\n\n💬 Беседа: {chat}\n🛡 Режим включил: {admin}\n📝 Причина: {reason}\n🕒 Время: {datetime}\n\nОтправлять сообщения могут только представители администрации."
    if action == "unlock":
        return "🔓 <b>Беседа снова открыта</b>\n\n💬 Беседа: {chat}\n🛡 Режим снял: {admin}\n🕒 Время: {datetime}\n\nОбычный режим общения восстановлен."
    if action == "report":
        return "📨 <b>Жалоба зарегистрирована</b>\n\n🆔 Номер: {report_id}\n👤 Отправитель: {admin}\n🎯 Пользователь: {user}\n📝 Причина: {reason}\n🕒 Время: {datetime}\n\nЖалоба передана модераторам и будет объединена с повторными обращениями по тому же сообщению."
    if action == "appeal":
        return "📩 <b>Апелляция принята</b>\n\n🆔 Номер: {appeal_id}\n👤 Заявитель: {admin}\n📁 Дело: {case_code}\n📝 Обоснование: {reason}\n🕒 Время: {datetime}\n\nРешение примет независимый модератор, не выдававший исходное наказание."
    return f"✅ <b>{title} выполнено</b>\n\n👤 Пользователь: {{user}}\n🛡 Администратор: {{admin}}\n📝 Причина: {{reason}}\n🕒 Время: {{datetime}}"


def _naruto(action: str) -> str:
    if action == "warn":
        return "🍃 <b>Кунай предупреждения достиг цели!</b>\n\n🥷 Ниндзя: {user}\n⚔️ Решение вынес: {admin}\n📜 Метки нарушения: {warnings} из {warning_limit}\n🗯 Причина: {reason}\n📁 Свиток дела: {case_code}\n🕒 Печать времени: {datetime}\n\n🔥 Совет деревни предупреждает: следующая ошибка может активировать более строгую технику."
    if action == "unwarn":
        return "✨ <b>Печать предупреждения снята!</b>\n\n🥷 Ниндзя: {user}\n🛡 Печать снял: {admin}\n📜 Осталось меток: {warnings} из {warning_limit}\n🗯 Основание: {reason}\n🕒 Время: {datetime}\n\n🍃 Репутация шиноби получила шанс на восстановление."
    if action == "mute":
        return "🤐 <b>Дзюцу немоты применено!</b>\n\n🥷 Цель техники: {user}\n⚔️ Технику применил: {admin}\n⏳ Действие печати: {duration}\n🗯 Причина: {reason}\n📁 Протокол АНБУ: {case_code}\n🕒 Активация: {datetime}\n\n🔒 До разрушения печати цель не сможет отправлять сообщения."
    if action == "unmute":
        return "🔓 <b>Печать немоты разрушена!</b>\n\n🥷 Ниндзя: {user}\n✨ Освободил: {admin}\n🗯 Основание: {reason}\n🕒 Время: {datetime}\n\n🗣 Каналы связи чакры восстановлены — пользователь снова может писать."
    if action == "ban":
        return "🌑 <b>Изгнание из деревни завершено!</b>\n\n🥷 Нукенин: {user}\n👁 Приговор исполнил: {admin}\n⏳ Срок изгнания: {duration}\n🗯 Причина: {reason}\n📁 Секретный свиток: {case_code}\n🕒 Время: {datetime}\n\n🚫 Барьер деревни не позволит цели вернуться до снятия печати."
    if action == "unban":
        return "🌤 <b>Врата деревни снова открыты!</b>\n\n🥷 Ниндзя: {user}\n🛡 Решение принял: {admin}\n🗯 Основание: {reason}\n🕒 Время: {datetime}\n\n🍃 Печать изгнания снята. Возвращение возможно по приглашению."
    if action == "kick":
        return "💨 <b>Техника выдворения сработала!</b>\n\n🥷 Цель: {user}\n⚔️ Исполнитель: {admin}\n🗯 Причина: {reason}\n📁 Протокол АНБУ: {case_code}\n🕒 Время: {datetime}\n\n🍃 Ниндзя покинул территорию деревни, но не занесён в вечный список изгнанников."
    if action == "quarantine":
        return "🌀 <b>Барьер карантина возведён!</b>\n\n🥷 Ниндзя внутри барьера: {user}\n🛡 Барьер создал: {admin}\n⏳ Срок: {duration}\n🗯 Причина: {reason}\n📁 Свиток наблюдения: {case_code}\n🕒 Время: {datetime}\n\n👁 АНБУ продолжит наблюдение до снятия барьера."
    if action == "unquarantine":
        return "✨ <b>Барьер карантина рассеян!</b>\n\n🥷 Ниндзя: {user}\n🛡 Решение принял: {admin}\n🗯 Основание: {reason}\n🕒 Время: {datetime}\n\n🍃 Ниндзя возвращён к обычному режиму деревни."
    if action in {"restrict_media", "restrict_links", "restrict_commands"}:
        detail = {"restrict_media": "свитки, изображения и файлы", "restrict_links": "порталы и внешние ссылки", "restrict_commands": "техники и команды"}[action]
        return f"🔏 <b>Запретная печать наложена!</b>\n\n🥷 Цель: {{user}}\n⚔️ Печать наложил: {{admin}}\n🚫 Заблокировано: {detail}\n⏳ Срок: {{duration}}\n🗯 Причина: {{reason}}\n📁 Протокол АНБУ: {{case_code}}\n🕒 Время: {{datetime}}"
    if action in {"unrestrict_media", "unrestrict_links", "unrestrict_commands"}:
        return "✨ <b>Запретная печать снята!</b>\n\n🥷 Ниндзя: {user}\n🛡 Печать снял: {admin}\n🗯 Основание: {reason}\n🕒 Время: {datetime}\n\n🍥 Доступ к ранее запрещённым действиям восстановлен."
    if action == "purge":
        return "🌪 <b>Фуутон: Великая очистка завершена!</b>\n\n📜 Уничтожено сообщений: {deleted_count}\n🥷 Технику применил: {admin}\n🏘 Деревня: {chat}\n🕒 Время: {datetime}\n\n✨ Следы нежелательных сообщений рассеяны ветром."
    if action == "slow":
        return "🐢 <b>Печать замедления активирована!</b>\n\n🏘 Деревня: {chat}\n⏳ Интервал между посланиями: {slow_seconds} сек.\n🥷 Печать установил: {admin}\n🕒 Время: {datetime}\n\n🍃 Поток сообщений теперь движется под контролем."
    if action == "lock":
        return "🟣 <b>Барьер четырёх фиолетовых возведён!</b>\n\n🏘 Деревня: {chat}\n🥷 Барьер активировал: {admin}\n🗯 Причина: {reason}\n🕒 Время: {datetime}\n\n🔒 До снятия барьера право говорить сохраняется только у совета деревни."
    if action == "unlock":
        return "🍃 <b>Барьер деревни снят!</b>\n\n🏘 Деревня: {chat}\n🥷 Барьер снял: {admin}\n🕒 Время: {datetime}\n\n✨ Врата открыты, обычное общение восстановлено."
    if action == "report":
        return "📜 <b>Свиток жалобы передан в АНБУ!</b>\n\n🆔 Свиток: {report_id}\n🥷 Автор: {admin}\n🎯 Объект проверки: {user}\n🗯 Причина: {reason}\n🕒 Время: {datetime}\n\n👁 АНБУ изучит доказательства и объединит одинаковые сигналы."
    if action == "appeal":
        return "🕊 <b>Свиток апелляции принят советом!</b>\n\n🆔 Апелляция: {appeal_id}\n🥷 Заявитель: {admin}\n📁 Дело: {case_code}\n🗯 Обоснование: {reason}\n🕒 Время: {datetime}\n\n⚖️ Решение вынесет независимый шиноби, не применявший исходную технику."
    return "🍥 <b>Техника успешно выполнена!</b>\n\n🥷 Цель: {user}\n⚔️ Исполнитель: {admin}\n🗯 Причина: {reason}\n🕒 Время: {datetime}"


BUILTIN_STYLE_TEMPLATES: dict[str, dict[str, str]] = {
    "ordinary": {action: _ordinary(action) for action in ACTION_TITLES},
    "naruto": {action: _naruto(action) for action in ACTION_TITLES},
}
BUILTIN_STYLE_TEMPLATES["minimal"] = {
    action: f"✅ <b>{title}</b>\n👤 {{user}} · 🛡 {{admin}}\n📝 {{reason}} · 🕒 {{datetime}}"
    for action, title in ACTION_TITLES.items()
}
BUILTIN_STYLE_TEMPLATES["strict"] = {
    action: f"<b>{title.upper()}</b>\nПользователь: {{user}}\nАдминистратор: {{admin}}\nСрок: {{duration}}\nПричина: {{reason}}\nДело: {{case_code}}\nДата: {{datetime}}"
    for action, title in ACTION_TITLES.items()
}

ALLOWED_TAGS = ("b", "strong", "i", "em", "u", "ins", "s", "strike", "del", "code", "pre", "blockquote")


def style_code() -> str:
    return "AGS-" + secrets.token_hex(4).upper()


def _restore_allowed_tags(value: str) -> str:
    for tag in ALLOWED_TAGS:
        value = value.replace(f"&lt;{tag}&gt;", f"<{tag}>").replace(f"&lt;/{tag}&gt;", f"</{tag}>")
    return value


def profile_link(user_id: int | None, label: str) -> str:
    clean = html.escape(label or "User")
    return f'<a href="tg://user?id={int(user_id)}">{clean}</a>' if user_id else clean


def build_context(**values: Any) -> dict[str, str]:
    now = values.pop("now", None) or datetime.now(timezone.utc)
    actor_id = values.get("actor_id") or values.get("admin_id")
    target_id = values.get("target_id") or values.get("user_id")
    actor_name = str(values.get("actor_name") or values.get("admin_name") or "Admin")
    target_name = str(values.get("target_name") or values.get("user_name") or "User")
    duration_seconds = values.get("duration_seconds")
    case_id = values.get("case_id")
    case_code = str(values.get("case_code") or (f"AG-{case_id}" if case_id else "не создано"))
    context: dict[str, str] = {
        "admin": profile_link(int(actor_id) if actor_id else None, actor_name),
        "admin_name": html.escape(actor_name),
        "admin_id": str(actor_id or "—"),
        "admin_username": html.escape(str(values.get("admin_username") or "—")),
        "admin_role": html.escape(str(values.get("admin_role") or "Администрация")),
        "user": profile_link(int(target_id) if target_id else None, target_name),
        "user_name": html.escape(target_name),
        "user_id": str(target_id or "—"),
        "username": html.escape(str(values.get("username") or "—")),
        "user_role": html.escape(str(values.get("user_role") or "Участник")),
        "command": html.escape(str(values.get("command") or values.get("command_name") or "команда")),
        "command_key": html.escape(str(values.get("command_key") or "—")),
        "command_description": html.escape(str(values.get("command_description") or "Описание не указано")),
        "command_number": html.escape(str(values.get("command_number") or "—")),
        "action": html.escape(str(values.get("action") or "action")),
        "action_title": html.escape(str(values.get("action_title") or ACTION_TITLES.get(str(values.get("action") or ""), "Действие"))),
        "duration": html.escape(format_duration_ru(int(duration_seconds)) if duration_seconds is not None else "бессрочно"),
        "duration_seconds": str(duration_seconds if duration_seconds is not None else 0),
        "reason": html.escape(str(values.get("reason") or "Причина не указана")),
        "chat": html.escape(str(values.get("chat_title") or values.get("chat") or "Беседа")),
        "group": html.escape(str(values.get("chat_title") or values.get("chat") or "Беседа")),
        "chat_id": str(values.get("chat_id") or "—"),
        "warnings": str(values.get("warnings") if values.get("warnings") is not None else 0),
        "warning_limit": str(values.get("warning_limit") if values.get("warning_limit") is not None else 3),
        "case_id": str(case_id or "—"),
        "case_code": html.escape(case_code),
        "report_id": html.escape(str(values.get("report_id") or "—")),
        "appeal_id": html.escape(str(values.get("appeal_id") or "—")),
        "message_id": str(values.get("message_id") or "—"),
        "deleted_count": str(values.get("deleted_count") or 0),
        "slow_seconds": str(values.get("slow_seconds") or 0),
        "status": html.escape(str(values.get("status") or "Активно")),
        "date": now.astimezone().strftime("%d.%m.%Y"),
        "time": now.astimezone().strftime("%H:%M"),
        "datetime": now.astimezone().strftime("%d.%m.%Y %H:%M"),
        "xp": str(values.get("xp") or 0),
        "coins": str(values.get("coins") or 0),
        "text": html.escape(str(values.get("text") or "")),
        "target": profile_link(int(target_id) if target_id else None, target_name),
        "actor": profile_link(int(actor_id) if actor_id else None, actor_name),
        "newline": "\n",
    }
    for key, value in values.items():
        context.setdefault(str(key), html.escape(str(value)) if value is not None else "")
    return context


def render_template(template: str, context: dict[str, str], *, custom: bool = False) -> str:
    text = html.escape(str(template or "")) if custom else str(template or "")
    if custom:
        text = _restore_allowed_tags(text)
    # Replace known variables only. Unknown placeholders remain visible so the
    # author can spot mistakes in preview/moderation.
    for key, value in sorted(context.items(), key=lambda item: len(item[0]), reverse=True):
        text = text.replace("{" + key + "}", value)
    return text.replace("{newline}", "\n")


def normalize_template_key(value: Any) -> str:
    return re.sub(r"[^\w.:-]+", "_", str(value or "").strip().casefold(), flags=re.UNICODE).strip("_")[:96]


def _moderation_template_keys(action: str, command_key: str | None, command_name: str | None) -> list[str]:
    keys: list[str] = []
    for raw in (command_key, command_name):
        normalized = normalize_template_key(raw)
        if not normalized:
            continue
        keys.extend((f"moderation.{normalized}", normalized))
    keys.extend((f"moderation.{normalize_template_key(action)}", normalize_template_key(action), "moderation.default", "default"))
    return list(dict.fromkeys(key for key in keys if key))


def template_for(
    style: str,
    action: str,
    custom_templates: dict[str, Any] | None = None,
    *,
    command_key: str | None = None,
    command_name: str | None = None,
    command_templates: dict[str, Any] | None = None,
) -> tuple[str, bool]:
    if custom_templates:
        for key in _moderation_template_keys(action, command_key, command_name):
            value = custom_templates.get(key)
            if isinstance(value, str) and value.strip():
                return value, True
    selected = style if style in BUILTIN_STYLE_TEMPLATES else "ordinary"
    if command_templates:
        command_template = command_templates.get(selected)
        if isinstance(command_template, str) and command_template.strip():
            return command_template, False
    return BUILTIN_STYLE_TEMPLATES[selected].get(action) or BUILTIN_STYLE_TEMPLATES[selected].get("warn", "✅ {action_title}"), False


def render_action_response(*, style: str, action: str, custom_templates: dict[str, Any] | None = None, **values: Any) -> str:
    command_key = str(values.get("command_key") or "")
    command_name = str(values.get("command") or values.get("command_name") or "")
    command_templates = values.pop("command_templates", None)
    template, is_custom = template_for(
        style,
        action,
        custom_templates,
        command_key=command_key,
        command_name=command_name,
        command_templates=command_templates,
    )
    context = build_context(action=action, action_title=ACTION_TITLES.get(action, action), **values)
    return render_template(template, context, custom=is_custom)


def validate_templates(templates: dict[str, Any]) -> dict[str, str]:
    cleaned: dict[str, str] = {}
    for raw_key, raw_value in templates.items():
        key = normalize_template_key(raw_key)
        value = str(raw_value or "").strip()
        if not key or not value:
            continue
        if len(value) > 4000:
            raise ValueError(f"Шаблон {key} превышает 4000 символов")
        cleaned[key] = value
    if not cleaned:
        raise ValueError("Добавьте хотя бы один шаблон ответа")
    return cleaned


STYLE_EXAMPLES = [
    {
        "title": "Обычный мут",
        "key": "moderation.mute",
        "template": "🔇 <b>Ограничение выдано</b>{newline}{newline}Пользователь: {user}{newline}Срок: {duration}{newline}Причина: {reason}{newline}Модератор: {admin}",
    },
    {
        "title": "Naruto-бан",
        "key": "moderation.ban",
        "template": "🌑 <b>Изгнание из деревни!</b>{newline}{newline}Нукенин: {user}{newline}Срок: {duration}{newline}Приговор: {reason}{newline}Хокаге: {admin}{newline}Свиток: {case_code}",
    },
    {
        "title": "RP-команда",
        "key": "rp.обнять",
        "template": "🤗 {actor} крепко обнимает {target}!{newline}✨ Настроение обоих улучшилось.",
    },
    {
        "title": "Игровая техника",
        "key": "game.расенган",
        "template": "🌀 {actor} создаёт Расенган и направляет его в {target}!{newline}💥 Техника выполнена в беседе «{chat}».",
    },
]
