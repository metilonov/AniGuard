from __future__ import annotations

import asyncio
import io
import json
import logging
import os
import re
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from aiogram import Bot
from aiogram.types import Message
from PIL import Image, ImageOps, UnidentifiedImageError

logger = logging.getLogger(__name__)

EXPLICIT_CLASSES = {
    # NudeNet 3.x.
    "ANUS_EXPOSED",
    "BUTTOCKS_EXPOSED",
    "FEMALE_BREAST_EXPOSED",
    "FEMALE_GENITALIA_EXPOSED",
    "MALE_GENITALIA_EXPOSED",
    # Совместимость со старыми названиями NudeNet.
    "EXPOSED_ANUS",
    "EXPOSED_BUTTOCKS",
    "EXPOSED_BREAST_F",
    "EXPOSED_GENITALIA_F",
    "EXPOSED_GENITALIA_M",
    "F_BREAST",
    "F_GENITALIA",
    "M_GENITALIA",
}

SEXUAL_CONTEXT_CLASSES = {
    "ANUS_COVERED",
    "BUTTOCKS_COVERED",
    "FEMALE_BREAST_COVERED",
    "FEMALE_GENITALIA_COVERED",
    "MALE_GENITALIA_COVERED",
    "COVERED_ANUS",
    "COVERED_BUTTOCKS",
    "COVERED_BREAST_F",
    "COVERED_GENITALIA_F",
    "COVERED_GENITALIA_M",
}

FACE_CLASSES = {
    "FACE_FEMALE",
    "FACE_MALE",
    "FEMALE_FACE",
    "MALE_FACE",
}

REFERENCE_DB = Path(__file__).with_name("media_safety_refs.json")
RUNTIME_REFERENCE_DB = (
    Path(os.getenv("DATA_DIR", "/app/data")) / "media_safety_refs.json"
)

_detector: Any | None = None
_detector_lock = asyncio.Lock()
_scan_semaphore = asyncio.Semaphore(
    max(1, int(os.getenv("LOCAL_MEDIA_SCAN_CONCURRENCY", "1")))
)
_result_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_nudenet_unavailable_logged = False
_ffmpeg_unavailable_logged = False

CACHE_VERSION = "media-v4"


@dataclass(frozen=True)
class MediaCandidate:
    file_id: str
    media_kind: str
    cache_key: str
    file_name: str = ""
    moving: bool = False
    thumbnail_file_id: str = ""


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


def _thumbnail_id(item: Any) -> str:
    thumbnail = getattr(item, "thumbnail", None)
    return str(getattr(thumbnail, "file_id", "") or "")


def _media_candidate(message: Message) -> MediaCandidate | None:
    """Return the original Telegram file instead of only its preview."""
    if message.photo:
        photo = message.photo[-1]
        return MediaCandidate(
            photo.file_id,
            "фото",
            _file_identity(
                photo,
                f"photo:{message.chat.id}:{message.message_id}",
            ),
            "photo.jpg",
        )

    if message.sticker:
        sticker = message.sticker
        is_animated = bool(getattr(sticker, "is_animated", False))
        is_video = bool(getattr(sticker, "is_video", False))
        return MediaCandidate(
            sticker.file_id,
            (
                "видео-стикер"
                if is_video
                else "анимированный стикер"
                if is_animated
                else "стикер"
            ),
            _file_identity(
                sticker,
                f"sticker:{message.chat.id}:{message.message_id}",
            ),
            (
                "sticker.webm"
                if is_video
                else "sticker.tgs"
                if is_animated
                else "sticker.webp"
            ),
            moving=is_video or is_animated,
            thumbnail_file_id=_thumbnail_id(sticker),
        )

    if message.animation:
        animation = message.animation
        return MediaCandidate(
            animation.file_id,
            "GIF",
            _file_identity(
                animation,
                f"animation:{message.chat.id}:{message.message_id}",
            ),
            str(getattr(animation, "file_name", "") or "animation.mp4"),
            moving=True,
            thumbnail_file_id=_thumbnail_id(animation),
        )

    if message.video:
        video = message.video
        return MediaCandidate(
            video.file_id,
            "видео",
            _file_identity(
                video,
                f"video:{message.chat.id}:{message.message_id}",
            ),
            str(getattr(video, "file_name", "") or "video.mp4"),
            moving=True,
            thumbnail_file_id=_thumbnail_id(video),
        )

    if message.video_note:
        video_note = message.video_note
        return MediaCandidate(
            video_note.file_id,
            "видеосообщение",
            _file_identity(
                video_note,
                f"video-note:{message.chat.id}:{message.message_id}",
            ),
            "video_note.mp4",
            moving=True,
            thumbnail_file_id=_thumbnail_id(video_note),
        )

    if message.document:
        document = message.document
        mime_type = str(document.mime_type or "").casefold()
        file_name = str(document.file_name or "")
        suffix = Path(file_name).suffix.casefold()
        supported_images = {
            ".jpg",
            ".jpeg",
            ".png",
            ".webp",
            ".gif",
            ".bmp",
        }
        supported_video = {
            ".mp4",
            ".webm",
            ".mov",
            ".mkv",
            ".avi",
            ".m4v",
        }
        supported_sticker = {".tgs"}

        if (
            mime_type.startswith("image/")
            or mime_type.startswith("video/")
            or suffix in supported_images
            or suffix in supported_video
            or suffix in supported_sticker
        ):
            moving = (
                mime_type.startswith("video/")
                or suffix in supported_video
                or suffix in {".gif", ".tgs"}
            )
            return MediaCandidate(
                document.file_id,
                "медиафайл",
                _file_identity(
                    document,
                    f"document:{message.chat.id}:{message.message_id}",
                ),
                file_name,
                moving=moving,
                thumbnail_file_id=_thumbnail_id(document),
            )

    return None


async def _download_media(
    bot: Bot,
    file_id: str,
) -> tuple[bytes, str]:
    telegram_file = await bot.get_file(file_id)
    if not telegram_file.file_path:
        raise RuntimeError("Telegram не вернул путь к медиафайлу")

    buffer = io.BytesIO()
    await bot.download_file(
        telegram_file.file_path,
        destination=buffer,
    )
    payload = buffer.getvalue()

    if not payload:
        raise RuntimeError("Получен пустой медиафайл")

    max_bytes = max(
        500_000,
        _env_int("LOCAL_MEDIA_MAX_BYTES", 19_000_000),
    )
    if len(payload) > max_bytes:
        raise ValueError(
            "Медиафайл превышает лимит локальной проверки"
        )

    return payload, str(telegram_file.file_path)


def _sample_indices(
    frame_count: int,
    max_frames: int,
) -> list[int]:
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
    frame = ImageOps.exif_transpose(image)

    # У прозрачных стикеров convert("RGB") создаёт чёрный фон,
    # который ухудшает распознавание. Компонуем на белом фоне.
    if frame.mode in {"RGBA", "LA"} or "transparency" in frame.info:
        rgba = frame.convert("RGBA")
        background = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
        background.alpha_composite(rgba)
        frame = background.convert("RGB")
    else:
        frame = frame.convert("RGB")

    frame.thumbnail((1280, 1280), Image.Resampling.LANCZOS)
    return frame


def _extract_pillow_frames(
    payload: bytes,
    max_frames: int,
) -> list[Image.Image]:
    with Image.open(io.BytesIO(payload)) as image:
        frame_count = max(
            1,
            int(getattr(image, "n_frames", 1)),
        )
        frames: list[Image.Image] = []

        for index in _sample_indices(frame_count, max_frames):
            image.seek(index)
            frames.append(_normalize_frame(image.copy()))

        return frames


def _get_ffmpeg_exe() -> str:
    global _ffmpeg_unavailable_logged

    try:
        import imageio_ffmpeg

        return str(imageio_ffmpeg.get_ffmpeg_exe())
    except Exception as exc:
        if not _ffmpeg_unavailable_logged:
            logger.exception(
                "Локальный FFmpeg недоступен: %s",
                exc,
            )
            _ffmpeg_unavailable_logged = True
        raise RuntimeError("FFmpeg недоступен") from exc


_DURATION_RE = re.compile(
    r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)"
)


def _probe_duration(
    ffmpeg_exe: str,
    input_path: Path,
) -> float:
    try:
        completed = subprocess.run(
            [
                ffmpeg_exe,
                "-hide_banner",
                "-i",
                str(input_path),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=8,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return 0.0

    text = completed.stderr.decode(
        "utf-8",
        errors="replace",
    )
    match = _DURATION_RE.search(text)
    if not match:
        return 0.0

    hours, minutes, seconds = match.groups()
    return (
        int(hours) * 3600
        + int(minutes) * 60
        + float(seconds)
    )


def _video_timestamps(
    duration: float,
    max_frames: int,
) -> list[float]:
    if duration > 0.2:
        usable = min(
            duration,
            max(
                1.0,
                _env_float(
                    "LOCAL_MEDIA_MAX_VIDEO_SECONDS",
                    180.0,
                ),
            ),
        )
        return [
            max(
                0.0,
                min(
                    duration - 0.03,
                    usable * (index + 0.5) / max_frames,
                ),
            )
            for index in range(max_frames)
        ]

    return [float(index) for index in range(max_frames)]


def _extract_video_frames(
    payload: bytes,
    suffix: str,
    max_frames: int,
) -> list[Image.Image]:
    ffmpeg_exe = _get_ffmpeg_exe()
    safe_suffix = suffix if suffix.startswith(".") else ".bin"
    if len(safe_suffix) > 8:
        safe_suffix = ".bin"

    frames: list[Image.Image] = []

    with tempfile.TemporaryDirectory(
        prefix="aniguard_media_"
    ) as temporary_directory:
        input_path = (
            Path(temporary_directory) / f"input{safe_suffix}"
        )
        input_path.write_bytes(payload)

        duration = _probe_duration(ffmpeg_exe, input_path)
        timestamps = _video_timestamps(
            duration,
            max_frames,
        )

        for timestamp in timestamps:
            try:
                completed = subprocess.run(
                    [
                        ffmpeg_exe,
                        "-hide_banner",
                        "-loglevel",
                        "error",
                        "-ss",
                        f"{timestamp:.3f}",
                        "-i",
                        str(input_path),
                        "-frames:v",
                        "1",
                        "-an",
                        "-f",
                        "image2pipe",
                        "-vcodec",
                        "png",
                        "pipe:1",
                    ],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    timeout=max(
                        4,
                        _env_int(
                            "LOCAL_MEDIA_FFMPEG_FRAME_TIMEOUT",
                            8,
                        ),
                    ),
                    check=False,
                )
            except (
                OSError,
                subprocess.SubprocessError,
            ):
                continue

            if completed.returncode != 0 or not completed.stdout:
                continue

            try:
                with Image.open(
                    io.BytesIO(completed.stdout)
                ) as image:
                    frames.append(
                        _normalize_frame(image.copy())
                    )
            except (
                UnidentifiedImageError,
                OSError,
            ):
                continue

    if not frames:
        raise UnidentifiedImageError(
            "FFmpeg не смог извлечь кадры"
        )

    return frames


def _file_suffix(
    file_name: str,
    telegram_path: str,
) -> str:
    for value in (file_name, telegram_path):
        suffix = Path(value or "").suffix.casefold()
        if suffix:
            return suffix
    return ".bin"


def _extract_frames(
    payload: bytes,
    file_name: str,
    telegram_path: str,
    moving: bool,
) -> list[Image.Image]:
    max_frames = max(
        1,
        min(
            _env_int("LOCAL_MEDIA_MAX_FRAMES", 8),
            16,
        ),
    )
    suffix = _file_suffix(file_name, telegram_path)

    # Статичные изображения и настоящие GIF сначала
    # открываются через Pillow.
    try:
        frames = _extract_pillow_frames(
            payload,
            max_frames,
        )
        if frames:
            return frames
    except (
        UnidentifiedImageError,
        OSError,
        ValueError,
    ):
        pass

    # Telegram обычно отправляет GIF как MP4, а
    # видео-стикеры — как WEBM.
    if moving or suffix in {
        ".mp4",
        ".webm",
        ".mov",
        ".mkv",
        ".avi",
        ".m4v",
    }:
        return _extract_video_frames(
            payload,
            suffix,
            max_frames,
        )

    raise UnidentifiedImageError(
        "Формат медиа не поддерживается"
    )


def _frame_bytes(frame: Image.Image) -> bytes:
    buffer = io.BytesIO()
    frame.save(
        buffer,
        format="JPEG",
        quality=92,
        optimize=True,
    )
    return buffer.getvalue()


def _average_hash(
    image: Image.Image,
    size: int = 8,
) -> str:
    gray = image.convert("L").resize(
        (size, size),
        Image.Resampling.LANCZOS,
    )
    pixels = list(gray.getdata())
    average = sum(pixels) / max(1, len(pixels))
    value = 0

    for pixel in pixels:
        value = (value << 1) | int(pixel >= average)

    return f"{value:0{size * size // 4}x}"


def _difference_hash(
    image: Image.Image,
    size: int = 8,
) -> str:
    gray = image.convert("L").resize(
        (size + 1, size),
        Image.Resampling.LANCZOS,
    )
    pixels = list(gray.getdata())
    value = 0

    for row in range(size):
        offset = row * (size + 1)
        for column in range(size):
            value = (value << 1) | int(
                pixels[offset + column]
                > pixels[offset + column + 1]
            )

    return f"{value:0{size * size // 4}x}"


def _hash_distance(left: str, right: str) -> int:
    try:
        return (
            int(left, 16) ^ int(right, 16)
        ).bit_count()
    except (TypeError, ValueError):
        return 10_000


def _load_db(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(
            path.read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError):
        return {"references": [], "terms": []}

    return (
        data
        if isinstance(data, dict)
        else {"references": [], "terms": []}
    )


def _load_reference_database(
) -> tuple[list[dict[str, str]], list[str]]:
    references: list[dict[str, str]] = []
    terms: list[str] = []
    seen: set[tuple[str, str]] = set()

    for path in (
        REFERENCE_DB,
        RUNTIME_REFERENCE_DB,
    ):
        data = _load_db(path)

        for row in data.get("references", []):
            if not isinstance(row, dict):
                continue

            dhash = str(
                row.get("dhash") or ""
            ).strip().casefold()
            ahash = str(
                row.get("ahash") or ""
            ).strip().casefold()
            identity = (dhash, ahash)

            if (
                not dhash
                or not ahash
                or identity in seen
            ):
                continue

            seen.add(identity)
            references.append(
                {
                    "dhash": dhash,
                    "ahash": ahash,
                    "label": str(
                        row.get("label")
                        or "локальная запрещённая база"
                    )[:120],
                }
            )

        for term in data.get("terms", []):
            normalized = str(
                term or ""
            ).strip().casefold()

            if normalized and normalized not in terms:
                terms.append(normalized)

    return references, terms


def _match_reference(
    frames: list[Image.Image],
    visible_text: str,
) -> tuple[bool, float, str]:
    references, terms = _load_reference_database()
    normalized_text = " ".join(
        visible_text.casefold().split()
    )

    for term in terms:
        if term and term in normalized_text:
            return (
                True,
                1.0,
                f"совпадение с локальным термином: {term}",
            )

    if not references:
        return False, 0.0, ""

    max_distance = max(
        0,
        min(
            _env_int(
                "LOCAL_MEDIA_HASH_DISTANCE",
                8,
            ),
            32,
        ),
    )
    best_confidence = 0.0
    best_label = ""

    for frame in frames:
        frame_dhash = _difference_hash(frame)
        frame_ahash = _average_hash(frame)

        for reference in references:
            d_distance = _hash_distance(
                frame_dhash,
                reference["dhash"],
            )
            a_distance = _hash_distance(
                frame_ahash,
                reference["ahash"],
            )
            distance = min(
                d_distance,
                a_distance,
            )
            confidence = max(
                0.0,
                1.0 - distance / 64.0,
            )

            if confidence > best_confidence:
                best_confidence = confidence
                best_label = reference["label"]

            if (
                d_distance <= max_distance
                and a_distance <= max_distance + 2
            ):
                return (
                    True,
                    confidence,
                    reference["label"],
                )

    return (
        False,
        best_confidence,
        best_label,
    )


async def _get_detector() -> Any | None:
    global _detector
    global _nudenet_unavailable_logged

    if _detector is not None:
        return _detector

    async with _detector_lock:
        if _detector is not None:
            return _detector

        try:
            from nudenet import NudeDetector

            model_path = str(
                os.getenv(
                    "LOCAL_MEDIA_NUDENET_MODEL",
                    "",
                )
                or ""
            ).strip()

            if model_path and Path(model_path).is_file():
                _detector = await asyncio.to_thread(
                    NudeDetector,
                    model_path=model_path,
                    inference_resolution=max(
                        320,
                        _env_int(
                            "LOCAL_MEDIA_NUDENET_RESOLUTION",
                            640,
                        ),
                    ),
                )
            else:
                _detector = await asyncio.to_thread(
                    NudeDetector
                )

        except Exception as exc:
            if not _nudenet_unavailable_logged:
                logger.exception(
                    "NudeNet недоступен: %s",
                    exc,
                )
                _nudenet_unavailable_logged = True
            return None

    return _detector


def _threshold_for_candidate(
    candidate: MediaCandidate,
) -> float:
    if "стикер" in candidate.media_kind.casefold():
        default = 0.28
        name = "LOCAL_MEDIA_STICKER_NSFW_THRESHOLD"
    elif candidate.moving:
        default = 0.32
        name = "LOCAL_MEDIA_VIDEO_NSFW_THRESHOLD"
    else:
        default = 0.45
        name = "LOCAL_MEDIA_NSFW_THRESHOLD"

    return max(
        0.0,
        min(
            _env_float(name, default),
            1.0,
        ),
    )


async def _nudity_score(
    frames: list[Image.Image],
    candidate: MediaCandidate,
) -> tuple[
    float,
    list[str],
    bool,
    float,
]:
    detector = await _get_detector()
    if detector is None:
        return 0.0, [], False, 0.0

    threshold = _threshold_for_candidate(candidate)
    weak_explicit_threshold = max(
        0.16,
        threshold - 0.12,
    )
    context_threshold = max(
        0.45,
        _env_float(
            "LOCAL_MEDIA_SEX_SCENE_CONTEXT_THRESHOLD",
            0.62,
        ),
    )

    best_score = 0.0
    found_classes: set[str] = set()
    suspicious_frames = 0
    scene_score = 0.0

    for frame in frames:
        detections = await asyncio.to_thread(
            detector.detect,
            _frame_bytes(frame),
        )

        explicit_hits: dict[str, float] = {}
        context_hits: dict[str, float] = {}
        face_count = 0

        for detection in detections or []:
            if not isinstance(detection, dict):
                continue

            class_name = str(
                detection.get("class") or ""
            ).upper()

            try:
                score = float(
                    detection.get("score") or 0.0
                )
            except (TypeError, ValueError):
                score = 0.0

            if class_name in EXPLICIT_CLASSES:
                explicit_hits[class_name] = max(
                    explicit_hits.get(class_name, 0.0),
                    score,
                )
                best_score = max(best_score, score)

                if score >= threshold:
                    found_classes.add(class_name)

            elif class_name in SEXUAL_CONTEXT_CLASSES:
                context_hits[class_name] = max(
                    context_hits.get(class_name, 0.0),
                    score,
                )

            elif (
                class_name in FACE_CLASSES
                and score >= 0.45
            ):
                face_count += 1

        weak_explicit = any(
            score >= weak_explicit_threshold
            for score in explicit_hits.values()
        )
        strong_context = {
            class_name: score
            for class_name, score in context_hits.items()
            if score >= context_threshold
        }
        genital_context = any(
            "GENITALIA" in class_name
            or "GENITAL" in class_name
            or "ANUS" in class_name
            for class_name in strong_context
        )
        body_context = any(
            "BUTTOCKS" in class_name
            or "BREAST" in class_name
            for class_name in strong_context
        )

        suspicious_frame = bool(
            weak_explicit
            or (
                candidate.moving
                and genital_context
                and body_context
                and face_count >= 1
            )
        )

        if suspicious_frame:
            suspicious_frames += 1
            frame_scene_score = max(
                list(explicit_hits.values())
                + list(strong_context.values())
                + [0.0]
            )
            scene_score = max(
                scene_score,
                frame_scene_score,
            )

    required_frames = (
        1
        if "стикер" in candidate.media_kind.casefold()
        else max(
            2,
            min(
                _env_int(
                    "LOCAL_MEDIA_SEX_SCENE_MIN_FRAMES",
                    2,
                ),
                max(2, len(frames)),
            ),
        )
    )
    sex_scene = bool(
        candidate.moving
        and suspicious_frames >= required_frames
    )

    return (
        best_score,
        sorted(found_classes),
        sex_scene,
        scene_score,
    )


def _prune_cache() -> None:
    if len(_result_cache) <= 1500:
        return

    oldest = sorted(
        _result_cache.items(),
        key=lambda item: item[1][0],
    )[:300]

    for key, _ in oldest:
        _result_cache.pop(key, None)


async def _extract_candidate_frames(
    bot: Bot,
    candidate: MediaCandidate,
) -> tuple[list[Image.Image], str]:
    primary_error: Exception | None = None

    try:
        payload, telegram_path = await _download_media(
            bot,
            candidate.file_id,
        )
        frames = await asyncio.to_thread(
            _extract_frames,
            payload,
            candidate.file_name,
            telegram_path,
            candidate.moving,
        )
        if frames:
            return frames, "original"
    except Exception as exc:
        primary_error = exc

    # Для TGS Telegram даёт статичный thumbnail.
    # Это резервный путь, когда локальный декодер TGS отсутствует.
    if candidate.thumbnail_file_id:
        try:
            payload, telegram_path = await _download_media(
                bot,
                candidate.thumbnail_file_id,
            )
            frames = await asyncio.to_thread(
                _extract_frames,
                payload,
                "thumbnail.jpg",
                telegram_path,
                False,
            )
            if frames:
                return frames, "thumbnail"
        except Exception as thumbnail_error:
            raise RuntimeError(
                "Не удалось разобрать оригинал и thumbnail: "
                f"{primary_error}; {thumbnail_error}"
            ) from thumbnail_error

    if primary_error is not None:
        raise primary_error

    raise RuntimeError(
        "Не удалось извлечь кадры"
    )


async def classify_message_media(
    bot: Bot,
    message: Message,
) -> dict[str, Any] | None:
    """Check Telegram media locally without external AI APIs."""
    candidate = _media_candidate(message)
    if candidate is None:
        return None

    cache_key = (
        f"{CACHE_VERSION}:{candidate.cache_key}"
    )
    now = time.monotonic()
    cache_seconds = max(
        60,
        _env_int(
            "LOCAL_MEDIA_CACHE_SECONDS",
            21_600,
        ),
    )
    cached = _result_cache.get(cache_key)

    if cached and now - cached[0] <= cache_seconds:
        return dict(cached[1])

    async with _scan_semaphore:
        try:
            frames, frame_source = (
                await _extract_candidate_frames(
                    bot,
                    candidate,
                )
            )
        except (
            UnidentifiedImageError,
            OSError,
            ValueError,
            RuntimeError,
        ) as exc:
            logger.warning(
                (
                    "Не удалось извлечь кадры: "
                    "chat=%s message=%s kind=%s error=%s"
                ),
                message.chat.id,
                message.message_id,
                candidate.media_kind,
                exc,
            )
            return None

        sexual_score = 0.0
        exposed_classes: list[str] = []
        sex_scene = False
        sex_scene_score = 0.0
        timeout = max(
            5.0,
            _env_float(
                "LOCAL_MEDIA_SCAN_TIMEOUT",
                45.0,
            ),
        )

        try:
            (
                sexual_score,
                exposed_classes,
                sex_scene,
                sex_scene_score,
            ) = await asyncio.wait_for(
                _nudity_score(
                    frames,
                    candidate,
                ),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            logger.warning(
                (
                    "Локальная NSFW-проверка превысила "
                    "таймаут: chat=%s message=%s kind=%s"
                ),
                message.chat.id,
                message.message_id,
                candidate.media_kind,
            )
        except Exception as exc:
            logger.exception(
                (
                    "Ошибка локальной NSFW-проверки: "
                    "chat=%s message=%s kind=%s error=%s"
                ),
                message.chat.id,
                message.message_id,
                candidate.media_kind,
                exc,
            )

        visible_text = " ".join(
            value
            for value in (
                message.caption or "",
                candidate.file_name,
            )
            if value
        )

        (
            extremist,
            extremist_confidence,
            extremist_reason,
        ) = await asyncio.to_thread(
            _match_reference,
            frames,
            visible_text,
        )

        threshold = _threshold_for_candidate(
            candidate
        )
        sexual = bool(
            (
                exposed_classes
                and sexual_score >= threshold
            )
            or sex_scene
        )

        logger.info(
            (
                "Local NSFW result: chat=%s message=%s "
                "kind=%s source=%s frames=%s "
                "score=%.4f scene=%.4f "
                "threshold=%.4f classes=%s "
                "sex_scene=%s unsafe=%s"
            ),
            message.chat.id,
            message.message_id,
            candidate.media_kind,
            frame_source,
            len(frames),
            sexual_score,
            sex_scene_score,
            threshold,
            exposed_classes,
            sex_scene,
            sexual,
        )

        labels: list[str] = []

        if sexual:
            labels.append(
                "порнографический или откровенный контент"
            )

        if extremist:
            labels.append(
                "совпадение с локальной базой "
                "экстремистских материалов"
            )

        result: dict[str, Any] = {
            "unsafe": bool(labels),
            "labels": labels,
            "media_kind": candidate.media_kind,
            "sexual": sexual,
            "sexual_score": round(
                float(sexual_score),
                4,
            ),
            "sexual_classes": exposed_classes,
            "sex_scene": sex_scene,
            "sex_scene_score": round(
                float(sex_scene_score),
                4,
            ),
            "sampled_frames": len(frames),
            "frame_source": frame_source,
            "extremist": extremist,
            "extremist_confidence": round(
                float(extremist_confidence),
                4,
            ),
            "extremist_reason": extremist_reason,
            "provider": "local",
        }

        _result_cache[cache_key] = (
            now,
            result,
        )
        _prune_cache()
        return dict(result)


inspect_message_media = classify_message_media
