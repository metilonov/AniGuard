from __future__ import annotations

import argparse
import json
from pathlib import Path

from PIL import Image, ImageOps


def _normalize(image: Image.Image) -> Image.Image:
    frame = ImageOps.exif_transpose(image).convert("RGB")
    frame.thumbnail((1280, 1280), Image.Resampling.LANCZOS)
    return frame


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


def _load_database(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        data = {"version": 1, "references": [], "terms": []}

    if not isinstance(data, dict):
        data = {"version": 1, "references": [], "terms": []}

    data.setdefault("version", 1)
    data.setdefault("references", [])
    data.setdefault("terms", [])
    return data


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Добавляет проверенные изображения в локальную базу AniGuard."
    )
    parser.add_argument("images", nargs="*", type=Path)
    parser.add_argument("--label", default="проверенный запрещённый материал")
    parser.add_argument("--term", action="append", default=[])
    parser.add_argument(
        "--database",
        type=Path,
        default=Path("app/media_safety_refs.json"),
    )
    args = parser.parse_args()

    database = _load_database(args.database)
    known = {
        (str(row.get("dhash") or ""), str(row.get("ahash") or ""))
        for row in database["references"]
        if isinstance(row, dict)
    }
    added = 0

    for image_path in args.images:
        if not image_path.is_file():
            print(f"Пропущено: {image_path}")
            continue

        with Image.open(image_path) as image:
            frame = _normalize(image.copy())

        entry = {
            "label": str(args.label)[:120],
            "dhash": _difference_hash(frame),
            "ahash": _average_hash(frame),
            "source": image_path.name,
        }
        identity = (entry["dhash"], entry["ahash"])

        if identity in known:
            print(f"Уже есть: {image_path}")
            continue

        known.add(identity)
        database["references"].append(entry)
        added += 1
        print(f"Добавлено: {image_path}")

    for term in args.term:
        normalized = str(term or "").strip().casefold()
        if normalized and normalized not in database["terms"]:
            database["terms"].append(normalized)

    args.database.parent.mkdir(parents=True, exist_ok=True)
    args.database.write_text(
        json.dumps(database, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Готово. Новых изображений: {added}. База: {args.database}")


if __name__ == "__main__":
    main()
