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


def pulse_green(strip: PixelStrip) -> None:
    steps = 18
    duration = 0.7
    for step in range(steps):
        phase = step / (steps - 1)
        intensity = math.sin(phase * math.pi)
        fill(strip, round(20 * intensity), round(255 * intensity), round(70 * intensity))
        time.sleep(duration / steps)
    fill(strip, 0, 0, 0)


def page_glow(strip: PixelStrip) -> None:
    steps = 26
    peak = 0.18
    decay = 0.9
    for step in range(steps):
        phase = step / (steps - 1)
        if phase <= peak:
            intensity = phase / peak
        else:
            tail = (phase - peak) / (1.0 - peak)
            intensity = pow(max(0.0, 1.0 - tail), decay)
        fill(strip, round(30 * intensity), round(170 * intensity), round(255 * intensity))
        time.sleep(0.035)
    fill(strip, 0, 0, 0)


def chase_purple(strip: PixelStrip) -> None:
    running = True

    def handle_signal(_signum: int, _frame: object) -> None:
        nonlocal running
        running = False

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    purple_lead = 4
    purple_position = 0
    red_position = strip.numPixels() // 2
    while running:
        for index in range(strip.numPixels()):
            purple_distance = (index - purple_position) % strip.numPixels()
            purple_red = 0
            purple_blue = 0
            if purple_distance < purple_lead:
                intensity = (purple_lead - purple_distance) / purple_lead
                purple_red = round(120 * intensity)
                purple_blue = round(255 * intensity)

            red_distance = (red_position - index) % strip.numPixels()
            red_red = 0
            if red_distance < 2:
                red_red = round(255 * ((2 - red_distance) / 2))

            strip.setPixelColor(
                index,
                Color(
                    min(255, purple_red + red_red),
                    0,
                    purple_blue,
                ),
            )
        strip.show()
        purple_position = (purple_position + 1) % strip.numPixels()
        red_position = (red_position - 1) % strip.numPixels()
        time.sleep(0.08)

    fill(strip, 0, 0, 0)


def main() -> int:
    command = sys.argv[1] if len(sys.argv) > 1 else "off"
    strip = build_strip()
    if command == "pulse-blue":
        pulse_blue(strip)
        return 0
    if command == "pulse-green":
        pulse_green(strip)
        return 0
    if command == "page-glow":
        page_glow(strip)
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
