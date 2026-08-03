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
DATA_DIR=/app/data
DATABASE_URL=sqlite+aiosqlite:////app/data/aniguard.db
ADMIN_IDS=[ВАШ_TELEGRAM_ID]
RECOVERY_CHAT_IDS=[]
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


## Постоянное сохранение групп и настроек

AniGuard хранит SQLite-базу и загруженные изображения в `/app/data`. Эта папка сохраняется Bothost между обновлениями и перезапусками. Не меняйте `DATABASE_URL` обратно на `./aniguard.db`.

После первого деплоя исправления один раз отправьте `/panel` в каждой уже существующей группе. После этого группы, настройки, Premium и аватары будут восстанавливаться автоматически при каждом запуске. Если старая база уже потеряна, можно временно указать ID групп в `RECOVERY_CHAT_IDS`, например `[-1001111111111,-1002222222222]`.

Бот также обрабатывает Telegram-событие `my_chat_member`: новые группы регистрируются сразу при добавлении или выдаче прав администратора, без дополнительной команды.

## Мониторинг ресурсов v22

Добавьте в переменные проекта BotHost:

```env
BOTHOST_RAM_LIMIT_MB=2048
BOTHOST_CPU_LIMIT=4
BOTHOST_DISK_LIMIT_GB=15
RESOURCE_POLL_INTERVAL_SECONDS=1
RESOURCE_PERSIST_INTERVAL_SECONDS=10
RESOURCE_HISTORY_DAYS=7
```

Админ-панель получает показатели через собственный endpoint `/api/admin/live` на домене проекта. CPU и RAM читаются из cgroup текущего контейнера. Панель обновляется каждую секунду, история записывается раз в 10 секунд.
