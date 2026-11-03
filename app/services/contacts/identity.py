"""Closed identity kinds and one normalized encrypted/index pair."""

from types import SimpleNamespace

from app.core.pii import PIIError, normalize_pii
from app.models.contacts.core import IDENTITY_KINDS
from app.models.types.encrypted import index_for, set_pii_pair


def pii_kind(kind):
    if kind not in IDENTITY_KINDS:
        raise ValueError("Unknown contact identity kind")
    return {"email": "email", "phone": "phone", "telegram_username": "username"}.get(kind, "opaque_id")


def normalized_identity(kind, value):
    if not isinstance(value, str) or not value.strip() or len(value) > 512:
        raise PIIError("Invalid contact identifier")
    normalized = normalize_pii(pii_kind(kind), value)
    if kind in {"telegram_id", "max_id", "bitrix24_contact", "bitrix24_company", "bitrix24_lead"}:
        if not normalized.isascii() or not normalized.isdigit() or int(normalized) <= 0:
            raise PIIError("Invalid native contact identifier")
        normalized = str(int(normalized))
    return normalized


def identity_values(kind, value):
    normalized = normalized_identity(kind, value)
    target = SimpleNamespace()
    field = pii_kind(kind)
    set_pii_pair(target, field, normalized)
    return {"value_encrypted": getattr(target, f"{field}_encrypted"), "blind_index": getattr(target, f"{field}_index")}


def identity_index(kind, value):
    return index_for(pii_kind(kind), normalized_identity(kind, value))
