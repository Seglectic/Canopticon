from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


CPU_PROVIDER = "CPUExecutionProvider"
DEFAULT_PORT = 8009
DATA_DIR = Path("data")
FRONTEND_DIR = Path("frontend")
INGEST_DIR = DATA_DIR / "ingest"
UPLOAD_DIR = DATA_DIR / "uploads"
RESULT_DIR = DATA_DIR / "results"
MAPS_DIR = DATA_DIR / "maps"
EVENT_LOG = DATA_DIR / "events.ndjson"
DEFAULT_THUMBNAIL_SIZE = 200
SUPPORTED_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tif", ".tiff"}


@dataclass
class WebConfig:
    host: str
    port: int
    model_path: str | None
    sky_threshold: int
    alpha: float
    device: str
    scale: float
    data_dir: Path
    ingest_dir: Path
    frontend_dir: Path
    event_log: Path
    maps_dir: Path
    thumbnail_size: int
