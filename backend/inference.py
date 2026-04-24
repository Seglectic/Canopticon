from __future__ import annotations

from pathlib import Path
import platform
import subprocess
import time

import cv2
from huggingface_hub import hf_hub_download
import numpy as np
import onnxruntime as ort

from .settings import CPU_PROVIDER, SUPPORTED_EXTS


MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)
MODEL_REPO = "JianyuanWang/skyseg"
MODEL_FILE = "skyseg.onnx"
LABEL_PADDING_RATIO = 0.015
LABEL_TEXT_HEIGHT_RATIO = 0.03
LABEL_LINE_GAP_RATIO = 0.01
LABEL_MIN_PADDING_PX = 10
LABEL_MIN_TEXT_HEIGHT_PX = 18
LABEL_MAX_TEXT_HEIGHT_PX = 40
LABEL_TEXT_THICKNESS = 2
PROVIDER_PRIORITY = {
    "nvidia": ["TensorrtExecutionProvider", "CUDAExecutionProvider", CPU_PROVIDER],
    "amd": ["MIGraphXExecutionProvider", "ROCMExecutionProvider", CPU_PROVIDER],
    "intel": ["OpenVINOExecutionProvider", "DmlExecutionProvider", CPU_PROVIDER],
    "generic_gpu": ["DmlExecutionProvider", "OpenVINOExecutionProvider", CPU_PROVIDER],
}


def detect_gpu_vendor() -> str | None:
    if platform.system() != "Linux":
        return None

    try:
        result = subprocess.run(
            ["lspci"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return None

    lines = [
        line.lower()
        for line in result.stdout.splitlines()
        if "vga compatible controller" in line.lower()
        or "3d controller" in line.lower()
        or "display controller" in line.lower()
    ]
    if any("nvidia" in line for line in lines):
        return "nvidia"
    if any("amd" in line or "ati" in line for line in lines):
        return "amd"
    if any("intel" in line for line in lines):
        return "intel"
    if lines:
        return "generic_gpu"
    return None


def choose_execution_providers(device: str) -> list[str]:
    available = set(ort.get_available_providers())
    if device == "cpu":
        return [CPU_PROVIDER]

    if device == "gpu":
        vendor = detect_gpu_vendor() or "generic_gpu"
        for provider in PROVIDER_PRIORITY.get(vendor, PROVIDER_PRIORITY["generic_gpu"]):
            if provider in available:
                return [provider, CPU_PROVIDER] if provider != CPU_PROVIDER else [CPU_PROVIDER]
        return [CPU_PROVIDER]

    vendor = detect_gpu_vendor()
    if vendor is not None:
        for provider in PROVIDER_PRIORITY.get(vendor, []):
            if provider in available:
                return [provider, CPU_PROVIDER] if provider != CPU_PROVIDER else [CPU_PROVIDER]

    for provider in ort.get_available_providers():
        if provider not in {CPU_PROVIDER, "AzureExecutionProvider"}:
            return [provider, CPU_PROVIDER]
    return [CPU_PROVIDER]


def load_model(
    local_model_path: str | None = None,
    device: str = "auto",
) -> ort.InferenceSession:
    model_path = local_model_path
    if model_path is None:
        model_path = hf_hub_download(repo_id=MODEL_REPO, filename=MODEL_FILE)
    providers = choose_execution_providers(device)
    print(f"Using providers: {providers}", flush=True)
    return ort.InferenceSession(model_path, providers=providers)


def preprocess_bgr(image_bgr: np.ndarray, input_size=(320, 320)) -> np.ndarray:
    resized = cv2.resize(image_bgr, input_size, interpolation=cv2.INTER_LINEAR)
    rgb = cv2.cvtColor(resized, cv2.COLOR_BGR2RGB).astype(np.float32)
    x = (rgb / 255.0 - MEAN) / STD
    x = np.transpose(x, (2, 0, 1))
    x = np.expand_dims(x, axis=0).astype(np.float32)
    return x


def infer_mask(session: ort.InferenceSession, image_bgr: np.ndarray) -> np.ndarray:
    input_name = session.get_inputs()[0].name
    output_name = session.get_outputs()[0].name

    x = preprocess_bgr(image_bgr, (320, 320))
    raw = session.run([output_name], {input_name: x})[0]
    raw = np.squeeze(raw)

    min_val = float(raw.min())
    max_val = float(raw.max())
    if max_val > min_val:
        raw = (raw - min_val) / (max_val - min_val)
    else:
        raw = np.zeros_like(raw, dtype=np.float32)

    mask_8 = (raw * 255.0).clip(0, 255).astype(np.uint8)
    mask_8 = cv2.resize(
        mask_8,
        (image_bgr.shape[1], image_bgr.shape[0]),
        interpolation=cv2.INTER_LINEAR,
    )
    return mask_8


def warm_model(session: ort.InferenceSession) -> None:
    print("Warming model with a dummy image...", flush=True)
    dummy = np.zeros((320, 320, 3), dtype=np.uint8)
    infer_mask(session, dummy)
    print("Model warmup complete.", flush=True)


def make_overlay(
    image_bgr: np.ndarray,
    sky_mask_8: np.ndarray,
    sky_threshold: int,
    alpha: float,
) -> tuple[np.ndarray, float]:
    sky_binary = sky_mask_8 >= sky_threshold
    occluded_binary = ~sky_binary

    total_pixels = occluded_binary.size
    occluded_pixels = int(occluded_binary.sum())
    occluded_pct = 100.0 * occluded_pixels / total_pixels if total_pixels else 0.0

    overlay = image_bgr.copy()
    red = np.zeros_like(image_bgr)
    red[:, :] = (0, 0, 255)

    overlay[occluded_binary] = cv2.addWeighted(
        image_bgr[occluded_binary],
        1.0 - alpha,
        red[occluded_binary],
        alpha,
        0.0,
    )

    font = cv2.FONT_HERSHEY_SIMPLEX
    short_side = min(image_bgr.shape[0], image_bgr.shape[1])
    padding_px = max(LABEL_MIN_PADDING_PX, int(round(short_side * LABEL_PADDING_RATIO)))
    text_height_px = min(
        LABEL_MAX_TEXT_HEIGHT_PX,
        max(LABEL_MIN_TEXT_HEIGHT_PX, int(round(short_side * LABEL_TEXT_HEIGHT_RATIO))),
    )
    line_gap_px = max(6, int(round(short_side * LABEL_LINE_GAP_RATIO)))
    font_scale = cv2.getFontScaleFromHeight(font, text_height_px, LABEL_TEXT_THICKNESS)
    labels = [f"Occluded: {occluded_pct:.1f}%"]
    text_sizes = [
        cv2.getTextSize(label, font, font_scale, LABEL_TEXT_THICKNESS)[0]
        for label in labels
    ]
    text_width = max(width for width, _ in text_sizes)
    text_height = text_sizes[0][1]
    x = padding_px
    y = padding_px

    cv2.rectangle(
        overlay,
        (x, y),
        (
            x + text_width + (padding_px * 2),
            y + text_height + (padding_px * 2),
        ),
        (0, 0, 0),
        -1,
    )
    baseline_y = y + padding_px + text_height
    for index, label in enumerate(labels):
        cv2.putText(
            overlay,
            label,
            (x + padding_px, baseline_y + (index * (text_height + line_gap_px))),
            font,
            font_scale,
            (255, 255, 255),
            LABEL_TEXT_THICKNESS,
            cv2.LINE_AA,
        )

    return overlay, occluded_pct


def scale_image(image_bgr: np.ndarray, scale: float) -> np.ndarray:
    if scale >= 1.0:
        return image_bgr

    width = max(1, int(round(image_bgr.shape[1] * scale)))
    height = max(1, int(round(image_bgr.shape[0] * scale)))
    return cv2.resize(image_bgr, (width, height), interpolation=cv2.INTER_AREA)


def process_image(
    session: ort.InferenceSession,
    image_bgr: np.ndarray,
    sky_threshold: int,
    alpha: float,
    scale: float,
) -> tuple[np.ndarray, float, float]:
    working_image = scale_image(image_bgr, scale)
    start_time = time.perf_counter()
    sky_mask_8 = infer_mask(session, working_image)
    elapsed_s = time.perf_counter() - start_time
    overlay, occluded_pct = make_overlay(working_image, sky_mask_8, sky_threshold, alpha)
    return overlay, occluded_pct, elapsed_s


def process_image_file(
    session: ort.InferenceSession,
    input_path: Path,
    output_path: Path,
    sky_threshold: int,
    alpha: float,
    scale: float,
) -> tuple[float, float]:
    image = cv2.imread(str(input_path), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError(f"Failed to read image: {input_path}")

    overlay, occluded_pct, elapsed_s = process_image(
        session=session,
        image_bgr=image,
        sky_threshold=sky_threshold,
        alpha=alpha,
        scale=scale,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(output_path), overlay)
    return occluded_pct, elapsed_s


def process_folder(
    input_dir: Path,
    output_dir: Path,
    model_path: str | None,
    sky_threshold: int,
    alpha: float,
    device: str,
    scale: float,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)

    session = load_model(model_path, device=device)
    files = sorted([path for path in input_dir.iterdir() if path.suffix.lower() in SUPPORTED_EXTS])
    processed_count = 0
    failed_files: list[str] = []

    for path in files:
        output_path = output_dir / f"{path.stem}_overlay{path.suffix}"
        try:
            occluded_pct, elapsed_s = process_image_file(
                session=session,
                input_path=path,
                output_path=output_path,
                sky_threshold=sky_threshold,
                alpha=alpha,
                scale=scale,
            )
        except ValueError:
            failed_files.append(path.name)
            continue
        print(
            f"Wrote {output_path} "
            f"(occluded {occluded_pct:.2f}%, model {elapsed_s:.2f}s)"
        )
        processed_count += 1

    print(f"Wrote {processed_count} overlay image(s) to: {output_dir}")
    if failed_files:
        print("Failed to read:")
        for name in failed_files:
            print(f"  {name}")

