#!/usr/bin/env python3

from __future__ import annotations

from datetime import datetime, timezone
import io
import json
import math
import os
from pathlib import Path
import subprocess
import time
from typing import Any
from urllib import error, request

import numpy as np
from PIL import Image, ImageDraw, ImageFont
import qrcode
import RPi.GPIO as GPIO
from picamera2 import Picamera2
import spidev


DISPLAY_SIZE = 240
SPI_BUS = 0
SPI_DEVICE = 0
SPI_HZ = 62_500_000
DISPLAY_DC_PIN = 25
DISPLAY_RST_PIN = 27
DISPLAY_MADCTL = 0x08
BUTTON_PIN = 23
BUTTON_DEBOUNCE_SEC = 0.25
SAFE_MARGIN = 20
CAPTION_TOP = 193
QR_BOX_SIZE = 152
PREVIEW_SIZE = (1280, 720)
LOGO_WIDTH = 84
LOGO_BOB_AMPLITUDE = 8
LOGO_BOB_PERIOD_SEC = 1.8

BASE_URL = os.environ["CANOPTICON_PLUGIN_BASE_URL"]
CAPTURE_URL = os.environ["CANOPTICON_PLUGIN_CAPTURE_URL"]
CAPTURE_TOKEN = os.environ["CANOPTICON_PLUGIN_CAPTURE_TOKEN"]
PORTAL_URL = os.environ.get("CANOPTICON_PLUGIN_PORTAL_URL", "http://sky.local:8009/")
AP_SSID = os.environ.get("CANOPTICON_PLUGIN_AP_SSID", "Canopticon")
AP_INTERFACE = os.environ.get("CANOPTICON_PLUGIN_AP_INTERFACE", "wlan0")
EVENT_LOG = Path(os.environ.get("CANOPTICON_PLUGIN_EVENT_LOG", "data/events.ndjson"))
PLUGIN_ID = os.environ.get("CANOPTICON_PLUGIN_ID", "Pi4B")
FRONTEND_DIR = Path(os.environ.get("CANOPTICON_PLUGIN_FRONTEND_DIR", "frontend"))


def log_event(event: str, **fields: Any) -> None:
    EVENT_LOG.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "event": event,
        "plugin": PLUGIN_ID,
        **fields,
    }
    with EVENT_LOG.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True) + "\n")


class GC9A01Display:
    def __init__(self) -> None:
        GPIO.setup(DISPLAY_DC_PIN, GPIO.OUT)
        GPIO.setup(DISPLAY_RST_PIN, GPIO.OUT)

        self.spi = spidev.SpiDev()
        self.spi.open(SPI_BUS, SPI_DEVICE)
        self.spi.max_speed_hz = SPI_HZ
        self.spi.mode = 0

        self.reset()
        self.init_display()

    def reset(self) -> None:
        GPIO.output(DISPLAY_RST_PIN, GPIO.HIGH)
        time.sleep(0.05)
        GPIO.output(DISPLAY_RST_PIN, GPIO.LOW)
        time.sleep(0.05)
        GPIO.output(DISPLAY_RST_PIN, GPIO.HIGH)
        time.sleep(0.15)

    def command(self, cmd: int, data: list[int] | None = None) -> None:
        GPIO.output(DISPLAY_DC_PIN, GPIO.LOW)
        self.spi.writebytes([cmd])
        if data:
            GPIO.output(DISPLAY_DC_PIN, GPIO.HIGH)
            self.spi.writebytes(data)

    def init_display(self) -> None:
        self.command(0xEF)
        self.command(0xEB, [0x14])
        self.command(0xFE)
        self.command(0xEF)
        self.command(0xEB, [0x14])
        self.command(0x84, [0x40])
        self.command(0x85, [0xFF])
        self.command(0x86, [0xFF])
        self.command(0x87, [0xFF])
        self.command(0x88, [0x0A])
        self.command(0x89, [0x21])
        self.command(0x8A, [0x00])
        self.command(0x8B, [0x80])
        self.command(0x8C, [0x01])
        self.command(0x8D, [0x01])
        self.command(0x8E, [0xFF])
        self.command(0x8F, [0xFF])
        self.command(0xB6, [0x00, 0x20])
        self.command(0x36, [DISPLAY_MADCTL])
        self.command(0x3A, [0x05])
        self.command(0x90, [0x08, 0x08, 0x08, 0x08])
        self.command(0xBD, [0x06])
        self.command(0xBC, [0x00])
        self.command(0xFF, [0x60, 0x01, 0x04])
        self.command(0xC3, [0x13])
        self.command(0xC4, [0x13])
        self.command(0xC9, [0x22])
        self.command(0xBE, [0x11])
        self.command(0xE1, [0x10, 0x0E])
        self.command(0xDF, [0x21, 0x0C, 0x02])
        self.command(0xF0, [0x45, 0x09, 0x08, 0x08, 0x26, 0x2A])
        self.command(0xF1, [0x43, 0x70, 0x72, 0x36, 0x37, 0x6F])
        self.command(0xF2, [0x45, 0x09, 0x08, 0x08, 0x26, 0x2A])
        self.command(0xF3, [0x43, 0x70, 0x72, 0x36, 0x37, 0x6F])
        self.command(0xED, [0x1B, 0x0B])
        self.command(0xAE, [0x77])
        self.command(0xCD, [0x63])
        self.command(0x70, [0x07, 0x07, 0x04, 0x0E, 0x0F, 0x09, 0x07, 0x08, 0x03])
        self.command(0xE8, [0x34])
        self.command(0x62, [0x18, 0x0D, 0x71, 0xED, 0x70, 0x70, 0x18, 0x0F, 0x71, 0xEF, 0x70, 0x70])
        self.command(0x63, [0x18, 0x11, 0x71, 0xF1, 0x70, 0x70, 0x18, 0x13, 0x71, 0xF3, 0x70, 0x70])
        self.command(0x64, [0x28, 0x29, 0xF1, 0x01, 0xF1, 0x00, 0x07])
        self.command(0x66, [0x3C, 0x00, 0xCD, 0x67, 0x45, 0x45, 0x10, 0x00, 0x00, 0x00])
        self.command(0x67, [0x00, 0x3C, 0x00, 0x00, 0x00, 0x01, 0x54, 0x10, 0x32, 0x98])
        self.command(0x74, [0x10, 0x85, 0x80, 0x00, 0x00, 0x4E, 0x00])
        self.command(0x98, [0x3E, 0x07])
        self.command(0x35)
        self.command(0x21)
        self.command(0x11)
        time.sleep(0.12)
        self.command(0x29)
        time.sleep(0.02)

    def show_image(self, image: Image.Image) -> None:
        arr = np.asarray(image.convert("RGB"), dtype=np.uint8)
        r = arr[:, :, 0].astype(np.uint16)
        g = arr[:, :, 1].astype(np.uint16)
        b = arr[:, :, 2].astype(np.uint16)
        rgb565 = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
        out = rgb565.byteswap().tobytes()
        self.command(0x2A, [0x00, 0x00, 0x00, DISPLAY_SIZE - 1])
        self.command(0x2B, [0x00, 0x00, 0x00, DISPLAY_SIZE - 1])
        self.command(0x2C)
        GPIO.output(DISPLAY_DC_PIN, GPIO.HIGH)
        self.spi.writebytes2(out)

    def close(self) -> None:
        self.spi.close()


class CameraPreview:
    def __init__(self) -> None:
        self.camera = Picamera2()
        config = self.camera.create_preview_configuration(
            main={
                "size": PREVIEW_SIZE,
                "format": "RGB888",
            },
            buffer_count=2,
        )
        self.camera.configure(config)
        self.camera.start()
        time.sleep(0.4)

    def read(self) -> np.ndarray:
        return self.camera.capture_array("main")

    def close(self) -> None:
        self.camera.stop()
        self.camera.close()


def load_font(size: int) -> ImageFont.ImageFont:
    for candidate in (
        FRONTEND_DIR / "assets" / "fonts" / "oxanium-700.ttf",
        FRONTEND_DIR / "assets" / "fonts" / "oxanium-500.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ):
        path = Path(candidate)
        if path.exists():
            return ImageFont.truetype(str(path), size=size)
    return ImageFont.load_default()


FONT = load_font(18)
SMALL_FONT = load_font(14)


def load_logo() -> Image.Image | None:
    logo_path = FRONTEND_DIR / "assets" / "canopticon-logo.png"
    if not logo_path.exists():
        return None
    image = Image.open(logo_path).convert("RGBA")
    width, height = image.size
    target_height = round((LOGO_WIDTH / width) * height)
    return image.resize((LOGO_WIDTH, target_height), Image.Resampling.LANCZOS)


LOGO = load_logo()


def draw_centered_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    *,
    y: int,
    font: ImageFont.ImageFont,
    fill: str | tuple[int, int, int],
) -> None:
    text_box = draw.textbbox((0, 0), text, font=font)
    text_width = text_box[2] - text_box[0]
    draw.text(((DISPLAY_SIZE - text_width) / 2, y), text, fill=fill, font=font)


def build_qr_image(payload: str, *, background: tuple[int, int, int], caption: str) -> Image.Image:
    canvas = Image.new("RGB", (DISPLAY_SIZE, DISPLAY_SIZE), background)
    qr = qrcode.QRCode(border=1, box_size=8)
    qr.add_data(payload)
    qr.make(fit=True)
    qr_image = qr.make_image(fill_color="black", back_color=background).convert("RGB")
    qr_image = qr_image.resize((QR_BOX_SIZE, QR_BOX_SIZE), Image.Resampling.NEAREST)
    qr_x = (DISPLAY_SIZE - QR_BOX_SIZE) // 2
    qr_y = SAFE_MARGIN + 6
    canvas.paste(qr_image, (qr_x, qr_y))

    draw = ImageDraw.Draw(canvas)
    draw_centered_text(draw, caption, y=CAPTION_TOP, font=FONT, fill="black")
    return canvas


def build_boot_frame(phase: float) -> Image.Image:
    background = (244, 247, 251)
    canvas = Image.new("RGBA", (DISPLAY_SIZE, DISPLAY_SIZE), background + (255,))
    draw = ImageDraw.Draw(canvas)
    bob_offset = round(math.sin(phase * math.tau) * LOGO_BOB_AMPLITUDE)

    if LOGO is not None:
        logo_x = (DISPLAY_SIZE - LOGO.width) // 2
        logo_y = 52 + bob_offset
        canvas.alpha_composite(LOGO, (logo_x, logo_y))

    draw_centered_text(draw, "Canopticon", y=156, font=FONT, fill=(16, 24, 32))
    draw_centered_text(draw, "Starting up", y=180, font=SMALL_FONT, fill=(85, 100, 115))

    dot_y = 208
    for index in range(3):
        dot_phase = (phase * math.tau) - (index * 0.7)
        radius = 4 + max(0, math.sin(dot_phase)) * 2
        x = 102 + (index * 18)
        draw.ellipse((x - radius, dot_y - radius, x + radius, dot_y + radius), fill=(19, 146, 204))

    return canvas.convert("RGB")


def crop_preview(frame: np.ndarray) -> Image.Image:
    height, width, _ = frame.shape
    side = min(height, width)
    top = max(0, (height - side) // 2)
    left = max(0, (width - side) // 2)
    cropped = frame[top : top + side, left : left + side]
    image = Image.fromarray(cropped, mode="RGB")
    return image.resize((DISPLAY_SIZE, DISPLAY_SIZE), Image.Resampling.LANCZOS)


def annotate_preview(frame: np.ndarray) -> Image.Image:
    image = crop_preview(frame)
    draw = ImageDraw.Draw(image)
    banner = (14, 180, 226, 220)
    draw.rounded_rectangle((30, 188, 210, 226), radius=14, fill=banner)
    label = "Press to capture"
    text_box = draw.textbbox((0, 0), label, font=SMALL_FONT)
    text_width = text_box[2] - text_box[0]
    draw.text(((DISPLAY_SIZE - text_width) / 2, 198), label, fill="white", font=SMALL_FONT)
    return image


def status_card(background: tuple[int, int, int], title: str, body: str) -> Image.Image:
    canvas = Image.new("RGB", (DISPLAY_SIZE, DISPLAY_SIZE), background)
    draw = ImageDraw.Draw(canvas)
    draw_centered_text(draw, title, y=86, font=FONT, fill="black")
    draw_centered_text(draw, body, y=118, font=SMALL_FONT, fill="black")
    return canvas


def current_clients() -> set[tuple[str, str]]:
    result = subprocess.run(
        ["ip", "neigh", "show", "dev", AP_INTERFACE],
        capture_output=True,
        text=True,
        check=False,
    )
    clients: set[tuple[str, str]] = set()
    for line in result.stdout.splitlines():
        parts = line.split()
        if "lladdr" not in parts:
            continue
        try:
            ip_address = parts[0]
            mac_address = parts[parts.index("lladdr") + 1].lower()
        except (ValueError, IndexError):
            continue
        clients.add((ip_address, mac_address))
    return clients


def post_capture(image_bytes: bytes, filename: str) -> dict[str, Any]:
    req = request.Request(
        CAPTURE_URL,
        data=image_bytes,
        method="POST",
        headers={
            "Content-Type": "image/jpeg",
            "X-Canopticon-Plugin-Token": CAPTURE_TOKEN,
            "X-Canopticon-Filename": filename,
            "X-Canopticon-Capture-Source": f"{PLUGIN_ID}-camera",
        },
    )
    with request.urlopen(req, timeout=30) as response:
        payload = response.read().decode("utf-8")
    return json.loads(payload) if payload else {}


def server_ready() -> bool:
    try:
        with request.urlopen(f"{BASE_URL}/api/items", timeout=1.5) as response:
            return response.status == 200
    except Exception:
        return False


def encode_jpeg(frame: np.ndarray) -> bytes:
    image = Image.fromarray(frame, mode="RGB")
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=95)
    return buffer.getvalue()


def wait_for_button_release() -> None:
    start = time.monotonic()
    while time.monotonic() - start < 1.5:
        if GPIO.input(BUTTON_PIN) == GPIO.HIGH:
            return
        time.sleep(0.01)


def main() -> None:
    GPIO.setwarnings(False)
    GPIO.setmode(GPIO.BCM)
    GPIO.setup(BUTTON_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)

    display = GC9A01Display()
    preview: CameraPreview | None = None
    wifi_qr = build_qr_image(
        f"WIFI:T:nopass;S:{AP_SSID};;",
        background=(255, 255, 255),
        caption="Connect WiFi",
    )
    portal_qr = build_qr_image(
        PORTAL_URL,
        background=(197, 235, 255),
        caption="Web Portal",
    )
    known_clients = current_clients()
    portal_until = 0.0
    mode = "boot"
    last_button_press = 0.0
    boot_started_at = time.monotonic()
    boot_ready_logged = False

    log_event("plugin_runtime_started", ap_ssid=AP_SSID, portal_url=PORTAL_URL)

    try:
        while True:
            now = time.monotonic()
            if mode == "boot":
                if server_ready():
                    if not boot_ready_logged:
                        log_event("plugin_runtime_ready")
                        boot_ready_logged = True
                    mode = "wifi"
                    display.show_image(wifi_qr)
                    time.sleep(0.15)
                    continue
                phase = ((now - boot_started_at) % LOGO_BOB_PERIOD_SEC) / LOGO_BOB_PERIOD_SEC
                display.show_image(build_boot_frame(phase))
                time.sleep(0.05)
                continue

            button_pressed = GPIO.input(BUTTON_PIN) == GPIO.LOW
            if button_pressed and (now - last_button_press) >= BUTTON_DEBOUNCE_SEC:
                last_button_press = now
                wait_for_button_release()
                if mode != "preview":
                    if preview is None:
                        preview = CameraPreview()
                    mode = "preview"
                    log_event("plugin_photo_mode_started")
                else:
                    frame = preview.read()
                    image_bytes = encode_jpeg(frame)
                    filename = f"pi4b-{datetime.now().strftime('%Y%m%d-%H%M%S')}.jpg"
                    display.show_image(status_card((224, 245, 232), "Saving Photo", "Uploading to Canopticon"))
                    try:
                        response = post_capture(image_bytes, filename)
                    except error.URLError as exc:
                        log_event("plugin_capture_error", error=str(exc))
                        display.show_image(status_card((255, 228, 228), "Capture Failed", "Check Canopticon app"))
                        time.sleep(1.5)
                    else:
                        log_event("plugin_capture_posted", filename=filename, response=response)
                        if response.get("duplicate"):
                            display.show_image(status_card((255, 244, 214), "Duplicate", filename))
                        else:
                            display.show_image(status_card((224, 245, 232), "Photo Saved", filename))
                        time.sleep(1.2)
                    preview.close()
                    preview = None
                    mode = "wifi"
                    display.show_image(wifi_qr)
                    portal_until = 0.0
                continue

            if mode == "preview":
                if preview is None:
                    preview = CameraPreview()
                frame = preview.read()
                display.show_image(annotate_preview(frame))
                time.sleep(0.03)
                continue

            clients = current_clients()
            new_clients = clients - known_clients
            if new_clients:
                known_clients |= new_clients
                portal_until = time.monotonic() + 10.0
                mode = "portal"
                log_event(
                    "plugin_ap_client_detected",
                    clients=[{"ip": ip_address, "mac": mac_address} for ip_address, mac_address in sorted(new_clients)],
                )
                display.show_image(portal_qr)
            elif mode == "portal" and time.monotonic() >= portal_until:
                mode = "wifi"
                display.show_image(wifi_qr)

            time.sleep(0.25)
    finally:
        log_event("plugin_runtime_stopped")
        if preview is not None:
            preview.close()
        display.close()
        GPIO.cleanup()


if __name__ == "__main__":
    main()
