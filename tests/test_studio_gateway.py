"""Gateway security boundaries exercised with synthetic bytes, never client media."""

import time
from urllib.parse import parse_qs, urlsplit

import pytest
from cryptography.fernet import Fernet

from studio_gateway.server import HOST, PRIVATE_CALLBACK, application, callback_location
from studio_gateway.tokens import issue_media_token, read_media_token


def request(path, query="", method="GET", host=HOST, **kwargs):
    captured = {}

    def start(status, headers):
        captured.update(status=status, headers=dict(headers))

    body = b"".join(
        application(
            {"HTTP_HOST": host, "REQUEST_METHOD": method, "PATH_INFO": path, "QUERY_STRING": query, **kwargs}, start
        )
    )
    return captured["status"], captured["headers"], body


@pytest.mark.parametrize("name", ["auth_code", "code"])
def test_callback_fixed_destination_no_arbitrary_return_url(name):
    location = callback_location(f"{name}=synthetic&state=signed&scopes=user.info.basic%2Cvideo.publish")
    assert location.startswith(PRIVATE_CALLBACK + "?")
    assert parse_qs(urlsplit(location).query) == {"auth_code": ["synthetic"], "state": ["signed"]}
    for query in (
        "auth_code=x",
        "auth_code=x&state=s&next=https://evil.test",
        "auth_code=x&state=s&state=other",
        "code=x&auth_code=x&state=s",
        "code=x&code=other&state=s",
        "code=&state=s",
        "auth_code=x&state=" + "x" * 4097,
    ):
        with pytest.raises(ValueError):
            callback_location(query)


def test_callback_and_security_headers():
    status, headers, _ = request("/social2/callback/", "auth_code=synthetic&state=signed")
    assert status.startswith("303")
    assert headers["Cache-Control"] == "no-store"
    assert headers["Referrer-Policy"] == "no-referrer"
    assert request("/social2/callback/")[0] == "200 OK"
    status, headers, _ = request("/social2/callback/", "code=synthetic&scopes=user.info.basic%2Cvideo.publish&state=s")
    assert status == "303 See Other"
    assert parse_qs(urlsplit(headers["Location"]).query) == {"auth_code": ["synthetic"], "state": ["s"]}
    assert request("/social2/callback/", "code=one&auth_code=other&state=s")[0] == "400 Bad Request"
    assert request("/", host="attacker.example")[0].startswith("421")
    assert request("/", method="POST")[0].startswith("405")
    assert request("/media_library/private.mp4")[0].startswith("404")
    assert "Location" not in request("/advertiser/callback/", "auth_code=unwanted")[1]


def test_encrypted_token_tamper_expiry_and_traversal():
    key = Fernet.generate_key()
    token = issue_media_token(key, "media_library/2026/11/random.mp4")
    assert "media_library" not in token
    assert str(read_media_token(key, token)) == "media_library/2026/11/random.mp4"
    expired = Fernet(key).encrypt_at_time(b'{"path":"media_library/x.mp4"}', int(time.time()) - 3601).decode()
    for bad in (
        token[:-5] + "xxxxx",
        expired,
        Fernet(key).encrypt(b'{"path":"media_library/../../secrets.mp4"}').decode(),
    ):
        with pytest.raises(ValueError):
            read_media_token(key, bad)


@pytest.fixture
def media(tmp_path, monkeypatch):
    key = Fernet.generate_key()
    key_file = tmp_path / "key"
    key_file.write_bytes(key)
    root = tmp_path / "media"
    (root / "media_library").mkdir(parents=True)
    (root / "media_library/x.mp4").write_bytes(b"0123456789")
    monkeypatch.setenv("STUDIO_MEDIA_ENABLED", "true")
    monkeypatch.setenv("STUDIO_MEDIA_ROOT", str(root))
    monkeypatch.setenv("STUDIO_MEDIA_KEY_FILE", str(key_file))
    return root, key, "/media/" + issue_media_token(key, "media_library/x.mp4") + "/video.mp4"


def test_media_range_head_and_off_switch(media, monkeypatch):
    _, _, path = media
    assert request(path)[2] == b"0123456789"
    status, headers, body = request(path, HTTP_RANGE="bytes=2-5")
    assert (status, body, headers["Content-Range"]) == ("206 Partial Content", b"2345", "bytes 2-5/10")
    assert request(path, HTTP_RANGE="bytes=-3")[2] == b"789"
    assert request(path, HTTP_RANGE="bytes=99-")[0].startswith("416")
    assert request(path, HTTP_RANGE="bytes=1-2,5-6")[0].startswith("416")
    assert request(path, method="HEAD")[2] == b""
    assert request(path, query="arbitrary=x")[0].startswith("404")
    monkeypatch.setenv("STUDIO_MEDIA_ENABLED", "false")
    assert request(path)[0].startswith("404")


def test_images_are_served_with_their_type_and_kind_must_match(media):
    root, key, _ = media
    (root / "media_library/x.jpg").write_bytes(b"\xff\xd8synthetic")
    (root / "media_library/x.png").write_bytes(b"\x89PNGsynthetic")
    jpg, png = (issue_media_token(key, f"media_library/x.{ext}") for ext in ("jpg", "png"))
    status, headers, body = request(f"/media/{jpg}/image.jpg")
    assert (status, headers["Content-Type"], body) == ("200 OK", "image/jpeg", b"\xff\xd8synthetic")
    assert request(f"/media/{png}/image.png")[1]["Content-Type"] == "image/png"
    # A token only opens under the name of its own kind.
    assert request(f"/media/{jpg}/video.mp4")[0].startswith("404")
    assert request(f"/media/{jpg}/image.png")[0].startswith("404")
    assert request(f"/media/{jpg}/image.webp")[0].startswith("404")
    with pytest.raises(ValueError):
        issue_media_token(key, "media_library/x.webp")


def test_deletion_page_is_static_html():
    status, headers, body = request("/data-deletion")
    assert status == "200 OK" and headers["Content-Type"].startswith("text/html")
    assert b"info@digicyfr.com" in body
    assert request("/data-deletion", query="x=1")[0].startswith("404")


def test_symlink_cannot_expose_other_files(media, tmp_path):
    root, key, _ = media
    outside = tmp_path / "outside.mp4"
    outside.write_bytes(b"private")
    (root / "media_library/link.mp4").symlink_to(outside)
    url = "/media/" + issue_media_token(key, "media_library/link.mp4") + "/video.mp4"
    assert request(url)[0].startswith("404")


def test_source_offer_streams_only_fixed_archive(tmp_path, monkeypatch):
    archive = tmp_path / "source.tar.gz"
    archive.write_bytes(b"synthetic source archive")
    monkeypatch.setenv("STUDIO_SOURCE_ARCHIVE", str(archive))
    assert request("/source/")[2] == archive.read_bytes()
    assert request("/source/", method="HEAD")[2] == b""
    assert request("/source/", query="file=/secrets")[0].startswith("404")
