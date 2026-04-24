from __future__ import annotations

import argparse
from pathlib import Path

import uvicorn

from .inference import process_folder
from .settings import (
    DATA_DIR,
    DEFAULT_PORT,
    DEFAULT_THUMBNAIL_SIZE,
    EVENT_LOG,
    FRONTEND_DIR,
    INGEST_DIR,
    MAPS_DIR,
    WebConfig,
)
from .web import create_app


def add_processing_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--model",
        type=str,
        default=None,
        help="Optional local path to skyseg.onnx; otherwise downloads from Hugging Face",
    )
    parser.add_argument(
        "--sky-threshold",
        type=int,
        default=160,
        help="0-255 threshold; higher means stricter sky detection",
    )
    parser.add_argument(
        "--alpha",
        type=float,
        default=0.55,
        help="Overlay strength for purple occlusion mask",
    )
    parser.add_argument(
        "--device",
        choices=["auto", "cpu", "gpu"],
        default="auto",
        help="Execution target: auto-detect, force CPU, or prefer GPU with CPU fallback",
    )
    parser.add_argument(
        "--scale",
        type=float,
        default=1.0,
        help="Optional image downscale factor before processing, e.g. 0.5 for 50%% size",
    )


def validate_processing_args(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    if not (0.0 < args.scale <= 1.0):
        parser.error("--scale must be greater than 0 and at most 1.0")
    if hasattr(args, "thumbnail_size") and args.thumbnail_size < 32:
        parser.error("--thumbnail-size must be at least 32 pixels")


def serve(args: argparse.Namespace) -> None:
    config = WebConfig(
        host=args.host,
        port=args.port,
        model_path=args.model,
        sky_threshold=args.sky_threshold,
        alpha=args.alpha,
        device=args.device,
        scale=args.scale,
        data_dir=args.data_dir,
        ingest_dir=args.ingest_dir,
        frontend_dir=args.frontend_dir,
        event_log=args.event_log,
        maps_dir=args.maps_dir,
        thumbnail_size=args.thumbnail_size,
    )
    app = create_app(config)
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Canopy/sky occlusion estimation using skyseg ONNX."
    )
    subparsers = parser.add_subparsers(dest="command")

    serve_parser = subparsers.add_parser("serve", help="Start the mobile web app")
    serve_parser.add_argument("--host", default="0.0.0.0", help="Host interface to bind")
    serve_parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="Port to bind")
    serve_parser.add_argument(
        "--data-dir",
        type=Path,
        default=DATA_DIR,
        help="Directory for managed uploads and results",
    )
    serve_parser.add_argument(
        "--ingest-dir",
        type=Path,
        default=INGEST_DIR,
        help="Directory for raw uploaded files before hash dedupe",
    )
    serve_parser.add_argument(
        "--frontend-dir",
        type=Path,
        default=FRONTEND_DIR,
        help="Directory for web UI assets",
    )
    serve_parser.add_argument(
        "--event-log",
        type=Path,
        default=EVENT_LOG,
        help="NDJSON file for upload and processing events",
    )
    serve_parser.add_argument(
        "--maps-dir",
        type=Path,
        default=MAPS_DIR,
        help="Directory for offline map assets such as PMTiles files",
    )
    serve_parser.add_argument(
        "--thumbnail-size",
        type=int,
        default=DEFAULT_THUMBNAIL_SIZE,
        help="Square thumbnail size in pixels",
    )
    add_processing_options(serve_parser)

    batch_parser = subparsers.add_parser("batch", help="Process a folder of photos")
    batch_parser.add_argument("input_dir", type=Path, help="Folder of input photos")
    batch_parser.add_argument("output_dir", type=Path, help="Folder for outputs")
    add_processing_options(batch_parser)

    args = parser.parse_args()

    if args.command == "batch":
        validate_processing_args(batch_parser, args)
        process_folder(
            input_dir=args.input_dir,
            output_dir=args.output_dir,
            model_path=args.model,
            sky_threshold=args.sky_threshold,
            alpha=args.alpha,
            device=args.device,
            scale=args.scale,
        )
        return

    if args.command is None:
        defaults = serve_parser.parse_args([])
        args = argparse.Namespace(command="serve", **vars(defaults))

    validate_processing_args(serve_parser, args)
    serve(args)
