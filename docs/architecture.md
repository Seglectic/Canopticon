# Canopticon Architecture

## Current Package Layout

- `canopticon.py`
  - thin entrypoint wrapper
- `backend/cli.py`
  - CLI parsing and command dispatch
- `backend/web.py`
  - FastAPI app, upload routes, WebSocket updates, worker lifecycle
- `backend/inference.py`
  - ONNX model loading, provider selection, preprocessing, inference, overlay generation
- `backend/storage.py`
  - upload ingest, hashing, EXIF/GPS extraction, event log read/write
- `backend/models.py`
  - application datamodels like `ImageItem`
- `backend/settings.py`
  - shared constants and web config
- `client/`
  - static web UI

## Runtime Flow

```text
Browser / Phone
    |
    v
FastAPI routes in web.py
    |
    +--> GET /                -> serves client/index.html
    +--> GET /api/items       -> returns current in-memory item snapshot
    +--> POST /api/upload     -> saves upload to ingest, hashes file, extracts GPS
    +--> GET /media/...       -> serves originals and overlays
    +--> WS /ws               -> pushes snapshot + live item updates
    |
    v
WebState
    |
    +--> in-memory items
    +--> digest dedupe map
    +--> websocket clients
    +--> async queue
    +--> single ONNX session
    |
    v
Background worker
    |
    +--> reads queued upload
    +--> runs ONNX inference
    +--> creates red occlusion overlay
    +--> writes result image
    +--> appends NDJSON event log
    +--> broadcasts status change over websocket
```

## Startup Flow

```text
systemd
    |
    v
uv run python canopticon.py serve
    |
    v
cli.py builds WebConfig
    |
    v
web.py creates FastAPI app
    |
    v
lifespan startup
    |
    +--> ensure runtime directories exist
    +--> log startup event
    +--> load ONNX model
    +--> warm model with dummy image
    +--> index prior uploads/results
    +--> start async processing worker
    |
    v
server ready on :8009
```

## Upload And Processing Flow

```text
User selects photo(s)
    |
    v
POST /api/upload
    |
    +--> write raw file to data/ingest/
    +--> hash file
    +--> inspect EXIF GPS
    +--> reject duplicate hashes
    +--> move unique file to data/uploads/
    +--> create ImageItem with status=queued
    +--> enqueue image id
    +--> broadcast queued item
    |
    v
processing_worker()
    |
    +--> mark item processing
    +--> run process_image_file()
    |     |
    |     +--> load image from disk
    |     +--> optionally scale
    |     +--> run sky segmentation model
    |     +--> threshold sky vs occlusion
    |     +--> paint red overlay
    |     +--> write result to data/results/
    |
    +--> mark item done or error
    +--> append event to events.ndjson
    +--> broadcast item update
```

## Map / GPS Flow

```text
Uploaded photo
    |
    v
storage.py extracts GPS EXIF
    |
    v
ImageItem stores gps_present / latitude / longitude
    |
    v
/api/items and websocket snapshot include coordinates
    |
    v
client/app.js renders map pins and cluster summaries
```

## Notes

- Processing is intentionally single-worker and in-process right now.
- The warmed ONNX session is shared by the queue worker.
- Runtime persistence is file-based:
  - originals in `data/uploads/`
  - overlays in `data/results/`
  - lifecycle events in `data/events.ndjson`
