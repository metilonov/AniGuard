# Развёртывание AniGuard на Bothost

## Маршруты одного домена

После деплоя одного контейнера FastAPI будут доступны:

- `https://aniguard.bothost.tech/` — перенаправление в панель управления;
- `https://aniguard.bothost.tech/panel` — Mini App для владельцев и администраторов бесед;
- `https://aniguard.bothost.tech/admin` — закрытая панель владельца AniGuard;
- `https://aniguard.bothost.tech/api/health` — проверка работы API.

`/panel` и `/admin` — URL-маршруты, а не отдельные DNS-домены. В панели Bothost достаточно подключить основной домен `aniguard.bothost.tech` к контейнеру на порту `3000`.

## Переменные окружения

Создайте `.env` на основе `.env.example`:

```env
BOT_TOKEN=токен_из_BotFather
WEBAPP_URL=https://aniguard.bothost.tech/panel
ADMIN_URL=https://aniguard.bothost.tech/admin
DATABASE_URL=sqlite+aiosqlite:///./aniguard.db
ADMIN_IDS=[ВАШ_TELEGRAM_ID]
DEV_MODE=false
INIT_DATA_MAX_AGE=3600
HOST=0.0.0.0
PORT=3000
LOG_LEVEL=INFO
```

В `ADMIN_IDS` можно указать несколько Telegram ID через запятую, например: `[123456789,987654321]`.

## Настройка BotFather

1. Откройте BotFather.
2. Выполните `/setdomain`.
3. Выберите AniGuard.
4. Укажите `aniguard.bothost.tech` без `/panel` и `/admin`.
5. Перезапустите контейнер.

## Команды

- `/panel` открывает пользовательскую панель управления беседами.
- `/admin` открывает панель владельца только пользователям из `ADMIN_IDS`.
- Для остальных пользователей `/admin` полностью игнорируется.
- `/promo КОД` активирует созданные в админ-панели промокоды.

## Безопасность админ-панели

В HTML нет постоянного логина и пароля. Панель отправляет Telegram `initData` в API, сервер проверяет подпись токеном бота и затем сверяет Telegram ID с `ADMIN_IDS`.
