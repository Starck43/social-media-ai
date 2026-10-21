"""Owner-approved analysis coverage semantics (2026-10-19) — source-isolated.

A staged attachment without a usable URL carries no analyzable media body, so
it is not media for analysis purposes: the post retires on its text coverage
instead of being withheld forever. Media modalities follow the scenario's
configured models — a modality without a model is ignored, not a coverage gap.

Run directly: python tests/test_media_coverage_semantics.py
Run under pytest: pytest tests/test_media_coverage_semantics.py --noconftest
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.ai.content_classifier import ContentClassifier


def _post(item_id: int, *, text: str = "пост", attachments=None) -> dict:
    item = {"id": item_id, "text": text}
    if attachments is not None:
        item["attachments"] = attachments
    return item


class MediaCoverageSemanticsTests(unittest.TestCase):
    def test_url_less_attachments_are_not_media(self):
        item = _post(1, attachments=[{"type": "image", "url": None}, {"type": "video", "url": None}])
        classified = ContentClassifier.classify_content([item])
        self.assertEqual(classified["image"], [])
        self.assertEqual(classified["video"], [])
        self.assertEqual(len(classified["text"]), 1)

    def test_url_ful_attachments_still_reach_media_buckets(self):
        item = _post(2, attachments=[{"type": "image", "url": "https://example.org/a.jpg"}])
        classified = ContentClassifier.classify_content([item])
        self.assertEqual(len(classified["image"]), 1)
        self.assertEqual(classified["image"][0]["media_url"], "https://example.org/a.jpg")

    def test_url_less_attachments_do_not_withhold_their_parent(self):
        item = _post(3, attachments=[{"type": "image", "url": None}])
        self.assertEqual(ContentClassifier.uncovered_attachment_items([item]), [])

    def test_malformed_attachment_collection_stays_unknown_evidence(self):
        item = _post(4, attachments="not-a-list")
        self.assertEqual(len(ContentClassifier.uncovered_attachment_items([item])), 1)

    def test_no_attachments_never_counts_against_coverage(self):
        item = _post(5, attachments=[])
        self.assertEqual(ContentClassifier.uncovered_attachment_items([item]), [])
        missing = _post(6)
        self.assertEqual(ContentClassifier.uncovered_attachment_items([missing]), [])

    def test_media_only_post_without_text_is_not_classified_anywhere(self):
        item = _post(7, text="", attachments=[{"type": "image", "url": None}])
        classified = ContentClassifier.classify_content([item])
        self.assertEqual(classified["text"], [])
        self.assertEqual(classified["image"], [])


if __name__ == "__main__":
    unittest.main()
