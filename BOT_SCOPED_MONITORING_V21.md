> **Архивный документ.** Актуальная схема v22 описана в `DOMAIN_API_MONITORING_V22.md`. Настройки агента из этого файла больше не используются.

# AniGuard v21 — мониторинг ресурсов отдельного бота

## Что исправлено

- CPU и RAM берутся для текущего `BOT_ID` через BotHost API.
- Если API недоступен, используются cgroup v2/v1 текущего контейнера.
- Если cgroup недоступен, используется только текущий Python-процесс AniGuard.
- Размер диска считается по каталогу `BOTHOST_PROJECT_DIR`, а не по всему файловому разделу сервера.
- Рабочий адрес агента определяется автоматически из основного и резервных URL.
- После ошибки агента включается пауза повторного поиска, но локальные метрики продолжают обновляться каждую секунду.
- Админ-панель явно показывает источник метрик: BotHost API, cgroup контейнера или процесс Python.

## Приоритет источников

1. BotHost API: `/api/bots/{BOT_ID}/stats`.
2. Linux cgroup текущего контейнера.
3. `/proc/self` текущего процесса.

## Переменные окружения

```env
BOTHOST_AGENT_URL=http://agent.bothost.ru
BOTHOST_AGENT_FALLBACK_URLS=http://agent:8000,http://msk1.bothost.ru
BOTHOST_AGENT_TIMEOUT_SECONDS=1.2
BOTHOST_AGENT_RETRY_SECONDS=10
BOTHOST_RAM_LIMIT_MB=2048
BOTHOST_CPU_LIMIT=4
BOTHOST_DISK_LIMIT_GB=15
BOTHOST_PROJECT_DIR=/app
BOTHOST_DISK_SCAN_INTERVAL_SECONDS=60
RESOURCE_POLL_INTERVAL_SECONDS=1
RESOURCE_PERSIST_INTERVAL_SECONDS=10
RESOURCE_HISTORY_DAYS=7
```

`BOT_ID` не задаётся вручную: BotHost добавляет его контейнеру автоматически.

## Частота обновления

- CPU/RAM/uptime и данные админ-панели: 1 секунда.
- Размер каталога бота: 60 секунд.
- Запись истории в БД: 10 секунд.
