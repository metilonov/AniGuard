# AniGuard Naruto MMO V3

MMO V3 is an additive update on top of the full working MMO V2 build. It does not replace AniGuard's admin panel, moderation, Premium/store, Mini App, group controls, media-safety, or previous Naruto RPG/MMO systems.

## New MMO V3 systems

- Five-village government layer with Kage, village council, public trust, ideology, treasury and 0–5% mission tax.
- Kage elections: open election, self-nomination, one-player-one-vote, timed resolution, automatic Kage/council assignment.
- Village development projects: hospital, walls, academy, market, intelligence center and research lab. Players can fund projects directly; Kage/council can finance them from the public treasury.
- Real mission tax flow: configured village tax is deducted from successful normal mission rewards and deposited into the village treasury. Nukenin are not taxed by a village.
- Criminal organizations for nukenin with 12-player cap, Naruto-style criminal roles, invites, treasury, secret-base upgrades, secrecy/heat and criminal operations.
- Server-unique Bijuu state for all nine tailed beasts. A free Bijuu is a shared world boss; after defeat it enters a controlled sealing phase. Kage nomination plus an independent Kage/council approval is required before a player becomes Jinchuriki.
- Global shinobi newspaper and permanent MMO V3 world chronicle for major political, village, underworld and Bijuu events.
- MMO V3 world-pulse dashboard summarizing Kage seats, village treasuries, criminal organizations, Bijuu and active world events.
- Main `/ninja` keyboard now contains Government, Newspaper and MMO V3 buttons.
- Natural-language aliases such as `Наруто, правительство`, `Наруто, выборы`, `Наруто, биджу`, `Наруто, газета`, and `Наруто, пульс мира`.

## Main commands

- `/mmo3`
- `/government`
- `/election start|nominate|vote USER_ID|status`
- `/ideology balanced|peaceful|military|trade|isolation|scientific`
- `/tax 0..5`
- `/villagefund AMOUNT`
- `/project status|start KEY|contribute AMOUNT|treasury AMOUNT`
- `/criminalorg`
- `/criminalorg create NAME`
- `/criminalorg invite USER_ID`
- `/criminalorg accept INVITE_ID`
- `/criminalorg role USER_ID ROLE`
- `/criminalorg donate AMOUNT`
- `/criminalorg upgrade base|intel|lab|defense`
- `/crime robbery|sabotage|intel`
- `/bijuu`
- `/bijuu hunt KEY`
- `/bijuu nominate KEY USER_ID`
- `/bijuu approve KEY`
- `/bijuu release KEY`
- `/newspaper`
- `/worldchronicle`

## Database

MMO V3 uses additive SQLAlchemy tables with the `naruto_v3_` prefix. `app.main` imports the V3 models before `init_db()`, therefore AniGuard's existing `Base.metadata.create_all()` creates the new tables automatically. Existing tables are not dropped or renamed.

## Validation

- `python -m compileall -q app` passes.
- Existing + new test suite in the build: `116 passed, 1 skipped` in the build environment.
- The single skipped test is the V3 async SQLite integration scenario because the artifact-generation environment did not have `aiosqlite` installed at test runtime. `aiosqlite==0.21.0` remains present in AniGuard's `requirements.txt` for deployment.
