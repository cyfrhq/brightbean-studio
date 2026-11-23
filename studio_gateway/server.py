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

from .tokens import media_content_type, media_filename, read_media_token

HOST = "studio-connect.digicyfr.com"
PRIVATE_CALLBACK = "https://studio-app.digicyfr.com/social-accounts/callback/social2/"
HEADERS = [
    ("Cache-Control", "no-store"),
    ("Referrer-Policy", "no-referrer"),
    ("X-Content-Type-Options", "nosniff"),
    ("X-Frame-Options", "DENY"),
    ("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'"),
]

DELETION_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Disconnect and delete data - DigiCyfr Studio</title></head>
<body style="font-family:system-ui,sans-serif;max-width:42rem;margin:2rem auto;padding:0 1rem;line-height:1.55">
<h1>Disconnect and delete your data</h1>
<p>DigiCyfr Studio is a social content workspace operated by CYFR Sp. z o.o. for agency-managed business content.
Account owners can ask us to disconnect a Facebook, Instagram, Threads or TikTok account and to delete the
information Studio holds about them. Studio cannot remove every copy held independently by a social platform.</p>

<h2>Disconnect an account</h2>
<ol>
<li>Ask the authorised DigiCyfr operator to open the correct client workspace and Social Accounts, and use Disconnect for that account.
Studio attempts to revoke the platform credential and removes the local connection.</li>
<li>Optionally revoke the grant on the platform yourself: Facebook or Instagram: Settings, Business Integrations;
Threads: Settings, Website permissions; TikTok: Settings, Security and permissions, Manage app permissions.
Platform menu names can change.</li>
</ol>

<h2>Request deletion of your information</h2>
<p>Email <!--email_off--><a href="mailto:info@digicyfr.com?subject=DigiCyfr%20Studio%20data%20deletion">info@digicyfr.com</a><!--/email_off-->
with the subject "DigiCyfr Studio data deletion" and include:</p>
<ul>
<li>your business or engagement name;</li>
<li>the platform and account handle, and the Studio workspace if known;</li>
<li>whether you want disconnection, deletion of drafts and media, removal of personal information, or termination of the whole service;</li>
<li>a reply address already associated with the engagement, where possible.</li>
</ul>
<p>Do not send a password, access token, recovery code or full identity document. We verify authority before acting.
If you contact us about a client-controlled record, the client may need to instruct the deletion.</p>

<h2>What happens next</h2>
<p>We identify the account and workspace, confirm authority, remove or coordinate removal of the relevant stored access tokens,
account details, drafts and uploaded media, and reply with the outcome within the applicable period.</p>
<p>Disconnecting does not delete a post already published on Facebook, Instagram, Threads or TikTok; remove it on the platform.
Removed information can remain in restricted recovery snapshots until they rotate, and information needed for a legal obligation,
dispute or security investigation may be retained in restricted form. We explain any exception in our reply.</p>

<h2>Contact</h2>
<p><!--email_off-->info@digicyfr.com<!--/email_off--><br>
CYFR Sp. z o.o., ul. Admiralska 10, lok. 1, 00-910 Warszawa, Poland<br>
KRS 0001266161 &middot; NIP 9522289731</p>
<p>More information: <a href="https://studio.digicyfr.com/data-deletion/">studio.digicyfr.com/data-deletion</a>
&middot; <a href="https://studio.digicyfr.com/privacy/">Privacy policy</a>
&middot; <a href="https://studio.digicyfr.com/terms/">Terms</a></p>
</body></html>
"""


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
    if path in ("/data-deletion", "/data-deletion/") and not query:
        body = DELETION_PAGE.encode()
        start_response(
            "200 OK",
            HEADERS[:-1]
            + [
                ("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'none'"),
                ("Content-Type", "text/html; charset=utf-8"),
                ("Content-Length", str(len(body))),
            ],
        )
        return [] if environ["REQUEST_METHOD"] == "HEAD" else [body]
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
        match = re.fullmatch(r"/media/([A-Za-z0-9_=-]+)/(video\.mp4|image\.jpg|image\.png)", path)
        if not match:
            return reply("404 Not Found", "Not found.")
        try:
            key = Path(os.environ["STUDIO_MEDIA_KEY_FILE"]).read_bytes().strip()
            relative = read_media_token(key, match[1])
            if media_filename(relative) != match[2]:
                raise ValueError("Media kind mismatch")
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
            ("Content-Type", media_content_type(relative)),
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
