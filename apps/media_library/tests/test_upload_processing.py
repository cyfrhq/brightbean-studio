"""Every upload path must process media, and unreadable video must not "complete".

Composer and idea uploads used to create assets without queueing processing,
so videos kept the model's COMPLETED default with 0x0 and 0 s. TikTok Business
then refused them at publish time. Synthetic files only.
"""

import shutil
import subprocess
from io import StringIO
from unittest.mock import patch

import pytest
from background_task.models import Task
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import Client
from django.urls import reverse

from apps.media_library.models import MediaAsset
from apps.media_library.tasks import process_media_asset
from apps.members.models import WorkspaceMembership

PROCESS_TASK = "apps.media_library.tasks.process_media_asset"


@pytest.fixture
def workspace(db, org_owner, organization):
    from apps.workspaces.models import Workspace

    workspace = Workspace.objects.create(name="Upload WS", organization=organization)
    WorkspaceMembership.objects.create(
        user=org_owner, workspace=workspace, workspace_role=WorkspaceMembership.WorkspaceRole.OWNER
    )
    return workspace


@pytest.fixture
def logged_in(org_owner):
    client = Client()
    client.force_login(org_owner)
    return client


def _queued_for(asset_id):
    return [t for t in Task.objects.filter(task_name=PROCESS_TASK) if str(asset_id) in t.task_params]


def _video_asset(workspace, user, name="clip.mp4"):
    media = MediaAsset.objects.create(
        organization=workspace.organization,
        workspace=workspace,
        uploaded_by=user,
        filename=name,
        media_type=MediaAsset.MediaType.VIDEO,
        mime_type="video/mp4",
        file_size=10,
    )
    media.file.save(name, ContentFile(b"synthetic-not-a-video"), save=True)
    return media


@pytest.mark.django_db
@pytest.mark.parametrize("url_name", ["composer:upload_media", "composer:idea_upload_media"])
def test_composer_and_idea_uploads_queue_processing(logged_in, workspace, url_name):
    upload = SimpleUploadedFile("clip.mp4", b"synthetic-video-bytes", content_type="video/mp4")
    response = logged_in.post(reverse(url_name, kwargs={"workspace_id": workspace.id}), {"file": upload})

    assert response.status_code == 200, response.content
    asset = MediaAsset.objects.get(workspace=workspace)
    assert asset.processing_status == MediaAsset.ProcessingStatus.PENDING
    assert len(_queued_for(asset.id)) == 1


@pytest.mark.django_db
def test_video_with_unreadable_metadata_is_failed_not_completed(workspace, org_owner):
    media = _video_asset(workspace, org_owner)

    with patch("apps.media_library.tasks.extract_video_metadata", return_value={}):
        process_media_asset.now(str(media.id))

    media.refresh_from_db()
    assert media.processing_status == MediaAsset.ProcessingStatus.FAILED
    assert (media.width, media.height, media.duration) == (0, 0, 0)


@pytest.mark.django_db
def test_video_metadata_is_recorded(workspace, org_owner):
    media = _video_asset(workspace, org_owner)
    metadata = {"width": 1080, "height": 1920, "duration_seconds": 35.1}

    with (
        patch("apps.media_library.tasks.extract_video_metadata", return_value=metadata),
        patch("apps.media_library.tasks.generate_video_thumbnail", return_value=None),
    ):
        process_media_asset.now(str(media.id))

    media.refresh_from_db()
    assert media.processing_status == MediaAsset.ProcessingStatus.COMPLETED
    assert (media.width, media.height, media.duration) == (1080, 1920, 35.1)


@pytest.mark.django_db
@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_real_composer_upload_gets_real_metadata(logged_in, workspace, tmp_path):
    """End to end with a generated synthetic clip: upload, process, read back."""
    clip = tmp_path / "synthetic.mp4"
    subprocess.run(
        [
            "ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=480x640:rate=30:duration=4",
            "-pix_fmt", "yuv420p", "-y", str(clip),
        ],
        check=True,
        timeout=60,
    )  # fmt: skip
    upload = SimpleUploadedFile("synthetic.mp4", clip.read_bytes(), content_type="video/mp4")
    response = logged_in.post(reverse("composer:upload_media", kwargs={"workspace_id": workspace.id}), {"file": upload})
    assert response.status_code == 200, response.content

    asset = MediaAsset.objects.get(workspace=workspace)
    process_media_asset.now(str(asset.id))
    asset.refresh_from_db()
    assert asset.processing_status == MediaAsset.ProcessingStatus.COMPLETED
    assert (asset.width, asset.height) == (480, 640)
    assert 3.5 <= asset.duration <= 4.5


@pytest.mark.django_db
def test_reprocess_command_lists_then_queues_zero_metadata_assets(workspace, org_owner):
    broken = _video_asset(workspace, org_owner, "broken.mp4")  # COMPLETED default, 0x0, 0 s
    healthy = _video_asset(workspace, org_owner, "healthy.mp4")
    MediaAsset.objects.filter(id=healthy.id).update(width=1080, height=1920, duration=12.0)

    out = StringIO()
    call_command("reprocess_media_metadata", stdout=out)
    assert str(broken.id) in out.getvalue()
    assert str(healthy.id) not in out.getvalue()
    assert not _queued_for(broken.id)

    call_command("reprocess_media_metadata", "--apply", stdout=StringIO())
    assert len(_queued_for(broken.id)) == 1
    assert not _queued_for(healthy.id)
