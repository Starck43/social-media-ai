"""Source-isolated checks for the ThemeMatcher similarity SQL contract.

The similarity search runs against `ai_analytics.main_topics`, a **json**
column: text[]-only array operators (`&`, `|`), an uninterpolated
`{settings.DB_SCHEMA}` placeholder and `:param::text[]` binds (SQLAlchemy
text() mis-parses a cast right after a bind name) each failed every call
silently, so topic chains never auto-linked. The SQL lives in
`AIAnalyticsManager.most_similar_by_topics`; `ThemeMatcher` delegates. These
checks pin that contract without a database.

Run directly: python tests/test_theme_matcher_sql.py
"""

from __future__ import annotations

import inspect
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.config import settings
from app.models.managers.ai_analytics_manager import AIAnalyticsManager
from app.services.ai.theme_matcher import ThemeMatcher


class ThemeMatcherSqlContractTests(unittest.TestCase):
    def setUp(self):
        self.source = inspect.getsource(AIAnalyticsManager.most_similar_by_topics)
        self.delegation = inspect.getsource(ThemeMatcher.find_similar_analytics)

    def test_theme_matcher_delegates_to_the_manager(self):
        # One SQL owner: the manager method; the matcher keeps no copy.
        self.assertIn("most_similar_by_topics", self.delegation)

    def test_schema_is_interpolated_not_a_literal_placeholder(self):
        # The manager interpolates the schema at call time (f-string); theme
        # delegation keeps no SQL copy of its own.
        self.assertIn("FROM {settings.DB_SCHEMA}.ai_analytics", self.source)
        # The only placeholder lives inside the SQL f-string, after it starts.
        self.assertGreater(
            self.source.find("{settings.DB_SCHEMA}"),
            self.source.find('text(f"""'),
        )

    def test_json_column_is_expanded_array_safe(self):
        # `main_topics` is json: expanded per row into text[], tolerant of
        # scalar (non-array) json values stored by older runs.
        self.assertIn("json_array_elements_text", self.source)
        self.assertIn("json_typeof", self.source)

    def test_no_array_operators_that_postgresql_does_not_define(self):
        # Text arrays support overlap (&&) only — no `&` intersection and no
        # `|` union operators exist; the Jaccard ratio must use unnest sets.
        self.assertNotIn("main_topics & ", self.source)
        self.assertNotIn("main_topics | ", self.source)
        self.assertIn("INTERSECT", self.source)
        self.assertIn("UNION", self.source)

    def test_parameters_bind_through_cast_not_inline_colon_cast(self):
        # `:name::text[]` breaks SQLAlchemy text() parsing (a bind followed by
        # a colon cast); parameters bind through explicit CAST(... AS text[]).
        self.assertIn("CAST(:topics AS text[])", self.source)
        tokens_after_binds = [part for part in self.source.split(":topics")[1:]]
        self.assertTrue(all(not part.lstrip().startswith("::") for part in tokens_after_binds))

    def test_matched_record_is_loaded_through_the_tenant_guarded_queryset(self):
        # raw SQL only ranks ids; the returned record goes through the ordinary
        # filter() so another workspace's analytics can never leak.
        self.assertIn("self.filter(id=int(row[0]))", self.source)


if __name__ == "__main__":
    unittest.main()
