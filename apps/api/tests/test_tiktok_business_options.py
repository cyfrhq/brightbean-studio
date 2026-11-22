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
    )


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
        assert extra["rights_confirmed"] is False
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

    def test_publisher_blocker_matches_provider_wording(self, ttb):
        assert publish_blocker(ttb, media_types={"video"}) == "Confirm media rights and client approval before posting."
        consented = {"rights_confirmed": True, "client_approval_confirmed": True}
        assert publish_blocker(ttb, media_types={"video"}, platform_extra=consented) == (
            "Choose the promotional-content disclosures for this video."
        )
