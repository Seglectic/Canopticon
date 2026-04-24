from __future__ import annotations

import importlib
from pathlib import Path

from .base import ManagedPlugin


PLUGINS_DIR = Path(__file__).resolve().parent


def available_plugins() -> list[str]:
    names: list[str] = []
    for path in sorted(PLUGINS_DIR.iterdir()):
        if not path.is_dir():
            continue
        if path.name.startswith("_") or path.name == "__pycache__":
            continue
        if not (path / "plugin.py").exists():
            continue
        names.append(path.name)
    return names


def load_plugin(name: str) -> ManagedPlugin:
    normalized = name.strip()
    if not normalized:
        raise RuntimeError("Plugin name is empty")

    for candidate in available_plugins():
        if candidate.lower() != normalized.lower():
            continue
        module = importlib.import_module(f"backend.plugins.{candidate}.plugin")
        plugin = module.create_plugin()
        if not isinstance(plugin, ManagedPlugin):
            raise RuntimeError(f"Plugin {candidate} did not return a ManagedPlugin instance")
        return plugin

    available = ", ".join(available_plugins()) or "none"
    raise RuntimeError(f"Unknown plugin '{name}'. Available plugins: {available}")
