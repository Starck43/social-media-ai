"""Validate trusted reviewed snapshots; no evidence inference or activation."""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from typing import Any, Mapping

from .budget_admission import exact_usd

DAY_FIELDS = (
    "tenant_id",
    "utc_day",
    "settled_usd",
    "reserved_usd",
    "undefined_count",
    "unbounded_count",
    "frozen",
    "baseline_version",
)
REVIEW_FIELDS = (
    "reviewed_by",
    "review_ref",
    "evidence_sha256",
    "cutoff_at",
    "reviewed_at",
    "fence_ref",
    "fence_version",
)
BASELINE_FIELDS = (*DAY_FIELDS, *REVIEW_FIELDS)
FENCE_VERSION = "quiesced-accounted-v1"


class BudgetBaselineConflict(RuntimeError):
    """Day already binds different or unreviewed evidence; never overwrite it."""


class BudgetBaselineUnavailable(RuntimeError):
    """Required provenance storage/guards are unavailable."""


def validate_baseline(record: Mapping[str, Any]) -> dict[str, Any]:
    """All values are explicit. A valid packet is an attestation, not proof.

    No float adapters, legacy projections, defaults or auto-unfreeze. References
    identify retained redacted review/fence evidence, not raw provider payloads.
    """
    if not isinstance(record, Mapping) or set(record) != set(BASELINE_FIELDS):
        raise ValueError("Expected the complete reviewed baseline packet only")
    value = dict(record)
    if type(value["tenant_id"]) is not int or not 0 < value["tenant_id"] <= 2147483647:
        raise ValueError("Expected an explicit payer identity")
    day = value["utc_day"]
    if type(day) is not date or day == date.max:
        raise ValueError("Expected a representable UTC date, not a datetime")
    for field in ("settled_usd", "reserved_usd"):
        value[field] = exact_usd(value[field])
    for field in ("undefined_count", "unbounded_count"):
        if type(value[field]) is not int or not 0 <= value[field] <= 2147483647:
            raise ValueError("Expected an explicit nonnegative int32 liability count")
    if value["frozen"] is not True:
        raise ValueError("Baseline provisioning is preparatory and requires initial freeze")
    for field in ("baseline_version", "reviewed_by", "review_ref", "fence_ref"):
        if type(value[field]) is not str or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.:-]{0,63}", value[field]):
            raise ValueError("Expected a bounded opaque review identifier")
    if type(value["evidence_sha256"]) is not str or not re.fullmatch(r"[0-9a-f]{64}", value["evidence_sha256"]):
        raise ValueError("Expected a canonical redacted evidence SHA256")
    if type(value["fence_version"]) is not str or value["fence_version"] != FENCE_VERSION:
        raise ValueError("Expected explicit quiesced/accounted fence attestation")
    for field in ("cutoff_at", "reviewed_at"):
        stamp = value[field]
        if type(stamp) is not datetime or stamp.utcoffset() != timedelta(0):
            raise ValueError("Expected an aware UTC timestamp")
        value[field] = stamp.astimezone(timezone.utc)
    start = datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc)
    if not start <= value["cutoff_at"] <= start + timedelta(days=1):
        raise ValueError("Cutoff must cover the selected UTC day prefix")
    if not value["cutoff_at"] <= value["reviewed_at"] <= datetime.now(timezone.utc):
        raise ValueError("Review must follow cutoff and cannot be in the future")
    return value
