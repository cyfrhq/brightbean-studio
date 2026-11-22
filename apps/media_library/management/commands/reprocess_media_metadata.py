"""Re-queue processing for media that was marked completed without metadata.

Composer and idea uploads used to skip processing, leaving videos and images
"completed" with 0x0 (and 0 s for video). This finds those assets and queues
the normal processing task for each. Dry run unless ``--apply`` is passed.
"""

from django.core.management.base import BaseCommand
from django.db.models import Q

from apps.media_library.models import MediaAsset
from apps.media_library.tasks import process_media_asset


class Command(BaseCommand):
    help = "Re-queue processing for completed images/videos stored with zero dimensions or duration."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="Queue processing (default: only list).")
        parser.add_argument("--workspace", help="Limit to one workspace ID.")

    def handle(self, *args, **options):
        qs = MediaAsset.objects.filter(processing_status=MediaAsset.ProcessingStatus.COMPLETED).filter(
            Q(
                media_type__in=[MediaAsset.MediaType.IMAGE, MediaAsset.MediaType.GIF],
            )
            & (Q(width=0) | Q(height=0))
            | Q(media_type=MediaAsset.MediaType.VIDEO) & (Q(width=0) | Q(height=0) | Q(duration=0))
        )
        if options["workspace"]:
            qs = qs.filter(workspace_id=options["workspace"])
        ids = [str(i) for i in qs.order_by("created_at").values_list("id", flat=True)]
        for asset_id in ids:
            self.stdout.write(asset_id)
        if not options["apply"]:
            self.stdout.write(f"{len(ids)} asset(s) need processing. Re-run with --apply to queue them.")
            return
        for asset_id in ids:
            process_media_asset(asset_id)
        self.stdout.write(f"Queued processing for {len(ids)} asset(s).")
