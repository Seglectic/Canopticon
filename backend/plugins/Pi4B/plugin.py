from __future__ import annotations

import os
from pathlib import Path
import subprocess

from ..base import ManagedPlugin, PluginContext


class Pi4BPlugin(ManagedPlugin):
    plugin_id = "Pi4B"

    def __init__(self) -> None:
        self.process: subprocess.Popen[str] | None = None

    def start(self, context: PluginContext) -> None:
        runtime_path = Path(__file__).with_name("runtime.py")
        if not runtime_path.exists():
            raise RuntimeError("Pi4B runtime.py is missing")

        env = {
            **os.environ,
            "CANOPTICON_PLUGIN_BASE_URL": context.base_url,
            "CANOPTICON_PLUGIN_CAPTURE_TOKEN": context.capture_token,
            "CANOPTICON_PLUGIN_CAPTURE_URL": f"{context.base_url}/api/plugin-capture",
            "CANOPTICON_PLUGIN_PORTAL_URL": f"http://sky.local:{context.config.port}/",
            "CANOPTICON_PLUGIN_AP_SSID": "Canopticon",
            "CANOPTICON_PLUGIN_AP_INTERFACE": "wlan0",
            "CANOPTICON_PLUGIN_EVENT_LOG": str(context.event_log),
            "CANOPTICON_PLUGIN_ID": self.plugin_id,
            "CANOPTICON_PLUGIN_FRONTEND_DIR": str(context.config.frontend_dir),
        }
        self.process = subprocess.Popen(
            ["/usr/bin/python3", str(runtime_path)],
            env=env,
        )

    def stop(self) -> None:
        if self.process is None:
            return
        self.process.terminate()
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=5)
        finally:
            self.process = None


def create_plugin() -> Pi4BPlugin:
    return Pi4BPlugin()
