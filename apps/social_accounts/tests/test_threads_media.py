"""Threads media checks and gateway links, with synthetic assets only."""

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from cryptography.fernet import Fernet
from django.test import override_settings

from apps.composer.services import publish_blocker
from apps.social_accounts.business_media import gateway_media_url, threads_media_problem, threads_media_url
from providers.exceptions import PublishError
from studio_gateway.tokens import read_media_token


def asset(name="media_library/a.jpg", media_type="image", size=1000, duration=None, status="completed"):
    return SimpleNamespace(
        file=SimpleNamespace(name=name),
        media_type=media_type,
        file_size=size,
        duration=duration,
        processing_status=status,
    )


@pytest.fixture(autouse=True)
def thirty_fps():
    with patch("apps.social_accounts.business_media.video_frame_rate", return_value=30.0):
        yield


@pytest.mark.parametrize(
    "item",
    [
        asset(),
        asset("media_library/a.png"),
        asset("media_library/v.mp4", "video", duration=30),
        asset("media_library/v.mov", "video", duration=300),
    ],
)
def test_threads_accepts_jpeg_png_and_short_videos(item):
    assert threads_media_problem(item) == ""


@pytest.mark.parametrize(
    ("item", "expected"),
    [
        (asset("media_library/a.webp"), "JPEG or PNG images, not WEBP"),
        (asset("media_library/a.gif"), "not GIF"),
        (asset(size=9 * 1024**2), "larger than 8 MB"),
        (asset("media_library/v.mp4", "video", duration=301), "301 s long"),
        (asset("media_library/v.mp4", "video", duration=0), "Couldn't read this video's length"),
        (asset("media_library/v.mp4", "video", duration=30, status="processing"), "still being processed"),
        (asset("media_library/v.webm", "video", duration=30), "MP4 or MOV"),
        (asset("media_library/d.pdf", "document"), "images and videos only"),
    ],
)
def test_threads_refusals_say_why(item, expected):
    assert expected in threads_media_problem(item)


def test_threads_frame_rate_is_checked():
    with patch("apps.social_accounts.business_media.video_frame_rate", return_value=120.0):
        assert "120 FPS" in threads_media_problem(asset("media_library/v.mp4", "video", duration=30))


def test_publish_blocker_checks_every_threads_item():
    account = SimpleNamespace(platform="threads", get_platform_display=lambda: "Threads")
    good, bad = asset(), asset("media_library/b.webp")
    assert publish_blocker(account, media_types={"image"}, media_assets=[good]) == ""
    assert "WEBP" in publish_blocker(account, media_types={"image"}, media_assets=[good, bad])
    # Text-only Threads posts and callers that don't pass assets are unaffected.
    assert publish_blocker(account, media_types=set(), media_assets=[]) == ""
    assert publish_blocker(account, media_types={"image"}) == ""


@pytest.fixture
def gateway(tmp_path):
    key = Fernet.generate_key()
    key_file = tmp_path / "key"
    key_file.write_bytes(key)
    with override_settings(
        TIKTOK_BUSINESS_MEDIA_KEY_FILE=str(key_file), TIKTOK_BUSINESS_MEDIA_ORIGIN="https://gateway.example/"
    ):
        yield key


@pytest.mark.parametrize(
    ("name", "suffix"),
    [
        ("media_library/a.jpeg", "/image.jpg"),
        ("media_library/a.png", "/image.png"),
        ("media_library/v.mov", "/video.mp4"),
    ],
)
def test_gateway_links_hide_the_path_and_name_the_kind(gateway, name, suffix):
    url = gateway_media_url(asset(name))
    assert url.startswith("https://gateway.example/media/") and url.endswith(suffix)
    assert "media_library" not in url
    token = url.split("/")[-2]
    assert str(read_media_token(gateway, token)) == name


def test_threads_media_url_refuses_before_minting(gateway):
    with pytest.raises(PublishError, match="WEBP"):
        threads_media_url(asset("media_library/a.webp"))
    assert threads_media_url(asset()).endswith("/image.jpg")


def test_gateway_misconfiguration_is_reported(tmp_path):
    missing = override_settings(
        TIKTOK_BUSINESS_MEDIA_KEY_FILE=str(tmp_path / "missing"), TIKTOK_BUSINESS_MEDIA_ORIGIN="x"
    )
    with missing, pytest.raises(PublishError, match="gateway configuration"):
        gateway_media_url(asset())
