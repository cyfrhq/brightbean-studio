"""TikTok Business post analytics and Threads account analytics are wired in.

Both are checked with synthetic values; nothing here calls a platform.
"""

from unittest.mock import patch

import pytest

from apps.analytics import services
from apps.analytics.metrics import PLATFORM_COLOR, PLATFORM_METRICS, PLATFORM_PRIMARY
from apps.analytics.tasks import BACKFILL_DAYS_PER_PLATFORM, _account_metrics_to_dict, _post_metrics_to_dict
from apps.social_accounts.models import SocialAccount
from providers.types import AccountMetrics, PostMetrics


def test_tiktok_business_is_a_catalogued_analytics_platform():
    assert PLATFORM_METRICS["tiktok_business"] == ["views", "reach", "likes", "comments", "shares", "engagement"]
    assert PLATFORM_PRIMARY["tiktok_business"] == "views"
    assert "tiktok_business" in PLATFORM_COLOR
    assert BACKFILL_DAYS_PER_PLATFORM["tiktok_business"] > 0
    assert services.analytics_availability("tiktok_business", enabled_platforms=["tiktok_business"]) is None


def test_the_admin_toggle_still_switches_tiktok_business_off():
    verdict = services.analytics_availability("tiktok_business", enabled_platforms=["facebook"])
    assert verdict.cause == services.CAUSE_DISABLED


def test_tiktok_business_counts_land_under_the_catalogue_keys():
    metrics = PostMetrics(video_views=120, reach=90, likes=5, comments=2, shares=1, extra={"favorites": 3})
    assert _post_metrics_to_dict(metrics, "tiktok_business") == {
        "views": 120.0,
        "reach": 90.0,
        "likes": 5.0,
        "comments": 2.0,
        "shares": 1.0,
        # Derived by the collector: (likes + comments + shares) / views, in percent.
        "engagement": 6.67,
    }


def test_the_noun_for_a_tiktok_business_post_is_video():
    assert services.content_noun(SocialAccount(platform="tiktok_business")) == "video"


def test_threads_follower_total_is_recorded():
    assert _account_metrics_to_dict(AccountMetrics(followers=42), "threads") == {"followers": 42.0}
    assert "followers" in PLATFORM_METRICS["threads"]


def test_an_unknown_threads_follower_total_writes_nothing():
    assert _account_metrics_to_dict(AccountMetrics(followers=None), "threads") == {}


@pytest.fixture
def workspace(db):
    from apps.organizations.models import Organization
    from apps.workspaces.models import Workspace

    org = Organization.objects.create(name="TikTok Business Analytics Org")
    return Workspace.objects.create(organization=org, name="TikTok Business Analytics WS")


def test_connecting_a_tiktok_business_account_queues_its_analytics_backfill(workspace):
    with patch("apps.analytics.tasks.backfill_account_analytics") as backfill:
        account = SocialAccount.objects.create(
            workspace=workspace,
            platform="tiktok_business",
            account_platform_id="owner-1",
            account_name="Synthetic",
            oauth_access_token="token",
            connection_status=SocialAccount.ConnectionStatus.CONNECTED,
        )
    backfill.assert_called_once_with(str(account.id), remove_existing_tasks=True)
