from __future__ import annotations

import asyncio
import io
import json
import logging
import os
import time
from pathlib import Path
from typing import Any

from aiogram import Bot
from aiogram.types import Message
from PIL import Image, ImageOps, UnidentifiedImageError

logger = logging.getLogger(__name__)

EXPLICIT_CLASSES = {
    "ANUS_EXPOSED",
    "BUTTOCKS_EXPOSED",
    "FEMALE_BREAST_EXPOSED",
    "FEMALE_GENITALIA_EXPOSED",
    "MALE_GENITALIA_EXPOSED",
}

REFERENCE_DB = Path(__file__).with_name("media_safety_refs.json")
RUNTIME_REFERENCE_DB = Path(os.getenv("DATA_DIR", "/app/data")) / "media_safety_refs.json"

_detector: Any | None = None
_detector_lock = asyncio.Lock()
_scan_semaphore = asyncio.Semaphore(max(1, int(os.getenv("LOCAL_MEDIA_SCAN_CONCURRENCY", "1"))))
_result_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_nudenet_unavailable_logged = False


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _file_identity(item: Any, fallback: str) -> str:
    return str(
        getattr(item, "file_unique_id", None)
        or getattr(item, "file_id", None)
        or fallback
    )


def _media_candidate(message: Message) -> tuple[str, str, str, str] | None:
    """Return Telegram file_id, media kind, cache key and filename."""

    if message.photo:
        photo = message.photo[-1]
        return (
            photo.file_id,
            "фото",
            _file_identity(photo, f"photo:{message.chat.id}:{message.message_id}"),
            "",
        )

    if message.sticker:
        sticker = message.sticker
        cache_key = _file_identity(
            sticker,
            f"sticker:{message.chat.id}:{message.message_id}",
        )

        if not bool(getattr(sticker, "is_animated", False)) and not bool(
            getattr(sticker, "is_video", False)
        ):
            return sticker.file_id, "стикер", cache_key, ""

        thumbnail = getattr(sticker, "thumbnail", None)
        if thumbnail is not None:
            return thumbnail.file_id, "стикер", cache_key, ""
        return None

    if message.animation:
        animation = message.animation
        cache_key = _file_identity(
            animation,
            f"animation:{message.chat.id}:{message.message_id}",
        )
        file_name = str(getattr(animation, "file_name", "") or "")
        mime_type = str(getattr(animation, "mime_type", "") or "").casefold()

        if mime_type == "image/gif" or file_name.casefold().endswith(".gif"):
            return animation.file_id, "GIF", cache_key, file_name

        thumbnail = getattr(animation, "thumbnail", None)
        if thumbnail is not None:
            return thumbnail.file_id, "GIF", cache_key, file_name
        return None

    if message.document:
        document = message.document
        mime_type = str(document.mime_type or "").casefold()
        file_name = str(document.file_name or "")
        suffix = Path(file_name).suffix.casefold()
        supported = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}

        if mime_type.startswith("image/") or suffix in supported:
            return (
                document.file_id,
                "изображение",
                _file_identity(
                    document,
                    f"document:{message.chat.id}:{message.message_id}",
                ),
                file_name,
            )

    return None


async def _download_media(bot: Bot, file_id: str) -> bytes:
    telegram_file = await bot.get_file(file_id)
    if not telegram_file.file_path:
        raise RuntimeError("Telegram не вернул путь к медиафайлу")

    buffer = io.BytesIO()
    await bot.download_file(telegram_file.file_path, destination=buffer)
    payload = buffer.getvalue()

    if not payload:
        raise RuntimeError("Получен пустой медиафайл")

    max_bytes = max(500_000, _env_int("LOCAL_MEDIA_MAX_BYTES", 12_000_000))
    if len(payload) > max_bytes:
        raise ValueError("Медиафайл превышает лимит локальной проверки")

    return payload


def _sample_indices(frame_count: int, max_frames: int) -> list[int]:
    if frame_count <= 1:
        return [0]
    count = max(1, min(max_frames, frame_count))
    if count == 1:
        return [0]
    return sorted(
        {
            round(position * (frame_count - 1) / (count - 1))
            for position in range(count)
        }
    )


def _normalize_frame(image: Image.Image) -> Image.Image:
    frame = ImageOps.exif_transpose(image).convert("RGB")
    frame.thumbnail((1280, 1280), Image.Resampling.LANCZOS)
    return frame


def _extract_frames(payload: bytes) -> list[Image.Image]:
    max_frames = max(1, min(_env_int("LOCAL_MEDIA_MAX_FRAMES", 5), 10))

    with Image.open(io.BytesIO(payload)) as image:
        frame_count = max(1, int(getattr(image, "n_frames", 1)))
        frames: list[Image.Image] = []

        for index in _sample_indices(frame_count, max_frames):
            image.seek(index)
            frames.append(_normalize_frame(image.copy()))

        return frames


def _frame_bytes(frame: Image.Image) -> bytes:
    buffer = io.BytesIO()
    frame.save(buffer, format="JPEG", quality=90, optimize=True)
    return buffer.getvalue()


def _average_hash(image: Image.Image, size: int = 8) -> str:
    gray = image.convert("L").resize((size, size), Image.Resampling.LANCZOS)
    pixels = list(gray.getdata())
    average = sum(pixels) / max(1, len(pixels))
    value = 0
    for pixel in pixels:
        value = (value << 1) | int(pixel >= average)
    return f"{value:0{size * size // 4}x}"


def _difference_hash(image: Image.Image, size: int = 8) -> str:
    gray = image.convert("L").resize((size + 1, size), Image.Resampling.LANCZOS)
    pixels = list(gray.getdata())
    value = 0

    for row in range(size):
        offset = row * (size + 1)
        for column in range(size):
            value = (value << 1) | int(
                pixels[offset + column] > pixels[offset + column + 1]
            )

    return f"{value:0{size * size // 4}x}"


def _hash_distance(left: str, right: str) -> int:
    try:
        return (int(left, 16) ^ int(right, 16)).bit_count()
    except (TypeError, ValueError):
        return 10_000


def _load_db(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"references": [], "terms": []}
    return data if isinstance(data, dict) else {"references": [], "terms": []}


def _load_reference_database() -> tuple[list[dict[str, str]], list[str]]:
    references: list[dict[str, str]] = []
    terms: list[str] = []
    seen: set[tuple[str, str]] = set()

    for path in (REFERENCE_DB, RUNTIME_REFERENCE_DB):
        data = _load_db(path)

        for row in data.get("references", []):
            if not isinstance(row, dict):
                continue
            dhash = str(row.get("dhash") or "").strip().casefold()
            ahash = str(row.get("ahash") or "").strip().casefold()
            identity = (dhash, ahash)
            if not dhash or not ahash or identity in seen:
                continue
            seen.add(identity)
            references.append(
                {
                    "dhash": dhash,
                    "ahash": ahash,
                    "label": str(row.get("label") or "локальная запрещённая база")[:120],
                }
            )

        for term in data.get("terms", []):
            normalized = str(term or "").strip().casefold()
            if normalized and normalized not in terms:
                terms.append(normalized)

    return references, terms


def _match_reference(
    frames: list[Image.Image],
    visible_text: str,
) -> tuple[bool, float, str]:
    references, terms = _load_reference_database()
    normalized_text = " ".join(visible_text.casefold().split())

    for term in terms:
        if term and term in normalized_text:
            return True, 1.0, f"совпадение с локальным термином: {term}"

    if not references:
        return False, 0.0, ""

    max_distance = max(0, min(_env_int("LOCAL_MEDIA_HASH_DISTANCE", 8), 32))
    best_confidence = 0.0
    best_label = ""

    for frame in frames:
        frame_dhash = _difference_hash(frame)
        frame_ahash = _average_hash(frame)

        for reference in references:
            d_distance = _hash_distance(frame_dhash, reference["dhash"])
            a_distance = _hash_distance(frame_ahash, reference["ahash"])
            distance = min(d_distance, a_distance)
            confidence = max(0.0, 1.0 - distance / 64.0)

            if confidence > best_confidence:
                best_confidence = confidence
                best_label = reference["label"]

            if d_distance <= max_distance and a_distance <= max_distance + 2:
                return True, confidence, reference["label"]

    return False, best_confidence, best_label


async def _get_detector() -> Any | None:
    global _detector, _nudenet_unavailable_logged

    if _detector is not None:
        return _detector

    async with _detector_lock:
        if _detector is not None:
            return _detector

        try:
            from nudenet import NudeDetector

            _detector = await asyncio.to_thread(NudeDetector)
        except Exception as exc:
            if not _nudenet_unavailable_logged:
                logger.exception("NudeNet недоступен: %s", exc)
                _nudenet_unavailable_logged = True
            return None

    return _detector


async def _nudity_score(frames: list[Image.Image]) -> tuple[float, list[str]]:
    detector = await _get_detector()
    if detector is None:
        return 0.0, []

    threshold = max(0.0, min(_env_float("LOCAL_MEDIA_NSFW_THRESHOLD", 0.62), 1.0))
    best_score = 0.0
    found_classes: set[str] = set()

    for frame in frames:
        detections = await asyncio.to_thread(detector.detect, _frame_bytes(frame))

        for detection in detections or []:
            if not isinstance(detection, dict):
                continue
            class_name = str(detection.get("class") or "").upper()
            try:
                score = float(detection.get("score") or 0.0)
            except (TypeError, ValueError):
                score = 0.0

            if class_name in EXPLICIT_CLASSES:
                best_score = max(best_score, score)
                if score >= threshold:
                    found_classes.add(class_name)

    return best_score, sorted(found_classes)


def _prune_cache() -> None:
    if len(_result_cache) <= 1500:
        return
    oldest = sorted(_result_cache.items(), key=lambda item: item[1][0])[:300]
    for key, _ in oldest:
        _result_cache.pop(key, None)


async def classify_message_media(bot: Bot, message: Message) -> dict[str, Any] | None:
    """Check Telegram media locally. No external neural-network API is used."""

    candidate = _media_candidate(message)
    if candidate is None:
        return None

    file_id, media_kind, cache_key, file_name = candidate
    now = time.monotonic()
    cache_seconds = max(60, _env_int("LOCAL_MEDIA_CACHE_SECONDS", 21_600))
    cached = _result_cache.get(cache_key)

    if cached and now - cached[0] <= cache_seconds:
        return dict(cached[1])

    async with _scan_semaphore:
        try:
            payload = await _download_media(bot, file_id)
            frames = await asyncio.to_thread(_extract_frames, payload)
        except (UnidentifiedImageError, OSError, ValueError, RuntimeError) as exc:
            logger.warning(
                "Не удалось извлечь кадры: chat=%s message=%s error=%s",
                message.chat.id,
                message.message_id,
                exc,
            )
            return None

        sexual_score = 0.0
        exposed_classes: list[str] = []
        timeout = max(3.0, _env_float("LOCAL_MEDIA_SCAN_TIMEOUT", 25.0))

        try:
            sexual_score, exposed_classes = await asyncio.wait_for(
                _nudity_score(frames),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            logger.warning(
                "Локальная NSFW-проверка превысила таймаут: chat=%s message=%s",
                message.chat.id,
                message.message_id,
            )
        except Exception as exc:
            logger.exception(
                "Ошибка локальной NSFW-проверки: chat=%s message=%s error=%s",
                message.chat.id,
                message.message_id,
                exc,
            )

        visible_text = " ".join(
            value for value in (message.caption or "", file_name) if value
        )
        extremist, extremist_confidence, extremist_reason = await asyncio.to_thread(
            _match_reference,
            frames,
            visible_text,
        )

        threshold = max(0.0, min(_env_float("LOCAL_MEDIA_NSFW_THRESHOLD", 0.62), 1.0))
        sexual = bool(exposed_classes) and sexual_score >= threshold
        labels: list[str] = []

        if sexual:
            labels.append("порнографический или откровенный контент")
        if extremist:
            labels.append("совпадение с локальной базой экстремистских материалов")

        result: dict[str, Any] = {
            "unsafe": bool(labels),
            "labels": labels,
            "media_kind": media_kind,
            "sexual": sexual,
            "sexual_score": round(float(sexual_score), 4),
            "sexual_classes": exposed_classes,
            "extremist": extremist,
            "extremist_confidence": round(float(extremist_confidence), 4),
            "extremist_reason": extremist_reason,
            "provider": "local",
        }

        _result_cache[cache_key] = (now, result)
        _prune_cache()
        return dict(result)


inspect_message_media = classify_message_media
