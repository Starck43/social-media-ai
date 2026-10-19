"""Quick-reply choices offered by the agent.

The model may end a reply with one marker line, for example
``[[options: Мои источники | Мои задачи | Сводка за неделю]]``. The web chat
renders those choices as buttons; messenger channels get the same choices as a
plain-text hint. A staged confirmation offers its own action as the primary
button plus «Покажи данные» and «Отмена»; a bare «Да» / «Нет» pair is never
shown on its own.

The marker is presentation only: a button sends its label as an ordinary user
message, so every choice still passes the normal turn, permission and
confirmation path.
"""

from __future__ import annotations

import re

MAX_OPTIONS = 6
MAX_OPTION_LENGTH = 60
# Short labels keep confirmation buttons readable next to a long question.
MAX_CONFIRMATION_LABEL = 32

_MARKER_RE = re.compile(r"\n?[ \t]*\[\[\s*(?:options|варианты)\s*:\s*([^\[\]]*?)\s*\]\][ \t]*\Z", re.IGNORECASE)
_CONFIRMATION_PREFIX = "Требуется подтверждение:"
# The instruction sentence the confirmation text ends with; the buttons make
# it redundant once «Ответьте «да» или «нет»» is no longer the only way to act.
_INSTRUCTION_LEADS = ("Ответьте", "Напишите")
_CANCEL_OPTION = "Отмена"
_DETAILS_OPTION = "Покажи данные"
# What the primary button sends: an explicit yes that the model understands.
_CONFIRM_OPTION = "Да, подтверждаю"


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
        options = confirmation_options(body)
        body = strip_instruction(body)
    return body, options


def strip_instruction(text: str) -> str:
    """Drop a trailing «Ответьте «да» или «нет».» sentence, keeping the rest.

    The buttons say the same thing, so the sentence is only noise once it is
    rendered under them. Plain string work, not a regex: the sentence is short
    and the text is Russian, where a character class is easy to misread.
    """
    body = text.rstrip()
    for lead in _INSTRUCTION_LEADS:
        index = body.rfind(" " + lead)
        if index < 0:
            continue
        tail = body[index + 1 :]
        if tail.rstrip().endswith(".") and "«" in tail and "»" in tail:
            return body[:index].rstrip()
    return body


def confirmation_options(body: str) -> list[str]:
    """Buttons for a staged action: do it, look at its data, or drop it.

    The primary label is the staged action itself, shortened to a button-width
    phrase; the model may replace it with its own marker when the action needs
    a shorter name.
    """
    action = strip_instruction(body).lstrip()[len(_CONFIRMATION_PREFIX) :].strip().rstrip(" .")
    action = _shorten(action, MAX_CONFIRMATION_LABEL)
    primary = _CONFIRM_OPTION if not action else f"{_CONFIRM_OPTION}: {action}"
    return [_shorten(primary, MAX_OPTION_LENGTH), _DETAILS_OPTION, _CANCEL_OPTION]


def _shorten(text: str, limit: int) -> str:
    """Cut at a word boundary, never mid-word, and mark the cut."""
    if len(text) <= limit:
        return text
    cut = text[: limit - 1]
    if " " in cut:
        cut = cut[: cut.rstrip().rfind(" ")]
    return cut.rstrip(" ,;:—-") + "…"


def render_for_channel(text: str | None) -> str:
    """Messenger text: strip the marker and list the choices as a short hint."""
    body, options = split_quick_replies(text)
    if not options or body.lstrip().startswith(_CONFIRMATION_PREFIX):
        return body
    return f"{body}\n\nВарианты: " + " · ".join(f"«{option}»" for option in options)
