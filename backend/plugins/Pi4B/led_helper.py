#!/usr/bin/env python3

from __future__ import annotations

import math
import signal
import sys
import time

from rpi_ws281x import PixelStrip, Color


LED_COUNT = 10
LED_PIN = 18
LED_FREQ_HZ = 800_000
LED_DMA = 10
LED_BRIGHTNESS = 96
LED_INVERT = False
LED_CHANNEL = 0


def build_strip() -> PixelStrip:
    strip = PixelStrip(
        LED_COUNT,
        LED_PIN,
        LED_FREQ_HZ,
        LED_DMA,
        LED_INVERT,
        LED_BRIGHTNESS,
        LED_CHANNEL,
    )
    strip.begin()
    return strip


def fill(strip: PixelStrip, red: int, green: int, blue: int) -> None:
    color = Color(red, green, blue)
    for index in range(strip.numPixels()):
        strip.setPixelColor(index, color)
    strip.show()


def pulse_blue(strip: PixelStrip) -> None:
    steps = 36
    duration = 1.8
    for step in range(steps):
        phase = step / (steps - 1)
        intensity = 0.15 + (0.85 * ((math.sin(phase * math.tau * 1.5) + 1.0) / 2.0))
        fill(strip, 0, round(70 * intensity), round(255 * intensity))
        time.sleep(duration / steps)
    fill(strip, 0, 0, 0)


def chase_purple(strip: PixelStrip) -> None:
    running = True

    def handle_signal(_signum: int, _frame: object) -> None:
        nonlocal running
        running = False

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    lead = 3
    position = 0
    while running:
        for index in range(strip.numPixels()):
            distance = (index - position) % strip.numPixels()
            if distance >= lead:
                strip.setPixelColor(index, Color(0, 0, 0))
                continue
            intensity = (lead - distance) / lead
            strip.setPixelColor(
                index,
                Color(round(120 * intensity), 0, round(255 * intensity)),
            )
        strip.show()
        position = (position + 1) % strip.numPixels()
        time.sleep(0.08)

    fill(strip, 0, 0, 0)


def main() -> int:
    command = sys.argv[1] if len(sys.argv) > 1 else "off"
    strip = build_strip()
    if command == "pulse-blue":
        pulse_blue(strip)
        return 0
    if command == "chase-purple":
        chase_purple(strip)
        return 0
    if command == "off":
        fill(strip, 0, 0, 0)
        return 0
    raise SystemExit(f"Unknown LED command: {command}")


if __name__ == "__main__":
    raise SystemExit(main())
