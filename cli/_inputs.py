"""Pure input parsing shared by CLI commands; no application initialization."""


def parse_usernames(value: str | None) -> list[str]:
    """Split comma/whitespace-separated usernames and remove leading @ signs.

    Preserve input order, case and duplicates. This is tokenization, not account
    validation or URL parsing. For compatibility, a token consisting only of @
    signs becomes an empty string rather than being silently discarded.
    """
    return [
        username.strip().lstrip("@")
        for username in (value or "").replace(",", " ").split()
        if username.strip()
    ]
