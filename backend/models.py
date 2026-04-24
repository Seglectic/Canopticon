from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass
class ImageItem:
    id: str
    filename: str
    digest: str
    status: str
    uploaded_url: str
    result_url: str | None = None
    occluded_pct: float | None = None
    elapsed_s: float | None = None
    gps_present: bool = False
    gps_latitude: float | None = None
    gps_longitude: float | None = None
    location_tag: str | None = None
    location_label: str | None = None
    capture_source: str | None = None
    error: str | None = None


def item_to_payload(item: ImageItem) -> dict[str, Any]:
    return asdict(item)
