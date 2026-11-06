"""AI disclosure reaches the actual Meta video/container request, with no network."""
from unittest.mock import MagicMock, patch

from providers.facebook import FacebookProvider
from providers.instagram import InstagramProvider
from providers.types import PostType, PublishContent


def video():
    return PublishContent(text="Synthetic demo", post_type=PostType.VIDEO,
                          media_urls=["https://gateway.example/media/opaque/video.mp4"],
                          media_types=["video"], extra={"is_ai_generated": True})


def test_facebook_video_request_discloses_ai():
    provider=FacebookProvider()
    provider._request=MagicMock(side_effect=[MagicMock(json=lambda:{"id":"v1"}),
                                           MagicMock(json=lambda:{"permalink_url":"https://www.facebook.com/example/videos/v1"})])
    provider._publish_video("synthetic", "page1", video())
    assert provider._request.call_args_list[0].kwargs["json"]["is_ai_generated"] is True


def test_instagram_published_container_discloses_ai():
    provider=InstagramProvider()
    with patch.object(provider,"_create_container",return_value="c1") as container, \
         patch.object(provider,"_wait_for_container"), \
         patch.object(provider,"_publish_container",return_value="m1"), \
         patch.object(provider,"_request",return_value=MagicMock(json=lambda:{"permalink":"https://www.instagram.com/reel/example"})):
        provider._publish_single("synthetic", "ig1", video())
    assert container.call_args.args[2]["is_ai_generated"] is True
