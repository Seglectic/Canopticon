from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..settings import WebConfig


@dataclass(frozen=True)
class PluginContext:
    config: WebConfig
    event_log: Path
    base_url: str
    capture_token: str


class ManagedPlugin:
    plugin_id = "plugin"

    def start(self, context: PluginContext) -> None:
        raise NotImplementedError

    def stop(self) -> None:
        raise NotImplementedError
