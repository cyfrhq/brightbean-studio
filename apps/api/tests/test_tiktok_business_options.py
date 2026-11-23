"""TikTok Business per-post confirmations through the Agent API.

A TikTok Business post may only publish with explicit media-rights and
owner/client approval confirmations and answered commercial disclosures.
The composer collects them; these tests hold the API to the same rule and
make sure it refuses at scheduling time, not at publish time. Synthetic
data only; nothing here calls TikTok.
"""

from __future__ import annotations

import json
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.test import Client
from django.utils import timezone

from apps.api_keys import services
from apps.composer.models import PlatformPost, Post
from apps.composer.services import publish_blocker
from apps.media_library.models import MediaAsset
from apps.members.models import PERMISSION_KEYS, OrgMembership, WorkspaceMembership
from apps.social_accounts.models import SocialAccount

MCP_URL = "/api/v1/mcp"
FULL_OPTIONS = {
    "media_rights_confirmed": True,
    "owner_approved": True,
    "promotional_content": True,
    "paid_partnership": False,
    "ai_generated_label": True,
}


class _SecureClient(Client):
    def generic(self, method, path, *args, **kwargs):
        kwargs["secure"] = True
        return super().generic(method, path, *args, **kwargs)


@pytest.fixture
def user(db):
    from apps.accounts.models import User

    return User.objects.create_user(
        email="ttb@example.com", password="testpass123", name="TTB", tos_accepted_at=timezone.now()
    )


@pytest.fixture
def workspace(db, user):
    from apps.organizations.models import Organization
    from apps.workspaces.models import Workspace

    org = Organization.objects.create(name="TTB Org")
    OrgMembership.objects.create(user=user, organization=org, org_role=OrgMembership.OrgRole.OWNER)
    workspace = Workspace.objects.create(organization=org, name="TTB WS")
    WorkspaceMembership.objects.create(
        user=user, workspace=workspace, workspace_role=WorkspaceMembership.WorkspaceRole.OWNER
    )
    return workspace


def _account(workspace, platform):
    return SocialAccount.objects.create(
        workspace=workspace,
        platform=platform,
        account_platform_id=f"{platform}-1",
        account_name=platform,
        connection_status="connected",
    )


@pytest.fixture
def ttb(workspace):
    return _account(workspace, "tiktok_business")


@pytest.fixture
def linkedin(workspace):
    return _account(workspace, "linkedin_personal")


@pytest.fixture
def client(user, workspace, ttb, linkedin):
    key = services.issue_api_key(
        workspace=workspace,
        social_accounts=[ttb, linkedin],
        issued_by=user,
        name="ttb-options",
        permissions=list(PERMISSION_KEYS),
    )
    return _SecureClient(HTTP_AUTHORIZATION=f"Bearer {key.plaintext_token}")


@pytest.fixture
def video(workspace):
    return MediaAsset.objects.create(
        organization=workspace.organization,
        workspace=workspace,
        file="test/video.mp4",
        filename="video.mp4",
        media_type=MediaAsset.MediaType.VIDEO,
        mime_type="video/mp4",
        width=1080,
        height=1920,
        duration=30,
        file_size=1000,
    )


@pytest.fixture(autouse=True)
def _frame_rate():
    """Synthetic files have no real frames; the spec check reads 30 FPS."""
    with patch("apps.social_accounts.business_media.video_frame_rate", return_value=30.0):
        yield


def _when():
    return (timezone.now() + timedelta(hours=2)).isoformat()


def _create(client, account, *, action="schedule", media=(), options=None):
    body = {
        "social_account_id": str(account.id),
        "caption": "synthetic caption",
        "action": action,
        "media_asset_ids": [str(asset.id) for asset in media],
    }
    if action == "schedule":
        body["scheduled_at"] = _when()
    if options is not None:
        body["tiktok_business"] = options
    return client.post("/api/v1/posts/", data=json.dumps(body), content_type="application/json")


def _patch(client, post_id, body):
    return client.patch(f"/api/v1/posts/{post_id}", data=json.dumps(body), content_type="application/json")


@pytest.mark.django_db
class TestCreate:
    def test_scheduling_without_options_is_refused_with_field_names(self, client, ttb, video):
        response = _create(client, ttb, media=[video])
        assert response.status_code == 422
        detail = response.json()["detail"]
        for field in ("media_rights_confirmed", "owner_approved", "promotional_content", "paid_partnership"):
            assert f"tiktok_business.{field}" in detail
        assert not Post.objects.exists()

    def test_unconfirmed_rights_are_refused(self, client, ttb, video):
        response = _create(client, ttb, media=[video], options={**FULL_OPTIONS, "media_rights_confirmed": False})
        assert response.status_code == 422
        assert "tiktok_business.media_rights_confirmed" in response.json()["detail"]
        assert "owner_approved" not in response.json()["detail"].split("Missing:")[1]

    def test_unanswered_disclosure_is_refused(self, client, ttb, video):
        options = {k: v for k, v in FULL_OPTIONS.items() if k != "paid_partnership"}
        response = _create(client, ttb, media=[video], options=options)
        assert response.status_code == 422
        assert "tiktok_business.paid_partnership" in response.json()["detail"]

    def test_full_options_schedule_and_are_stored(self, client, ttb, video):
        response = _create(client, ttb, media=[video], options=FULL_OPTIONS)
        assert response.status_code == 201, response.content
        pp = PlatformPost.objects.get(post_id=response.json()["id"])
        assert pp.status == "scheduled"
        assert pp.platform_extra == {
            "rights_confirmed": True,
            "client_approval_confirmed": True,
            "is_brand_organic": True,
            "is_branded_content": False,
            "is_ai_generated": True,
            "disable_comment": False,
            "disable_duet": False,
            "disable_stitch": False,
        }
        assert publish_blocker(ttb, media_types={"video"}, platform_extra=pp.platform_extra) == ""

    def test_draft_without_options_is_accepted(self, client, ttb, video):
        response = _create(client, ttb, action="draft", media=[video])
        assert response.status_code == 201

    def test_options_for_another_platform_are_refused(self, client, linkedin):
        response = _create(client, linkedin, options=FULL_OPTIONS)
        assert response.status_code == 422
        assert "tiktok_business" in response.json()["detail"]


@pytest.mark.django_db
class TestUpdateAndSchedule:
    def test_schedule_route_refuses_draft_without_options(self, client, ttb, video):
        post_id = _create(client, ttb, action="draft", media=[video]).json()["id"]
        response = client.post(
            f"/api/v1/posts/{post_id}/schedule",
            data=json.dumps({"scheduled_at": _when()}),
            content_type="application/json",
        )
        assert response.status_code == 422
        assert "tiktok_business.owner_approved" in response.json()["detail"]
        assert PlatformPost.objects.get(post_id=post_id).status == "draft"

    def test_patch_options_then_schedule(self, client, ttb, video):
        post_id = _create(client, ttb, action="draft", media=[video]).json()["id"]
        assert _patch(client, post_id, {"tiktok_business": FULL_OPTIONS}).status_code == 200
        response = client.post(
            f"/api/v1/posts/{post_id}/schedule",
            data=json.dumps({"scheduled_at": _when()}),
            content_type="application/json",
        )
        assert response.status_code == 200, response.content
        assert PlatformPost.objects.get(post_id=post_id).status == "scheduled"

    def test_patch_replaces_rather_than_merges(self, client, ttb, video):
        post_id = _create(client, ttb, action="draft", media=[video], options=FULL_OPTIONS).json()["id"]
        assert _patch(client, post_id, {"tiktok_business": {"disable_duet": True}}).status_code == 200
        extra = PlatformPost.objects.get(post_id=post_id).platform_extra
        assert "rights_confirmed" not in extra
        assert "is_brand_organic" not in extra

    def test_patch_cannot_strip_a_scheduled_posts_confirmation(self, client, ttb, video):
        post_id = _create(client, ttb, media=[video], options=FULL_OPTIONS).json()["id"]
        response = _patch(client, post_id, {"tiktok_business": {**FULL_OPTIONS, "owner_approved": False}})
        assert response.status_code == 422
        assert PlatformPost.objects.get(post_id=post_id).platform_extra["client_approval_confirmed"] is True

    def test_patch_options_on_non_tiktok_post_is_refused(self, client, linkedin):
        post_id = _create(client, linkedin, action="draft").json()["id"]
        assert _patch(client, post_id, {"tiktok_business": FULL_OPTIONS}).status_code == 422


@pytest.mark.django_db
class TestCapabilitiesAndBackstops:
    def test_accounts_report_required_options(self, client, ttb, linkedin):
        accounts = {a["id"]: a for a in client.get("/api/v1/accounts/").json()["accounts"]}
        assert accounts[str(ttb.id)]["required_options"] == [
            "tiktok_business.media_rights_confirmed",
            "tiktok_business.owner_approved",
            "tiktok_business.promotional_content",
            "tiktok_business.paid_partnership",
        ]
        assert accounts[str(linkedin.id)]["required_options"] == []

    def test_mcp_schedule_post_is_refused_without_confirmation(self, client, ttb, video):
        rpc = {
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {
                "name": "schedule_post",
                "arguments": {
                    "social_account_id": str(ttb.id),
                    "caption": "synthetic caption",
                    "media_asset_ids": [str(video.id)],
                    "scheduled_at": _when(),
                },
            },
            "id": 1,
        }
        result = client.post(MCP_URL, data=json.dumps(rpc), content_type="application/json").json()
        assert "Confirm media rights" in json.dumps(result)
        assert not PlatformPost.objects.filter(status="scheduled").exists()

    def test_publisher_blocker_starts_with_provider_wording(self, ttb):
        assert publish_blocker(ttb, media_types={"video"}).startswith(
            "Confirm media rights and client approval before posting."
        )
        consented = {"rights_confirmed": True, "client_approval_confirmed": True}
        assert publish_blocker(ttb, media_types={"video"}, platform_extra=consented).startswith(
            "Choose the promotional-content disclosures for this video."
        )


# ---------------------------------------------------------------------------
# Account posting defaults: answered once per account, used by every path.
# ---------------------------------------------------------------------------

OWN_ACCOUNT_DEFAULTS = {
    "rights_confirmed": True,
    "client_approval_confirmed": True,
    "is_brand_organic": False,
    "is_branded_content": False,
    "is_ai_generated": False,
    "set_by": "TTB",
    "set_at": "2026-11-23T00:00:00+00:00",
}


@pytest.fixture
def ttb_with_defaults(ttb):
    ttb.posting_defaults = dict(OWN_ACCOUNT_DEFAULTS)
    ttb.save(update_fields=["posting_defaults"])
    return ttb


@pytest.fixture
def browser(user, workspace):
    session = _SecureClient()
    session.force_login(user)
    return session


def _failed_post(workspace, user, account, video, platform_extra=None):
    post = Post.objects.create(workspace=workspace, author=user, caption="synthetic caption")
    post.media_attachments.create(media_asset=video, position=0)
    return PlatformPost.objects.create(
        post=post,
        social_account=account,
        status="failed",
        publish_error="Confirm media rights and client approval before posting.",
        platform_extra=platform_extra or {},
    )


@pytest.mark.django_db
class TestAccountDefaults:
    def test_defaults_fill_only_what_the_post_left_unset(self, ttb_with_defaults):
        from apps.composer.services import effective_platform_extra

        extra = effective_platform_extra(ttb_with_defaults, {"is_brand_organic": True})
        assert extra["rights_confirmed"] is True
        assert extra["is_brand_organic"] is True  # the post's own answer wins
        assert extra["account_defaults_applied"]["set_by"] == "TTB"
        assert "is_brand_organic" not in extra["account_defaults_applied"]["keys"]

    def test_explicit_unticked_consent_on_the_post_still_blocks(self, ttb_with_defaults):
        assert publish_blocker(
            ttb_with_defaults, media_types={"video"}, platform_extra={"rights_confirmed": False}
        ).startswith("Confirm media rights")

    def test_api_schedules_with_no_options_when_defaults_are_set(self, client, ttb_with_defaults, video):
        response = _create(client, ttb_with_defaults, media=[video])
        assert response.status_code == 201, response.content
        assert PlatformPost.objects.get(post_id=response.json()["id"]).status == "scheduled"

    def test_accounts_report_defaults_and_nothing_left_required(self, client, ttb_with_defaults):
        accounts = {a["id"]: a for a in client.get("/api/v1/accounts/").json()["accounts"]}
        summary = accounts[str(ttb_with_defaults.id)]
        assert summary["required_options"] == []
        assert summary["option_defaults"]["tiktok_business.owner_approved"] is True
        assert summary["option_defaults"]["tiktok_business.paid_partnership"] is False

    def test_mcp_schedule_works_with_defaults(self, client, ttb_with_defaults, video):
        rpc = {
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {
                "name": "schedule_post",
                "arguments": {
                    "social_account_id": str(ttb_with_defaults.id),
                    "caption": "synthetic caption",
                    "media_asset_ids": [str(video.id)],
                    "scheduled_at": _when(),
                },
            },
            "id": 1,
        }
        client.post(MCP_URL, data=json.dumps(rpc), content_type="application/json")
        assert PlatformPost.objects.filter(status="scheduled", social_account=ttb_with_defaults).exists()

    def test_owner_saves_and_clears_defaults(self, browser, workspace, ttb):
        from django.urls import reverse

        url = reverse("social_accounts:posting_defaults", kwargs={"workspace_id": workspace.id, "account_id": ttb.id})
        response = browser.post(
            url,
            {
                "rights_confirmed": "true",
                "client_approval_confirmed": "true",
                "is_brand_organic": "false",
                "is_branded_content": "false",
            },
        )
        assert response.status_code == 302
        ttb.refresh_from_db()
        assert ttb.posting_defaults["rights_confirmed"] is True
        assert ttb.posting_defaults["is_branded_content"] is False
        assert ttb.posting_defaults["set_by"] == "TTB"

        browser.post(url, {"action": "clear"})
        ttb.refresh_from_db()
        assert ttb.posting_defaults == {}

    def test_unticked_consent_is_not_stored_as_a_default(self, browser, workspace, ttb):
        from django.urls import reverse

        url = reverse("social_accounts:posting_defaults", kwargs={"workspace_id": workspace.id, "account_id": ttb.id})
        browser.post(url, {"rights_confirmed": "true", "is_brand_organic": "false", "is_branded_content": "false"})
        ttb.refresh_from_db()
        assert "client_approval_confirmed" not in ttb.posting_defaults
        assert publish_blocker(ttb, media_types={"video"}).startswith("Confirm media rights")

    def test_defaults_only_for_tiktok_business(self, browser, workspace, linkedin):
        from django.urls import reverse

        url = reverse(
            "social_accounts:posting_defaults", kwargs={"workspace_id": workspace.id, "account_id": linkedin.id}
        )
        assert browser.post(url, {"rights_confirmed": "true"}).status_code == 403


@pytest.mark.django_db
class TestVideoCheckedAtScheduling:
    def test_too_short_video_is_refused_when_scheduling(self, client, ttb_with_defaults, video):
        MediaAsset.objects.filter(id=video.id).update(duration=2)
        response = _create(client, ttb_with_defaults, media=[video])
        assert response.status_code == 422
        assert "2 s long" in json.dumps(response.json())
        assert not PlatformPost.objects.filter(status="scheduled").exists()

    def test_wrong_frame_rate_is_refused_when_scheduling(self, client, ttb_with_defaults, video):
        with patch("apps.social_accounts.business_media.video_frame_rate", return_value=15.0):
            response = _create(client, ttb_with_defaults, media=[video])
        assert response.status_code == 422
        assert "15 FPS" in json.dumps(response.json())

    def test_video_still_processing_is_refused(self, ttb_with_defaults, video):
        video.processing_status = MediaAsset.ProcessingStatus.PROCESSING
        assert "still being processed" in publish_blocker(
            ttb_with_defaults, media_types={"video"}, media_assets=[video]
        )


@pytest.mark.django_db
class TestRetry:
    def test_api_retry_reschedules_a_failed_post_using_defaults(
        self, client, workspace, user, ttb_with_defaults, video
    ):
        pp = _failed_post(workspace, user, ttb_with_defaults, video)
        response = client.post(f"/api/v1/posts/{pp.post_id}/retry")
        assert response.status_code == 200, response.content
        pp.refresh_from_db()
        assert pp.status == "scheduled"
        assert pp.publish_error == ""
        assert pp.scheduled_at <= timezone.now()

    def test_api_retry_says_why_it_cannot_and_changes_nothing(self, client, workspace, user, ttb, video):
        pp = _failed_post(workspace, user, ttb, video)
        response = client.post(f"/api/v1/posts/{pp.post_id}/retry")
        assert response.status_code == 422
        assert "Confirm media rights" in response.json()["detail"]
        pp.refresh_from_db()
        assert pp.status == "failed"

    def test_api_retry_with_nothing_failed_is_409(self, client, ttb, video):
        post_id = _create(client, ttb, action="draft", media=[video]).json()["id"]
        assert client.post(f"/api/v1/posts/{post_id}/retry").status_code == 409

    def test_composer_retry_now(self, browser, workspace, user, ttb_with_defaults, video):
        from django.urls import reverse

        pp = _failed_post(workspace, user, ttb_with_defaults, video)
        url = reverse("composer:retry_failed", kwargs={"workspace_id": workspace.id, "post_id": pp.post_id})
        assert browser.post(url).status_code == 302
        pp.refresh_from_db()
        assert pp.status == "scheduled"

    def test_composer_retry_now_reports_the_blocker(self, browser, workspace, user, ttb, video):
        from django.urls import reverse

        pp = _failed_post(workspace, user, ttb, video)
        url = reverse("composer:retry_failed", kwargs={"workspace_id": workspace.id, "post_id": pp.post_id})
        response = browser.post(url)
        assert response.status_code == 400
        assert "Confirm media rights" in response.json()["errors"]["retry"]
        pp.refresh_from_db()
        assert pp.status == "failed"


@pytest.mark.django_db
class TestComposerScheduling:
    def _schedule(self, browser, workspace, post, account, **fields):
        from django.urls import reverse

        url = reverse("composer:save_post_edit", kwargs={"workspace_id": workspace.id, "post_id": post.id})
        data = {
            "action": "schedule",
            "caption": "synthetic caption",
            "tags": "",
            "selected_accounts": str(account.id),
            "scheduled_date": "2027-09-01",
            "scheduled_time": "09:00",
            **fields,
        }
        return browser.post(url, data)

    def _draft(self, workspace, user, video):
        post = Post.objects.create(workspace=workspace, author=user, caption="synthetic caption")
        post.media_attachments.create(media_asset=video, position=0)
        return post

    def test_composer_refuses_to_schedule_without_answers(self, browser, workspace, user, ttb, video):
        post = self._draft(workspace, user, video)
        response = self._schedule(browser, workspace, post, ttb)
        assert response.status_code == 400
        assert "Confirm media rights" in response.json()["errors"]["tiktok_business"]
        assert not PlatformPost.objects.filter(status="scheduled").exists()

    def test_composer_schedules_with_prefilled_defaults(self, browser, workspace, user, ttb_with_defaults, video):
        post = self._draft(workspace, user, video)
        acc = ttb_with_defaults.id
        # What the pre-filled panel submits.
        response = self._schedule(
            browser,
            workspace,
            post,
            ttb_with_defaults,
            **{
                f"ttb_rights_confirmed_{acc}": "true",
                f"ttb_client_approval_confirmed_{acc}": "true",
                f"ttb_is_brand_organic_{acc}": "false",
                f"ttb_is_branded_content_{acc}": "false",
            },
        )
        assert response.status_code in (200, 204, 302), response.content
        assert PlatformPost.objects.filter(status="scheduled", social_account=ttb_with_defaults).exists()

    def test_composer_shows_account_defaults(self, browser, workspace, ttb_with_defaults):
        from django.urls import reverse

        response = browser.get(reverse("composer:compose", kwargs={"workspace_id": workspace.id}))
        assert response.status_code == 200
        assert "Pre-filled from this account" in response.content.decode()
