#!/usr/bin/env python3
"""Warn about rows whose work is finished but whose registry row is still open.

Run from the repository root with ``python3 scripts/check_registry.py``.
Exit status is 0 while only warnings are printed; ``--strict`` turns warnings
into a non-zero exit for local use. The check is advisory on purpose: it cannot
know why a row is open, it only points at rows that look forgotten.

Heuristics, per open row in ``ROADMAP.md``:

* the linked task card carries an ``## Acceptance (<date>)`` record — the
  executor recorded acceptance with a date, so the row should have been closed
  (the ``## Acceptance`` section header itself is part of normal cards);
* the card links a merged PR but the row is not ``READY``/``BLOCKED``/``DEFERRED``;
* the card links ``docs/audit/<row>.md`` — the audit closeout step was skipped.

Second check: every card in ``docs/tasks/`` must appear in ``ROADMAP.md``. A card
left there after its row was removed belongs in ``docs/archive/tasks/``; a card
the registry cannot see is either closed or never registered.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROADMAP = Path("ROADMAP.md")
ROW = re.compile(r"^- \[(?P<id>[A-Z][\w.-]*)\]\((?P<card>[^)]+)\)\s+(?P<body>.*)$")
STATUS = re.compile(r"\*\*(TODO|IN_PROGRESS|READY|BLOCKED|DEFERRED|OPEN)\*\*")
TERMINAL = {"READY", "BLOCKED", "DEFERRED", "OPEN"}


def open_rows(text: str) -> list[tuple[str, str, str]]:
    rows: list[tuple[str, str, str]] = []
    for line in text.splitlines():
        match = ROW.match(line)
        if match:
            rows.append((match.group(1), match.group(2), match.group(3)))
    return rows


def findings(row: tuple[str, str, str]) -> list[str]:
    row_id, card_ref, body = row
    status_match = STATUS.search(body)
    status = status_match.group(1) if status_match else "?"
    if status in TERMINAL:
        return []
    card = Path(card_ref)
    if not card.exists():
        return [f"{row_id}: card {card_ref} is missing"]
    text = card.read_text(encoding="utf-8")
    results: list[str] = []
    if re.search(r"^## Acceptance[^\n]*\b\d{4}-\d{2}-\d{2}\b", text, re.MULTILINE):
        results.append(
            f"{row_id} ({status}): card has an '## Acceptance' record — "
            "close the row or explain the remaining scope in the card"
        )
    prs = re.findall(r"pull/(\d+)", text)
    if prs and re.search(r"merged|merge commit", text, re.IGNORECASE):
        results.append(
            f"{row_id} ({status}): card references PR(s) {', '.join(sorted(set(prs)))} with a "
            "merge record — verify the row still has open work"
        )
    if (Path("docs/audit") / f"{row_id.lower()}.md").exists():
        results.append(
            f"{row_id} ({status}): docs/audit/{row_id.lower()}.md exists — the audit closeout step looks skipped"
        )
    return results


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--strict", action="store_true", help="exit 1 when warnings are found")
    args = parser.parse_args(argv)

    if not ROADMAP.exists():
        print("check_registry: run from the repository root", file=sys.stderr)
        return 2

    rows = open_rows(ROADMAP.read_text(encoding="utf-8"))
    messages = [message for row in rows for message in findings(row)]
    linked = {Path(card).resolve() for _, card, _ in rows}
    for card in sorted(Path("docs/tasks").glob("*.md")):
        if card.resolve() not in linked:
            messages.append(
                f"{card.as_posix()}: card has no open ROADMAP row — "
                "archive it to docs/archive/tasks/ or register the work"
            )
    for message in messages:
        print(f"WARN {message}")
    print(f"check_registry: {len(rows)} open rows, {len(messages)} warnings")
    if args.strict and messages:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
