"""The snapshot tool must accept the application's DSN, not demand a secret.

The regression: `scripts/ci/schema_inventory.py` read `os.environ` only and
handed `postgresql+asyncpg://…` straight to psycopg2, which rejects the driver
suffix as an invalid DSN. The visible symptom was "you must pass a URL with a
password", which invited passing credentials around to run a layout check.

No database and no app import.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.ci.schema_inventory import libpq_dsn  # noqa: E402


def test_async_driver_suffix_is_stripped():
    assert libpq_dsn("postgresql+asyncpg://u@localhost:5432/db") == "postgresql://u@localhost:5432/db"


def test_psycopg_suffixes_are_stripped():
    assert libpq_dsn("postgresql+psycopg://u:p@h/db") == "postgresql://u:p@h/db"
    assert libpq_dsn("postgresql+psycopg2://u:p@h/db") == "postgresql://u:p@h/db"


def test_plain_urls_are_untouched():
    for url in ("postgresql://u@h/db", "postgres://u@h/db", "host=localhost dbname=db"):
        assert libpq_dsn(url) == url


def test_a_password_in_the_url_survives():
    """Stripping the suffix must not mangle credentials or the query string."""
    assert libpq_dsn("postgresql+asyncpg://u:p%40ss@h:5432/db?sslmode=require") == (
        "postgresql://u:p%40ss@h:5432/db?sslmode=require"
    )
