"""Personal-data encryption, blind indexes and log redaction (P13-T02).

Three separate jobs with three separate keys, by design (D8):

- `encrypt_pii`/`decrypt_pii` use `PII_ENCRYPTION_KEYS`, an ordered Fernet list.
  The first key encrypts; every key decrypts, so a rotation is "prepend a key,
  re-encrypt, drop the old one" without a window where data is unreadable.
- `blind_index` uses `PII_INDEX_KEY` (HMAC-SHA256) and is deliberately stable:
  the same normalized value always yields the same index, which is what makes
  exact lookup and deduplication possible over ciphertext. It is never rotated
  by P13-T02; rebuilding an index is a separate approved operation.
- `redact_pii` strips personal values from text headed for logs.

Neither key may be `CREDENTIALS_KEY`: one compromise must not open the credential
vault and every personal record at once.

Ciphertext is versioned (`pii:v1:<token>`) so a future scheme can be recognized
without guessing, and blind indexes carry their kind (`pii:idx:v1:<kind>:<hex>`)
so an email index can never be compared with a phone index. Missing, malformed
or non-matching keys fail closed — no plaintext fallback exists.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import re
import traceback
import unicodedata
from typing import Iterable

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings

ENCRYPTION_PREFIX = "pii:v1:"
INDEX_PREFIX = "pii:idx:v1:"
_INDEX_HEX_LENGTH = 64

# Normalization kinds the blind index can vouch for. Anything else is a
# programming error, not a silently different normalization.
INDEX_KINDS = frozenset({"email", "phone", "username", "opaque_id"})

_PHONE_ALLOWED = re.compile(r"^\+?[0-9 ()\-]+$")
DEFAULT_PHONE_REGION = "RU"
# Only one region is implemented; a caller asking for another must not get a
# silently wrong E.164 value.
_PHONE_REGIONS = {"RU": "7"}

_REDACTION = "[redacted]"
_REDACTION_PATTERNS = (
    # Email: keep the shape of the text, drop the identity.
    (re.compile(r"(?<![\w.+%\-])[\w.+%\-]+@[\w\-]+(?:\.[\w\-]+)+", re.UNICODE), _REDACTION),
    # Phone: international or local with enough digits to be a phone, not a date
    # or an internal id. Grouping characters are optional.
    (re.compile(r"(?<![\w.])\+?\d[\d\s()\-]{8,}\d(?![\w.])"), _REDACTION),
)


class PIIError(RuntimeError):
    """A safe, value-free PII failure suitable for diagnostics and logs."""


def _sorted_key_list(raw: str | None, setting_name: str) -> list[str]:
    keys = [key.strip() for key in (raw or "").split(",") if key.strip()]
    if not keys:
        raise PIIError(f"{setting_name} is not set — cannot handle personal data")
    for key in keys:
        try:
            Fernet(key.encode())
        except (ValueError, TypeError) as e:
            # Never echo the value: the message is what ends up in logs.
            raise PIIError(f"{setting_name} contains a key that is not a valid Fernet key") from e
    return keys


def _index_key() -> bytes:
    raw = (settings.PII_INDEX_KEY or "").strip()
    if not raw:
        raise PIIError("PII_INDEX_KEY is not set — cannot build a blind index")
    try:
        key = base64.b64decode(raw.encode("ascii"), altchars=b"-_", validate=True)
    except (ValueError, TypeError) as e:
        raise PIIError("PII_INDEX_KEY is not a urlsafe-base64 value") from e
    if len(key) < 32:
        raise PIIError("PII_INDEX_KEY must decode to at least 32 bytes")
    credentials = (settings.CREDENTIALS_KEY or "").strip()
    if credentials and _same_key_bytes(key, credentials):
        raise PIIError("PII_INDEX_KEY must not reuse CREDENTIALS_KEY")
    encryption = settings.PII_ENCRYPTION_KEYS or ""
    if encryption.strip():
        for encryption_key in _sorted_key_list(encryption, "PII_ENCRYPTION_KEYS"):
            if _same_key_bytes(key, encryption_key):
                raise PIIError("PII_INDEX_KEY must not reuse PII_ENCRYPTION_KEYS")
    return key


def _same_key_bytes(decoded: bytes, candidate: str) -> bool:
    try:
        return hmac.compare_digest(decoded, base64.urlsafe_b64decode(candidate.encode("ascii")))
    except (ValueError, TypeError):
        return False


def _guard_key_reuse(keys: Iterable[str]) -> None:
    credentials = (settings.CREDENTIALS_KEY or "").strip()
    for key in keys:
        if credentials and _same_key_bytes(base64.urlsafe_b64decode(key.encode("ascii")), credentials):
            raise PIIError("PII_ENCRYPTION_KEYS must not reuse CREDENTIALS_KEY")


def encryption_keys() -> list[Fernet]:
    """Return `[active, ...older]`; the first entry encrypts."""
    keys = _sorted_key_list(settings.PII_ENCRYPTION_KEYS, "PII_ENCRYPTION_KEYS")
    _guard_key_reuse(keys)
    if (settings.PII_INDEX_KEY or "").strip():
        _index_key()
    return [Fernet(key.encode()) for key in keys]


def encrypt_pii(plaintext: str) -> str:
    """Encrypt with the active key. Empty input is stored as an empty string."""
    if not isinstance(plaintext, str):
        raise PIIError("personal data must be a string")
    if plaintext == "":
        return ""
    token = encryption_keys()[0].encrypt(plaintext.encode()).decode()
    return f"{ENCRYPTION_PREFIX}{token}"


def decrypt_pii(value: str | None) -> str | None:
    """Decrypt with any configured key. A wrong key fails closed."""
    if value is None or value == "":
        return None
    if not value.startswith(ENCRYPTION_PREFIX):
        # Legacy or foreign ciphertext must never be interpreted as PII.
        raise PIIError("value is not a PII ciphertext")
    token = value[len(ENCRYPTION_PREFIX) :].encode()
    for fernet in encryption_keys():
        try:
            return fernet.decrypt(token).decode()
        except InvalidToken:
            continue
    raise PIIError("no configured PII key decrypts this value")


def normalize_email(value: str) -> str:
    text = unicodedata.normalize("NFKC", value).strip().lower()
    local, at, domain = text.rpartition("@")
    if not at or not local or not domain:
        raise PIIError("not an email address")
    try:
        domain = domain.encode("idna").decode("ascii")
    except UnicodeError as e:
        raise PIIError("email domain is not IDNA-encodable") from e
    return f"{local}@{domain}"


def normalize_phone(value: str, region: str = DEFAULT_PHONE_REGION) -> str:
    """Return E.164 (`+7XXXXXXXXXX`). Only RU is implemented."""
    text = unicodedata.normalize("NFKC", value).strip()
    if not text or not _PHONE_ALLOWED.match(text):
        raise PIIError("not a phone number")
    digits = re.sub(r"\D", "", text)
    country = _PHONE_REGIONS.get(region.upper())
    if country is None:
        raise PIIError(f"phone region {region!r} is not supported")
    if text.startswith("+"):
        return f"+{digits}"
    if digits.startswith("00"):
        return f"+{digits[2:]}"
    if digits.startswith(country) and len(digits) == 11:
        return f"+{digits}"
    if len(digits) == 10 and digits.startswith("9"):
        return f"+{country}{digits}"
    if len(digits) == 11 and digits.startswith("8"):
        return f"+{country}{digits[1:]}"
    raise PIIError("phone number does not match the expected national format")


def normalize_username(value: str) -> str:
    text = unicodedata.normalize("NFKC", value).strip().lower()
    if text.startswith("@"):
        text = text[1:]
    if not text:
        raise PIIError("empty username")
    return text


def normalize_opaque_id(value: str) -> str:
    """Exact native identifier, without username case folding."""
    text = unicodedata.normalize("NFKC", value).strip()
    if not text or len(text) > 512 or any(unicodedata.category(c).startswith("C") for c in text):
        raise PIIError("invalid opaque identifier")
    return text


_NORMALIZERS = {
    "email": normalize_email,
    "phone": normalize_phone,
    "username": normalize_username,
    "opaque_id": normalize_opaque_id,
}


def normalize_pii(kind: str, value: str) -> str:
    normalizer = _NORMALIZERS.get(kind)
    if normalizer is None:
        raise PIIError(f"unknown blind-index kind {kind!r}")
    return normalizer(value)


def blind_index(kind: str, value: str) -> str:
    """Stable exact-match key for a normalized personal value.

    Deterministic by design (that is the point), keyed by `PII_INDEX_KEY`, and
    kind-separated so values from different domains never collide.
    """
    if value is None or not str(value).strip():
        raise PIIError("cannot index an empty value")
    normalized = normalize_pii(kind, str(value))
    message = f"{kind}:{normalized}".encode()
    digest = hmac.new(_index_key(), message, hashlib.sha256).hexdigest()
    return f"{INDEX_PREFIX}{kind}:{digest}"


def is_active_ciphertext(value: str | None) -> bool:
    """True when `value` already decrypts with the active (first) key.

    Rotation uses this to skip rows, which keeps a repeated run idempotent and
    makes `--limit` resumable.
    """
    if not value or not value.startswith(ENCRYPTION_PREFIX):
        return False
    try:
        encryption_keys()[0].decrypt(value[len(ENCRYPTION_PREFIX) :].encode())
    except InvalidToken:
        return False
    return True


def is_blind_index(value: str) -> bool:
    """True for a well-formed index string (used to refuse accidental re-indexing)."""
    if not isinstance(value, str) or not value.startswith(INDEX_PREFIX):
        return False
    kind, _, digest = value[len(INDEX_PREFIX) :].partition(":")
    return kind in INDEX_KINDS and len(digest) == _INDEX_HEX_LENGTH


def redact_pii(text: str) -> str:
    """Remove emails and phone numbers from text headed for logs or errors."""
    if not isinstance(text, str) or not text:
        return text
    for pattern, replacement in _REDACTION_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


class PIIRedactionFilter(logging.Filter):
    """Redact recognizable emails/phones from formatted messages and tracebacks.

    Attach to each output handler that may receive business-adapter logs. This
    is defense in depth, not permission to log personal data or secrets: arbitrary
    structured record attributes and unrecognized values are not sanitized.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            record.msg = redact_pii(record.getMessage())
            record.args = ()
            if record.exc_info:
                record.exc_text = redact_pii("".join(traceback.format_exception(*record.exc_info)))
                record.exc_info = None
            elif record.exc_text:
                record.exc_text = redact_pii(record.exc_text)
            if record.stack_info:
                record.stack_info = redact_pii(record.stack_info)
        except Exception:  # fail closed if interpolation or traceback formatting fails
            record.msg = "[log message unavailable]"
            record.args = ()
            record.exc_info = None
            record.exc_text = None
            record.stack_info = None
        return True
