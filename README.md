# Canopticon

Batch canopy/sky occlusion overlays for a folder of photos.

This project is configured to use the CPU-only `onnxruntime` package so it stays simple and portable across:

- Arch Linux on x86_64
- Apple Silicon Macs like a 2020 M1 MacBook

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

That will create `.venv/` and install everything needed for this project.

## Run it

Put your source images into `photos/`, then run:

```bash
uv run python canopticon.py photos outputs
```

The output folder will contain one annotated overlay image per input image.

## Useful commands

Recreate the environment from the lockfile:

```bash
uv sync --frozen
```

Run with explicit CPU mode:

```bash
uv run python canopticon.py photos outputs --device cpu
```

Prefer GPU if a supported ONNX Runtime GPU provider is installed, otherwise fall back to CPU:

```bash
uv run python canopticon.py photos outputs --device auto
```

## Notes

- CPU mode is the default and is the most reliable cross-platform setup here.
- If you later want GPU acceleration, that should be treated as an optional platform-specific add-on, not the default install path.
