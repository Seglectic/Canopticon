#!/usr/bin/env python3

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
import argparse
import asyncio
import hashlib
import json
import platform
import subprocess
import time
import uuid

import cv2
from fastapi import FastAPI, File, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
import numpy as np
import onnxruntime as ort
import uvicorn
from huggingface_hub import hf_hub_download


MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)
MODEL_REPO = "JianyuanWang/skyseg"
MODEL_FILE = "skyseg.onnx"
LABEL_PADDING_RATIO = 0.015
LABEL_TEXT_HEIGHT_RATIO = 0.03
LABEL_LINE_GAP_RATIO = 0.01
LABEL_MIN_PADDING_PX = 10
LABEL_MIN_TEXT_HEIGHT_PX = 18
LABEL_MAX_TEXT_HEIGHT_PX = 40
LABEL_TEXT_THICKNESS = 2
CPU_PROVIDER = "CPUExecutionProvider"
DEFAULT_PORT = 8009
DATA_DIR = Path("canopticon_data")
STAGING_DIR = Path("staging")
UPLOAD_DIR = DATA_DIR / "uploads"
RESULT_DIR = DATA_DIR / "results"
SUPPORTED_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tif", ".tiff"}
PROVIDER_PRIORITY = {
    "nvidia": ["TensorrtExecutionProvider", "CUDAExecutionProvider", CPU_PROVIDER],
    "amd": ["MIGraphXExecutionProvider", "ROCMExecutionProvider", CPU_PROVIDER],
    "intel": ["OpenVINOExecutionProvider", "DmlExecutionProvider", CPU_PROVIDER],
    "generic_gpu": ["DmlExecutionProvider", "OpenVINOExecutionProvider", CPU_PROVIDER],
}


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
    error: str | None = None


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
    staging_dir: Path


class WebState:
    def __init__(self, config: WebConfig):
        self.config = config
        self.upload_dir = config.data_dir / "uploads"
        self.result_dir = config.data_dir / "results"
        self.queue: asyncio.Queue[str | None] = asyncio.Queue()
        self.items: dict[str, ImageItem] = {}
        self.hash_to_id: dict[str, str] = {}
        self.websockets: set[WebSocket] = set()
        self.lock = asyncio.Lock()
        self.session: ort.InferenceSession | None = None
        self.worker_task: asyncio.Task[None] | None = None
        self.accepting_uploads = False


def detect_gpu_vendor() -> str | None:
    if platform.system() != "Linux":
        return None

    try:
        result = subprocess.run(
            ["lspci"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return None

    lines = [
        line.lower()
        for line in result.stdout.splitlines()
        if "vga compatible controller" in line.lower()
        or "3d controller" in line.lower()
        or "display controller" in line.lower()
    ]
    if any("nvidia" in line for line in lines):
        return "nvidia"
    if any("amd" in line or "ati" in line for line in lines):
        return "amd"
    if any("intel" in line for line in lines):
        return "intel"
    if lines:
        return "generic_gpu"
    return None


def choose_execution_providers(device: str) -> list[str]:
    available = set(ort.get_available_providers())
    if device == "cpu":
        return [CPU_PROVIDER]

    if device == "gpu":
        vendor = detect_gpu_vendor() or "generic_gpu"
        for provider in PROVIDER_PRIORITY.get(vendor, PROVIDER_PRIORITY["generic_gpu"]):
            if provider in available:
                return [provider, CPU_PROVIDER] if provider != CPU_PROVIDER else [CPU_PROVIDER]
        return [CPU_PROVIDER]

    vendor = detect_gpu_vendor()
    if vendor is not None:
        for provider in PROVIDER_PRIORITY.get(vendor, []):
            if provider in available:
                return [provider, CPU_PROVIDER] if provider != CPU_PROVIDER else [CPU_PROVIDER]

    for provider in ort.get_available_providers():
        if provider not in {CPU_PROVIDER, "AzureExecutionProvider"}:
            return [provider, CPU_PROVIDER]
    return [CPU_PROVIDER]


def load_model(
    local_model_path: str | None = None,
    device: str = "auto",
) -> ort.InferenceSession:
    model_path = local_model_path
    if model_path is None:
        model_path = hf_hub_download(repo_id=MODEL_REPO, filename=MODEL_FILE)
    providers = choose_execution_providers(device)
    print(f"Using providers: {providers}", flush=True)
    return ort.InferenceSession(model_path, providers=providers)


def warm_model(session: ort.InferenceSession) -> None:
    print("Warming model with a dummy image...", flush=True)
    dummy = np.zeros((320, 320, 3), dtype=np.uint8)
    infer_mask(session, dummy)
    print("Model warmup complete.", flush=True)


def preprocess_bgr(image_bgr: np.ndarray, input_size=(320, 320)) -> np.ndarray:
    resized = cv2.resize(image_bgr, input_size, interpolation=cv2.INTER_LINEAR)
    rgb = cv2.cvtColor(resized, cv2.COLOR_BGR2RGB).astype(np.float32)
    x = (rgb / 255.0 - MEAN) / STD
    x = np.transpose(x, (2, 0, 1))
    x = np.expand_dims(x, axis=0).astype(np.float32)
    return x


def infer_mask(session: ort.InferenceSession, image_bgr: np.ndarray) -> np.ndarray:
    input_name = session.get_inputs()[0].name
    output_name = session.get_outputs()[0].name

    x = preprocess_bgr(image_bgr, (320, 320))
    raw = session.run([output_name], {input_name: x})[0]
    raw = np.squeeze(raw)

    min_val = float(raw.min())
    max_val = float(raw.max())
    if max_val > min_val:
        raw = (raw - min_val) / (max_val - min_val)
    else:
        raw = np.zeros_like(raw, dtype=np.float32)

    mask_8 = (raw * 255.0).clip(0, 255).astype(np.uint8)
    mask_8 = cv2.resize(
        mask_8,
        (image_bgr.shape[1], image_bgr.shape[0]),
        interpolation=cv2.INTER_LINEAR,
    )
    return mask_8


def make_overlay(
    image_bgr: np.ndarray,
    sky_mask_8: np.ndarray,
    sky_threshold: int,
    alpha: float,
    elapsed_s: float,
) -> tuple[np.ndarray, float]:
    # skyseg output: brighter = more sky likelihood
    sky_binary = sky_mask_8 >= sky_threshold
    occluded_binary = ~sky_binary

    total_pixels = occluded_binary.size
    occluded_pixels = int(occluded_binary.sum())
    occluded_pct = 100.0 * occluded_pixels / total_pixels if total_pixels else 0.0

    overlay = image_bgr.copy()
    red = np.zeros_like(image_bgr)
    red[:, :] = (0, 0, 255)  # BGR bright red

    overlay[occluded_binary] = cv2.addWeighted(
        image_bgr[occluded_binary],
        1.0 - alpha,
        red[occluded_binary],
        alpha,
        0.0,
    )

    font = cv2.FONT_HERSHEY_SIMPLEX
    short_side = min(image_bgr.shape[0], image_bgr.shape[1])
    padding_px = max(LABEL_MIN_PADDING_PX, int(round(short_side * LABEL_PADDING_RATIO)))
    text_height_px = min(
        LABEL_MAX_TEXT_HEIGHT_PX,
        max(LABEL_MIN_TEXT_HEIGHT_PX, int(round(short_side * LABEL_TEXT_HEIGHT_RATIO))),
    )
    line_gap_px = max(6, int(round(short_side * LABEL_LINE_GAP_RATIO)))
    font_scale = cv2.getFontScaleFromHeight(
        font,
        text_height_px,
        LABEL_TEXT_THICKNESS,
    )
    labels = [
        f"Occluded: {occluded_pct:.1f}%",
        f"Model: {elapsed_s:.2f}s",
    ]
    text_sizes = [
        cv2.getTextSize(label, font, font_scale, LABEL_TEXT_THICKNESS)[0]
        for label in labels
    ]
    text_width = max(width for width, _ in text_sizes)
    text_height = text_sizes[0][1]
    total_text_height = (text_height * len(labels)) + (line_gap_px * (len(labels) - 1))
    x = padding_px
    y = padding_px

    cv2.rectangle(
        overlay,
        (x, y),
        (
            x + text_width + (padding_px * 2),
            y + total_text_height + (padding_px * 2),
        ),
        (0, 0, 0),
        -1,
    )
    baseline_y = y + padding_px + text_height
    for index, label in enumerate(labels):
        cv2.putText(
            overlay,
            label,
            (x + padding_px, baseline_y + (index * (text_height + line_gap_px))),
            font,
            font_scale,
            (255, 255, 255),
            LABEL_TEXT_THICKNESS,
            cv2.LINE_AA,
        )

    return overlay, occluded_pct


def scale_image(image_bgr: np.ndarray, scale: float) -> np.ndarray:
    if scale >= 1.0:
        return image_bgr

    width = max(1, int(round(image_bgr.shape[1] * scale)))
    height = max(1, int(round(image_bgr.shape[0] * scale)))
    return cv2.resize(image_bgr, (width, height), interpolation=cv2.INTER_AREA)


def process_image(
    session: ort.InferenceSession,
    image_bgr: np.ndarray,
    sky_threshold: int,
    alpha: float,
    scale: float,
) -> tuple[np.ndarray, float, float]:
    working_image = scale_image(image_bgr, scale)
    start_time = time.perf_counter()
    sky_mask_8 = infer_mask(session, working_image)
    elapsed_s = time.perf_counter() - start_time
    overlay, occluded_pct = make_overlay(
        working_image, sky_mask_8, sky_threshold, alpha, elapsed_s
    )
    return overlay, occluded_pct, elapsed_s


def process_image_file(
    session: ort.InferenceSession,
    input_path: Path,
    output_path: Path,
    sky_threshold: int,
    alpha: float,
    scale: float,
) -> tuple[float, float]:
    image = cv2.imread(str(input_path), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError(f"Failed to read image: {input_path}")

    overlay, occluded_pct, elapsed_s = process_image(
        session=session,
        image_bgr=image,
        sky_threshold=sky_threshold,
        alpha=alpha,
        scale=scale,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(output_path), overlay)
    return occluded_pct, elapsed_s


def process_folder(
    input_dir: Path,
    output_dir: Path,
    model_path: str | None,
    sky_threshold: int,
    alpha: float,
    device: str,
    scale: float,
):
    output_dir.mkdir(parents=True, exist_ok=True)

    session = load_model(model_path, device=device)

    files = sorted([p for p in input_dir.iterdir() if p.suffix.lower() in SUPPORTED_EXTS])
    processed_count = 0
    failed_files: list[str] = []

    for path in files:
        stem = path.stem
        output_path = output_dir / f"{stem}_overlay{path.suffix}"
        try:
            occluded_pct, elapsed_s = process_image_file(
                session=session,
                input_path=path,
                output_path=output_path,
                sky_threshold=sky_threshold,
                alpha=alpha,
                scale=scale,
            )
        except ValueError:
            failed_files.append(path.name)
            continue
        print(
            f"Wrote {output_path} "
            f"(occluded {occluded_pct:.2f}%, model {elapsed_s:.2f}s)"
        )
        processed_count += 1

    print(f"Wrote {processed_count} overlay image(s) to: {output_dir}")
    if failed_files:
        print("Failed to read:")
        for name in failed_files:
            print(f"  {name}")


def item_to_payload(item: ImageItem) -> dict[str, Any]:
    return asdict(item)


def safe_filename(name: str | None) -> str:
    cleaned = Path(name or "photo").name.strip()
    return cleaned or "photo"


def item_id_from_digest(digest: str, existing: dict[str, ImageItem]) -> str:
    base = digest[:16]
    if base not in existing:
        return base
    return f"{base}-{uuid.uuid4().hex[:8]}"


def hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


async def save_upload_to_staging(upload: UploadFile, staging_dir: Path) -> tuple[Path, str, int]:
    filename = safe_filename(upload.filename)
    suffix = Path(filename).suffix.lower()
    staging_path = staging_dir / f"{uuid.uuid4().hex}{suffix}"
    digest = hashlib.sha256()
    size = 0

    staging_dir.mkdir(parents=True, exist_ok=True)
    with staging_path.open("wb") as file:
        while True:
            chunk = await upload.read(1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            digest.update(chunk)
            file.write(chunk)

    return staging_path, digest.hexdigest(), size


def process_queued_item(
    session: ort.InferenceSession,
    input_path: Path,
    output_path: Path,
    sky_threshold: int,
    alpha: float,
    scale: float,
) -> tuple[float, float]:
    return process_image_file(
        session=session,
        input_path=input_path,
        output_path=output_path,
        sky_threshold=sky_threshold,
        alpha=alpha,
        scale=scale,
    )


async def broadcast(state: WebState, event: dict[str, Any]) -> None:
    if not state.websockets:
        return

    message = json.dumps(event)
    disconnected: list[WebSocket] = []
    for websocket in list(state.websockets):
        try:
            await websocket.send_text(message)
        except Exception:
            disconnected.append(websocket)

    for websocket in disconnected:
        state.websockets.discard(websocket)


async def processing_worker(state: WebState) -> None:
    while True:
        image_id = await state.queue.get()
        try:
            if image_id is None:
                return

            async with state.lock:
                item = state.items.get(image_id)
                if item is None:
                    continue
                item.status = "processing"
                item.error = None
                payload = item_to_payload(item)

            await broadcast(state, {"type": "item", "item": payload})

            upload_path = state.upload_dir / f"{image_id}{Path(item.filename).suffix.lower()}"
            result_path = state.result_dir / f"{image_id}_overlay.jpg"
            try:
                if state.session is None:
                    raise RuntimeError("Model session is not loaded")
                occluded_pct, elapsed_s = await asyncio.to_thread(
                    process_queued_item,
                    state.session,
                    upload_path,
                    result_path,
                    state.config.sky_threshold,
                    state.config.alpha,
                    state.config.scale,
                )
            except Exception as exc:
                async with state.lock:
                    item.status = "error"
                    item.error = str(exc)
                    payload = item_to_payload(item)
            else:
                async with state.lock:
                    item.status = "done"
                    item.result_url = f"/media/results/{result_path.name}"
                    item.occluded_pct = occluded_pct
                    item.elapsed_s = elapsed_s
                    item.error = None
                    payload = item_to_payload(item)

            await broadcast(state, {"type": "item", "item": payload})
        finally:
            state.queue.task_done()


def index_existing_uploads(state: WebState) -> None:
    for upload_path in sorted(state.upload_dir.iterdir()):
        if not upload_path.is_file() or upload_path.suffix.lower() not in SUPPORTED_EXTS:
            continue
        digest = hash_file(upload_path)
        image_id = upload_path.stem
        result_path = state.result_dir / f"{image_id}_overlay.jpg"
        item = ImageItem(
            id=image_id,
            filename=upload_path.name,
            digest=digest,
            status="done" if result_path.exists() else "queued",
            uploaded_url=f"/media/uploads/{upload_path.name}",
            result_url=f"/media/results/{result_path.name}" if result_path.exists() else None,
        )
        state.items[image_id] = item
        state.hash_to_id[digest] = image_id
        if not result_path.exists():
            state.queue.put_nowait(image_id)


def html_page() -> str:
    return """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Canopticon</title>
  <style>
    :root {
      color-scheme: light;
      --ink: #182024;
      --muted: #66737c;
      --line: #d9e1e5;
      --panel: #ffffff;
      --wash: #f3f7f4;
      --leaf: #28795d;
      --sun: #e2a72e;
      --mark: #ca3e47;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      min-height: 100vh;
      font-family: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      color: var(--ink);
      background: var(--wash);
      letter-spacing: 0;
    }
    header {
      position: sticky;
      top: 0;
      z-index: 2;
      padding: 18px 16px 12px;
      background: rgba(243, 247, 244, 0.94);
      border-bottom: 1px solid var(--line);
      backdrop-filter: blur(12px);
    }
    h1 {
      margin: 0;
      font-size: 1.45rem;
      line-height: 1.1;
    }
    .status {
      margin-top: 6px;
      color: var(--muted);
      font-size: 0.92rem;
    }
    main {
      width: min(920px, 100%);
      margin: 0 auto;
      padding: 16px 14px 112px;
    }
    .gallery {
      display: grid;
      gap: 12px;
    }
    .empty {
      min-height: 58vh;
      display: grid;
      place-items: center;
      text-align: center;
      color: var(--muted);
      padding: 24px;
    }
    .card {
      overflow: hidden;
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      box-shadow: 0 8px 22px rgba(24, 32, 36, 0.08);
    }
    .thumb {
      width: 100%;
      aspect-ratio: 4 / 3;
      object-fit: cover;
      display: block;
      background: #dfe7e2;
    }
    .body {
      display: grid;
      gap: 8px;
      padding: 12px;
    }
    .row {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
    }
    .name {
      min-width: 0;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      font-weight: 700;
    }
    .pill {
      flex: 0 0 auto;
      border-radius: 999px;
      padding: 5px 9px;
      font-size: 0.78rem;
      line-height: 1;
      color: #fff;
      background: var(--leaf);
      text-transform: uppercase;
    }
    .pill.queued { background: var(--sun); color: #2a220f; }
    .pill.processing { background: #3278a8; }
    .pill.error { background: var(--mark); }
    .meta {
      color: var(--muted);
      font-size: 0.9rem;
      line-height: 1.35;
    }
    .upload-bar {
      position: fixed;
      left: 0;
      right: 0;
      bottom: 0;
      z-index: 3;
      padding: 12px 14px calc(12px + env(safe-area-inset-bottom));
      background: rgba(255, 255, 255, 0.94);
      border-top: 1px solid var(--line);
      backdrop-filter: blur(12px);
    }
    .upload-inner {
      width: min(920px, 100%);
      margin: 0 auto;
      display: grid;
      grid-template-columns: 1fr auto;
      gap: 10px;
      align-items: center;
    }
    .queue-note {
      min-width: 0;
      color: var(--muted);
      font-size: 0.88rem;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .upload-button {
      min-width: 152px;
      min-height: 48px;
      border: 0;
      border-radius: 8px;
      background: var(--leaf);
      color: #fff;
      font: inherit;
      font-weight: 800;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      gap: 10px;
      cursor: pointer;
    }
    .upload-button span:first-child {
      width: 22px;
      height: 22px;
      display: inline-grid;
      place-items: center;
      border: 2px solid currentColor;
      border-radius: 50%;
      line-height: 1;
      font-size: 1rem;
    }
    input[type="file"] { display: none; }
    @media (min-width: 720px) {
      .gallery { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      header { padding-left: 24px; padding-right: 24px; }
      main { padding-left: 24px; padding-right: 24px; }
    }
  </style>
</head>
<body>
  <header>
    <h1>Canopticon</h1>
    <div class="status" id="status">Connecting...</div>
  </header>
  <main>
    <section class="gallery" id="gallery"></section>
    <section class="empty" id="empty">Add canopy photos to begin.</section>
  </main>
  <form class="upload-bar" id="uploadForm">
    <div class="upload-inner">
      <div class="queue-note" id="queueNote">Ready on port 8009</div>
      <label class="upload-button" for="files" title="Add photos">
        <span>+</span><span>Add Photos</span>
      </label>
      <input id="files" name="files" type="file" accept="image/*" multiple>
    </div>
  </form>
  <script>
    const gallery = document.querySelector("#gallery");
    const empty = document.querySelector("#empty");
    const statusEl = document.querySelector("#status");
    const queueNote = document.querySelector("#queueNote");
    const input = document.querySelector("#files");
    const items = new Map();

    function statusText(item) {
      if (item.status === "done" && item.occluded_pct !== null) {
        return `Occluded ${item.occluded_pct.toFixed(1)}% - Model ${item.elapsed_s.toFixed(2)}s`;
      }
      if (item.status === "error") return item.error || "Processing failed";
      if (item.status === "processing") return "Processing";
      return "Queued";
    }

    function cardImage(item) {
      return item.result_url || item.uploaded_url;
    }

    function render() {
      const list = Array.from(items.values()).reverse();
      empty.style.display = list.length ? "none" : "grid";
      gallery.innerHTML = list.map((item) => `
        <article class="card">
          <img class="thumb" src="${cardImage(item)}" alt="">
          <div class="body">
            <div class="row">
              <div class="name">${escapeHtml(item.filename)}</div>
              <div class="pill ${item.status}">${item.status}</div>
            </div>
            <div class="meta">${escapeHtml(statusText(item))}</div>
          </div>
        </article>
      `).join("");

      const queued = list.filter((item) => item.status === "queued" || item.status === "processing").length;
      queueNote.textContent = queued ? `${queued} waiting or processing` : "Ready on port 8009";
    }

    function escapeHtml(value) {
      return String(value).replace(/[&<>"']/g, (char) => ({
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#039;"
      }[char]));
    }

    async function loadItems() {
      const response = await fetch("/api/items");
      const data = await response.json();
      data.items.forEach((item) => items.set(item.id, item));
      render();
    }

    function connect() {
      const proto = location.protocol === "https:" ? "wss" : "ws";
      const ws = new WebSocket(`${proto}://${location.host}/ws`);
      ws.addEventListener("open", () => { statusEl.textContent = "Live updates connected"; });
      ws.addEventListener("close", () => {
        statusEl.textContent = "Reconnecting...";
        setTimeout(connect, 1200);
      });
      ws.addEventListener("message", (event) => {
        const data = JSON.parse(event.data);
        if (data.type === "snapshot") {
          data.items.forEach((item) => items.set(item.id, item));
        }
        if (data.type === "item") {
          items.set(data.item.id, data.item);
        }
        if (data.type === "duplicate") {
          statusEl.textContent = `Duplicate skipped: ${data.filename}`;
        }
        render();
      });
    }

    input.addEventListener("change", async () => {
      if (!input.files.length) return;
      queueNote.textContent = `Uploading ${input.files.length} photo${input.files.length === 1 ? "" : "s"}...`;
      const formData = new FormData();
      for (const file of input.files) formData.append("files", file);
      input.value = "";

      const response = await fetch("/api/upload", { method: "POST", body: formData });
      if (!response.ok) {
        statusEl.textContent = "Upload failed";
        queueNote.textContent = "Ready on port 8009";
        return;
      }
      const data = await response.json();
      data.items.forEach((item) => items.set(item.id, item));
      render();
    });

    loadItems();
    connect();
  </script>
</body>
</html>"""


def media_response(directory: Path, filename: str) -> FileResponse:
    safe_name = Path(filename).name
    path = directory / safe_name
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(path)


def create_app(config: WebConfig) -> FastAPI:
    state = WebState(config)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        config.staging_dir.mkdir(parents=True, exist_ok=True)
        state.upload_dir.mkdir(parents=True, exist_ok=True)
        state.result_dir.mkdir(parents=True, exist_ok=True)
        state.session = await asyncio.to_thread(load_model, config.model_path, config.device)
        await asyncio.to_thread(warm_model, state.session)
        index_existing_uploads(state)
        state.accepting_uploads = True
        state.worker_task = asyncio.create_task(processing_worker(state))
        print(f"Canopticon web app listening on http://{config.host}:{config.port}", flush=True)
        try:
            yield
        finally:
            print("Shutting down Canopticon web app...", flush=True)
            state.accepting_uploads = False
            await state.queue.put(None)
            if state.worker_task is not None:
                try:
                    await asyncio.wait_for(state.worker_task, timeout=10)
                except asyncio.TimeoutError:
                    state.worker_task.cancel()
                    await asyncio.gather(state.worker_task, return_exceptions=True)
            state.session = None
            state.websockets.clear()
            print("Canopticon shutdown complete.", flush=True)

    app = FastAPI(title="Canopticon", lifespan=lifespan)

    @app.get("/", response_class=HTMLResponse)
    async def index() -> str:
        return html_page()

    @app.get("/api/items")
    async def get_items() -> JSONResponse:
        async with state.lock:
            items = [item_to_payload(item) for item in state.items.values()]
        return JSONResponse({"items": items})

    @app.post("/api/upload")
    async def upload_photos(files: list[UploadFile] = File(...)) -> JSONResponse:
        if not state.accepting_uploads:
            raise HTTPException(status_code=503, detail="Server is shutting down")

        accepted: list[dict[str, Any]] = []
        duplicates: list[dict[str, str]] = []
        for upload in files:
            original_name = safe_filename(upload.filename)
            suffix = Path(original_name).suffix.lower()
            if suffix not in SUPPORTED_EXTS:
                await upload.close()
                continue

            staging_path, digest, size = await save_upload_to_staging(upload, config.staging_dir)
            await upload.close()
            if size == 0:
                staging_path.unlink(missing_ok=True)
                continue

            async with state.lock:
                duplicate_id = state.hash_to_id.get(digest)
                if duplicate_id is not None:
                    duplicate = {
                        "filename": original_name,
                        "existing_id": duplicate_id,
                    }
                    duplicates.append(duplicate)
                    existing = item_to_payload(state.items[duplicate_id])
                else:
                    image_id = item_id_from_digest(digest, state.items)
                    upload_path = state.upload_dir / f"{image_id}{suffix}"
                    staging_path.replace(upload_path)
                    item = ImageItem(
                        id=image_id,
                        filename=original_name,
                        digest=digest,
                        status="queued",
                        uploaded_url=f"/media/uploads/{upload_path.name}",
                    )
                    state.items[image_id] = item
                    state.hash_to_id[digest] = image_id
                    accepted.append(item_to_payload(item))
                    existing = None

            if duplicate_id is not None:
                staging_path.unlink(missing_ok=True)
                await broadcast(
                    state,
                    {
                        "type": "duplicate",
                        "filename": original_name,
                        "existing_id": duplicate_id,
                    },
                )
                accepted.append(existing)
            else:
                await state.queue.put(image_id)
                await broadcast(state, {"type": "item", "item": accepted[-1]})

        return JSONResponse({"items": accepted, "duplicates": duplicates})

    @app.get("/media/uploads/{filename}")
    async def uploaded_media(filename: str) -> FileResponse:
        return media_response(state.upload_dir, filename)

    @app.get("/media/results/{filename}")
    async def result_media(filename: str) -> FileResponse:
        return media_response(state.result_dir, filename)

    @app.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket) -> None:
        await websocket.accept()
        state.websockets.add(websocket)
        async with state.lock:
            items = [item_to_payload(item) for item in state.items.values()]
        await websocket.send_text(json.dumps({"type": "snapshot", "items": items}))
        try:
            while True:
                await websocket.receive_text()
        except WebSocketDisconnect:
            state.websockets.discard(websocket)

    return app


def add_processing_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--model",
        type=str,
        default=None,
        help="Optional local path to skyseg.onnx; otherwise downloads from Hugging Face",
    )
    parser.add_argument(
        "--sky-threshold",
        type=int,
        default=160,
        help="0-255 threshold; higher means stricter sky detection",
    )
    parser.add_argument(
        "--alpha",
        type=float,
        default=0.55,
        help="Overlay strength for red occlusion mask",
    )
    parser.add_argument(
        "--device",
        choices=["auto", "cpu", "gpu"],
        default="auto",
        help="Execution target: auto-detect, force CPU, or prefer GPU with CPU fallback",
    )
    parser.add_argument(
        "--scale",
        type=float,
        default=1.0,
        help="Optional image downscale factor before processing, e.g. 0.5 for 50%% size",
    )


def validate_processing_args(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    if not (0.0 < args.scale <= 1.0):
        parser.error("--scale must be greater than 0 and at most 1.0")


def serve(args: argparse.Namespace) -> None:
    config = WebConfig(
        host=args.host,
        port=args.port,
        model_path=args.model,
        sky_threshold=args.sky_threshold,
        alpha=args.alpha,
        device=args.device,
        scale=args.scale,
        data_dir=args.data_dir,
        staging_dir=args.staging_dir,
    )
    app = create_app(config)
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Canopy/sky occlusion estimation using skyseg ONNX."
    )
    subparsers = parser.add_subparsers(dest="command")

    serve_parser = subparsers.add_parser("serve", help="Start the mobile web app")
    serve_parser.add_argument("--host", default="0.0.0.0", help="Host interface to bind")
    serve_parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="Port to bind")
    serve_parser.add_argument(
        "--data-dir",
        type=Path,
        default=DATA_DIR,
        help="Directory for managed uploads and results",
    )
    serve_parser.add_argument(
        "--staging-dir",
        type=Path,
        default=STAGING_DIR,
        help="Directory for temporary uploaded files before hashing",
    )
    add_processing_options(serve_parser)

    batch_parser = subparsers.add_parser("batch", help="Process a folder of photos")
    batch_parser.add_argument("input_dir", type=Path, help="Folder of input photos")
    batch_parser.add_argument("output_dir", type=Path, help="Folder for outputs")
    add_processing_options(batch_parser)

    args = parser.parse_args()

    if args.command == "batch":
        validate_processing_args(batch_parser, args)
        process_folder(
            input_dir=args.input_dir,
            output_dir=args.output_dir,
            model_path=args.model,
            sky_threshold=args.sky_threshold,
            alpha=args.alpha,
            device=args.device,
            scale=args.scale,
        )
        return

    if args.command is None:
        defaults = serve_parser.parse_args([])
        args = argparse.Namespace(command="serve", **vars(defaults))

    validate_processing_args(serve_parser, args)
    serve(args)


if __name__ == "__main__":
    main()
