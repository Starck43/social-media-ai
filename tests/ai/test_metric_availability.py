"""Normalizers distinguish unavailable metric placeholders from measured zero."""

from types import SimpleNamespace

from app.services.monitoring.ingest import normalize_channel_post
from app.services.social.tg_client import TelegramClient
from app.services.social.vk_client import VKClient
from app.types import SourceType


def test_telegram_bot_placeholder_reactions_are_unavailable():
    inbound = SimpleNamespace(
        chat_id="-1001", raw={"channel_post": {"message_id": 1, "text": "post", "date": 1, "views": 0}}
    )
    item = normalize_channel_post(inbound)
    assert item["metric_availability"] == {"reactions": False, "comments": False, "views": True}


def test_vk_missing_counts_not_confirmed_zero():
    normalized = VKClient._normalize_response(
        SimpleNamespace(), {"response": {"items": [{"id": 1, "text": "post", "date": 1, "views": {"count": 0}}]}}, SourceType.CHANNEL
    )
    assert normalized[0]["metric_availability"] == {"reactions": False, "comments": False, "views": True}
    item = {
        "id": 2,
        "text": "post",
        "date": 1,
        "views": {"count": 0},
        "likes": {"count": 0},
        "comments": {"count": 0},
        "reposts": {"count": 0},
    }
    normalized = VKClient._normalize_response(SimpleNamespace(), {"response": {"items": [item]}}, SourceType.CHANNEL)
    assert all(normalized[0]["metric_availability"].values())


def test_telegram_api_present_zero_and_missing_fields():
    normalized = TelegramClient._normalize_response(
        SimpleNamespace(),
        {
            "messages": [
                {
                    "id": 1,
                    "message": "post",
                    "date": 1,
                    "views": 0,
                    "reactions": {"results": []},
                    "replies": {"replies": 0},
                }
            ]
        },
        SourceType.CHANNEL,
    )
    assert all(normalized[0]["metric_availability"].values())
    assert normalized[0]["comments"] == 0
    normalized = TelegramClient._normalize_response(
        SimpleNamespace(), {"messages": [{"id": 2, "message": "post", "date": 1}]}, SourceType.CHANNEL
    )
    assert not any(normalized[0]["metric_availability"].values())
