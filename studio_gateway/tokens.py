"""Encrypted bearer URLs conceal storage keys and expire after one hour."""

import json
from pathlib import PurePosixPath

from cryptography.fernet import Fernet, InvalidToken

MEDIA_TTL = 3600


def safe_media_path(name):
    path = PurePosixPath(name)
    if (
        not isinstance(name, str)
        or "\\" in name
        or path.is_absolute()
        or not name.startswith("media_library/")
        or ".." in path.parts
        or path.suffix.lower() not in (".mp4", ".mov")
        or len(name) > 500
    ):
        raise ValueError("Invalid media path")
    return path


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
