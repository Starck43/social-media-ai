"""Seal a secret before the row is written, never after it.

SQLAdmin applies the accepted form data to the row *after* ``on_model_change``
(``_queries.py``: hook, then ``_set_attributes``, then ``commit``). That hook is
therefore the last point where plaintext can be replaced with ciphertext before
the INSERT/UPDATE. Encrypting in ``after_model_change`` — the previous contract
in both secret-bearing views — flushed the plaintext to the database first and
then overwrote it.

Two halves of the contract live here:

* :func:`seal_form_secret` rewrites the accepted ``data`` in place;
* :func:`hide_form_secret` blanks the value the edit form would otherwise render,
  so ciphertext never round-trips to the browser.
"""

from __future__ import annotations

from typing import Any

ENCRYPTED_PREFIX = "gAAAAA"


def is_sealed(value: Any) -> bool:
    """True when the value is already ciphertext (Fernet's ``gAAAAA`` marker)."""
    return isinstance(value, str) and value.startswith(ENCRYPTED_PREFIX)


def seal_form_secret(data: dict[str, Any], field: str, stored: Any = None) -> None:
    """Replace a plaintext form value with ciphertext, in place.

    * a blank field keeps the stored secret on an edit and writes NULL on create;
    * an already sealed value is left untouched, so a stale form cannot double
      encrypt;
    * anything else is encrypted here, before SQLAdmin writes the row.
    """
    if field not in data:
        return
    raw = data[field]
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        if stored is None:
            data[field] = None
        else:
            data.pop(field, None)
        return
    if not isinstance(raw, str):
        # A non-string is never a secret; drop it instead of storing garbage.
        data.pop(field, None)
        return
    if is_sealed(raw):
        return
    from app.utils.crypto import encrypt_secret

    data[field] = encrypt_secret(raw.strip())


def hide_form_secret(data: dict[str, Any], field: str) -> None:
    """Blank a stored ciphertext so the edit form never renders it."""
    if field in data:
        data[field] = ""


__all__ = ["ENCRYPTED_PREFIX", "hide_form_secret", "is_sealed", "seal_form_secret"]
