# AniGuard v22 — мониторинг через домен проекта

## Схема

Админ-панель получает сведения каждую секунду через собственный защищённый endpoint:

```text
https://aniguard.bothost.tech/api/admin/live
```

В браузере используется относительный путь `/api/admin/live`. Backend AniGuard читает CPU и RAM текущего контейнера из Linux cgroup, а при отсутствии cgroup — показатели текущего Python-процесса. Размер диска считается только по каталогу `/app` и обновляется раз в 60 секунд.

Запросы к `agent:8000`, `agent.bothost.ru` и `msk1.bothost.ru` полностью удалены. Bearer-токен и `BOT_ID` для мониторинга не требуются.

## Переменные

```env
WEBAPP_URL=https://aniguard.bothost.tech
ADMIN_URL=https://aniguard.bothost.tech/admin
HOST=0.0.0.0

BOTHOST_RAM_LIMIT_MB=2048
BOTHOST_CPU_LIMIT=4
BOTHOST_DISK_LIMIT_GB=15
BOTHOST_PROJECT_DIR=/app
BOTHOST_DISK_SCAN_INTERVAL_SECONDS=60
RESOURCE_POLL_INTERVAL_SECONDS=1
RESOURCE_PERSIST_INTERVAL_SECONDS=10
RESOURCE_HISTORY_DAYS=7
```
