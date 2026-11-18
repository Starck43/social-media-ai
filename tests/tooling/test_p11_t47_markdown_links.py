"""Offline gate: every tracked Markdown link resolves (P11-T47).

Archiving a card moves it one directory deeper, so its ``../phases/…``,
``../audit/…`` and ``../../ROADMAP.md`` targets silently start pointing outside
``docs/``. The same happens to an audit record that links a card later moved
into ``docs/archive/tasks/``. ``scripts/check_markdown_links.py`` already detects
both shapes; this test makes the result a required check instead of an
occasionally-run script, so the next move cannot land unnoticed.

Stdlib only: it runs the repository script as a subprocess from the repository
root. Nothing imports the application, settings or a database driver.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

__source_only__ = True
ROOT = Path(__file__).resolve().parents[2]


def test_every_tracked_markdown_link_resolves():
    result = subprocess.run(
        [sys.executable, "scripts/check_markdown_links.py"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, f"broken Markdown links:\n{result.stdout}\n{result.stderr}"
    assert "0 detected errors" in result.stdout, result.stdout
