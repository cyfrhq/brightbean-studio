"""TikTok API for Business Accounts API, separate from the creator API.

Official references: accounts-api-authentication, accounts-api-authorization,
publish-a-public-video-post-to-an-owned-account, and
get-the-publishing-status-of-a-tiktok-post at business-api.tiktok.com/portal/docs.
"""

from __future__ import annotations

import json
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx

from .base import SocialProvider
from .exceptions import APIError, OAuthError, PublishError, RateLimitError
from .types import (
    AccountProfile,
    AuthType,
    MediaType,
    OAuthTokens,
    PostMetrics,
    PostType,
    PublishContent,
    PublishResult,
    PublishState,
    PublishStatus,
    RateLimitConfig,
)

API_BASE = "https://business-api.tiktok.com/open_api/v1.3"
SCOPES = ("user.info.basic", "video.publish")
# Reading a post's counts needs one more scope than publishing does. It is NOT
# in SCOPES: a connection without it still works for publishing, and the
# portal's account-holder URL decides whether it is requested.
ANALYTICS_SCOPE = "video.list"
# Every field below is gated by ``video.list`` alone; ``video.insights`` (watch
# time, retention, audience) is deliberately not requested.
METRIC_FIELDS = ("item_id", "likes", "comments", "shares", "favorites", "reach", "video_views")
METRICS_PAGE_SIZE = 20  # the endpoint's maximum


def validate_callback(url: str) -> str:
    """The account-holder callback is HTTPS, static, port-free and slash-ended."""
    parts = urlsplit(url)
    try:
        port = parts.port
    except ValueError:
        port = -1
    if (
        parts.scheme != "https"
        or not parts.hostname
        or port is not None
        or parts.username
        or parts.password
        or parts.query
        or parts.fragment
        or not parts.path.endswith("/")
        or not 10 <= len(url) <= 512
    ):
        raise OAuthError("Configure a valid HTTPS Business API callback without a port.", retryable=False)
    return url


class TikTokBusinessProvider(SocialProvider):
    publish_is_async = True
    needs_local_media = False
    post_metrics_batch_size = METRICS_PAGE_SIZE
    # Counts are lifetime totals, so past days can't be reconstructed.
    account_metrics_supports_date_range = False

    @property
    def platform_name(self):
        return "TikTok (Business API)"

    @property
    def auth_type(self):
        return AuthType.OAUTH2

    @property
    def max_caption_length(self):
        return 2200

    @property
    def supported_post_types(self):
        return [PostType.VIDEO]

    @property
    def supported_media_types(self):
        return [MediaType.MP4, MediaType.MOV]

    @property
    def required_scopes(self):
        return list(SCOPES)

    @property
    def rate_limits(self):
        return RateLimitConfig(publish_per_day=15)

    def _api(self, method, path, *, token=None, params=None, payload=None):
        # Never include provider response bodies, credentials or codes in errors.
        try:
            response = self._request(
                method,
                f"{API_BASE}{path}",
                headers={"Access-Token": token} if token else {},
                params=params,
                json=payload,
            )
        except (APIError, RateLimitError) as exc:
            status = getattr(exc, "status_code", None)
            if isinstance(exc, RateLimitError):
                raise RateLimitError("TikTok Business API rate limit reached.", platform=self.platform_name) from None
            raise APIError(
                "TikTok Business API request failed.",
                status_code=status,
                platform=self.platform_name,
                retryable=status is None or status >= 500,
            ) from None
        except httpx.HTTPError:
            raise APIError("TikTok Business API connection interrupted.", platform=self.platform_name) from None
        try:
            body = response.json()
        except (ValueError, TypeError):
            raise APIError("TikTok Business API returned an invalid response.", platform=self.platform_name) from None
        if not isinstance(body, dict) or body.get("code") != 0 or not isinstance(body.get("data"), dict):
            code = body.get("code") if isinstance(body, dict) else None
            safe_code = str(code) if isinstance(code, int) else "invalid"
            raise APIError(
                f"TikTok Business API rejected the request (code {safe_code}). Check approval and permissions.",
                platform=self.platform_name,
                retryable=False,
            )
        return body["data"]

    def get_auth_url(self, redirect_uri, state, code_verifier=None):
        validate_callback(redirect_uri)
        # Use the portal-generated *account holder* URL. An advertiser URL
        # or a URL assembled from creator-app assumptions is not equivalent.
        parts = urlsplit(self.credentials.get("authorization_url", ""))
        if (
            parts.scheme != "https"
            or parts.netloc != "www.tiktok.com"
            or parts.path.rstrip("/") != "/v2/auth/authorize"
        ):
            raise OAuthError("Configure the portal's TikTok account holder authorization URL.", retryable=False)
        params = dict(parse_qsl(parts.query))
        if params.get("redirect_uri") != redirect_uri:
            raise OAuthError("The Business API authorization and callback URLs do not match.", retryable=False)
        params.update(state=state, disable_auto_auth="1")
        return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(params), ""))

    def _tokens(self, data):
        if not data.get("access_token") or not data.get("refresh_token") or not data.get("open_id"):
            raise OAuthError("TikTok Business API returned an incomplete account grant.", retryable=False)
        scope = data.get("scope", "")
        if not set(SCOPES).issubset(set(scope.split(","))):
            raise OAuthError("TikTok did not grant basic account information and video publishing.", retryable=False)
        return OAuthTokens(
            access_token=data["access_token"],
            refresh_token=data["refresh_token"],
            expires_in=int(data.get("expires_in", 86400)),
            scope=scope,
            raw_response={"open_id": data["open_id"]},
        )

    def exchange_code(self, code, redirect_uri, code_verifier=None):
        data = self._api(
            "POST",
            "/tt_user/oauth2/token/",
            payload={
                "client_id": self.credentials["client_id"],
                "client_secret": self.credentials["client_secret"],
                "grant_type": "authorization_code",
                "auth_code": code,
                "redirect_uri": validate_callback(redirect_uri),
            },
        )
        return self._tokens(data)

    def refresh_token(self, refresh_token):
        return self._tokens(
            self._api(
                "POST",
                "/tt_user/oauth2/refresh_token/",
                payload={
                    "client_id": self.credentials["client_id"],
                    "client_secret": self.credentials["client_secret"],
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                },
            )
        )

    def _token_info(self, access_token):
        return self._api(
            "POST",
            "/tt_user/token_info/get/",
            payload={
                "app_id": self.credentials["client_id"],
                "access_token": access_token,
            },
        )

    def _account_id(self, access_token):
        account_id = self.credentials.get("business_id") or self._token_info(access_token).get("creator_id")
        if not account_id:
            raise OAuthError("TikTok Business account identity was not returned.", retryable=False)
        return str(account_id)

    def get_granted_scopes(self, access_token):
        return set(self._token_info(access_token).get("scope", "").split(","))

    def get_profile(self, access_token):
        account_id = self._account_id(access_token)
        data = self._api(
            "GET",
            "/business/get/",
            token=access_token,
            params={
                "business_id": account_id,
                "fields": json.dumps(["display_name", "profile_image"]),
            },
        )
        return AccountProfile(
            platform_id=account_id,
            name=data.get("display_name") or "TikTok account",
            avatar_url=data.get("profile_image") or None,
        )

    def revoke_token(self, access_token):
        self._api(
            "POST",
            "/tt_user/oauth2/revoke/",
            payload={
                "client_id": self.credentials["client_id"],
                "client_secret": self.credentials["client_secret"],
                "access_token": access_token,
            },
        )
        return True

    def publish_post(self, access_token, content: PublishContent):
        from django.conf import settings

        if not getattr(settings, "TIKTOK_BUSINESS_PUBLISH_ENABLED", False):
            raise PublishError("TikTok Business publishing is awaiting production activation.", retryable=False)
        # Scope approval is not permission to publish arbitrary media. The
        # operator explicitly confirms these fields for each platform post.
        extra = content.extra
        for key in ("rights_confirmed", "client_approval_confirmed"):
            if extra.get(key) is not True:
                raise PublishError("Confirm media rights and client approval before posting.", retryable=False)
        if not isinstance(extra.get("is_brand_organic"), bool) or not isinstance(extra.get("is_branded_content"), bool):
            raise PublishError("Choose the promotional-content disclosures for this video.", retryable=False)
        if len(content.media_urls) != 1 or not content.is_video():
            raise PublishError("TikTok Business requires one original video.", retryable=False)
        if len(content.text.encode("utf-16-le")) // 2 > 2200:
            raise PublishError("TikTok captions must be at most 2,200 UTF-16 characters.", retryable=False)
        video_url = content.media_urls[0]
        allowed_origin = getattr(settings, "TIKTOK_BUSINESS_MEDIA_ORIGIN", "")
        if not allowed_origin or not video_url.startswith(allowed_origin.rstrip("/") + "/media/"):
            raise PublishError("The video must use Studio's verified, temporary media URL.", retryable=False)
        account_id = self._account_id(access_token)
        prefs = self._api("GET", "/business/video/settings/", token=access_token, params={"business_id": account_id})
        if "PUBLIC_TO_EVERYONE" not in prefs.get("privacy_level_options", []):
            raise PublishError("This TikTok account does not permit public video posting.", retryable=False)
        max_duration = min(float(prefs.get("max_video_post_duration_sec", 600)), 600)
        if content.video_duration_sec is None or not 3 <= content.video_duration_sec <= max_duration:
            raise PublishError("Check the video duration against this TikTok account's limit.", retryable=False)
        info = {
            "caption": content.text,
            "is_brand_organic": extra["is_brand_organic"],
            "is_branded_content": extra["is_branded_content"],
            "is_ai_generated": extra.get("is_ai_generated") is True,
            "upload_to_draft": False,
            "is_ads_only": False,
        }
        for setting in ("comment", "duet", "stitch"):
            info[f"disable_{setting}"] = (
                bool(prefs.get(f"{setting}_disabled")) or extra.get(f"disable_{setting}") is True
            )
        try:
            data = self._api(
                "POST",
                "/business/video/publish/",
                token=access_token,
                payload={
                    "business_id": account_id,
                    "video_url": video_url,
                    "post_info": info,
                },
            )
        except APIError as exc:
            # A timeout may have happened after TikTok accepted the video.
            # Never automatically repeat this non-idempotent submission.
            raise PublishError(str(exc), platform=self.platform_name, retryable=False) from None
        handle = data.get("share_id")
        if not handle:
            # Outcome may be ambiguous; the engine must not blindly resubmit.
            raise PublishError(
                "TikTok did not return a publishing receipt. Check before resubmitting.", retryable=False
            )
        return PublishResult(platform_post_id=handle)

    def check_publish_status(self, access_token, handle):
        data = self._api(
            "GET",
            "/business/publish/status/",
            token=access_token,
            params={
                "business_id": self._account_id(access_token),
                "publish_id": handle,
            },
        )
        status = data.get("status")
        if status == "PUBLISH_COMPLETE" and data.get("post_ids"):
            return PublishStatus(PublishState.COMPLETE, platform_post_id=str(data["post_ids"][0]))
        if status == "FAILED":
            return PublishStatus(
                PublishState.FAILED, error="TikTok could not publish this video. Review its processing result."
            )
        if status == "SEND_TO_USER_INBOX":
            return PublishStatus(
                PublishState.INBOX, error="TikTok sent this video to the account's inbox; it is not public."
            )
        # Post ids may lag PUBLISH_COMPLETE by up to three minutes. Unknown
        # statuses likewise cannot be promoted to a public success.
        return PublishStatus(PublishState.PENDING)

    def get_post_metrics_batch(self, access_token, post_ids):
        """Lifetime counts for up to 20 published posts in one request.

        Needs the ``video.list`` scope. Ids TikTok doesn't return (deleted,
        private, or a publish handle that never became a post) are absent from
        the result rather than zero, so history is never flattened.
        """
        ids = [str(i) for i in post_ids if str(i).isdigit()][:METRICS_PAGE_SIZE]
        if not ids:
            return {}
        data = self._api(
            "GET",
            "/business/video/list/",
            token=access_token,
            params={
                "business_id": self._account_id(access_token),
                "fields": json.dumps(list(METRIC_FIELDS)),
                "filters": json.dumps({"video_ids": ids}),
                "max_count": METRICS_PAGE_SIZE,
            },
        )
        out = {}
        for video in data.get("videos") or []:
            item_id = str(video.get("item_id") or "")
            if item_id not in ids:
                continue
            out[item_id] = PostMetrics(
                reach=_count(video.get("reach")),
                likes=_count(video.get("likes")),
                comments=_count(video.get("comments")),
                shares=_count(video.get("shares")),
                video_views=_count(video.get("video_views")),
                extra={"favorites": _count(video.get("favorites"))},
            )
        return out

    def get_post_metrics(self, access_token, post_id):
        return self.get_post_metrics_batch(access_token, [post_id]).get(str(post_id), PostMetrics())


def _count(value):
    return int(value) if isinstance(value, (int, float)) and value >= 0 else 0
