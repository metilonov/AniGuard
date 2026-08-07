from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Any

from .content import ELEMENT_ADVANTAGE, TECHNIQUES, TechniqueDef


@dataclass(slots=True)
class ActionResult:
    state: dict[str, Any]
    log: list[str]
    finished: bool
    victory: bool | None = None


def xp_required(level: int) -> int:
    level = max(1, int(level))
    return int(120 * (level ** 1.42))


def player_power(profile: Any) -> int:
    return int(
        profile.level * 18
        + profile.ninjutsu * 1.4
        + profile.taijutsu * 1.3
        + profile.genjutsu * 1.15
        + profile.defense * 1.25
        + profile.speed * 1.1
        + profile.chakra_control * 0.8
    )


def elemental_modifier(attacking: str | None, defending: str | None) -> float:
    if not attacking or not defending or attacking == defending:
        return 1.0
    if ELEMENT_ADVANTAGE.get(attacking) == defending:
        return 1.20
    if ELEMENT_ADVANTAGE.get(defending) == attacking:
        return 0.85
    return 1.0


def _stat_for_technique(player: dict[str, Any], technique: TechniqueDef) -> float:
    kind = technique.kind
    if kind == "taijutsu":
        return float(player["taijutsu"])
    if kind == "genjutsu":
        return float(player["genjutsu"])
    if kind == "medical":
        return float(player["chakra_control"])
    if kind == "control":
        return (float(player["genjutsu"]) + float(player["chakra_control"])) / 2
    return float(player["ninjutsu"])


def _damage(
    attacker: dict[str, Any],
    defender: dict[str, Any],
    technique: TechniqueDef,
    technique_level: int = 1,
) -> int:
    if technique.power < 0:
        stat = _stat_for_technique(attacker, technique)
        return -max(1, int(abs(technique.power) * (1 + stat / 650)))

    stat = _stat_for_technique(attacker, technique)
    base = technique.power * (1 + (technique_level - 1) * 0.045)
    scaling = 1 + stat / 520
    element = elemental_modifier(technique.element, defender.get("element"))
    mitigation = 100 / (100 + max(0, float(defender.get("defense", 0))) * 0.82)
    variance = random.uniform(0.95, 1.05)
    damage = base * scaling * element * mitigation * variance

    crit_chance = min(0.35, max(0.0, float(attacker.get("crit", 0.05))))
    if random.random() < crit_chance:
        damage *= 1.5
        attacker["last_crit"] = True
    else:
        attacker["last_crit"] = False

    if defender.get("defending"):
        damage *= 0.60
    if defender.get("guard", 0) > 0:
        damage *= 0.72
    if defender.get("susanoo", 0) > 0:
        damage *= 0.58
    if defender.get("phase", 0) > 0:
        damage = 0

    return max(0, int(damage))


def _accuracy(attacker: dict[str, Any], defender: dict[str, Any], technique: TechniqueDef) -> float:
    acc_stat = max(1.0, float(attacker.get("accuracy", 10)))
    speed_delta = float(attacker.get("speed", 10)) - float(defender.get("speed", 10))
    chance = technique.accuracy + (acc_stat - 10) / 1800 + speed_delta / 4500
    if defender.get("evasion", 0) > 0:
        chance -= 0.18
    return min(0.995, max(0.55, chance))


def _tick_statuses(actor: dict[str, Any], log: list[str], label: str) -> bool:
    statuses = actor.setdefault("statuses", {})
    total_dot = 0
    for key, amount in list(statuses.items()):
        turns = int(amount)
        if turns <= 0:
            statuses.pop(key, None)
            continue
        if key == "burn":
            total_dot += max(8, int(actor["max_hp"] * 0.035))
        elif key == "black_flame":
            total_dot += max(14, int(actor["max_hp"] * 0.055))
        elif key == "poison":
            total_dot += max(7, int(actor["max_hp"] * 0.03))
        elif key == "bleed":
            total_dot += max(6, int(actor["max_hp"] * 0.025))
        statuses[key] = turns - 1
        if statuses[key] <= 0:
            statuses.pop(key, None)
    if total_dot:
        actor["hp"] = max(0, int(actor["hp"]) - total_dot)
        log.append(f"{label} получает {total_dot} периодического урона.")
    for field in ("guard", "evasion", "phase", "susanoo", "sage"):
        if int(actor.get(field, 0)) > 0:
            actor[field] = int(actor[field]) - 1
    return int(actor["hp"]) <= 0


def _status_locks_turn(actor: dict[str, Any]) -> bool:
    statuses = actor.get("statuses") or {}
    if int(statuses.get("stun", 0)) > 0:
        return True
    if int(statuses.get("paralyze", 0)) > 0 and random.random() < 0.35:
        return True
    return False


def _apply_status(target: dict[str, Any], technique: TechniqueDef, log: list[str]) -> None:
    if not technique.status or random.random() > technique.status_chance:
        return
    if technique.status == "guard":
        target["guard"] = max(int(target.get("guard", 0)), 2)
        return
    if technique.status == "evasion":
        target["evasion"] = max(int(target.get("evasion", 0)), 2)
        return
    if technique.status == "phase":
        target["phase"] = max(int(target.get("phase", 0)), 1)
        return
    if technique.status == "susanoo":
        target["susanoo"] = max(int(target.get("susanoo", 0)), 3)
        return
    if technique.status == "sage":
        target["sage"] = max(int(target.get("sage", 0)), 3)
        return
    durations = {
        "burn": 3,
        "black_flame": 4,
        "poison": 4,
        "bleed": 3,
        "stun": 1,
        "paralyze": 2,
        "slow": 3,
        "chakra_lock": 3,
    }
    target.setdefault("statuses", {})[technique.status] = durations.get(technique.status, 2)
    log.append(f"Наложен эффект: {technique.status}.")


def make_battle_state(
    profile: Any,
    enemy: dict[str, Any],
    techniques: list[tuple[str, int]],
    custom_techniques: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    player = {
        "name": profile.name,
        "hp": int(profile.max_hp),
        "max_hp": int(profile.max_hp),
        "chakra": int(profile.max_chakra),
        "max_chakra": int(profile.max_chakra),
        "ninjutsu": int(profile.ninjutsu),
        "taijutsu": int(profile.taijutsu),
        "genjutsu": int(profile.genjutsu),
        "defense": int(profile.defense),
        "speed": int(profile.speed),
        "accuracy": int(profile.accuracy),
        "chakra_control": int(profile.chakra_control),
        "crit": float(profile.crit_chance),
        "element": profile.primary_element,
        "statuses": {},
        "cooldowns": {},
        "techniques": [{"key": key, "level": level} for key, level in techniques],
        "custom_techniques": dict(custom_techniques or {}),
    }
    flags = dict(getattr(profile, "flags", {}) or {})
    form = str(flags.get("battle_form") or "")
    dojutsu = str(flags.get("dojutsu") or "")
    if form in {"sharingan", "mangekyo", "eternal_mangekyo", "rinnegan"} or dojutsu in {"sharingan", "sharingan_2", "sharingan_3", "mangekyo", "eternal_mangekyo", "rinnegan"}:
        player["accuracy"] = int(player["accuracy"] * 1.12)
        player["speed"] = int(player["speed"] * 1.08)
        player["genjutsu"] = int(player["genjutsu"] * 1.10)
    if form == "byakugan" or dojutsu == "byakugan":
        player["accuracy"] = int(player["accuracy"] * 1.16)
        player["speed"] = int(player["speed"] * 1.06)
        player["chakra_control"] = int(player["chakra_control"] * 1.12)
    if form == "sage":
        player["ninjutsu"] = int(player["ninjutsu"] * 1.30)
        player["defense"] = int(player["defense"] * 1.25)
        player["speed"] = int(player["speed"] * 1.18)
        player["max_chakra"] = int(player["max_chakra"] * 1.15)
        player["chakra"] = player["max_chakra"]
    if form.startswith("gates_"):
        try:
            gate = max(1, min(8, int(form.split("_", 1)[1])))
        except Exception:
            gate = 1
        player["taijutsu"] = int(player["taijutsu"] * (1.0 + gate * 0.12))
        player["speed"] = int(player["speed"] * (1.0 + gate * 0.09))
        player["defense"] = max(1, int(player["defense"] * (1.0 - min(0.45, gate * 0.05))))
        player["gate_level"] = gate
    if form == "biju":
        trust = int((getattr(profile, "biju", {}) or {}).get("trust", 0))
        factor = 1.18 + min(0.22, trust / 500)
        player["ninjutsu"] = int(player["ninjutsu"] * factor)
        player["taijutsu"] = int(player["taijutsu"] * factor)
        player["defense"] = int(player["defense"] * 1.18)
        player["max_chakra"] = int(player["max_chakra"] * 1.45)
        player["chakra"] = player["max_chakra"]
    player["form"] = form or dojutsu or None
    active_summon = str(flags.get("battle_summon") or "")
    if active_summon:
        player["summon"] = active_summon

    foe = {
        "name": enemy["name"],
        "hp": int(enemy["hp"]),
        "max_hp": int(enemy["hp"]),
        "chakra": int(enemy["chakra"]),
        "max_chakra": int(enemy["chakra"]),
        "ninjutsu": int(enemy["attack"]),
        "taijutsu": int(enemy["attack"] * 0.88),
        "genjutsu": int(enemy["attack"] * 0.72),
        "defense": int(enemy["defense"]),
        "speed": int(enemy["speed"]),
        "accuracy": int(enemy["speed"] * 0.55),
        "chakra_control": int(enemy["attack"] * 0.6),
        "crit": min(0.22, 0.05 + enemy.get("level", 1) / 800),
        "element": enemy.get("element"),
        "statuses": {},
        "cooldowns": {},
    }
    return {
        "turn": 1,
        "player": player,
        "enemy": foe,
        "enemy_level": int(enemy.get("level", 1)),
        "rewards": {"xp": int(enemy.get("xp", 0)), "ryo": int(enemy.get("ryo", 0))},
        "log": [f"⚔️ Бой начался: {profile.name} против {enemy['name']}"],
    }


def _reduce_cooldowns(actor: dict[str, Any]) -> None:
    cooldowns = actor.setdefault("cooldowns", {})
    for key in list(cooldowns):
        cooldowns[key] = max(0, int(cooldowns[key]) - 1)
        if cooldowns[key] <= 0:
            cooldowns.pop(key, None)


def _technique_for_actor(actor: dict[str, Any], key: str) -> TechniqueDef | None:
    technique = TECHNIQUES.get(key)
    if technique is not None:
        return technique
    raw = (actor.get("custom_techniques") or {}).get(key)
    if not isinstance(raw, dict):
        return None
    try:
        return TechniqueDef(
            key=key,
            name=str(raw.get("name") or key),
            element=raw.get("element"),
            kind=str(raw.get("kind") or "ninjutsu"),
            rank="CUSTOM",
            chakra=max(0, int(raw.get("chakra", 0))),
            power=max(0, int(raw.get("power", 0))),
            accuracy=max(0.50, min(1.0, float(raw.get("accuracy", 0.90)))),
            cooldown=max(0, min(8, int(raw.get("cooldown", 2)))),
            description="Персональная техника игрока.",
        )
    except Exception:
        return None


def _use_technique(
    actor: dict[str, Any],
    target: dict[str, Any],
    key: str,
    level: int,
    log: list[str],
) -> bool:
    technique = _technique_for_actor(actor, key)
    if technique is None:
        log.append("⚠️ Техника недоступна.")
        return False
    cooldowns = actor.setdefault("cooldowns", {})
    if cooldowns.get(key, 0):
        log.append(f"⏳ {technique.name} ещё восстанавливается.")
        return False
    cost_discount = min(0.22, max(0.0, (float(actor.get("chakra_control", 10)) - 10) / 2200))
    chakra_cost = max(0, int(technique.chakra * (1 - cost_discount)))
    if actor["chakra"] < chakra_cost:
        log.append(f"🔵 Не хватает чакры для {technique.name}.")
        return False
    actor["chakra"] -= chakra_cost
    cooldowns[key] = technique.cooldown

    # Utility/defensive techniques target the actor.
    if technique.kind in {"defense", "utility", "senjutsu"} and technique.power == 0:
        _apply_status(actor, technique, log)
        log.append(f"{actor['name']} использует {technique.name}.")
        return True

    if technique.power < 0:
        heal = abs(_damage(actor, actor, technique, level))
        actor["hp"] = min(actor["max_hp"], actor["hp"] + heal)
        log.append(f"💚 {actor['name']} восстанавливает {heal} HP техникой {technique.name}.")
        return True

    if random.random() > _accuracy(actor, target, technique):
        log.append(f"💨 {actor['name']} использует {technique.name}, но атака не попадает.")
        return True

    damage = _damage(actor, target, technique, level)
    target["hp"] = max(0, int(target["hp"]) - damage)
    critical = " КРИТ!" if actor.pop("last_crit", False) else ""
    log.append(f"{actor['name']} → {technique.name}: {damage} урона.{critical}")
    _apply_status(target, technique, log)
    return True


def _enemy_action(enemy: dict[str, Any], player: dict[str, Any], log: list[str]) -> None:
    if _status_locks_turn(enemy):
        log.append(f"😵 {enemy['name']} не может действовать.")
        return
    # Boss AI: defend or recover chakra when pressured, otherwise attack.
    hp_ratio = enemy["hp"] / max(1, enemy["max_hp"])
    chakra_ratio = enemy["chakra"] / max(1, enemy["max_chakra"])
    if hp_ratio < 0.30 and random.random() < 0.17:
        enemy["defending"] = True
        enemy["chakra"] = min(enemy["max_chakra"], enemy["chakra"] + 35)
        log.append(f"🛡 {enemy['name']} уходит в защиту.")
        return
    if chakra_ratio < 0.15 and random.random() < 0.35:
        enemy["chakra"] = min(enemy["max_chakra"], enemy["chakra"] + 70)
        log.append(f"🔵 {enemy['name']} концентрирует чакру.")
        return

    basic = TECHNIQUES["basic_strike"]
    elemental_pool = {
        "fire": "fireball",
        "water": "water_dragon",
        "lightning": "lightning_spear",
        "wind": "wind_blade",
        "earth": "earth_wall",
    }
    special = elemental_pool.get(enemy.get("element"))
    if special and enemy["chakra"] >= TECHNIQUES[special].chakra and random.random() < 0.42:
        _use_technique(enemy, player, special, 1, log)
    else:
        _use_technique(enemy, player, basic.key, 1, log)


def apply_player_action(state: dict[str, Any], action: str) -> ActionResult:
    player = state["player"]
    enemy = state["enemy"]
    log: list[str] = []
    player["defending"] = False
    enemy["defending"] = False
    _reduce_cooldowns(player)
    _reduce_cooldowns(enemy)

    if _tick_statuses(player, log, player["name"]):
        state["log"] = (state.get("log", []) + log)[-12:]
        return ActionResult(state, log, True, False)
    if _tick_statuses(enemy, log, enemy["name"]):
        state["log"] = (state.get("log", []) + log)[-12:]
        return ActionResult(state, log, True, True)

    if _status_locks_turn(player):
        log.append(f"😵 {player['name']} пропускает ход.")
    elif action == "defend":
        player["defending"] = True
        player["chakra"] = min(player["max_chakra"], player["chakra"] + 18)
        log.append(f"🛡 {player['name']} защищается и восстанавливает чакру.")
    elif action == "focus":
        player["chakra"] = min(player["max_chakra"], player["chakra"] + 65)
        log.append(f"🔵 {player['name']} концентрирует чакру.")
    elif action.startswith("item:"):
        item_key = action.removeprefix("item:")
        if item_key == "medkit":
            amount = min(250, player["max_hp"] - player["hp"])
            player["hp"] += max(0, amount)
            state["consumed_item"] = item_key
            log.append(f"💊 {player['name']} использует аптечку и восстанавливает {max(0, amount)} HP.")
        elif item_key == "chakra_pill":
            amount = min(180, player["max_chakra"] - player["chakra"])
            player["chakra"] += max(0, amount)
            state["consumed_item"] = item_key
            log.append(f"🔵 {player['name']} использует пилюлю чакры и восстанавливает {max(0, amount)} чакры.")
        elif item_key == "explosive_tag":
            damage = max(25, int(95 * (1 + player.get("ninjutsu", 10) / 900)))
            enemy["hp"] = max(0, enemy["hp"] - damage)
            state["consumed_item"] = item_key
            log.append(f"💥 Взрывная печать наносит {damage} урона.")
        else:
            log.append("🎒 Этот предмет нельзя использовать в бою.")
    else:
        key = action.removeprefix("tech:") if action.startswith("tech:") else "basic_strike"
        available = {entry["key"]: int(entry.get("level", 1)) for entry in player.get("techniques", [])}
        if key not in available:
            key = "basic_strike"
            available[key] = 1
        if not _use_technique(player, enemy, key, available[key], log):
            _use_technique(player, enemy, "basic_strike", 1, log)

    if enemy["hp"] <= 0:
        state["log"] = (state.get("log", []) + log)[-12:]
        return ActionResult(state, log, True, True)

    summon = str(player.get("summon") or "")
    if summon and int(state.get("turn", 1)) % 2 == 1:
        summon_names = {"toads": "🐸 Жаба", "snakes": "🐍 Змея", "slugs": "🐌 Слизень", "ninken": "🐕 Нинкен", "hawks": "🦅 Ястреб"}
        base = {"toads": 0.055, "snakes": 0.060, "slugs": 0.030, "ninken": 0.045, "hawks": 0.050}.get(summon, 0.035)
        if summon == "slugs" and player["hp"] < player["max_hp"]:
            heal = max(12, int(player["max_hp"] * 0.04))
            player["hp"] = min(player["max_hp"], player["hp"] + heal)
            log.append(f"🐌 Призыв лечит {player['name']} на {heal} HP.")
        else:
            summon_damage = max(15, int(enemy["max_hp"] * base))
            enemy["hp"] = max(0, enemy["hp"] - summon_damage)
            log.append(f"{summon_names.get(summon, '✨ Призыв')} помогает и наносит {summon_damage} урона.")
        if enemy["hp"] <= 0:
            state["log"] = (state.get("log", []) + log)[-12:]
            return ActionResult(state, log, True, True)

    _enemy_action(enemy, player, log)
    if player["hp"] <= 0:
        state["log"] = (state.get("log", []) + log)[-12:]
        return ActionResult(state, log, True, False)

    # Baseline regeneration and status penalties.
    chakra_regen = 14
    if int((player.get("statuses") or {}).get("chakra_lock", 0)) > 0:
        chakra_regen = 5
    player["chakra"] = min(player["max_chakra"], player["chakra"] + chakra_regen)
    enemy["chakra"] = min(enemy["max_chakra"], enemy["chakra"] + 10)
    state["turn"] = int(state.get("turn", 1)) + 1
    state["log"] = (state.get("log", []) + log)[-12:]
    return ActionResult(state, log, False, None)


def bar(current: int, maximum: int, segments: int = 10) -> str:
    maximum = max(1, maximum)
    current = max(0, min(current, maximum))
    filled = min(segments, max(0, round(current / maximum * segments)))
    return "█" * filled + "░" * (segments - filled)


def battle_text(state: dict[str, Any]) -> str:
    p = state["player"]
    e = state["enemy"]
    recent = state.get("log", [])[-4:]
    return (
        f"⚔️ <b>Бой · ход {state.get('turn', 1)}</b>\n\n"
        f"🥷 <b>{p['name']}</b>\n"
        f"❤️ {bar(p['hp'], p['max_hp'])} {p['hp']}/{p['max_hp']}\n"
        f"🔵 {bar(p['chakra'], p['max_chakra'])} {p['chakra']}/{p['max_chakra']}\n\n"
        f"VS\n\n"
        f"👹 <b>{e['name']}</b>\n"
        f"❤️ {bar(e['hp'], e['max_hp'])} {e['hp']}/{e['max_hp']}\n"
        f"🔵 {bar(e['chakra'], e['max_chakra'])} {e['chakra']}/{e['max_chakra']}\n\n"
        + ("\n".join(f"• {line}" for line in recent) if recent else "")
    )


def level_up_stats(profile: Any) -> list[str]:
    messages: list[str] = []
    while profile.xp >= xp_required(profile.level) and profile.level < 100:
        needed = xp_required(profile.level)
        profile.xp -= needed
        profile.level += 1
        hp_gain = 22 + profile.level // 5
        chakra_gain = 18 + profile.level // 6
        profile.max_hp += hp_gain
        profile.max_chakra += chakra_gain
        profile.hp = profile.max_hp
        profile.chakra = profile.max_chakra
        profile.ninjutsu += 3
        profile.taijutsu += 3
        profile.genjutsu += 2
        profile.defense += 2
        profile.speed += 2
        profile.accuracy += 1
        profile.chakra_control += 2
        profile.genjutsu_resist += 1
        messages.append(f"🌟 Новый уровень: {profile.level}")
    return messages


def arena_league(rating: int) -> str:
    if rating < 1100:
        return "🥉 Бронза"
    if rating < 1300:
        return "🥈 Серебро"
    if rating < 1550:
        return "🥇 Золото"
    if rating < 1850:
        return "💎 Алмаз"
    if rating < 2200:
        return "🔥 Каге"
    return "🌑 Шесть Путей"


def upgrade_success_chance(level: int) -> float:
    table = {0: 1.0, 1: 1.0, 2: 1.0, 3: 0.90, 4: 0.80, 5: 0.65, 6: 0.50, 7: 0.35, 8: 0.20, 9: 0.10}
    return table.get(level, 0.0)


def expected_market_price(base_price: int, level: int = 0, quality_mult: float = 1.0) -> tuple[int, int]:
    center = int(base_price * (1 + level * 0.22) * quality_mult)
    return max(1, int(center * 0.25)), int(center * 4.0)


def weighted_choice(weights: dict[str, float]) -> str:
    keys = list(weights)
    values = [max(0.0, weights[k]) for k in keys]
    return random.choices(keys, weights=values, k=1)[0]


def training_gain(level: int) -> int:
    return max(1, int(2 + math.sqrt(max(1, level)) / 2))
