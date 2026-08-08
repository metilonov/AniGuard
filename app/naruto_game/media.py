from __future__ import annotations

import math
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont, ImageOps

from app.db import SessionFactory

from .models import NinjaProfile
from .service import get_profile

ASSET_DIR = Path(__file__).resolve().parents[2] / "assets" / "naruto_ui"
CACHE_DIR = Path("/tmp/aniguard_naruto_media")
CACHE_DIR.mkdir(parents=True, exist_ok=True)

FONT_REGULAR = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
FONT_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
OUTPUT_SIZE = (1280, 720)
FPS = 20
CRF = 18

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
    "home": "Командный центр",
    "profile": "Профиль шиноби",
    "daily": "Ежедневная награда",
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
    "ready": "Боевая готовность",
}


def _font(size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    try:
        return ImageFont.truetype(FONT_BOLD if bold else FONT_REGULAR, size)
    except Exception:
        return ImageFont.load_default()


def _resolve_asset(screen: str) -> Path:
    filename = SCREEN_ASSETS.get(screen, SCREEN_ASSETS["home"])
    path = ASSET_DIR / filename
    if path.exists():
        return path
    return ASSET_DIR / "profile.png"


def _load_canvas(path: Path) -> Image.Image:
    """Always create the overlay on the final 16:9 canvas, never after drawing."""
    image = Image.open(path).convert("RGB")
    image = ImageOps.fit(image, OUTPUT_SIZE, method=Image.Resampling.LANCZOS, centering=(0.5, 0.5))
    return image.convert("RGBA")


def _snapshot(profile: NinjaProfile) -> dict[str, int]:
    return {
        "hp": int(profile.hp),
        "max_hp": max(1, int(profile.max_hp)),
        "chakra": int(profile.chakra),
        "max_chakra": max(1, int(profile.max_chakra)),
        "energy": int(profile.energy),
        "xp": int(profile.xp),
        "xp_cap": max(100, int(profile.level) * 100),
    }


def _previous_snapshot(profile: NinjaProfile, current: dict[str, int]) -> dict[str, int]:
    raw = dict(profile.flags or {}).get("ui_last_stats") or {}
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
) -> None:
    ratio = max(0.0, min(1.0, ratio))
    radius = h // 2
    draw.rounded_rectangle((x, y, x + w, y + h), radius=radius, fill=(10, 12, 17, 230), outline=(240, 221, 180, 150), width=2)
    inner = 3
    ix0, iy0, ix1, iy1 = x + inner, y + inner, x + w - inner, y + h - inner
    draw.rounded_rectangle((ix0, iy0, ix1, iy1), radius=max(2, (h - inner * 2) // 2), fill=(47, 45, 48, 235))
    fill_w = int((ix1 - ix0) * ratio)
    if fill_w <= 0:
        return
    fx1 = ix0 + fill_w
    draw.rounded_rectangle((ix0, iy0, fx1, iy1), radius=max(2, (h - inner * 2) // 2), fill=(*color, 255))

    # Fine highlight instead of a broad white GIF band.
    if fill_w > 28:
        shine_w = min(46, max(16, fill_w // 5))
        travel = fill_w + shine_w
        sx = ix0 - shine_w + int((phase % 1.0) * travel)
        sx0, sx1 = max(ix0, sx), min(fx1, sx + shine_w)
        if sx1 > sx0:
            draw.rounded_rectangle((sx0, iy0 + 2, sx1, iy1 - 2), radius=max(2, (h - 10) // 2), fill=(255, 255, 255, 58))


def _write_frames(frames: list[Image.Image], directory: Path) -> None:
    for idx, frame in enumerate(frames):
        frame.convert("RGB").save(directory / f"frame_{idx:04d}.png", format="PNG", optimize=False)


def _ffmpeg_executable() -> str | None:
    system = shutil.which("ffmpeg")
    if system:
        return system
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def _encode_mp4(frames: list[Image.Image], out: Path, *, fps: int = FPS) -> bool:
    ffmpeg = _ffmpeg_executable()
    if not ffmpeg:
        return False
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="aniguard_anim_") as tmp_name:
        tmp = Path(tmp_name)
        _write_frames(frames, tmp)
        command = [
            ffmpeg,
            "-hide_banner",
            "-loglevel", "error",
            "-y",
            "-framerate", str(fps),
            "-i", str(tmp / "frame_%04d.png"),
            "-c:v", "libx264",
            "-preset", "medium",
            "-crf", str(CRF),
            "-pix_fmt", "yuv420p",
            "-movflags", "+faststart",
            "-an",
            str(out),
        ]
        try:
            subprocess.run(command, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=45)
        except Exception:
            return False
    return out.exists() and out.stat().st_size > 0


def _save_static(frame: Image.Image, out: Path) -> Path:
    out = out.with_suffix(".png")
    frame.convert("RGB").save(out, format="PNG", optimize=True)
    return out


def _profile_frame(base_path: Path, profile: NinjaProfile, values: dict[str, int], screen: str, *, phase: float) -> Image.Image:
    image = _load_canvas(base_path)
    overlay = Image.new("RGBA", OUTPUT_SIZE, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay, "RGBA")

    title_font = _font(30, bold=True)
    body_font = _font(19)
    body_bold = _font(20, bold=True)
    small_font = _font(16, bold=True)

    # The generated profile art intentionally has parchment on the left.
    panel = (50, 42, 565, 677)
    panel_alpha = 188 + int(8 * math.sin(phase * math.tau))
    draw.rounded_rectangle(panel, radius=24, fill=(241, 222, 188, panel_alpha), outline=(88, 61, 39, 155), width=2)

    title = SCREEN_TITLES.get(screen, "Статус шиноби")
    draw.text((78, 72), title, fill=(44, 30, 21), font=title_font)
    draw.text((78, 116), f"{profile.name}  •  ур. {profile.level}  •  {profile.ninja_rank}", fill=(61, 42, 29), font=body_bold)
    draw.text((78, 148), f"Деревня: {profile.village}  |  Стихия: {profile.primary_element}", fill=(69, 48, 33), font=body_font)
    draw.text((78, 179), f"Рё: {profile.ryo:,}  |  Репутация: {profile.reputation}", fill=(69, 48, 33), font=body_font)

    stats = [
        ("Здоровье", values["hp"], values["max_hp"], (208, 62, 70)),
        ("Чакра", values["chakra"], values["max_chakra"], (49, 127, 232)),
        ("Энергия", values["energy"], 100, (108, 181, 78)),
        ("Опыт", values["xp"], values["xp_cap"], (143, 90, 200)),
    ]
    y = 235
    for label, current, maximum, color in stats:
        draw.text((78, y), label, fill=(52, 36, 25), font=body_bold)
        draw.text((524, y + 1), f"{current}/{maximum}", fill=(52, 36, 25), font=body_font, anchor="ra")
        _draw_bar(draw, 78, y + 29, 446, 24, _ratio(current, maximum), color, phase=phase)
        y += 82

    draw.rounded_rectangle((72, 570, 536, 650), radius=18, fill=(40, 29, 24, 188))
    draw.text((92, 591), f"NIN {profile.ninjutsu}   TAI {profile.taijutsu}   GEN {profile.genjutsu}", fill=(255, 239, 211), font=small_font)
    draw.text((92, 620), f"DEF {profile.defense}   SPD {profile.speed}   CTRL {profile.chakra_control}", fill=(255, 239, 211), font=small_font)

    if screen == "ready":
        readiness = int(max(0, min(100, _ratio(values["hp"], values["max_hp"]) * 40 + _ratio(values["chakra"], values["max_chakra"]) * 30 + _ratio(values["energy"], 100) * 30)))
        draw.rounded_rectangle((350, 66, 535, 110), radius=15, fill=(28, 25, 22, 210), outline=(235, 202, 118, 175), width=2)
        draw.text((442, 78), f"Готовность {readiness}%", fill=(255, 240, 205), font=_font(18, bold=True), anchor="ma")

    return Image.alpha_composite(image, overlay).convert("RGB")


def _generic_frame(base_path: Path, profile: NinjaProfile, values: dict[str, int], screen: str, *, phase: float) -> Image.Image:
    image = _load_canvas(base_path)
    overlay = Image.new("RGBA", OUTPUT_SIZE, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay, "RGBA")

    title_font = _font(25, bold=True)
    value_font = _font(15, bold=True)
    panel_alpha = 196 + int(8 * math.sin(phase * math.tau))
    draw.rounded_rectangle((24, 20, 1256, 128), radius=24, fill=(8, 13, 22, panel_alpha), outline=(236, 207, 149, 130), width=2)
    draw.text((48, 40), SCREEN_TITLES.get(screen, "Мир шиноби"), fill=(255, 237, 200), font=title_font)
    draw.text((1230, 44), f"{profile.name} • ур. {profile.level}", fill=(237, 242, 249), font=_font(16, bold=True), anchor="ra")

    columns = [
        (48,  "HP",     values["hp"],      values["max_hp"],      (211, 62, 73)),
        (448, "CHAKRA", values["chakra"], values["max_chakra"], (47, 129, 234)),
        (848, "ENERGY", values["energy"], 100,                    (106, 181, 78)),
    ]
    for x, label, current, maximum, color in columns:
        draw.text((x, 80), label, fill=(238, 242, 248), font=value_font)
        draw.text((x + 355, 80), f"{current}/{maximum}", fill=(238, 242, 248), font=value_font, anchor="ra")
        _draw_bar(draw, x, 101, 355, 18, _ratio(current, maximum), color, phase=phase)

    return Image.alpha_composite(image, overlay).convert("RGB")


def _build_screen_animation(base_path: Path, profile: NinjaProfile, current: dict[str, int], previous: dict[str, int], screen: str) -> tuple[Path, bool]:
    frames: list[Image.Image] = []
    total_frames = 24
    for idx in range(total_frames):
        raw = idx / max(1, total_frames - 1)
        # Stats finish changing during the first 70%, the rest holds the clean final state.
        transition = _ease(min(1.0, raw / 0.70))
        values = {
            "hp": _lerp(previous["hp"], current["hp"], transition),
            "max_hp": current["max_hp"],
            "chakra": _lerp(previous["chakra"], current["chakra"], transition),
            "max_chakra": current["max_chakra"],
            "energy": _lerp(previous["energy"], current["energy"], transition),
            "xp": _lerp(previous["xp"], current["xp"], transition),
            "xp_cap": current["xp_cap"],
        }
        if screen in {"profile", "ready", "home", "daily"}:
            frame = _profile_frame(base_path, profile, values, "profile" if screen == "daily" else screen, phase=raw)
        else:
            frame = _generic_frame(base_path, profile, values, screen, phase=raw)
        frames.append(frame)

    cache_key = f"{screen}_{profile.user_id}_{current['hp']}_{current['chakra']}_{current['energy']}_{current['xp']}"
    out = CACHE_DIR / f"screen_{cache_key}.mp4"
    if out.exists() and out.stat().st_size > 0:
        return out, True
    if _encode_mp4(frames, out, fps=FPS):
        return out, True
    return _save_static(frames[-1], CACHE_DIR / f"screen_{cache_key}.png"), False


def _battle_actor(state: dict[str, Any] | None, side: str) -> dict[str, Any]:
    if not state:
        return {}
    return dict(state.get(side) or {})


def _actor_values(actor: dict[str, Any]) -> dict[str, int]:
    return {
        "hp": int(actor.get("hp", 0)),
        "max_hp": max(1, int(actor.get("max_hp", 1))),
        "chakra": int(actor.get("chakra", 0)),
        "max_chakra": max(1, int(actor.get("max_chakra", 1))),
    }


def _transition_actor(previous: dict[str, Any], current: dict[str, Any], t: float) -> dict[str, int]:
    before = _actor_values(previous or current)
    after = _actor_values(current)
    return {
        "hp": _lerp(before["hp"], after["hp"], t),
        "max_hp": after["max_hp"],
        "chakra": _lerp(before["chakra"], after["chakra"], t),
        "max_chakra": after["max_chakra"],
    }


def _delta(before: int, after: int) -> str:
    value = after - before
    if value > 0:
        return f"+{value}"
    if value < 0:
        return str(value)
    return ""


def _battle_frame(base_path: Path, previous_state: dict[str, Any] | None, current_state: dict[str, Any], *, phase: float, transition: float, finished: bool) -> Image.Image:
    image = _load_canvas(base_path)
    overlay = Image.new("RGBA", OUTPUT_SIZE, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay, "RGBA")

    p_now = _battle_actor(current_state, "player")
    e_now = _battle_actor(current_state, "enemy")
    p_prev = _battle_actor(previous_state, "player") or p_now
    e_prev = _battle_actor(previous_state, "enemy") or e_now
    p = _transition_actor(p_prev, p_now, transition)
    e = _transition_actor(e_prev, e_now, transition)

    # Clean symmetrical panels aligned to the final 1280x720 canvas.
    draw.rounded_rectangle((22, 18, 470, 155), radius=22, fill=(9, 15, 26, 215), outline=(83, 164, 250, 170), width=2)
    draw.rounded_rectangle((810, 18, 1258, 155), radius=22, fill=(29, 10, 18, 215), outline=(250, 91, 112, 175), width=2)
    draw.rounded_rectangle((545, 22, 735, 75), radius=17, fill=(15, 14, 19, 220), outline=(237, 204, 137, 155), width=2)

    name_font = _font(22, bold=True)
    label_font = _font(14, bold=True)
    number_font = _font(14, bold=True)
    turn_font = _font(21, bold=True)
    delta_font = _font(17, bold=True)

    draw.text((42, 34), str(p_now.get("name") or "Шиноби")[:24], fill=(235, 245, 255), font=name_font)
    draw.text((1238, 34), str(e_now.get("name") or "Противник")[:24], fill=(255, 235, 239), font=name_font, anchor="ra")
    draw.text((640, 35), f"ХОД {int(current_state.get('turn', 1))}", fill=(255, 235, 191), font=turn_font, anchor="ma")

    # Player bars
    draw.text((42, 75), "HP", fill=(255, 208, 212), font=label_font)
    draw.text((438, 75), f"{p['hp']}/{p['max_hp']}", fill=(255, 233, 236), font=number_font, anchor="ra")
    _draw_bar(draw, 42, 96, 396, 18, _ratio(p["hp"], p["max_hp"]), (211, 61, 73), phase=phase)
    draw.text((42, 120), "CHAKRA", fill=(195, 222, 255), font=label_font)
    draw.text((438, 120), f"{p['chakra']}/{p['max_chakra']}", fill=(222, 239, 255), font=number_font, anchor="ra")
    _draw_bar(draw, 42, 140, 396, 14, _ratio(p["chakra"], p["max_chakra"]), (47, 128, 234), phase=(phase + 0.2) % 1.0)

    # Enemy bars
    draw.text((832, 75), "HP", fill=(255, 208, 212), font=label_font)
    draw.text((1236, 75), f"{e['hp']}/{e['max_hp']}", fill=(255, 233, 236), font=number_font, anchor="ra")
    _draw_bar(draw, 832, 96, 404, 18, _ratio(e["hp"], e["max_hp"]), (204, 51, 68), phase=(phase + 0.5) % 1.0)
    draw.text((832, 120), "CHAKRA", fill=(195, 222, 255), font=label_font)
    draw.text((1236, 120), f"{e['chakra']}/{e['max_chakra']}", fill=(222, 239, 255), font=number_font, anchor="ra")
    _draw_bar(draw, 832, 140, 404, 14, _ratio(e["chakra"], e["max_chakra"]), (102, 78, 221), phase=(phase + 0.7) % 1.0)

    p0, p1 = _actor_values(p_prev), _actor_values(p_now)
    e0, e1 = _actor_values(e_prev), _actor_values(e_now)
    if transition > 0.16:
        pd_hp, pd_ch = _delta(p0["hp"], p1["hp"]), _delta(p0["chakra"], p1["chakra"])
        ed_hp, ed_ch = _delta(e0["hp"], e1["hp"]), _delta(e0["chakra"], e1["chakra"])
        if pd_hp:
            draw.text((462, 93), pd_hp, fill=(255, 99, 108) if pd_hp.startswith("-") else (115, 244, 145), font=delta_font, anchor="ra")
        if pd_ch:
            draw.text((462, 136), pd_ch, fill=(181, 159, 255) if pd_ch.startswith("-") else (114, 190, 255), font=delta_font, anchor="ra")
        if ed_hp:
            draw.text((1253, 93), ed_hp, fill=(255, 99, 108) if ed_hp.startswith("-") else (115, 244, 145), font=delta_font, anchor="ra")
        if ed_ch:
            draw.text((1253, 136), ed_ch, fill=(181, 159, 255) if ed_ch.startswith("-") else (114, 190, 255), font=delta_font, anchor="ra")

    # Subtle damage flash, no camera zoom/distortion.
    flash = max(0.0, math.sin(min(1.0, transition) * math.pi))
    if flash > 0.05:
        if p1["hp"] < p0["hp"]:
            draw.rectangle((0, 0, 500, 720), fill=(255, 20, 30, int(18 * flash)))
        if e1["hp"] < e0["hp"]:
            draw.rectangle((780, 0, 1280, 720), fill=(255, 25, 38, int(20 * flash)))

    if finished:
        if int(e_now.get("hp", 0)) <= 0:
            label, color = "ПОБЕДА", (249, 207, 91)
        elif int(p_now.get("hp", 0)) <= 0:
            label, color = "ПОРАЖЕНИЕ", (227, 70, 84)
        else:
            label, color = "БОЙ ЗАВЕРШЁН", (235, 229, 217)
        alpha = 215 + int(15 * math.sin(phase * math.tau))
        draw.rounded_rectangle((420, 585, 860, 665), radius=26, fill=(9, 10, 15, alpha), outline=(*color, 225), width=3)
        draw.text((640, 609), label, fill=color, font=_font(36, bold=True), anchor="ma")

    return Image.alpha_composite(image, overlay).convert("RGB")


def build_battle_animation(user_id: int, current_state: dict[str, Any], previous_state: dict[str, Any] | None = None, *, finished: bool = False) -> Path:
    base_path = _resolve_asset("battle")
    frames: list[Image.Image] = []
    total_frames = 26
    for idx in range(total_frames):
        raw = idx / max(1, total_frames - 1)
        transition = _ease(min(1.0, raw / 0.68))
        frames.append(_battle_frame(base_path, previous_state, current_state, phase=raw, transition=transition, finished=finished))

    p = _actor_values(_battle_actor(current_state, "player"))
    e = _actor_values(_battle_actor(current_state, "enemy"))
    out = CACHE_DIR / f"battle_{user_id}_{int(current_state.get('turn', 1))}_{p['hp']}_{p['chakra']}_{e['hp']}_{e['chakra']}_{int(finished)}.mp4"
    if out.exists() and out.stat().st_size > 0:
        return out
    if _encode_mp4(frames, out, fps=FPS):
        return out
    return _save_static(frames[-1], CACHE_DIR / f"battle_{user_id}_{int(current_state.get('turn', 1))}_{p['hp']}_{e['hp']}.png")


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
        out, animated = _build_screen_animation(base_path, profile, current, previous, screen)
        _remember_snapshot(profile, current)
        await session.commit()
        return out, animated
