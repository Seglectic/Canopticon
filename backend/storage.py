from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import secrets
from typing import Any
import uuid

from fastapi import UploadFile
from PIL import ExifTags, Image, ImageOps

from .models import ImageItem

GPS_LOCATION_TAG = "exif-gps"
GPS_LOCATION_LABEL = "GPS found"
MANUAL_LOCATION_TAG = "manual-placement-needed"
MANUAL_LOCATION_LABEL = "Needs map placement"


def safe_filename(name: str | None) -> str:
    cleaned = Path(name or "photo").name.strip()
    return cleaned or "photo"


ID_ALPHABET = "0123456789abcdefghijklmnopqrstuvwxyz"
MAX_ID_EPOCH_MS = (36**9) - 1


def _base36(value: int) -> str:
    if value == 0:
        return "0"
    digits: list[str] = []
    while value:
        value, remainder = divmod(value, 36)
        digits.append(ID_ALPHABET[remainder])
    return "".join(reversed(digits))


def item_id_from_digest(digest: str, existing: dict[str, ImageItem]) -> str:
    del digest
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    reverse_ms = max(0, MAX_ID_EPOCH_MS - min(now_ms, MAX_ID_EPOCH_MS))
    prefix = _base36(reverse_ms).rjust(9, "0")
    while True:
        suffix = _base36(secrets.randbelow(36**3)).rjust(3, "0")
        candidate = f"{prefix}{suffix}"
        if candidate not in existing:
            return candidate


def hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def gps_decimal(values: Any, ref: str | None) -> float | None:
    try:
        degrees, minutes, seconds = values
        decimal = float(degrees) + (float(minutes) / 60.0) + (float(seconds) / 3600.0)
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    if ref in {"S", "W"}:
        decimal *= -1
    return decimal


def build_location_metadata(
    latitude: float | None,
    longitude: float | None,
    *,
    capture_source: str | None = None,
) -> dict[str, Any]:
    gps_present = latitude is not None and longitude is not None
    if gps_present:
        location_tag = GPS_LOCATION_TAG
        location_label = GPS_LOCATION_LABEL
    else:
        location_tag = MANUAL_LOCATION_TAG
        location_label = MANUAL_LOCATION_LABEL

    return {
        "gps_present": gps_present,
        "gps_latitude": latitude,
        "gps_longitude": longitude,
        "location_tag": location_tag,
        "location_label": location_label,
        "capture_source": capture_source,
    }


def manual_location_metadata(*, capture_source: str | None = None) -> dict[str, Any]:
    return build_location_metadata(None, None, capture_source=capture_source)


def extract_photo_metadata(path: Path) -> dict[str, Any]:
    try:
        with Image.open(path) as image:
            exif = image.getexif()
            gps_ifd = exif.get_ifd(ExifTags.IFD.GPSInfo) if exif else {}
    except Exception:
        return manual_location_metadata()

    if not gps_ifd:
        return manual_location_metadata()

    latitude = gps_decimal(gps_ifd.get(2), gps_ifd.get(1))
    longitude = gps_decimal(gps_ifd.get(4), gps_ifd.get(3))
    return build_location_metadata(latitude, longitude)


def create_thumbnail(input_path: Path, output_path: Path, *, size: int) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(input_path) as image:
        thumbnail = ImageOps.exif_transpose(image).convert("RGB")
        thumbnail = ImageOps.fit(
            thumbnail,
            (size, size),
            method=Image.Resampling.LANCZOS,
            centering=(0.5, 0.5),
        )
        thumbnail.save(output_path, format="JPEG", quality=84, optimize=True)


def log_event(event_log: Path, event: str, **fields: Any) -> None:
    event_log.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "event": event,
        **fields,
    }
    with event_log.open("a", encoding="utf-8") as file:
        file.write(json.dumps(record, sort_keys=True) + "\n")


def read_latest_processing_results(event_log: Path) -> dict[str, dict[str, float]]:
    results: dict[str, dict[str, float]] = {}
    if not event_log.exists():
        return results

    with event_log.open("r", encoding="utf-8") as file:
        for line in file:
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if record.get("event") != "processing_done":
                continue
            image_id = record.get("image_id")
            occluded_pct = record.get("occluded_pct")
            elapsed_s = record.get("elapsed_s")
            if not image_id or occluded_pct is None:
                continue
            results[image_id] = {
                "occluded_pct": float(occluded_pct),
                "elapsed_s": float(elapsed_s) if elapsed_s is not None else 0.0,
            }
    return results


async def save_upload_to_ingest(upload: UploadFile, ingest_dir: Path) -> tuple[Path, str, int]:
    filename = safe_filename(upload.filename)
    suffix = Path(filename).suffix.lower()
    ingest_path = ingest_dir / f"{uuid.uuid4().hex}{suffix}"
    digest = hashlib.sha256()
    size = 0

    ingest_dir.mkdir(parents=True, exist_ok=True)
    with ingest_path.open("wb") as file:
        while True:
            chunk = await upload.read(1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            digest.update(chunk)
            file.write(chunk)

    return ingest_path, digest.hexdigest(), size


def save_bytes_to_ingest(
    filename: str,
    payload: bytes,
    ingest_dir: Path,
) -> tuple[Path, str, int]:
    safe_name = safe_filename(filename)
    suffix = Path(safe_name).suffix.lower() or ".jpg"
    ingest_path = ingest_dir / f"{uuid.uuid4().hex}{suffix}"
    ingest_dir.mkdir(parents=True, exist_ok=True)
    ingest_path.write_bytes(payload)
    return ingest_path, hash_file(ingest_path), len(payload)
