from __future__ import annotations

from contextlib import asynccontextmanager
import asyncio
from datetime import datetime
import json
import os
from pathlib import Path
import subprocess
from typing import Any
import uuid

from fastapi import FastAPI, File, HTTPException, Response, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
import onnxruntime as ort

from .inference import load_model, process_image_file, warm_model
from .models import ImageItem, item_to_payload
from .settings import SUPPORTED_EXTS, WebConfig
from .storage import (
    extract_photo_metadata,
    hash_file,
    item_id_from_digest,
    log_event,
    manual_location_metadata,
    read_latest_processing_results,
    safe_filename,
    save_upload_to_ingest,
)

GPIO_TRIGGER_PIN = 17
GPIO_TRIGGER_LABEL = "GPIO17 to GND"
GPIO_CAPTURE_SOURCE = "gpio-trigger"


class WebState:
    def __init__(self, config: WebConfig):
        self.config = config
        self.upload_dir = config.data_dir / "uploads"
        self.result_dir = config.data_dir / "results"
        self.maps_dir = config.maps_dir
        self.ingest_dir = config.ingest_dir
        self.event_log = config.event_log
        self.queue: asyncio.Queue[str | None] = asyncio.Queue()
        self.items: dict[str, ImageItem] = {}
        self.hash_to_id: dict[str, str] = {}
        self.websockets: set[WebSocket] = set()
        self.lock = asyncio.Lock()
        self.capture_lock = asyncio.Lock()
        self.session: ort.InferenceSession | None = None
        self.worker_task: asyncio.Task[None] | None = None
        self.accepting_uploads = False
        self.loop: asyncio.AbstractEventLoop | None = None
        self.capture_helper: subprocess.Popen[str] | None = None


def capture_photo_to_ingest(output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [
            "rpicam-still",
            "--immediate",
            "--nopreview",
            "--encoding",
            "jpg",
            "--output",
            str(output_path),
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip() or "Camera capture failed"
        raise RuntimeError(message)


def build_item(
    *,
    image_id: str,
    filename: str,
    digest: str,
    uploaded_url: str,
    metadata: dict[str, Any],
) -> ImageItem:
    return ImageItem(
        id=image_id,
        filename=filename,
        digest=digest,
        status="queued",
        uploaded_url=uploaded_url,
        gps_present=metadata["gps_present"],
        gps_latitude=metadata["gps_latitude"],
        gps_longitude=metadata["gps_longitude"],
        location_tag=metadata.get("location_tag"),
        location_label=metadata.get("location_label"),
        capture_source=metadata.get("capture_source"),
    )


async def register_ingested_photo(
    state: WebState,
    *,
    original_name: str,
    ingest_path: Path,
    digest: str,
    metadata: dict[str, Any],
    event_prefix: str,
) -> tuple[dict[str, Any], dict[str, str] | None]:
    async with state.lock:
        duplicate_id = state.hash_to_id.get(digest)
        if duplicate_id is not None:
            existing = item_to_payload(state.items[duplicate_id])
            image_id = None
            created_item = None
        else:
            image_id = item_id_from_digest(digest, state.items)
            upload_path = state.upload_dir / f"{image_id}{Path(original_name).suffix.lower()}"
            ingest_path.replace(upload_path)
            created_item = build_item(
                image_id=image_id,
                filename=original_name,
                digest=digest,
                uploaded_url=f"/media/uploads/{upload_path.name}",
                metadata=metadata,
            )
            state.items[image_id] = created_item
            state.hash_to_id[digest] = image_id
            existing = item_to_payload(created_item)

    if duplicate_id is not None:
        ingest_path.unlink(missing_ok=True)
        log_event(
            state.event_log,
            f"{event_prefix}_duplicate",
            filename=original_name,
            digest=digest,
            existing_id=duplicate_id,
            gps_present=metadata["gps_present"],
        )
        await broadcast(
            state,
            {
                "type": "duplicate",
                "filename": original_name,
                "existing_id": duplicate_id,
            },
        )
        return existing, {"filename": original_name, "existing_id": duplicate_id}

    await state.queue.put(image_id)
    log_event(
        state.event_log,
        f"{event_prefix}_queued",
        image_id=image_id,
        filename=original_name,
        digest=digest,
        gps_present=metadata["gps_present"],
        capture_source=metadata.get("capture_source"),
        location_tag=metadata.get("location_tag"),
    )
    await broadcast(state, {"type": "item", "item": existing})
    return existing, None


async def handle_gpio_capture(state: WebState) -> None:
    if not state.accepting_uploads:
        return
    if state.capture_lock.locked():
        await broadcast(
            state,
            {"type": "notice", "message": "Snapshot skipped because another capture is still running."},
        )
        return

    async with state.capture_lock:
        original_name = f"gpio-{datetime.now().strftime('%Y%m%d-%H%M%S')}.jpg"
        ingest_path = state.ingest_dir / f"{uuid.uuid4().hex}.jpg"
        log_event(
            state.event_log,
            "capture_requested",
            filename=original_name,
            trigger="gpio-short",
            trigger_pin=GPIO_TRIGGER_PIN,
        )
        try:
            await asyncio.to_thread(capture_photo_to_ingest, ingest_path)
        except Exception as exc:
            ingest_path.unlink(missing_ok=True)
            log_event(
                state.event_log,
                "capture_error",
                filename=original_name,
                error=str(exc),
                capture_source=GPIO_CAPTURE_SOURCE,
            )
            await broadcast(state, {"type": "notice", "message": f"GPIO capture failed: {exc}"})
            return

        digest = await asyncio.to_thread(hash_file, ingest_path)
        metadata = manual_location_metadata(capture_source=GPIO_CAPTURE_SOURCE)
        log_event(
            state.event_log,
            "capture_received",
            filename=original_name,
            digest=digest,
            gps_present=metadata["gps_present"],
            capture_source=GPIO_CAPTURE_SOURCE,
            location_tag=metadata["location_tag"],
        )
        payload, duplicate = await register_ingested_photo(
            state,
            original_name=original_name,
            ingest_path=ingest_path,
            digest=digest,
            metadata=metadata,
            event_prefix="capture",
        )
        if duplicate is None:
            await broadcast(
                state,
                {"type": "notice", "message": f"Snapshot captured: {payload['filename']}"},
            )
        else:
            await broadcast(
                state,
                {
                    "type": "notice",
                    "message": f"Snapshot skipped as duplicate: {duplicate['filename']}",
                },
            )


def start_gpio_trigger_helper(state: WebState) -> None:
    helper_path = Path(__file__).resolve().parent.parent / "device" / "gpio_trigger_helper.py"
    if not helper_path.exists():
        log_event(state.event_log, "capture_trigger_unavailable", error="GPIO helper script is missing")
        return

    try:
        state.capture_helper = subprocess.Popen(
            ["/usr/bin/python3", str(helper_path)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env={
                **os.environ,
                "CANOPTICON_TRIGGER_PIN": str(GPIO_TRIGGER_PIN),
                "CANOPTICON_TRIGGER_URL": f"http://127.0.0.1:{state.config.port}/api/capture",
            },
        )
    except Exception as exc:
        log_event(
            state.event_log,
            "capture_trigger_setup_error",
            trigger_pin=GPIO_TRIGGER_PIN,
            error=str(exc),
        )
        return

    log_event(
        state.event_log,
        "capture_trigger_ready",
        trigger="gpio-helper",
        trigger_pin=GPIO_TRIGGER_PIN,
        wiring=GPIO_TRIGGER_LABEL,
    )


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

            log_event(state.event_log, "processing_started", image_id=image_id, filename=item.filename)
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
                log_event(
                    state.event_log,
                    "processing_error",
                    image_id=image_id,
                    filename=item.filename,
                    error=str(exc),
                )
            else:
                async with state.lock:
                    item.status = "done"
                    item.result_url = f"/media/results/{result_path.name}"
                    item.occluded_pct = occluded_pct
                    item.elapsed_s = elapsed_s
                    item.error = None
                    payload = item_to_payload(item)
                log_event(
                    state.event_log,
                    "processing_done",
                    image_id=image_id,
                    filename=item.filename,
                    occluded_pct=round(occluded_pct, 4),
                    elapsed_s=round(elapsed_s, 4),
                )

            await broadcast(state, {"type": "item", "item": payload})
        finally:
            state.queue.task_done()


def index_existing_uploads(state: WebState) -> None:
    prior_results = read_latest_processing_results(state.event_log)
    for upload_path in sorted(state.upload_dir.iterdir()):
        if not upload_path.is_file() or upload_path.suffix.lower() not in SUPPORTED_EXTS:
            continue
        digest = hash_file(upload_path)
        image_id = upload_path.stem
        result_path = state.result_dir / f"{image_id}_overlay.jpg"
        metadata = extract_photo_metadata(upload_path)
        prior_result = prior_results.get(image_id, {})
        item = ImageItem(
            id=image_id,
            filename=upload_path.name,
            digest=digest,
            status="done" if result_path.exists() else "queued",
            uploaded_url=f"/media/uploads/{upload_path.name}",
            result_url=f"/media/results/{result_path.name}" if result_path.exists() else None,
            occluded_pct=prior_result.get("occluded_pct"),
            elapsed_s=prior_result.get("elapsed_s"),
            gps_present=metadata["gps_present"],
            gps_latitude=metadata["gps_latitude"],
            gps_longitude=metadata["gps_longitude"],
            location_tag=metadata.get("location_tag"),
            location_label=metadata.get("location_label"),
            capture_source=metadata.get("capture_source"),
        )
        state.items[image_id] = item
        state.hash_to_id[digest] = image_id
        if not result_path.exists():
            state.queue.put_nowait(image_id)


def media_response(directory: Path, filename: str) -> FileResponse:
    safe_name = Path(filename).name
    path = directory / safe_name
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(path)


def build_map_sources(state: WebState) -> tuple[list[dict[str, Any]], str]:
    sources: list[dict[str, Any]] = []
    default_source = "live-osm"

    florida_pmtiles = state.maps_dir / "florida.pmtiles"
    if florida_pmtiles.exists():
        sources.append(
            {
                "id": "offline-florida",
                "label": "Offline Florida",
                "kind": "pmtiles",
                "url": f"/maps/{florida_pmtiles.name}",
                "default": True,
                "bounds": [[24.3, -87.7], [31.1, -79.8]],
                "center": [27.8, -81.7],
                "zoom": 6,
                "max_zoom": 15,
            }
        )
        default_source = "offline-florida"

    central_florida_pmtiles = state.maps_dir / "central-florida.pmtiles"
    if central_florida_pmtiles.exists():
        sources.append(
            {
                "id": "offline-central-florida",
                "label": "Central Florida Detail",
                "kind": "pmtiles",
                "url": f"/maps/{central_florida_pmtiles.name}",
                "default": False,
                "bounds": [[27.0, -82.9], [29.5, -80.7]],
                "center": [28.25, -81.7],
                "zoom": 8,
                "max_zoom": 15,
            }
        )

    sources.append(
        {
            "id": "live-osm",
            "label": "Live OSM",
            "kind": "raster",
            "url": "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png",
            "default": default_source == "live-osm",
            "attribution": '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
            "max_zoom": 19,
        }
    )
    return sources, default_source


def create_app(config: WebConfig) -> FastAPI:
    state = WebState(config)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        config.frontend_dir.mkdir(parents=True, exist_ok=True)
        state.ingest_dir.mkdir(parents=True, exist_ok=True)
        state.upload_dir.mkdir(parents=True, exist_ok=True)
        state.result_dir.mkdir(parents=True, exist_ok=True)
        state.maps_dir.mkdir(parents=True, exist_ok=True)
        state.loop = asyncio.get_running_loop()
        log_event(state.event_log, "startup")
        state.session = await asyncio.to_thread(load_model, config.model_path, config.device)
        await asyncio.to_thread(warm_model, state.session)
        index_existing_uploads(state)
        state.accepting_uploads = True
        start_gpio_trigger_helper(state)
        state.worker_task = asyncio.create_task(processing_worker(state))
        print(f"Canopticon web app listening on http://{config.host}:{config.port}", flush=True)
        try:
            yield
        finally:
            print("Shutting down Canopticon web app...", flush=True)
            state.accepting_uploads = False
            log_event(state.event_log, "shutdown_started")
            await state.queue.put(None)
            if state.worker_task is not None:
                try:
                    await asyncio.wait_for(state.worker_task, timeout=10)
                except asyncio.TimeoutError:
                    state.worker_task.cancel()
                    await asyncio.gather(state.worker_task, return_exceptions=True)
            if state.capture_helper is not None:
                state.capture_helper.terminate()
                try:
                    state.capture_helper.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    state.capture_helper.kill()
                    state.capture_helper.wait(timeout=3)
                state.capture_helper = None
            state.session = None
            state.websockets.clear()
            state.loop = None
            log_event(state.event_log, "shutdown_complete")
            print("Canopticon shutdown complete.", flush=True)

    app = FastAPI(title="Canopticon", lifespan=lifespan)
    app.mount("/frontend", StaticFiles(directory=config.frontend_dir), name="frontend")
    app.mount("/maps", StaticFiles(directory=config.maps_dir), name="maps")

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(config.frontend_dir / "index.html")

    @app.get("/api/items")
    async def get_items() -> JSONResponse:
        async with state.lock:
            items = [item_to_payload(item) for item in state.items.values()]
        return JSONResponse({"items": items})

    @app.get("/api/map-config")
    async def get_map_config() -> JSONResponse:
        sources, default_source = build_map_sources(state)
        return JSONResponse(
            {
                "sources": sources,
                "default_source": default_source,
                "hotspot_hint": "Live tiles can work over the hotspot when Ethernet provides uplink.",
            }
        )

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

            ingest_path, digest, size = await save_upload_to_ingest(upload, state.ingest_dir)
            await upload.close()
            if size == 0:
                ingest_path.unlink(missing_ok=True)
                continue
            metadata = extract_photo_metadata(ingest_path)
            log_event(
                state.event_log,
                "upload_received",
                filename=original_name,
                digest=digest,
                size=size,
                gps_present=metadata["gps_present"],
                location_tag=metadata.get("location_tag"),
            )
            payload, duplicate = await register_ingested_photo(
                state,
                original_name=original_name,
                ingest_path=ingest_path,
                digest=digest,
                metadata=metadata,
                event_prefix="upload",
            )
            accepted.append(payload)
            if duplicate is not None:
                duplicates.append(duplicate)

        return JSONResponse({"items": accepted, "duplicates": duplicates})

    @app.post("/api/capture")
    async def capture_photo() -> Response:
        await handle_gpio_capture(state)
        return Response(status_code=204)

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
