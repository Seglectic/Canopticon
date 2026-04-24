#!/usr/bin/env python3

from __future__ import annotations

import os
import sys
import time
from urllib import error, request

from gpiozero import Button


TRIGGER_PIN = int(os.environ.get("CANOPTICON_TRIGGER_PIN", "17"))
TRIGGER_URL = os.environ.get("CANOPTICON_TRIGGER_URL", "http://127.0.0.1:8009/api/capture")


def trigger_capture() -> None:
    req = request.Request(TRIGGER_URL, data=b"", method="POST")
    with request.urlopen(req, timeout=10):
        pass


def main() -> None:
    button = Button(TRIGGER_PIN, pull_up=True, bounce_time=0.15)
    print(f"GPIO trigger helper armed on BCM{TRIGGER_PIN}", flush=True)
    try:
        while True:
            button.wait_for_press()
            try:
                trigger_capture()
            except error.URLError as exc:
                print(f"GPIO trigger POST failed: {exc}", file=sys.stderr, flush=True)
            time.sleep(0.2)
            button.wait_for_release(timeout=1)
    finally:
        button.close()


if __name__ == "__main__":
    main()
