"""Telegram-only compatibility for the two historical Bot API mode spellings."""


def is_bot_push(params) -> bool:
    # Exact allowlist. Unknown/absent modes must never silently bypass personal
    # authorization. In particular VK's api mode has a different contract.
    return type(params) is dict and params.get("mode") in ("push", "api")
