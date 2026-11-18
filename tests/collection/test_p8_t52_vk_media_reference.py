"""P8-T52: VK wall photos keep a fetchable provider URL after normalization.

Source-only: the VK client and the staging guard are loaded by path with local
doubles, so no app bootstrap, DB, network or provider call happens here.
Run: PYTHONPATH=.:tests python -m pytest tests/collection/test_p8_t52_vk_media_reference.py
"""

import unittest
from pathlib import Path
from types import SimpleNamespace as NS

from source_import_isolation import load_isolated_source

ROOT = Path(__file__).resolve().parents[2]
MODERN = "https://sun9-42.userapi.com/s/v1/ig2/abc.jpg?quality=96&as=1080x1080&cs=1080x1080&u=OPAQUE&from=bu"
BARE = "https://cdn.example.org/photo.jpg"


def forbidden(*args, **kwargs):
    raise AssertionError("Out-of-scope dependency called")


class VKMediaReferenceTests(unittest.TestCase):
    def setUp(self):
        self.util = load_isolated_source("_p8t52_media_utility", ROOT / "app/utils/content_attachments.py")
        imports = {
            "app.core.config": NS(settings=NS()),
            "app.models": NS(Source=object),
            "app.services.social.base": NS(BaseClient=object),
            "app.services.social.credentials": NS(
                AuthorizationRequired=RuntimeError, NarrowedAccess=RuntimeError, resolve_token=forbidden
            ),
            "app.services.social.owner": NS(resolve_source_owner=forbidden),
            "app.types": NS(SourceType=object),
            "app.utils.date_parsing": NS(to_unix_timestamp=forbidden, universal_date_parser=forbidden),
            "app.utils.enum_helpers": NS(get_enum_value=lambda value: value),
            "app.utils.content_attachments": NS(normalize_attachments=self.util.normalize_attachments),
        }
        self.module = load_isolated_source("_p8t52_vk_client", ROOT / "app/services/social/vk_client.py", imports)
        self.client = self.module.VKClient()
        self.raw = {"id": 7, "owner_id": -5, "text": "A wall post with one photo", "date": 1700000000}

    def normalize(self, attachments):
        item = {**self.raw, "attachments": attachments}
        return self.client._normalize_response({"response": {"items": [item]}}, "group")[0]

    def photo(self, url, width=1080, height=1080):
        return {
            "type": "photo",
            "photo": {"id": 11, "owner_id": -5, "sizes": [{"url": url, "width": width, "height": height}]},
        }

    def test_provider_query_url_survives_and_wins_on_area(self):
        photo = self.photo(BARE, 10, 10)
        photo["photo"]["sizes"].append({"url": MODERN, "width": 1080, "height": 1080})
        item = self.normalize([photo])
        self.assertEqual(item["attachments"], [{"type": "image", "url": MODERN}])
        self.assertTrue(item["has_attachments"])
        self.assertEqual(item["attachment_types"], ["photo"])

    def test_bare_reference_still_survives(self):
        item = self.normalize([self.photo(BARE)])
        self.assertEqual(item["attachments"], [{"type": "image", "url": BARE}])

    def test_video_preview_is_still_not_promoted_to_media(self):
        item = self.normalize([{"type": "video", "video": {"image": [{"url": MODERN}], "player": MODERN}}])
        self.assertEqual(item["attachments"], [{"type": "video", "url": None}])

    def test_post_permalink_is_built_from_owner_and_post_id(self):
        self.assertEqual(self.normalize([self.photo(MODERN)])["permalink"], "https://vk.com/wall-5_7")


if __name__ == "__main__":
    unittest.main()
