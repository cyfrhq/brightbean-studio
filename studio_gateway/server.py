"""WSGI gateway. Run with Gunicorn, never log request URIs or query strings.

The callback only redirects to one fixed private app; its signed state and
one-use nonce are verified by that app before token exchange. No app secrets,
sessions, browser cookies or database credentials are available here.
"""

import json
import os
import re
from pathlib import Path
from urllib.parse import parse_qs, urlencode

from .tokens import read_media_token

HOST = "studio-connect.digicyfr.com"
PRIVATE_CALLBACK = "https://app.studio.digicyfr.com:18081/social-accounts/callback/social2/"
HEADERS = [
    ("Cache-Control", "no-store"),
    ("Referrer-Policy", "no-referrer"),
    ("X-Content-Type-Options", "nosniff"),
    ("X-Frame-Options", "DENY"),
    ("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'"),
]


def callback_location(query):
    values = parse_qs(query, keep_blank_values=True, max_num_fields=8)
    if set(values) - {"auth_code", "state", "error", "error_description", "code", "scopes"}:
        raise ValueError("Unexpected callback parameters")
    if any(len(v) != 1 for v in values.values()):
        raise ValueError("Duplicate callback parameters")
    state = values.get("state", [""])[0]
    if not state or len(state) > 4096:
        raise ValueError("Missing state")
    if "error" in values:
        params = {"error": "authorization_denied", "state": state}
    else:
        # The current account-holder consent returns `code`; Business token
        # exchange calls that value `auth_code`. Normalize at this boundary,
        # preserving support for the documented auth_code callback spelling.
        # Reject both spellings together rather than choosing an ambiguous one.
        if "code" in values and "auth_code" in values:
            raise ValueError("Ambiguous authorization code")
        code = values.get("auth_code", values.get("code", [""]))[0]
        if not code or len(code) > 2048:
            raise ValueError("Missing authorization code")
        params = {"auth_code": code, "state": state}
    return PRIVATE_CALLBACK + "?" + urlencode(params)


def byte_range(header, size):
    if not header:
        return 0, size - 1, False
    match = re.fullmatch(r"bytes=(\d*)-(\d*)", header)
    if not match or not any(match.groups()):
        raise ValueError("Invalid range")
    left, right = match.groups()
    if not left:
        suffix = int(right)
        if suffix <= 0:
            raise ValueError("Invalid suffix")
        start, end = max(0, size - suffix), size - 1
    else:
        start, end = int(left), min(int(right), size - 1) if right else size - 1
    if not 0 <= start <= end < size:
        raise ValueError("Unsatisfiable range")
    return start, end, True


def _stream(file, start, length):
    try:
        file.seek(start)
        while length:
            data = file.read(min(length, 256 * 1024))
            if not data:
                break
            length -= len(data)
            yield data
    finally:
        file.close()


def application(environ, start_response):
    def reply(status, text, extra=()):
        body = text.encode()
        start_response(
            status,
            HEADERS + [("Content-Type", "text/plain; charset=utf-8"), ("Content-Length", str(len(body)))] + list(extra),
        )
        return [] if environ["REQUEST_METHOD"] == "HEAD" else [body]

    if environ.get("HTTP_HOST") != HOST:
        return reply("421 Misdirected Request", "Unknown service hostname.")
    if environ["REQUEST_METHOD"] not in ("GET", "HEAD"):
        return reply("405 Method Not Allowed", "GET or HEAD required.", [("Allow", "GET, HEAD")])
    path = environ.get("PATH_INFO", "")
    query = environ.get("QUERY_STRING", "")
    if len(query) > 8192 or len(path) > 2048:
        return reply("400 Bad Request", "Invalid request.")
    if path == "/social2/callback/":
        if not query:
            return reply(
                "200 OK",
                "DigiCyfr Studio account authorization callback. Start connection inside Studio.\n"
                "Service and policies: https://studio.digicyfr.com/\n",
            )
        try:
            location = callback_location(query)
        except ValueError:
            return reply("400 Bad Request", "Invalid authorization callback. Start again inside Studio.")
        return reply("303 See Other", "Returning to your private Studio workspace.", [("Location", location)])
    if path == "/advertiser/callback/":
        # The portal has a required advertiser field even for organic apps.
        # No advertiser scopes are requested and no advertiser grant is exchanged.
        return reply(
            "200 OK",
            "DigiCyfr Studio does not configure advertiser access.\n"
            "For organic account connections, start inside Studio.\n",
        )
    if path in ("/", "/health/"):
        return reply(
            "200 OK",
            "DigiCyfr Studio connection service. https://studio.digicyfr.com/\nCorresponding source (AGPL): /source/\n",
        )
    if path == "/source/" and not query:
        try:
            source = Path(os.environ["STUDIO_SOURCE_ARCHIVE"]).open("rb")  # noqa: SIM115 - streamed/closed by _stream
            size = os.fstat(source.fileno()).st_size
        except (KeyError, OSError):
            return reply("503 Service Unavailable", "Source temporarily unavailable; contact info@digicyfr.com.")
        start_response(
            "200 OK",
            HEADERS
            + [
                ("Content-Type", "application/gzip"),
                ("Content-Disposition", 'attachment; filename="digicyfr-studio-source.tar.gz"'),
                ("Content-Length", str(size)),
            ],
        )
        if environ["REQUEST_METHOD"] == "HEAD":
            source.close()
            return []
        return _stream(source, 0, size)
    if path.startswith("/media/"):
        if os.environ.get("STUDIO_MEDIA_ENABLED") != "true" or query:
            return reply("404 Not Found", "Not found.")
        match = re.fullmatch(r"/media/([A-Za-z0-9_=-]+)/video\.mp4", path)
        if not match:
            return reply("404 Not Found", "Not found.")
        try:
            key = Path(os.environ["STUDIO_MEDIA_KEY_FILE"]).read_bytes().strip()
            relative = read_media_token(key, match[1])
            root = Path(os.environ["STUDIO_MEDIA_ROOT"]).resolve(strict=True)
            target = root.joinpath(relative).resolve(strict=True)
            if not target.is_relative_to(root) or not target.is_file():
                raise ValueError("Invalid media")
            # Storage is mounted read-only and the app uses random immutable
            # keys. Reject symlink components even if they stay inside root.
            current = root
            for part in relative.parts:
                current = current / part
                if current.is_symlink():
                    raise ValueError("Symlink media")
            file = target.open("rb")
            size = os.fstat(file.fileno()).st_size
            if not 0 < size <= 1024**3:
                file.close()
                raise ValueError("Invalid size")
        except (OSError, KeyError, ValueError):
            return reply("404 Not Found", "Not found.")
        try:
            start, end, partial = byte_range(environ.get("HTTP_RANGE"), size)
        except ValueError:
            file.close()
            return reply("416 Range Not Satisfiable", "Invalid range.", [("Content-Range", f"bytes */{size}")])
        headers = HEADERS + [
            ("Content-Type", "video/quicktime" if relative.suffix.lower() == ".mov" else "video/mp4"),
            ("Content-Length", str(end - start + 1)),
            ("Accept-Ranges", "bytes"),
        ]
        if partial:
            headers.append(("Content-Range", f"bytes {start}-{end}/{size}"))
        start_response("206 Partial Content" if partial else "200 OK", headers)
        if environ["REQUEST_METHOD"] == "HEAD":
            file.close()
            return []
        return _stream(file, start, end - start + 1)
    # Only exact provider ownership files explicitly listed by the operator.
    try:
        checks = json.loads(Path(os.environ["STUDIO_VERIFICATION_FILE"]).read_text())
        value = checks.get(path)
        if re.fullmatch(r"/[A-Za-z0-9_-]{1,150}\.txt", path) and isinstance(value, str) and len(value) <= 8192:
            return reply("200 OK", value)
    except (OSError, KeyError, ValueError):
        pass
    return reply("404 Not Found", "Not found.")
