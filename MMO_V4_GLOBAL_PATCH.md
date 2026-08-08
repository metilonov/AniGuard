# AniGuard Naruto MMO V4 — Global UX & Expansion Patch

MMO V4 rebuilds the Telegram RPG navigation without deleting or replacing AniGuard's admin panel, Mini App, moderation, Premium, store or existing MMO systems.

## Main fix

Previously most RPG buttons returned the same large root keyboard, and several menu entries (`daily`, `mission`, `arena`, `raid`) immediately executed an action just by opening the section. V4 separates navigation from actions.

The root `/ninja` menu now contains only seven high-level entries:

- 🥷 Шиноби
- ⚔️ Активности
- 🌍 Мир
- 👥 Сообщество
- 💰 Экономика
- 🌀 Развитие
- 🌐 MMO V4

Every hub opens its own text and its own contextual buttons. Every leaf screen has its own keyboard and context-aware Back/Home navigation.

## Safety / UX changes

- Opening `🎁 Ежедневная` shows cooldown/streak; `Забрать награду` is separate.
- Opening `📜 Миссии` shows the mission center; mission execution is separate.
- Opening `🏆 Арена` only shows arena status; matchmaking is separate.
- Opening `👹 Рейд` only shows raid state/cooldown; attack is separate.
- Battle completion returns to the battle section, not the giant root keyboard.
- Card summons return to the Cards section.
- Mentor/profession/training flows return to Development.
- Existing slash commands remain available.

## Global V4 extensions

- 🌐 MMO V4 command center with active battle/mission/low-resource alerts.
- ⚔️ Combat-readiness dashboard with HP/chakra/energy readiness score and recommended threat tier.
- 💰 Economy center with wallet, crystals, market volume, clan treasury and custom-technique count.
- 🧭 Operational digest combining personal recommendations and active world events.
- 📜 Live mission control from buttons: create/status/prepare/investigate/direct/negotiate.
- 🗺 Territory expeditions are directly selectable from the territory screen.
- 👥 Contextual clan/social screens for base, roles, alliance, history, friendship, mentorship and mail.
- 🏯 Government subnavigation for elections, projects and treasury/tax status.
- 📰 World chronicle accessible from the newspaper screen.

## Compatibility

V4 adds no destructive database migration. It reuses existing AniGuard/MMO tables and keeps all V1–V3 systems intact.
