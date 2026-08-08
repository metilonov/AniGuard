from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageEnhance, ImageFont

from app.db import SessionFactory

from .models import NinjaProfile
from .service import get_profile

ASSET_DIR = Path(__file__).resolve().parents[2] / "assets" / "naruto_ui"
CACHE_DIR = Path("/tmp/aniguard_naruto_media")
CACHE_DIR.mkdir(parents=True, exist_ok=True)

FONT_REGULAR = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
FONT_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
OUTPUT_SIZE = (1280, 720)

SCREEN_ASSETS: dict[str, str] = {
    "home": "profile.png",
    "shinobi": "profile.png",
    "profile": "profile.png",
    "daily": "profile.png",
    "mission": "mission.png",
    "story": "mission.png",
    "battle": "battle.png",
    "arena": "battle.png",
    "techniques": "growth.png",
    "cards": "cards.png",
    "inventory": "inventory.png",
    "world": "world_map.png",
    "territory": "world_map.png",
    "government": "world_map.png",
    "newspaper": "world_map.png",
    "mmo3": "world_map.png",
    "mmo4": "world_map.png",
    "events": "world_map.png",
    "clan": "clan_room.png",
    "social": "clan_room.png",
    "mmo": "clan_room.png",
    "path": "growth.png",
    "raid": "raid.png",
    "economy": "market.png",
    "market": "market.png",
    "craft": "market.png",
    "growth": "growth.png",
    "recommend": "growth.png",
}

ANIMATED_SCREENS = set(SCREEN_ASSETS)


SCREEN_TITLES = {
    "mission": "Центр миссий",
    "story": "Сюжет",
    "battle": "Бой",
    "arena": "Арена",
    "techniques": "Техники",
    "cards": "Карточки",
    "inventory": "Инвентарь",
    "world": "Мир шиноби",
    "territory": "Территории",
    "government": "Правительство",
    "newspaper": "Газета",
    "mmo3": "Пульс мира",
    "mmo4": "MMO",
    "events": "События",
    "clan": "Клан",
    "social": "Сообщество",
    "mmo": "MMO-рейтинг",
    "path": "Путь шиноби",
    "raid": "Рейд",
    "economy": "Экономика",
    "market": "Рынок",
    "craft": "Крафт",
    "growth": "Развитие",
    "recommend": "Рекомендации",
}


def _font(size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    try:
        return ImageFont.truetype(FONT_BOLD if bold else FONT_REGULAR, size)
    except Exception:
        return ImageFont.load_default()


def _resolve_asset(screen: str) -> Path:
    filename = SCREEN_ASSETS.get(screen, SCREEN_ASSETS.get("home", "profile.png"))
    path = ASSET_DIR / filename
    if not path.exists():
        return ASSET_DIR / "profile.png"
    return path


def _snapshot(profile: NinjaProfile) -> dict[str, int]:
    return {
        "hp": int(profile.hp),
        "max_hp": int(profile.max_hp),
        "chakra": int(profile.chakra),
        "max_chakra": int(profile.max_chakra),
        "energy": int(profile.energy),
        "xp": int(profile.xp),
        "xp_cap": max(100, int(profile.level) * 100),
    }


def _previous_snapshot(profile: NinjaProfile, current: dict[str, int]) -> dict[str, int]:
    flags = dict(profile.flags or {})
    raw = flags.get("ui_last_stats") or {}
    return {
        "hp": int(raw.get("hp", current["hp"])),
        "max_hp": int(raw.get("max_hp", current["max_hp"])),
        "chakra": int(raw.get("chakra", current["chakra"])),
        "max_chakra": int(raw.get("max_chakra", current["max_chakra"])),
        "energy": int(raw.get("energy", current["energy"])),
        "xp": int(raw.get("xp", current["xp"])),
        "xp_cap": int(raw.get("xp_cap", current["xp_cap"])),
    }


def _remember_snapshot(profile: NinjaProfile, current: dict[str, int]) -> None:
    flags = dict(profile.flags or {})
    flags["ui_last_stats"] = current
    profile.flags = flags


def _lerp(a: int, b: int, t: float) -> int:
    return int(round(a + (b - a) * t))


def _ease(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return 1.0 - (1.0 - t) ** 3


def _ratio(current: int, maximum: int) -> float:
    return max(0.0, min(1.0, current / max(1, maximum)))


def _draw_bar(
    draw: ImageDraw.ImageDraw,
    x: int,
    y: int,
    w: int,
    h: int,
    ratio: float,
    color: tuple[int, int, int],
    *,
    phase: float = 0.0,
    bg: tuple[int, int, int] = (51, 43, 40),
) -> None:
    ratio = max(0.0, min(1.0, ratio))
    radius = max(2, h // 2)
    draw.rounded_rectangle((x, y, x + w, y + h), radius=radius, fill=(14, 13, 17, 225), outline=(232, 210, 170, 160), width=2)
    inner = 4
    draw.rounded_rectangle((x + inner, y + inner, x + w - inner, y + h - inner), radius=max(2, (h - inner * 2) // 2), fill=bg)
    fill_w = max(0, min(w - inner * 2, int((w - inner * 2) * ratio)))
    if fill_w <= 0:
        return
    x0, y0 = x + inner, y + inner
    x1, y1 = x0 + fill_w, y + h - inner
    draw.rounded_rectangle((x0, y0, x1, y1), radius=max(2, (h - inner * 2) // 2), fill=color)

    # Animated chakra-like highlight travelling along the filled part.
    shine_w = max(10, min(70, fill_w // 3))
    if fill_w > 18:
        travel = max(1, fill_w + shine_w)
        shine_x = x0 - shine_w + int((phase % 1.0) * travel)
        sx0 = max(x0, shine_x)
        sx1 = min(x1, shine_x + shine_w)
        if sx1 > sx0:
            draw.rounded_rectangle((sx0, y0 + 2, sx1, y1 - 2), radius=max(2, (h - inner * 2) // 3), fill=(255, 255, 255, 82))


def _fit_frame(image: Image.Image) -> Image.Image:
    frame = image.convert("RGB")
    if frame.size != OUTPUT_SIZE:
        frame = frame.resize(OUTPUT_SIZE, Image.Resampling.LANCZOS)
    return frame.convert("P", palette=Image.Palette.ADAPTIVE, colors=192)


def _render_profile_frame(
    base_path: Path,
    profile: NinjaProfile,
    values: dict[str, int],
    screen: str,
    *,
    phase: float = 0.0,
) -> Image.Image:
    image = Image.open(base_path).convert("RGBA")
    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay, "RGBA")
    title_font = _font(40, bold=True)
    body_font = _font(26)
    body_bold = _font(26, bold=True)
    small_font = _font(22)

    panel = (96, 88, 736, 848)
    pulse_alpha = 62 + int(12 * (0.5 + 0.5 * math.sin(phase * math.tau)))
    draw.rounded_rectangle(panel, radius=32, fill=(248, 231, 201, pulse_alpha), outline=(111, 73, 43, 100), width=2)

    title_map = {
        "profile": "Профиль шиноби",
        "ready": "Боевая готовность",
        "home": "Командный центр",
    }
    title = title_map.get(screen, "Статус шиноби")
    draw.text((135, 120), title, fill=(48, 31, 19), font=title_font)
    draw.text((135, 176), f"{profile.name} • ур. {profile.level} • {profile.ninja_rank}", fill=(72, 48, 31), font=body_bold)
    draw.text((135, 214), f"Деревня: {profile.village}  |  Стихия: {profile.primary_element}", fill=(72, 48, 31), font=body_font)
    draw.text((135, 252), f"Рё: {profile.ryo:,}  •  Репутация: {profile.reputation}", fill=(72, 48, 31), font=body_font)

    hp_ratio = _ratio(values["hp"], values["max_hp"])
    ch_ratio = _ratio(values["chakra"], values["max_chakra"])
    en_ratio = _ratio(values["energy"], 100)
    xp_ratio = _ratio(values["xp"], values["xp_cap"])

    stats = [
        ("Здоровье", f"{values['hp']}/{values['max_hp']}", hp_ratio, (205, 68, 68)),
        ("Чакра", f"{values['chakra']}/{values['max_chakra']}", ch_ratio, (48, 126, 232)),
        ("Энергия", f"{values['energy']}/100", en_ratio, (111, 184, 78)),
        ("Опыт", f"{values['xp']}/{values['xp_cap']}", xp_ratio, (143, 91, 204)),
    ]
    y = 330
    for label, value, ratio, color in stats:
        draw.text((135, y), label, fill=(58, 37, 21), font=body_bold)
        draw.text((620, y), value, fill=(58, 37, 21), font=body_font, anchor="ra")
        _draw_bar(draw, 135, y + 34, 490, 28, ratio, color, phase=phase)
        y += 96

    draw.rounded_rectangle((125, 725, 650, 825), radius=24, fill=(65, 45, 30, 170))
    draw.text((150, 748), f"NIN {profile.ninjutsu}   TAI {profile.taijutsu}   GEN {profile.genjutsu}", fill=(255, 241, 221), font=small_font)
    draw.text((150, 783), f"DEF {profile.defense}   SPD {profile.speed}   CTRL {profile.chakra_control}", fill=(255, 241, 221), font=small_font)

    if screen == "ready":
        readiness = int(max(0, min(100, hp_ratio * 40 + ch_ratio * 30 + en_ratio * 30)))
        glow = 145 + int(45 * (0.5 + 0.5 * math.sin(phase * math.tau)))
        draw.rounded_rectangle((420, 112, 660, 178), radius=22, fill=(42, 30, 21, glow), outline=(250, 218, 120, 170), width=2)
        draw.text((540, 127), f"Готовность {readiness}%", fill=(255, 242, 219), font=body_bold, anchor="ma")

    image = Image.alpha_composite(image, overlay)
    return _fit_frame(image)


def _build_profile_animation(
    base_path: Path,
    profile: NinjaProfile,
    current: dict[str, int],
    previous: dict[str, int],
    screen: str,
) -> Path:
    frames: list[Image.Image] = []
    steps = 10
    for step in range(steps):
        raw_t = step / max(1, steps - 1)
        t = _ease(raw_t)
        values = {
            "hp": _lerp(previous["hp"], current["hp"], t),
            "max_hp": current["max_hp"],
            "chakra": _lerp(previous["chakra"], current["chakra"], t),
            "max_chakra": current["max_chakra"],
            "energy": _lerp(previous["energy"], current["energy"], t),
            "xp": _lerp(previous["xp"], current["xp"], t),
            "xp_cap": current["xp_cap"],
        }
        frames.append(_render_profile_frame(base_path, profile, values, screen, phase=raw_t))
    out = CACHE_DIR / f"profile_{screen}_{profile.user_id}.gif"
    frames[0].save(out, save_all=True, append_images=frames[1:], duration=85, loop=0, optimize=True, disposal=2)
    return out


def _render_generic_frame(
    base_path: Path,
    profile: NinjaProfile,
    values: dict[str, int],
    screen: str,
    *,
    phase: float,
) -> Image.Image:
    image = Image.open(base_path).convert("RGBA")
    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay, "RGBA")
    title_font = _font(31, bold=True)
    small_font = _font(20, bold=True)
    value_font = _font(18, bold=True)

    # Compact HUD: it keeps the art visible while every mechanic still shows live stats.
    pulse = 175 + int(20 * (0.5 + 0.5 * math.sin(phase * math.tau)))
    draw.rounded_rectangle((54, 42, 1618, 174), radius=28, fill=(10, 15, 25, pulse), outline=(235, 205, 148, 130), width=2)
    title = SCREEN_TITLES.get(screen, screen.replace("_", " ").title())
    draw.text((85, 62), title, fill=(255, 237, 201), font=title_font)
    draw.text((1578, 65), f"{profile.name} • ур. {profile.level}", fill=(238, 241, 247), font=small_font, anchor="ra")

    hp_ratio = _ratio(values["hp"], values["max_hp"])
    ch_ratio = _ratio(values["chakra"], values["max_chakra"])
    en_ratio = _ratio(values["energy"], 100)
    columns = [
        (85, "HP", values["hp"], values["max_hp"], hp_ratio, (214, 65, 77)),
        (575, "CHAKRA", values["chakra"], values["max_chakra"], ch_ratio, (48, 129, 235)),
        (1065, "ENERGY", values["energy"], 100, en_ratio, (105, 184, 78)),
    ]
    for x, label, cur, maximum, ratio, color in columns:
        draw.text((x, 114), label, fill=(235, 240, 248), font=value_font)
        draw.text((x + 405, 114), f"{cur}/{maximum}", fill=(235, 240, 248), font=value_font, anchor="ra")
        _draw_bar(draw, x, 139, 405, 22, ratio, color, phase=phase)

    image = Image.alpha_composite(image, overlay)
    return _fit_frame(image)


def _build_generic_animation(
    base_path: Path,
    profile: NinjaProfile,
    current: dict[str, int],
    previous: dict[str, int],
    screen: str,
) -> Path:
    frames: list[Image.Image] = []
    steps = 7
    for step in range(steps):
        raw_t = step / max(1, steps - 1)
        t = _ease(raw_t)
        values = {
            "hp": _lerp(previous["hp"], current["hp"], t),
            "max_hp": current["max_hp"],
            "chakra": _lerp(previous["chakra"], current["chakra"], t),
            "max_chakra": current["max_chakra"],
            "energy": _lerp(previous["energy"], current["energy"], t),
            "xp": _lerp(previous["xp"], current["xp"], t),
            "xp_cap": current["xp_cap"],
        }
        frames.append(_render_generic_frame(base_path, profile, values, screen, phase=raw_t))
    out = CACHE_DIR / f"screen_{screen}_{profile.user_id}_{current['hp']}_{current['chakra']}_{current['energy']}.gif"
    frames[0].save(out, save_all=True, append_images=frames[1:], duration=95, loop=0, optimize=True, disposal=2)
    return out


def _battle_actor(state: dict[str, Any] | None, side: str) -> dict[str, Any]:
    if not state:
        return {}
    actor = state.get(side) or {}
    return dict(actor)


def _actor_values(actor: dict[str, Any]) -> dict[str, int]:
    return {
        "hp": int(actor.get("hp", 0)),
        "max_hp": max(1, int(actor.get("max_hp", 1))),
        "chakra": int(actor.get("chakra", 0)),
        "max_chakra": max(1, int(actor.get("max_chakra", 1))),
    }


def _transition_actor(previous: dict[str, Any], current: dict[str, Any], t: float) -> dict[str, int]:
    p = _actor_values(previous or current)
    c = _actor_values(current)
    return {
        "hp": _lerp(p["hp"], c["hp"], t),
        "max_hp": c["max_hp"],
        "chakra": _lerp(p["chakra"], c["chakra"], t),
        "max_chakra": c["max_chakra"],
    }


def _delta_text(before: int, after: int) -> str:
    delta = after - before
    if delta > 0:
        return f"+{delta}"
    if delta < 0:
        return str(delta)
    return ""


def _render_battle_frame(
    base_path: Path,
    previous_state: dict[str, Any] | None,
    current_state: dict[str, Any],
    *,
    phase: float,
    transition_t: float,
    finished: bool,
) -> Image.Image:
    base = Image.open(base_path).convert("RGBA")
    # Slight breathing zoom for a more alive background.
    zoom = 1.0 + 0.006 * math.sin(phase * math.tau)
    if zoom != 1.0:
        w, h = base.size
        zw, zh = int(w * zoom), int(h * zoom)
        tmp = base.resize((zw, zh), Image.Resampling.BICUBIC)
        left, top = (zw - w) // 2, (zh - h) // 2
        base = tmp.crop((left, top, left + w, top + h))

    damage_overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
    damage_draw = ImageDraw.Draw(damage_overlay, "RGBA")
    overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay, "RGBA")
    title_font = _font(36, bold=True)
    name_font = _font(30, bold=True)
    value_font = _font(23, bold=True)
    delta_font = _font(25, bold=True)
    status_font = _font(20)

    p_now = _battle_actor(current_state, "player")
    e_now = _battle_actor(current_state, "enemy")
    p_prev = _battle_actor(previous_state, "player") or p_now
    e_prev = _battle_actor(previous_state, "enemy") or e_now
    p = _transition_actor(p_prev, p_now, transition_t)
    e = _transition_actor(e_prev, e_now, transition_t)

    # HUD glass panels.
    draw.rounded_rectangle((40, 30, 595, 230), radius=28, fill=(12, 17, 29, 205), outline=(95, 171, 255, 160), width=3)
    draw.rounded_rectangle((1077, 30, 1632, 230), radius=28, fill=(29, 12, 20, 205), outline=(255, 103, 123, 170), width=3)
    draw.rounded_rectangle((702, 30, 970, 100), radius=24, fill=(16, 15, 20, 205), outline=(244, 211, 142, 150), width=2)

    turn = int(current_state.get("turn", 1))
    draw.text((836, 48), f"ХОД {turn}", fill=(255, 236, 194), font=title_font, anchor="ma")

    # Player panel.
    p_name = str(p_now.get("name") or "Шиноби")[:24]
    draw.text((72, 55), p_name, fill=(235, 245, 255), font=name_font)
    draw.text((72, 101), "HP", fill=(255, 205, 205), font=value_font)
    _draw_bar(draw, 125, 105, 410, 26, _ratio(p["hp"], p["max_hp"]), (214, 62, 75), phase=phase)
    draw.text((540, 102), f"{p['hp']}/{p['max_hp']}", fill=(255, 235, 235), font=value_font, anchor="ra")
    draw.text((72, 155), "CHK", fill=(185, 220, 255), font=value_font)
    _draw_bar(draw, 125, 159, 410, 26, _ratio(p["chakra"], p["max_chakra"]), (48, 129, 235), phase=(phase + 0.25) % 1)
    draw.text((540, 156), f"{p['chakra']}/{p['max_chakra']}", fill=(220, 239, 255), font=value_font, anchor="ra")

    # Enemy panel, right aligned.
    e_name = str(e_now.get("name") or "Противник")[:24]
    draw.text((1600, 55), e_name, fill=(255, 235, 238), font=name_font, anchor="ra")
    draw.text((1104, 101), "HP", fill=(255, 205, 205), font=value_font)
    _draw_bar(draw, 1155, 105, 410, 26, _ratio(e["hp"], e["max_hp"]), (208, 52, 68), phase=(phase + 0.5) % 1)
    draw.text((1570, 102), f"{e['hp']}/{e['max_hp']}", fill=(255, 235, 235), font=value_font, anchor="ra")
    draw.text((1104, 155), "CHK", fill=(185, 220, 255), font=value_font)
    _draw_bar(draw, 1155, 159, 410, 26, _ratio(e["chakra"], e["max_chakra"]), (104, 79, 224), phase=(phase + 0.75) % 1)
    draw.text((1570, 156), f"{e['chakra']}/{e['max_chakra']}", fill=(220, 239, 255), font=value_font, anchor="ra")

    p0, p1 = _actor_values(p_prev), _actor_values(p_now)
    e0, e1 = _actor_values(e_prev), _actor_values(e_now)
    if transition_t > 0.25:
        pd_hp = _delta_text(p0["hp"], p1["hp"])
        pd_ch = _delta_text(p0["chakra"], p1["chakra"])
        ed_hp = _delta_text(e0["hp"], e1["hp"])
        ed_ch = _delta_text(e0["chakra"], e1["chakra"])
        if pd_hp:
            draw.text((560, 126), pd_hp, fill=(255, 104, 104) if pd_hp.startswith("-") else (117, 255, 150), font=delta_font, anchor="ra")
        if pd_ch:
            draw.text((560, 181), pd_ch, fill=(130, 187, 255) if pd_ch.startswith("+") else (194, 152, 255), font=delta_font, anchor="ra")
        if ed_hp:
            draw.text((1595, 126), ed_hp, fill=(255, 104, 104) if ed_hp.startswith("-") else (117, 255, 150), font=delta_font, anchor="ra")
        if ed_ch:
            draw.text((1595, 181), ed_ch, fill=(130, 187, 255) if ed_ch.startswith("+") else (194, 152, 255), font=delta_font, anchor="ra")

    # Damage flashes make a hit visually obvious.
    player_damaged = p1["hp"] < p0["hp"]
    enemy_damaged = e1["hp"] < e0["hp"]
    flash = max(0.0, math.sin(min(1.0, transition_t) * math.pi))
    if flash > 0.05:
        if player_damaged:
            damage_draw.rectangle((0, 0, 760, base.height), fill=(255, 25, 35, int(35 * flash)))
        if enemy_damaged:
            damage_draw.rectangle((900, 0, base.width, base.height), fill=(255, 35, 50, int(42 * flash)))

    # Small status strip from current battle state.
    p_status = [str(k) for k, v in (p_now.get("statuses") or {}).items() if int(v or 0) > 0][:3]
    e_status = [str(k) for k, v in (e_now.get("statuses") or {}).items() if int(v or 0) > 0][:3]
    if p_status:
        draw.text((72, 202), "Статусы: " + ", ".join(p_status), fill=(211, 221, 238), font=status_font)
    if e_status:
        draw.text((1600, 202), "Статусы: " + ", ".join(e_status), fill=(238, 211, 219), font=status_font, anchor="ra")

    if finished:
        if int(e_now.get("hp", 0)) <= 0:
            label, frame_color = "ПОБЕДА", (250, 206, 91, 225)
        elif int(p_now.get("hp", 0)) <= 0:
            label, frame_color = "ПОРАЖЕНИЕ", (217, 65, 79, 225)
        else:
            label, frame_color = "БОЙ ЗАВЕРШЁН", (230, 225, 215, 220)
        alpha = 135 + int(80 * (0.5 + 0.5 * math.sin(phase * math.tau)))
        draw.rounded_rectangle((570, 710, 1100, 845), radius=32, fill=(10, 10, 14, alpha), outline=frame_color, width=5)
        end_font = _font(52, bold=True)
        draw.text((835, 748), label, fill=frame_color, font=end_font, anchor="ma")

    base = Image.alpha_composite(base, damage_overlay)
    base = Image.alpha_composite(base, overlay)
    return _fit_frame(base)


def build_battle_animation(
    user_id: int,
    current_state: dict[str, Any],
    previous_state: dict[str, Any] | None = None,
    *,
    finished: bool = False,
) -> Path:
    base_path = _resolve_asset("battle")
    frames: list[Image.Image] = []
    steps = 12
    for index in range(steps):
        phase = index / steps
        # First 8 frames perform the stat transition; last frames hold final values while the HUD shimmers.
        transition_raw = min(1.0, index / 7.0)
        transition_t = _ease(transition_raw)
        frames.append(
            _render_battle_frame(
                base_path,
                previous_state,
                current_state,
                phase=phase,
                transition_t=transition_t,
                finished=finished,
            )
        )
    p = _actor_values(_battle_actor(current_state, "player"))
    e = _actor_values(_battle_actor(current_state, "enemy"))
    out = CACHE_DIR / f"battle_{user_id}_{int(current_state.get('turn', 1))}_{p['hp']}_{p['chakra']}_{e['hp']}_{e['chakra']}.gif"
    frames[0].save(out, save_all=True, append_images=frames[1:], duration=78, loop=0, optimize=True, disposal=2)
    return out


async def get_screen_media(user_id: int, screen: str) -> tuple[Path, bool]:
    screen = (screen or "home").lower()
    base_path = _resolve_asset(screen)
    if screen not in ANIMATED_SCREENS:
        return base_path, False

    async with SessionFactory() as session:
        profile = await get_profile(session, user_id)
        if profile is None:
            return base_path, False
        current = _snapshot(profile)
        previous = _previous_snapshot(profile, current)
        if screen in {"profile", "ready", "home"}:
            out = _build_profile_animation(base_path, profile, current, previous, screen)
        else:
            out = _build_generic_animation(base_path, profile, current, previous, screen)
        _remember_snapshot(profile, current)
        await session.commit()
        return out, True
