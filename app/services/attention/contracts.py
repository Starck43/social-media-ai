"""Shared attention-item interface (P20-T02); no persistence or provider bodies."""

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Literal

Category = Literal["needs_attention", "can_wait", "uncertain"]


@dataclass(frozen=True)
class AttentionAction:
    kind: str
    label: str
    href: str

    def __post_init__(self):
        if not self.href.startswith("/app/") or any(c in self.href for c in "\\\r\n"):
            raise ValueError("attention_action_requires_internal_route")


@dataclass(frozen=True)
class AttentionItem:
    provider: str
    kind: str
    authoritative_ref: str
    title: str
    reason: str
    category: Category
    recommended_priority: int
    priority_basis: str
    freshness: datetime
    allowed_actions: tuple[AttentionAction, ...]
    data_boundary: Literal["local_only", "workspace"]
    owner_state: str = "available"
    due_at: datetime | None = None
    expires_at: datetime | None = None

    def __post_init__(self):
        if self.category not in ("needs_attention", "can_wait", "uncertain"):
            raise ValueError("attention_category_invalid")
        if type(self.recommended_priority) is not int or not 0 <= self.recommended_priority <= 100:
            raise ValueError("attention_priority_invalid")
        if not self.provider or not self.kind or not self.authoritative_ref or not self.reason:
            raise ValueError("attention_identity_and_reason_required")
        if len(self.title) > 160 or len(self.reason) > 240:
            raise ValueError("attention_text_exceeds_limit")
        if self.data_boundary not in ("local_only", "workspace"):
            raise ValueError("attention_boundary_invalid")
        for stamp in (self.freshness, self.due_at, self.expires_at):
            if stamp is not None and (stamp.tzinfo is None or stamp.utcoffset() is None):
                raise ValueError("attention_aware_time_required")

    def to_dict(self) -> dict:
        result = asdict(self)
        for key in ("freshness", "due_at", "expires_at"):
            value = result[key]
            result[key] = value.isoformat() if value else None
        result["item_key"] = f"{self.provider}:{self.authoritative_ref}"
        result["is_model_fact"] = False
        return result
