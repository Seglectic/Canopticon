from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import platform
import shutil
import subprocess
import tarfile
import tempfile
import zipfile
from urllib.request import urlretrieve

from .settings import PROTOMAPS_BUILD_URL


PMTILES_VERSION = "1.30.2"


@dataclass(frozen=True)
class StateMapConfig:
    slug: str
    label: str
    bounds: tuple[float, float, float, float]
    center: tuple[float, float]
    zoom: int
    max_zoom: int

    @property
    def filename(self) -> str:
        return f"{self.slug}.pmtiles"


@dataclass(frozen=True)
class PmtilesAsset:
    archive_name: str
    archive_kind: str
    binary_name: str = "pmtiles"


STATE_MAPS: dict[str, StateMapConfig] = {
    "florida": StateMapConfig(
        slug="florida",
        label="Offline Florida",
        bounds=(-87.7, 24.3, -79.8, 31.1),
        center=(27.8, -81.7),
        zoom=6,
        max_zoom=15,
    ),
}


def state_map_config(state_slug: str) -> StateMapConfig | None:
    return STATE_MAPS.get(state_slug.strip().lower())


def ensure_state_map(maps_dir: Path, state_slug: str) -> Path | None:
    config = state_map_config(state_slug)
    if config is None:
        return None

    maps_dir.mkdir(parents=True, exist_ok=True)
    output_path = maps_dir / config.filename
    if output_path.exists() and output_path.stat().st_size > 0:
        return output_path

    pmtiles_bin = ensure_pmtiles_cli(maps_dir)
    extract_state_map(pmtiles_bin, config, output_path)
    return output_path


def ensure_pmtiles_cli(maps_dir: Path) -> Path:
    tools_dir = maps_dir / ".tools"
    tools_dir.mkdir(parents=True, exist_ok=True)
    binary_path = tools_dir / "pmtiles"
    if binary_path.exists():
        return binary_path

    asset = pmtiles_asset()
    asset_url = (
        f"https://github.com/protomaps/go-pmtiles/releases/download/v{PMTILES_VERSION}/{asset.archive_name}"
    )
    with tempfile.TemporaryDirectory(prefix="canopticon-pmtiles-") as temp_dir:
        temp_path = Path(temp_dir)
        archive_path = temp_path / asset.archive_name
        urlretrieve(asset_url, archive_path)
        extracted_path = extract_pmtiles_binary(archive_path, asset, temp_path)
        if not extracted_path.exists():
            raise RuntimeError("pmtiles CLI archive did not contain the expected binary")
        shutil.copy2(extracted_path, binary_path)
    binary_path.chmod(0o755)
    return binary_path


def pmtiles_asset() -> PmtilesAsset:
    system = platform.system()
    machine = platform.machine().lower()
    if system == "Linux":
        if machine in {"x86_64", "amd64"}:
            return PmtilesAsset(
                archive_name=f"go-pmtiles_{PMTILES_VERSION}_Linux_x86_64.tar.gz",
                archive_kind="tar.gz",
            )
        if machine in {"aarch64", "arm64"}:
            return PmtilesAsset(
                archive_name=f"go-pmtiles_{PMTILES_VERSION}_Linux_arm64.tar.gz",
                archive_kind="tar.gz",
            )
    if system == "Darwin":
        if machine in {"x86_64", "amd64"}:
            return PmtilesAsset(
                archive_name=f"go-pmtiles-{PMTILES_VERSION}_Darwin_x86_64.zip",
                archive_kind="zip",
            )
        if machine in {"arm64", "aarch64"}:
            return PmtilesAsset(
                archive_name=f"go-pmtiles-{PMTILES_VERSION}_Darwin_arm64.zip",
                archive_kind="zip",
            )
    if system == "Windows":
        raise RuntimeError("Automatic pmtiles download is not supported on Windows yet")

    raise RuntimeError(f"Unsupported architecture for automatic pmtiles download: {machine}")


def extract_pmtiles_binary(archive_path: Path, asset: PmtilesAsset, temp_path: Path) -> Path:
    if asset.archive_kind == "tar.gz":
        with tarfile.open(archive_path, "r:gz") as archive:
            archive.extractall(temp_path, filter="data")
    elif asset.archive_kind == "zip":
        with zipfile.ZipFile(archive_path) as archive:
            archive.extractall(temp_path)
    else:
        raise RuntimeError(f"Unsupported pmtiles archive kind: {asset.archive_kind}")

    direct_path = temp_path / asset.binary_name
    if direct_path.exists():
        return direct_path

    for candidate in temp_path.rglob(asset.binary_name):
        if candidate.is_file():
            return candidate

    raise RuntimeError("pmtiles CLI archive did not contain the expected binary")


def extract_state_map(pmtiles_bin: Path, config: StateMapConfig, output_path: Path) -> None:
    with tempfile.TemporaryDirectory(prefix=f"canopticon-{config.slug}-") as temp_dir:
        temp_path = Path(temp_dir)
        temp_output = temp_path / output_path.name
        min_lng, min_lat, max_lng, max_lat = config.bounds
        subprocess.run(
            [
                str(pmtiles_bin),
                "extract",
                PROTOMAPS_BUILD_URL,
                str(temp_output),
                f"--bbox={min_lng},{min_lat},{max_lng},{max_lat}",
                f"--maxzoom={config.max_zoom}",
                "--download-threads=8",
            ],
            check=True,
        )
        shutil.copyfile(temp_output, output_path)
        temp_output.unlink(missing_ok=True)
