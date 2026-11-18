"""P8-T52: only provider media CDNs may keep a query string in a staged URL.

Tool-only: ``app/utils/content_attachments.py`` is loaded by path with no ``app``
import, so the offline lane needs no settings, driver or network. The rule under
test is the owner's choice B in [P8-T52](../../docs/tasks/p8-t52.md): a modern VK
photo link is fetchable only together with its query, and every other host keeps
the stricter bare-URL contract.
"""

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

_MODERN_VK = "https://sun9-42.userapi.com/s/v1/ig2/abc.jpg?quality=96&cs=1080x1080&u=OPAQUE&from=bu"
_REJECTED = (
    "https://cdn.example.org/photo.jpg?token=redacted",
    "https://userapi.com.evil.example/photo.jpg?u=redacted",
    "https://userapi.com@evil.example/photo.jpg?u=redacted",
    _MODERN_VK + "#secret",
    _MODERN_VK.replace("https://", "http://"),
    "https://127.0.0.1/photo.jpg?u=redacted",
    "https://singlehost/photo.jpg?u=redacted",
    "https://userapi.com:8443/photo.jpg?u=redacted",
    "https://userapi.com/photo.jpg?has space",
)


def _utility():
    spec = importlib.util.spec_from_file_location("_p8t52_media_utility", ROOT / "app/utils/content_attachments.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


NORMALIZE = _utility().normalize_attachments


def test_provider_cdn_query_is_preserved_for_photos_and_videos():
    assert NORMALIZE([{"type": "photo", "url": _MODERN_VK}]) == [{"type": "image", "url": _MODERN_VK}]
    legacy = "https://pp.userapi.com/c1/photo.jpg?cs=1"
    assert NORMALIZE([{"type": "video", "url": legacy}]) == [{"type": "video", "url": legacy}]
    assert NORMALIZE([{"type": "image", "url": "https://sun9-7.vkuser.net/x.jpg?cs=2"}]) == [
        {"type": "image", "url": "https://sun9-7.vkuser.net/x.jpg?cs=2"}
    ]


def test_bare_url_contract_still_applies_everywhere_else():
    assert NORMALIZE([{"type": "image", "url": "https://cdn.example.org/photo.jpg"}]) == [
        {"type": "image", "url": "https://cdn.example.org/photo.jpg"}
    ]
    for url in _REJECTED:
        assert NORMALIZE([{"type": "image", "url": url}]) == [{"type": "image", "url": None}], url


def test_provider_host_matching_is_suffix_exact_not_substring():
    for host in ("userapi.com.evil.example", "notuserapi.com", "userapi.com-evil.example"):
        assert NORMALIZE([{"type": "image", "url": f"https://{host}/p.jpg?u=1"}]) == [
            {"type": "image", "url": None}
        ], host
    for host in ("userapi.com", "sun9-42.userapi.com", "SUN9-42.USERAPI.COM."):
        normalized = NORMALIZE([{"type": "image", "url": f"https://{host}/p.jpg?u=1"}])
        assert normalized[0]["url"] is not None, host
