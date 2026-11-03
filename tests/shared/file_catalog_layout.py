"""Standalone synthetic catalog UI states for reproducible local visual QA.

Run: python -m tests.shared.file_catalog_layout --output /tmp/catalog-previews
No app, database, credentials or network imports. Assets use the checkout's CSS/JS.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path

from tests.shared.settings_layout_fixtures import ROOT, context, environment


def fixture(case):
    ctx = context("connections")
    ctx.update(section="knowledge", _tz="Europe/Kirov", file_catalog_enabled=True)
    mount = {"id": 17, "label": "Рабочие документы", "root": "disk:/Проекты/Договоры"}
    files = [
        {
            "id": i,
            "name": name,
            "kind": kind,
            "path": mount["root"] + "/" + name,
            "observed_at": datetime(2026, 11, 2, 15, 30, tzinfo=timezone.utc),
        }
        for i, (name, kind) in enumerate(
            [
                ("Договор на сопровождение — ноябрь.pdf", "file"),
                ("Смета проекта 2026.xlsx", "file"),
                ("Архив документов", "dir"),
            ],
            start=101,
        )
    ]
    ctx.update(mounts=[mount], selected=mount, files=files, q="", next_url=None, after=0, scan_message=None)
    if case == "no-mounts":
        ctx.update(mounts=[], selected=None, files=[])
    elif case in {"empty", "search"}:
        ctx.update(files=[], q="Договор 2027" if case == "search" else "")
    elif case == "long-name":
        files[0]["name"] = "Очень-длинное-название-документа-без-пробелов-" * 8 + ".pdf"
        files[0]["path"] = mount["root"] + "/" + files[0]["name"]
        ctx.update(files=files[:1])
    if case == "detail":
        item = dict(files[0])
        item["observations"] = [dict(number=1, name=item["name"], kind="file", observed_at=item["observed_at"])]
        ctx.update(file=item, connection_id=17)
    return ctx


def render(case):
    env = environment()
    env.globals["static_v"] = lambda path: (ROOT / "app/static" / path).as_uri()
    env.globals["url_for"] = lambda endpoint, path: (ROOT / "app/static" / path).as_uri()
    template = "file_catalog_detail" if case == "detail" else "file_catalog"
    return env.get_template("web/" + template + ".html").render(**fixture(case))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    for case in ("list", "no-mounts", "empty", "search", "detail", "long-name"):
        (args.output / (case + ".html")).write_text(render(case), encoding="utf-8")
