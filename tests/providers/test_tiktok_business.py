"""Business API contract and publishing gates; no network or real posts."""

from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
import pytest

from providers.exceptions import APIError, OAuthError, PublishError
from providers.tiktok_business import API_BASE, TikTokBusinessProvider, validate_callback
from providers.types import PostType, PublishContent, PublishState

CALLBACK = "https://studio-connect.example.com/social2/callback/"


@pytest.fixture
def provider():
    return TikTokBusinessProvider(
        {
            "client_id": "test-app",
            "client_secret": "test-secret",
            "business_id": "owner-1",
            "authorization_url": "https://www.tiktok.com/v2/auth/authorize?"
            + urlencode(
                {
                    "client_key": "portal-app-key",
                    "redirect_uri": CALLBACK,
                    "scope": "user.info.basic,video.publish",
                }
            ),
        }
    )


@pytest.mark.parametrize(
    "url",
    [
        "",
        "http://example.com/cb/",
        "https://example.com:443/cb/",
        "https://example.com/cb",
        "https://example.com/cb/?x=1",
        "https://example.com/cb/#x",
        "https://user@example.com/cb/",
    ],
)
def test_invalid_callback_rejected(url):
    with pytest.raises(OAuthError):
        validate_callback(url)


def test_portal_generated_account_url_and_state(provider):
    url = provider.get_auth_url(CALLBACK, "signed-workspace-state")
    params = parse_qs(urlsplit(url).query)
    assert params["client_key"] == ["portal-app-key"]
    assert params["state"] == ["signed-workspace-state"]
    assert params["disable_auto_auth"] == ["1"]
    with pytest.raises(OAuthError):
        provider.get_auth_url("https://other.example.com/cb/", "state")
    provider.credentials["authorization_url"] = "https://business-api.tiktok.com/portal/auth"
    with pytest.raises(OAuthError):
        provider.get_auth_url(CALLBACK, "state")


def test_exchange_business_code_not_creator_protocol(provider):
    with patch.object(provider, "_request") as request:
        request.return_value.json.return_value = {
            "code": 0,
            "data": {
                "access_token": "access",
                "refresh_token": "refresh",
                "open_id": "owner-1",
                "scope": "user.info.basic,video.publish",
                "expires_in": 86400,
            },
        }
        token = provider.exchange_code("single-use-code", CALLBACK)
    assert token.raw_response == {"open_id": "owner-1"}
    assert request.call_args.args == ("POST", API_BASE + "/tt_user/oauth2/token/")
    assert request.call_args.kwargs["json"] == {
        "client_id": "test-app",
        "client_secret": "test-secret",
        "grant_type": "authorization_code",
        "auth_code": "single-use-code",
        "redirect_uri": CALLBACK,
    }


def test_incomplete_scope_rejected(provider):
    with pytest.raises(OAuthError):
        provider._tokens({"access_token": "a", "refresh_token": "r", "open_id": "x", "scope": "user.info.basic"})


def test_profile_uses_basic_fields_and_account_header(provider):
    with patch.object(provider, "_request") as request:
        request.return_value.json.return_value = {"code": 0, "data": {"display_name": "Example"}}
        profile = provider.get_profile("access")
    assert profile.platform_id == "owner-1"
    assert request.call_args.kwargs["headers"] == {"Access-Token": "access"}
    assert request.call_args.kwargs["params"]["fields"] == '["display_name", "profile_image"]'


def content(**extra):
    return PublishContent(
        text="Original caption",
        post_type=PostType.VIDEO,
        video_duration_sec=10,
        media_urls=["https://studio-connect.example.com/media/test/video.mp4"],
        media_types=["video"],
        extra={
            "rights_confirmed": True,
            "client_approval_confirmed": True,
            "is_brand_organic": True,
            "is_branded_content": False,
            **extra,
        },
    )


def test_publish_disabled_makes_no_requests(provider, settings):
    settings.TIKTOK_BUSINESS_PUBLISH_ENABLED = False
    with patch.object(provider, "_request") as request, pytest.raises(PublishError):
        provider.publish_post("access", content())
    request.assert_not_called()


@pytest.mark.parametrize(
    "extra", [{"rights_confirmed": False}, {"client_approval_confirmed": False}, {"is_brand_organic": None}]
)
def test_per_post_checks_before_requests(provider, settings, extra):
    settings.TIKTOK_BUSINESS_PUBLISH_ENABLED = True
    with patch.object(provider, "_request") as request, pytest.raises(PublishError):
        provider.publish_post("access", content(**extra))
    request.assert_not_called()


def test_settings_restrictions_and_correct_publish_payload(provider, settings):
    settings.TIKTOK_BUSINESS_PUBLISH_ENABLED = True
    settings.TIKTOK_BUSINESS_MEDIA_ORIGIN = "https://studio-connect.example.com"
    with patch.object(
        provider,
        "_api",
        side_effect=[
            {
                "privacy_level_options": ["PUBLIC_TO_EVERYONE"],
                "max_video_post_duration_sec": 60,
                "comment_disabled": True,
            },
            {"share_id": "receipt"},
        ],
    ) as request:
        assert provider.publish_post("access", content()).platform_post_id == "receipt"
    payload = request.call_args.kwargs["payload"]
    assert payload["business_id"] == "owner-1"
    assert payload["post_info"]["disable_comment"] is True
    assert payload["post_info"]["is_brand_organic"] is True
    assert payload["post_info"]["upload_to_draft"] is False


def test_ambiguous_submission_is_never_retried(provider, settings):
    settings.TIKTOK_BUSINESS_PUBLISH_ENABLED = True
    settings.TIKTOK_BUSINESS_MEDIA_ORIGIN = "https://studio-connect.example.com"
    with (
        patch.object(
            provider,
            "_api",
            side_effect=[
                {
                    "privacy_level_options": ["PUBLIC_TO_EVERYONE"],
                    "max_video_post_duration_sec": 60,
                },
                APIError("Interrupted", retryable=True),
            ],
        ),
        pytest.raises(PublishError) as error,
    ):
        provider.publish_post("access", content())
    assert error.value.retryable is False


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        ({"status": "PUBLISH_COMPLETE", "post_ids": []}, PublishState.PENDING),
        ({"status": "PUBLISH_COMPLETE", "post_ids": ["actual-video-id"]}, PublishState.COMPLETE),
        ({"status": "PROCESSING_DOWNLOAD"}, PublishState.PENDING),
        ({"status": "SEND_TO_USER_INBOX"}, PublishState.INBOX),
        ({"status": "FAILED"}, PublishState.FAILED),
    ],
)
def test_receipt_not_treated_as_public_post(provider, data, expected):
    with patch.object(provider, "_api", return_value=data):
        result = provider.check_publish_status("access", "receipt")
    assert result.state == expected
    assert result.platform_post_id == ("actual-video-id" if expected == PublishState.COMPLETE else "")


def test_provider_error_never_contains_secrets(provider):
    response = MagicMock()
    response.json.return_value = {"code": 40100, "message": "access_token=SECRET client_secret=SECRET"}
    with patch.object(provider, "_request", return_value=response), pytest.raises(APIError) as error:
        provider._api("POST", "/test/", payload={"client_secret": "SECRET"})
    assert "SECRET" not in str(error.value)
    with patch.object(provider, "_request", side_effect=httpx.ReadTimeout("SECRET")), pytest.raises(APIError) as error:
        provider._api("POST", "/test/")
    assert "SECRET" not in str(error.value)
