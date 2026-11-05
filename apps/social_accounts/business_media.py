"""Mint a fetch URL only while dispatching one authorized Business API post."""

import json
import subprocess
from fractions import Fraction
from pathlib import Path

from django.conf import settings

from providers.exceptions import PublishError
from studio_gateway.tokens import issue_media_token


def public_media_url(asset):
    if not settings.TIKTOK_BUSINESS_PUBLISH_ENABLED:
        raise PublishError("TikTok Business publishing is awaiting production activation.", retryable=False)
    if (
        asset.media_type != "video"
        or not 3 <= (asset.duration or 0) <= 600
        or min(asset.width or 0, asset.height or 0) < 360
        or not 0 < asset.file_size <= 1024**3
    ):
        raise PublishError("Use a 3–600 second video, at least 360 × 360 and at most 1 GB.", retryable=False)
    try:
        # Inspect actual bytes; saved metadata does not include frame rate.
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
        fps = float(Fraction(json.loads(result.stdout)["streams"][0]["avg_frame_rate"]))
        if not 23 <= fps <= 60:
            raise ValueError("Frame rate")
        key = Path(settings.TIKTOK_BUSINESS_MEDIA_KEY_FILE).read_bytes().strip()
        token = issue_media_token(key, asset.file.name)
        origin = settings.TIKTOK_BUSINESS_MEDIA_ORIGIN.rstrip("/")
        if not origin.startswith("https://"):
            raise ValueError("Media origin")
    except (
        OSError,
        ValueError,
        KeyError,
        IndexError,
        ZeroDivisionError,
        NotImplementedError,
        subprocess.SubprocessError,
    ):
        raise PublishError(
            "Check the video frame rate (23–60 FPS) and private media gateway configuration.", retryable=False
        ) from None
    return f"{origin}/media/{token}/video.mp4"
