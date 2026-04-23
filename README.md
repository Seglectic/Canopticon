# Canopticon

Mobile-first canopy/sky occlusion processing with a local web UI.

Canopticon runs a small web server, warms the ONNX sky segmentation model at startup, and lets a phone upload photos for processing. The app is meant to run on a Raspberry Pi 4B or a development machine, with the current default listening on port `8009`.

## What `uv` does

`uv` is a fast Python project manager. In this project it handles:

- installing a compatible Python version from [`.python-version`](./.python-version)
- creating a local virtual environment in `.venv/`
- installing the dependencies from [`pyproject.toml`](./pyproject.toml)
- locking exact versions in `uv.lock`

The main command you want is `uv sync`.

## One-time setup

Install `uv`:

- Arch Linux: `sudo pacman -S uv`
- macOS: `brew install uv`

Then from this folder run:

```bash
uv sync
```

That creates `.venv/` and installs everything needed for this project.

## Run the web app

Start Canopticon on port `8009`:

```bash
uv run python canopticon.py
```

Or run the explicit server command:

```bash
uv run python canopticon.py serve --port 8009
```

Open this from the same machine:

```text
http://localhost:8009
```

On a phone, open the host's LAN address with port `8009`.

## Mobile upload flow

- Select one or more photos with the bottom `Add Photos` button.
- Uploads are written to `canopticon_data/ingest/` first.
- Each uploaded file is hashed.
- Duplicate hashes are discarded and are not processed again.
- New files move into `canopticon_data/uploads/` and enter a FIFO queue.
- A single in-process worker runs the warmed ONNX model and writes overlays to `canopticon_data/results/`.
- The gallery updates over a WebSocket with `queued`, `processing`, `done`, `duplicate`, and `error` states.
- Upload and processing lifecycle events are appended to `canopticon_data/events.ndjson`.
- GPS EXIF metadata is checked after upload; the UI shows whether GPS was found.

## Web UI files

The frontend lives in [`client/`](./client/) so the UI can be maintained without editing the Python server:

- `client/index.html`
- `client/styles.css`
- `client/app.js`

## Shutdown behavior

The model session and processing worker live inside the `canopticon.py` process. Stop the app with `Ctrl-C`; shutdown stops new uploads, signals the worker, releases the model session reference, and exits without leaving a detached model process behind.

## Batch mode

The original folder workflow is still available as a subcommand:

```bash
uv run python canopticon.py batch path/to/photos outputs
```

Run with explicit CPU mode:

```bash
uv run python canopticon.py batch path/to/photos outputs --device cpu
```

Prefer GPU if a supported ONNX Runtime GPU provider is installed, otherwise fall back to CPU:

```bash
uv run python canopticon.py batch path/to/photos outputs --device auto
```

## Runtime directories

These are local runtime directories and are ignored by git:

- `canopticon_data/`
- `outputs/`

`pyproject.toml`, `.python-version`, and `uv.lock` stay in the repository root because `uv` discovers project configuration there.

## Notes

- CPU mode is the most reliable cross-platform setup.
- If GPU acceleration is added later, treat it as an optional platform-specific install path.
- The future appliance target is a Pi-hosted access point with a friendly local hostname such as `sky.local`.
