"""Encrypted bearer URLs conceal storage keys and expire after one hour."""

import json
from pathlib import PurePosixPath

from cryptography.fernet import Fernet, InvalidToken

MEDIA_TTL = 3600

# Stored suffix -> (public file name in the URL, Content-Type). Platforms that
# fetch by URL (TikTok Business, Threads) see only these generic names.
MEDIA_KINDS = {
    ".mp4": ("video.mp4", "video/mp4"),
    ".mov": ("video.mp4", "video/quicktime"),
    ".jpg": ("image.jpg", "image/jpeg"),
    ".jpeg": ("image.jpg", "image/jpeg"),
    ".png": ("image.png", "image/png"),
}


def safe_media_path(name):
    path = PurePosixPath(name)
    if (
        not isinstance(name, str)
        or "\\" in name
        or path.is_absolute()
        or not name.startswith("media_library/")
        or ".." in path.parts
        or path.suffix.lower() not in MEDIA_KINDS
        or len(name) > 500
    ):
        raise ValueError("Invalid media path")
    return path


def media_filename(path):
    """The generic file name a media URL ends with for this stored path."""
    return MEDIA_KINDS[PurePosixPath(path).suffix.lower()][0]


def media_content_type(path):
    return MEDIA_KINDS[PurePosixPath(path).suffix.lower()][1]


def issue_media_token(key, name):
    safe_media_path(name)
    return Fernet(key).encrypt(json.dumps({"path": name}).encode()).decode()


def read_media_token(key, token):
    if len(token) > 1600:
        raise ValueError("Invalid media token")
    try:
        data = json.loads(Fernet(key).decrypt(token.encode(), ttl=MEDIA_TTL))
        return safe_media_path(data["path"])
    except (InvalidToken, ValueError, TypeError, KeyError, UnicodeError) as exc:
        raise ValueError("Invalid or expired media token") from exc
