#!/usr/bin/env python3
"""Check tracked Markdown links, local targets, and heading anchors.

Run from the repository root with ``python3 scripts/check_markdown_links.py``.
External URLs are intentionally not fetched.
"""
from __future__ import annotations

import html
import re
import subprocess
import sys
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path.cwd()


class HTMLLinks(HTMLParser):
    """Collect explicit HTML anchors and local-resource references."""

    def __init__(self) -> None:
        super().__init__()
        self.anchors: set[str] = set()
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        for key in ("id", "name"):
            if attributes.get(key):
                self.anchors.add(attributes[key])
        if tag in ("a", "area") and attributes.get("href"):
            self.links.append(attributes["href"])
        if tag in ("img", "source", "iframe", "video", "audio") and attributes.get("src"):
            self.links.append(attributes["src"])


def tracked_markdown() -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "-z", "*.md"], check=True, capture_output=True
    )
    return [Path(item.decode()) for item in result.stdout.split(b"\0") if item]


def strip_fenced_code(text: str) -> str:
    lines: list[str] = []
    fence: tuple[str, int] | None = None
    for line in text.splitlines():
        match = re.match(r"^ {0,3}(`{3,}|~{3,})", line)
        if match:
            marker = match.group(1)
            if fence is None:
                fence = (marker[0], len(marker))
            elif marker[0] == fence[0] and len(marker) >= fence[1]:
                fence = None
            lines.append("")
        else:
            lines.append("" if fence else line)
    return "\n".join(lines)


def heading_slug(heading: str) -> str:
    heading = re.sub(r"<[^>]*>", "", heading)
    heading = re.sub(
        r"!?\[([^]]*)\](?:\([^)]*\))?", lambda match: match.group(1), heading
    )
    heading = re.sub(r"(?<!\w)_([^_]+)_(?!\w)", r"\1", heading)
    heading = re.sub(r"[`*~]", "", heading).strip().lower()
    heading = re.sub(r"\s", "-", heading)
    return re.sub(r"[^\w\-]", "", heading, flags=re.UNICODE)


def parse_links(text: str) -> tuple[list[str], set[str], str]:
    source = strip_fenced_code(text)
    link_text = re.sub(r"(?<!`)`+[^`\n]*`+(?!`)", "", source)

    references = {
        match.group(1).casefold(): match.group(2) or match.group(3)
        for match in re.finditer(
            r"^ {0,3}\[([^\]]+)\]:\s*(?:<([^>]+)>|(\S+))", link_text, re.M
        )
    }
    link_text = re.sub(
        r"^ {0,3}\[[^\]]+\]:\s*(?:<[^>]+>|\S+).*$",
        "",
        link_text,
        flags=re.M,
    )

    destinations: list[str] = []
    inline_link = re.compile(
        r"!?\[(?:[^\[\]]|\[[^\]]*\])*\]\(\s*(?:<([^>]+)>|((?:\\.|[^\s)])+))",
        re.S,
    )
    for match in inline_link.finditer(link_text):
        destinations.append(match.group(1) or match.group(2))
    link_text = inline_link.sub("", link_text)

    for match in re.finditer(r"!?\[([^\]]+)\](?:\[([^\]]*)\])?", link_text):
        label = (match.group(2) or match.group(1)).strip().casefold()
        if label in references:
            destinations.append(references[label])

    parser = HTMLLinks()
    parser.feed(link_text)
    destinations.extend(parser.links)
    return destinations, parser.anchors, source


def markdown_anchors(text: str, html_anchors: set[str]) -> set[str]:
    headings: list[str] = []
    lines = text.splitlines()
    for index, line in enumerate(lines):
        match = re.match(r"^ {0,3}#{1,6}\s+(.+?)\s*#*\s*$", line)
        if match:
            headings.append(match.group(1))
        elif (
            index > 0
            and re.match(r"^ {0,3}(=+|-+)\s*$", line)
            and lines[index - 1].strip()
        ):
            headings.append(lines[index - 1].strip())

    anchors = set(html_anchors)
    duplicates: dict[str, int] = {}
    for heading in headings:
        base = heading_slug(heading)
        count = duplicates.get(base, 0)
        duplicates[base] = count + 1
        anchors.add(base if count == 0 else f"{base}-{count}")
    return anchors


def main() -> int:
    if not (ROOT / ".git").exists() and not (ROOT / ".git").is_file():
        print("run this checker from the repository root", file=sys.stderr)
        return 2

    contents = {
        path: path.read_text(errors="replace")
        for path in tracked_markdown()
        if path.exists()
    }
    anchors: dict[Path, set[str]] = {}
    links: list[tuple[Path, str]] = []
    for path, text in contents.items():
        destinations, html_anchors, source = parse_links(text)
        anchors[path] = markdown_anchors(source, html_anchors)
        links.extend((path, destination) for destination in destinations)

    errors: list[tuple[Path, str, str]] = []
    local_link_count = 0
    for source, destination in links:
        destination = html.unescape(destination).replace(r"\(", "(").replace(r"\)", ")")
        parsed = urlsplit(destination)
        if parsed.scheme or destination.startswith("//"):
            continue
        local_link_count += 1

        path_part = unquote(parsed.path)
        target = (source.parent / path_part).resolve() if path_part else (ROOT / source).resolve()
        try:
            relative_target = target.relative_to(ROOT.resolve())
        except ValueError:
            errors.append((source, destination, "outside repository"))
            continue

        if target.is_dir():
            if parsed.fragment:
                errors.append((source, destination, "fragment on directory target"))
            continue
        if not target.exists():
            errors.append((source, destination, "missing target"))
            continue
        if parsed.fragment and target.suffix.lower() == ".md":
            fragment = unquote(parsed.fragment)
            if fragment not in anchors.get(Path(relative_target.as_posix()), set()):
                errors.append((source, destination, "missing anchor"))

    print(
        f"{len(contents)} tracked Markdown files, {local_link_count} local-link candidates, "
        f"{len(errors)} detected errors"
    )
    for source, destination, reason in errors[:100]:
        print(f"{source}: {destination} ({reason})")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
