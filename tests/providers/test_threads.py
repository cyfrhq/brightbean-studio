"""Tests for Threads provider OAuth token handling.

Threads issues no separate refresh token: the long-lived access token is its own
refresh credential, replayed against ``th_refresh_token``. The provider therefore
has to return it as ``OAuthTokens.refresh_token`` — every refresh path in the app
skips accounts whose ``oauth_refresh_token`` is empty.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from providers.exceptions import APIError
from providers.threads import LONG_LIVED_TOKEN_TTL, ThreadsProvider

CREDENTIALS = {"app_id": "th-id", "app_secret": "th-sec"}


def _response(body: dict) -> MagicMock:
    return MagicMock(json=MagicMock(return_value=body))


class TestExchangeCode:
    @patch.object(ThreadsProvider, "_request")
    def test_returns_long_lived_token_as_its_own_refresh_credential(self, mock_request):
        mock_request.side_effect = [
            _response({"access_token": "short-lived", "user_id": "1"}),
            _response({"access_token": "long-lived", "expires_in": 5184000, "token_type": "bearer"}),
        ]

        tokens = ThreadsProvider(CREDENTIALS).exchange_code("code", "https://example.com/cb/")

        assert tokens.access_token == "long-lived"
        # Without this the connect callback stores oauth_refresh_token="" and the
        # 60-day token expires unrefreshed. It must be the long-lived token, not
        # the short-lived one the first leg returned.
        assert tokens.refresh_token == "long-lived"
        assert tokens.expires_in == 5184000

    @patch.object(ThreadsProvider, "_request")
    def test_missing_expires_in_falls_back_to_the_60_day_ttl(self, mock_request):
        mock_request.side_effect = [
            _response({"access_token": "short-lived", "user_id": "1"}),
            _response({"access_token": "long-lived"}),
        ]

        tokens = ThreadsProvider(CREDENTIALS).exchange_code("code", "https://example.com/cb/")

        # A None here leaves token_expires_at unset, which keeps the account out
        # of the refresh cycle entirely.
        assert tokens.expires_in == LONG_LIVED_TOKEN_TTL


class TestRefreshToken:
    @patch.object(ThreadsProvider, "_request")
    def test_rotated_token_carries_forward_as_the_next_refresh_credential(self, mock_request):
        mock_request.return_value = _response({"access_token": "rotated", "expires_in": 5184000})

        tokens = ThreadsProvider(CREDENTIALS).refresh_token("current-long-lived")

        assert tokens.access_token == "rotated"
        # Otherwise the account refreshes once, then replays the stale original.
        assert tokens.refresh_token == "rotated"
        assert tokens.expires_in == 5184000

    @patch.object(ThreadsProvider, "_request")
    def test_missing_expires_in_falls_back_to_the_60_day_ttl(self, mock_request):
        mock_request.return_value = _response({"access_token": "rotated"})

        tokens = ThreadsProvider(CREDENTIALS).refresh_token("current-long-lived")

        # Consumers only advance token_expires_at when expires_in is truthy, so a
        # None would leave the stale past expiry in place and re-trigger a
        # refresh on every single health check.
        assert tokens.expires_in == LONG_LIVED_TOKEN_TTL

    @patch.object(ThreadsProvider, "_request")
    def test_replays_the_access_token_against_th_refresh_token(self, mock_request):
        mock_request.return_value = _response({"access_token": "rotated", "expires_in": 5184000})

        ThreadsProvider(CREDENTIALS).refresh_token("current-long-lived")

        _, kwargs = mock_request.call_args
        assert kwargs["params"] == {
            "grant_type": "th_refresh_token",
            "access_token": "current-long-lived",
        }


class TestAccountMetrics:
    """Daily profile views and the live follower total, from /me/threads_insights."""

    @staticmethod
    def window(day=22):
        tz = timezone(timedelta(hours=1))
        return datetime(2026, 11, day, 0, 0, tzinfo=tz), datetime(2026, 11, day, 23, 59, 59, tzinfo=tz)

    def run(self, views_body, followers_body=None):
        followers_body = followers_body or {"data": [{"name": "followers_count", "total_value": {"value": 42}}]}
        with patch.object(ThreadsProvider, "_request") as request:
            request.side_effect = [_response(views_body), _response(followers_body)]
            metrics = ThreadsProvider(CREDENTIALS).get_account_metrics("tok", self.window())
        return metrics, request

    def test_picks_the_bucket_that_closes_the_requested_day(self):
        body = {
            "data": [
                {
                    "name": "views",
                    "values": [
                        {"value": 8, "end_time": "2026-11-22T08:00:00+0000"},  # the day before
                        {"value": 134, "end_time": "2026-11-23T08:00:00+0000"},  # the requested day
                    ],
                }
            ]
        }
        metrics, request = self.run(body)
        assert metrics.extra["views"] == 134
        assert metrics.followers == 42
        first = request.call_args_list[0]
        assert first.args[:2] == ("GET", "https://graph.threads.net/v1.0/me/threads_insights")
        assert first.kwargs["params"]["metric"] == "views"
        assert request.call_args_list[1].kwargs["params"] == {"metric": "followers_count"}

    def test_a_day_threads_has_not_closed_yet_has_no_views_rather_than_zero(self):
        body = {"data": [{"name": "views", "values": [{"value": 8, "end_time": "2026-11-22T08:00:00+0000"}]}]}
        metrics, _ = self.run(body)
        assert "views" not in metrics.extra and metrics.followers == 42

    def test_a_failed_follower_fetch_is_unknown_not_zero(self):
        body = {"data": [{"name": "views", "values": [{"value": 5, "end_time": "2026-11-23T08:00:00+0000"}]}]}
        with patch.object(ThreadsProvider, "_request") as request:
            request.side_effect = [_response(body), APIError("Threads API error 400", status_code=400)]
            metrics = ThreadsProvider(CREDENTIALS).get_account_metrics("tok", self.window())
        assert metrics.followers is None and metrics.extra["views"] == 5
        assert "followers_count" in metrics.extra["insight_errors"]
