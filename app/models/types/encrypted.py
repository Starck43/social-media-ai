"""SQLAlchemy types for personal data (P13-T02).

Both types keep plaintext out of every place a value is inspected rather than
stored: `repr`, `str` inside error messages, and logs. Encryption happens in
the type, so a model cannot forget it; the descending index column is derived
from the same plaintext by `BlindIndexMixin` helpers below, not by hand.

Headers for a new business table (owned by its module, e.g. P13-T07):

    email_encrypted: Mapped[str | None] = mapped_column(EncryptedText())
    email_index: Mapped[str | None] = mapped_column(String(128), index=True)

and set both through `set_pii_pair(row, "email", value)`, never by assignment.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import String, TypeDecorator

from app.core.pii import PIIError, blind_index, decrypt_pii, encrypt_pii

INDEX_COLUMN_LENGTH = 128


class EncryptedText(TypeDecorator):
    """Text stored as `pii:v1:` ciphertext and handed back as plaintext."""

    impl = String
    cache_ok = True

    def process_bind_param(self, value: Any, dialect: Any) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise PIIError("encrypted personal data must be a string")
        return encrypt_pii(value)

    def process_result_value(self, value: Any, dialect: Any) -> str | None:
        if value is None:
            return None
        return decrypt_pii(value)

    def process_literal_param(self, value: Any, dialect: Any) -> str:
        # Literal rendering is for offline scripts; a plaintext bind here would
        # write personal data into generated SQL and shell history.
        raise PIIError("EncryptedText cannot be rendered as a SQL literal")

    @property
    def python_type(self) -> type:
        return str

    def __repr__(self) -> str:
        return "EncryptedText()"


def index_for(kind: str, value: str | None) -> str | None:
    """Blind index of `value`, or None for empty input."""
    if value is None or not str(value).strip():
        return None
    return blind_index(kind, value)


def set_pii_pair(instance: Any, kind: str, value: str | None, *, encrypted_suffix: str = "_encrypted") -> None:
    """Set `foo_encrypted` and `foo_index` together from one plaintext value.

    Writing them apart is how a row ends up searchable but unreadable (or the
    reverse), so this helper is the documented way to assign personal fields.
    """
    setattr(instance, f"{kind}{encrypted_suffix}", value if value is not None else None)
    setattr(instance, f"{kind}_index", index_for(kind, value))
