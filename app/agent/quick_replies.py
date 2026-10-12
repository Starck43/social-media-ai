"""Quick-reply choices offered by the agent.

The model may end a reply with one marker line, for example
``[[options: Мои источники | Мои задачи | Сводка за неделю]]``. The web chat
renders those choices as buttons; messenger channels get the same choices as a
plain-text hint. Pending confirmations always offer «Да» / «Нет» without
relying on the model.

The marker is presentation only: a button sends its label as an ordinary user
message, so every choice still passes the normal turn, permission and
confirmation path.
"""

from __future__ import annotations

import re

MAX_OPTIONS = 6
MAX_OPTION_LENGTH = 60

_MARKER_RE = re.compile(r"\n?[ \t]*\[\[\s*(?:options|варианты)\s*:\s*([^\[\]]*?)\s*\]\][ \t]*\Z", re.IGNORECASE)
_CONFIRMATION_PREFIX = "Требуется подтверждение"
CONFIRMATION_OPTIONS = ("Да", "Нет")


def split_quick_replies(text: str | None) -> tuple[str, list[str]]:
    """Return the reply without a trailing marker and its validated options."""
    if not text:
        return "", []
    body = text.rstrip()
    options: list[str] = []
    match = _MARKER_RE.search(body)
    if match:
        body = body[: match.start()].rstrip()
        seen: set[str] = set()
        for raw in match.group(1).split("|"):
            label = " ".join(raw.split())
            if not label or len(label) > MAX_OPTION_LENGTH or label.casefold() in seen:
                continue
            seen.add(label.casefold())
            options.append(label)
            if len(options) == MAX_OPTIONS:
                break
    if not options and body.lstrip().startswith(_CONFIRMATION_PREFIX):
        options = list(CONFIRMATION_OPTIONS)
    return body, options


def render_for_channel(text: str | None) -> str:
    """Messenger text: strip the marker and list the choices as a short hint."""
    body, options = split_quick_replies(text)
    if not options or body.lstrip().startswith(_CONFIRMATION_PREFIX):
        return body
    return f"{body}\n\nВарианты: " + " · ".join(f"«{option}»" for option in options)
