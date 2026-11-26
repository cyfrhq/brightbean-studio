"""Tests for Threads provider OAuth token handling.

Threads issues no separate refresh token: the long-lived access token is its own
refresh credential, replayed against ``th_refresh_token``. The provider therefore
has to return it as ``OAuthTokens.refresh_token`` — every refresh path in the app
skips accounts whose ``oauth_refresh_token`` is empty.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

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
    """The follower total from /me/threads_insights; daily views are not recorded."""

    @staticmethod
    def window():
        tz = timezone(timedelta(hours=1))
        return datetime(2026, 11, 22, 0, 0, tzinfo=tz), datetime(2026, 11, 22, 23, 59, 59, tzinfo=tz)

    def run(self, body):
        with patch.object(ThreadsProvider, "_request", return_value=_response(body)) as request:
            metrics = ThreadsProvider(CREDENTIALS).get_account_metrics("tok", self.window())
        return metrics, request

    def test_reads_the_live_follower_total_and_asks_for_nothing_else(self):
        metrics, request = self.run({"data": [{"name": "followers_count", "total_value": {"value": 42}}]})
        assert metrics.followers == 42
        assert request.call_count == 1
        assert request.call_args.args[:2] == ("GET", "https://graph.threads.net/v1.0/me/threads_insights")
        # Daily views are provisional (see the docstring), so they are never requested.
        assert request.call_args.kwargs["params"] == {"metric": "followers_count"}
        assert "views" not in metrics.extra

    def test_a_response_without_the_metric_is_unknown_not_zero(self):
        metrics, _ = self.run({"data": []})
        assert metrics.followers is None
