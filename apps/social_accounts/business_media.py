"""Mint a fetch URL only while dispatching one authorized Business API post."""

import json
import subprocess
from fractions import Fraction
from pathlib import Path

from django.conf import settings

from providers.exceptions import PublishError
from studio_gateway.tokens import issue_media_token

MIN_SECONDS, MAX_SECONDS = 3, 600
MIN_SIDE = 360
MAX_BYTES = 1024**3
MIN_FPS, MAX_FPS = 23, 60


def video_frame_rate(asset) -> float:
    """Average frame rate read from the stored bytes (not kept in metadata)."""
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=avg_frame_rate",
            "-of",
            "json",
            asset.file.path,
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    return float(Fraction(json.loads(result.stdout)["streams"][0]["avg_frame_rate"]))


def business_video_problem(asset, *, label: str = "TikTok Business", check_frame_rate: bool = True) -> str:
    """Why ``asset`` can't be delivered as a Business API video, or "".

    Scheduling asks this so a video the platform will refuse is turned away
    then, with the exact reason, instead of failing at publish time.
    """
    if asset.media_type != "video":
        return f"{label} posts need one video."
    status = getattr(asset, "processing_status", "completed")
    if status in ("pending", "processing"):
        return "This video is still being processed. Try again in a minute."
    if status == "failed":
        return "This video couldn't be read. Re-export it as an MP4 and upload it again."
    duration = asset.duration or 0
    if not MIN_SECONDS <= duration <= MAX_SECONDS:
        return f"This video is {duration:.0f} s long. {label} needs {MIN_SECONDS}–{MAX_SECONDS} seconds."
    width, height = asset.width or 0, asset.height or 0
    if min(width, height) < MIN_SIDE:
        return f"This video is {width} × {height}. {label} needs at least {MIN_SIDE} × {MIN_SIDE}."
    if not 0 < (asset.file_size or 0) <= MAX_BYTES:
        return f"This video is larger than 1 GB. {label} accepts up to 1 GB."
    if check_frame_rate:
        try:
            fps = video_frame_rate(asset)
        except (
            OSError,
            ValueError,
            KeyError,
            IndexError,
            ZeroDivisionError,
            NotImplementedError,
            subprocess.SubprocessError,
        ):
            return "Couldn't read this video's frame rate. Re-export it as an MP4 at 23–60 FPS."
        if not MIN_FPS <= fps <= MAX_FPS:
            return f"This video is {fps:.0f} FPS. {label} needs {MIN_FPS}–{MAX_FPS} FPS. Re-export it."
    return ""


def public_media_url(asset, *, label: str = "TikTok Business"):
    if not settings.TIKTOK_BUSINESS_PUBLISH_ENABLED:
        raise PublishError("TikTok Business publishing is awaiting production activation.", retryable=False)
    problem = business_video_problem(asset, label=label)
    if problem:
        raise PublishError(problem, retryable=False)
    try:
        key = Path(settings.TIKTOK_BUSINESS_MEDIA_KEY_FILE).read_bytes().strip()
        token = issue_media_token(key, asset.file.name)
        origin = settings.TIKTOK_BUSINESS_MEDIA_ORIGIN.rstrip("/")
        if not origin.startswith("https://"):
            raise ValueError("Media origin")
    except (OSError, ValueError):
        raise PublishError("Check the private media gateway configuration.", retryable=False) from None
    return f"{origin}/media/{token}/video.mp4"
