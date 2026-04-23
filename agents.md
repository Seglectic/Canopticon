# Agent Notes

This file is a shared handoff for future agents working in this repo.

## Current Best Result

- The ONNX sky segmentation pipeline in [canopticon.py](/home/segger/Projects/Canopticon/canopticon.py) runs reliably on the Raspberry Pi 4B (`zinger`).
- The Pi Zero 2 W was not a good fit for the same workload.
- Current recommendation: use the Pi 4B CPU path as the baseline for product work.

## Raspberry Pi 4B

- Hostname: `zinger.local`
- User: `zing`
- SSH key auth is configured.
- Password SSH was disabled.
- Avahi is enabled so `.local` naming works again.
- Direct IP seen during setup: `192.168.1.250`

## Pi 4B Cleanup

The Pi 4B was trimmed down for headless use.

Removed or disabled from the boot path:

- `NetworkManager-wait-online`
- `lightdm`
- `cups`
- `cloud-init`
- `udisks2`
- `wayvnc`
- large parts of the desktop stack
- user audio/session services like `wireplumber`, `pipewire`, and `pipewire-pulse` were masked for the `zing` user

Post-reboot, the active system service set was reduced to about 13 services.

## ONNX Findings

- `uv sync` works on the Pi 4B for this repo.
- The current ONNX path uses `onnxruntime` CPU execution on the Pi 4B.
- ONNX Runtime GPU acceleration is not realistically available for the Raspberry Pi 4B's VideoCore VI GPU through the normal ONNX Runtime providers.
- Practical acceleration options are CPU tuning or input scaling.

## ONNX Benchmark

Fresh full-folder run on Pi 4B CPU:

- command:
  - `uv run python canopticon.py photos outputs/onnx2 --device cpu --scale 1.0`
- images processed: `46`
- wall-clock time: about `280.1s`
- per-image model time: about `4.62s` to `4.76s`

Results were copied locally to:

- [outputs/onnx2](/home/segger/Projects/Canopticon/outputs/onnx2)

There is also an earlier smaller batch copied to:

- [outputs/onnx](/home/segger/Projects/Canopticon/outputs/onnx)

## Overlay Changes Made

The ONNX overlay in [canopticon.py](/home/segger/Projects/Canopticon/canopticon.py) was updated to:

- scale the annotation box based on image size so it stays visually consistent across resolutions
- add a second line showing per-image model inference time in seconds

The overlay now shows:

- `Occluded: ...%`
- `Model: ...s`

## Good Next Steps

- Exercise the new mobile web UI on the Pi 4B with real phone uploads.
- If more performance is needed, test ONNX with `--scale 0.75` and `--scale 0.5` before changing models.

## Intended Product Direction

The intended way forward is a self-hosted Pi appliance with a mobile-first web UI.

## Current Web Implementation

- [canopticon.py](/home/segger/Projects/Canopticon/canopticon.py) now hosts the web app directly.
- Default command:
  - `uv run python canopticon.py`
- Explicit server command:
  - `uv run python canopticon.py serve --port 8009`
- The app binds `0.0.0.0:8009` by default.
- Startup loads the ONNX session once and warms it with a dummy image.
- Processing uses one in-process FIFO worker and one ONNX session.
- There are no detached model processes or daemonized workers; stopping `canopticon.py` stops the app and model owner.
- The original folder processor remains available:
  - `uv run python canopticon.py batch photos outputs`

### Implemented upload workflow

1. User uploads one or more photos from the web UI.
2. Each file is written into `staging/` first.
3. The staged file is hashed.
4. Duplicate hashes are discarded from staging and are not processed again.
5. New files move to `canopticon_data/uploads/`.
6. New files enter the FIFO processing queue.
7. Finished overlays are written to `canopticon_data/results/`.
8. The browser receives WebSocket updates for `queued`, `processing`, `done`, `duplicate`, and `error` events.

### Runtime directories

- `staging/` is temporary upload scratch space.
- `canopticon_data/uploads/` stores managed originals.
- `canopticon_data/results/` stores generated overlays.
- These directories are ignored by git.

### Deployment shape

- The Pi should host its own Wi-Fi access point.
- The Pi should host a web server.
- The expected user entrypoint is:
  - connect phone to the Pi access point
  - open `sky.local`

### UI direction

- Design the interface mobile first.
- Uploaded images should appear in a gallery-like view.
- A gallery or carousel are both acceptable directions.
- Each image should be expandable so the user can inspect status and result.
- There should be a bottom-anchored upload button for selecting photos from the phone camera roll.

### Live updates

- Maintain a WebSocket connection so the UI updates as processing completes.
- While processing:
  - show a loading state
  - show the image name
  - update the thumbnail/result automatically when finished

### Upload and processing workflow

- The core app workflow is implemented in [canopticon.py](/home/segger/Projects/Canopticon/canopticon.py).
- Remaining appliance work is Pi network setup, local hostname routing, and any service-level deployment.

### Duplicate handling

- Duplicate uploads should be rejected based on file hash.
- Duplicates should not be processed again.
- Duplicates should not remain in staging after detection.

### Current implementation priority

- Use the ONNX-on-Pi-4B path as the first production implementation.
- Do not block UI work on Coral.
- Treat Coral as a future optimization path that likely needs a different model.

## Physical Product Direction

The project is also intended to become a self-contained physical device, not just a Pi on a desk.

### Enclosure

- Plan for a custom 3D printed enclosure.
- Include a laser-cut window or viewing panel as part of the final housing.
- Leave room for cable management, battery mounting, airflow, and service access.

### Lighting

- Plan for addressable LEDs for physical feedback and presentation.
- LEDs are intended both for status indication and for showy "AI device" style animations.
- Future UI / device-state design should account for LED states such as:
  - booting
  - AP ready
  - upload in progress
  - queued
  - processing
  - success
  - duplicate / rejected
  - error

### Optional Local Display

- A TFT connected to the Pi is being considered.
- This is not yet locked in, but future hardware/software decisions should avoid making a TFT impossible to add later.
- If added, likely uses:
  - status display
  - upload / ready indicators
  - local branding / idle screen
  - maybe a thumbnail / queue preview

### Power

- Planned power source: USB-C battery pack
- Battery noted: UGREEN `100W`, `20000 mAh`
- Current assumption is that the battery should provide enough power budget for:
  - Raspberry Pi 4B
  - Coral USB Accelerator
  - LEDs
  - possible TFT

### Future Hardware Help Needed

Future agents may need to help with:

- enclosure layout planning
- thermal considerations
- cable routing and connector placement
- LED part selection and animation control
- TFT selection and integration
- power budgeting and runtime estimates
- safe startup / shutdown behavior for battery-powered operation
