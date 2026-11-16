"""Bounded, revision-checked cache of grant-eligible model metadata (P21-T13 B).

The cache holds **metadata only**: immutable, secret-free DTOs that a caller may
render or price against. It never holds an authorization decision, a holder, a
catalog grant or a credential. Every `resolve` re-reads storage independently, so
a cache hit can never keep a downgraded plan or a revoked grant usable.

Invalidation is content based, not size based. A revision entry is the SHA-256 of
the *safe projection* of the answer: every returned metadata field plus the grant
identity, activity, priority and default flag. A stored `max(id)` or row count
would miss an in-place edit (new price, renamed model, replaced default), so the
hash is over values. Any failed query drops the entry and denies instead of
serving the previous value.

Bounding is a plain `OrderedDict` LRU; the module reads and writes no storage and
imports no provider, cipher or tenancy service.
"""

from __future__ import annotations

import hashlib
import json
from collections import OrderedDict
from dataclasses import dataclass, fields
from decimal import Decimal
from typing import Any, Iterable

#: Module-level marker used by the task's source-only test registry.
__source_only__ = True


class CatalogMetadataError(ValueError):
    """A safe projection could not be described; callers treat it as a denial."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True)
class CatalogModelMetadata:
    """One eligible model, safe for every projection (no key, ref or endpoint).

    `id` is the numeric `llm_models.id` row identity a consumer re-loads through
    typed ORM by, never the string API `model_id`. `provider_format` is the
    provider's `api_format` adapter selector, which is safe. `capabilities` is a
    sorted tuple, prices are exact `Decimal` or `None` (undefined, never inferred
    free), and `is_default`/`priority` describe the managed plan default when
    `funding_class == "platform"` and the model's own personal flag (with
    `priority=None`) otherwise.
    """

    id: int
    name: str
    model_id: str
    provider_id: int
    provider_name: str
    provider_uid: str
    provider_format: str
    catalog_id: int
    funding_class: str
    capabilities: tuple[str, ...]
    max_tokens: int
    max_output_tokens: int | None
    context_window_tokens: int | None
    default_temperature: float | None
    pricing_state: str | None
    input_price: Decimal | None
    output_price: Decimal | None
    pricing_token_unit: int
    is_active: bool
    is_default: bool
    priority: int | None

    def safe_dict(self) -> dict[str, Any]:
        """JSON-safe projection: the only shape hashed, cached or serialized."""
        return {
            "id": self.id,
            "name": self.name,
            "model_id": self.model_id,
            "provider_id": self.provider_id,
            "provider_name": self.provider_name,
            "provider_uid": self.provider_uid,
            "provider_format": self.provider_format,
            "catalog_id": self.catalog_id,
            "funding_class": self.funding_class,
            "capabilities": list(self.capabilities),
            "max_tokens": self.max_tokens,
            "max_output_tokens": self.max_output_tokens,
            "context_window_tokens": self.context_window_tokens,
            "default_temperature": self.default_temperature,
            "pricing_state": self.pricing_state,
            "input_price": None if self.input_price is None else str(self.input_price),
            "output_price": None if self.output_price is None else str(self.output_price),
            "pricing_token_unit": self.pricing_token_unit,
            "is_active": self.is_active,
            "is_default": self.is_default,
            "priority": self.priority,
        }


#: The fields every metadata object must carry; the DTO set is closed.
METADATA_FIELDS: frozenset[str] = frozenset(f.name for f in fields(CatalogModelMetadata))


def _revision_part(value: Any) -> Any:
    if isinstance(value, Decimal):
        # `str` keeps exact scale: "1.20" and "1.2" are different stored values.
        return str(value)
    if isinstance(value, tuple):
        return ["tuple", [_revision_part(item) for item in value]]
    if isinstance(value, list):
        return [_revision_part(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _revision_part(item) for key, item in value.items()}
    if value is None or type(value) in (bool, int, float, str):
        return [type(value).__name__, value]
    raise CatalogMetadataError("unsafe_revision_value")


def safe_revision(payload: Any) -> str:
    """SHA-256 of a safe projection; `Decimal`, `bool` and `None` stay distinct."""
    try:
        encoded = json.dumps(_revision_part(payload), sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError):
        raise CatalogMetadataError("unsafe_revision_value") from None
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class CatalogMetadataAnswer:
    """One cache value: the safe DTO tuple and its own content revision."""

    revision: str
    metadata: tuple[CatalogModelMetadata, ...]


def build_answer(
    metadata: Iterable[CatalogModelMetadata],
    *,
    grants: Iterable[Any] = (),
) -> CatalogMetadataAnswer:
    """Build the immutable value; `grants` contribute identity/state to the hash.

    `grants` are safe mappings already produced by the resolver
    (`grant_id`, `is_active`, `priority`, `is_default`); their identity is part
    of the revision so retiring or re-ordering one grant invalidates the entry
    even when the returned metadata happens to be unchanged.
    """
    items = tuple(metadata)
    projection = {
        "metadata": [item.safe_dict() for item in items],
        "grants": [_revision_part(dict(grant)) for grant in grants],
    }
    return CatalogMetadataAnswer(revision=safe_revision(projection), metadata=items)


#: The cache key: everything that decides the answer but is not the answer.
CacheKey = tuple[Any, ...]


class MetadataRevisionCache:
    """Bounded LRU of immutable answers; never stores a decision or an error."""

    def __init__(self, *, max_entries: int = 64):
        if type(max_entries) is not int or max_entries <= 0:
            raise CatalogMetadataError("invalid_cache_size")
        self._max_entries = max_entries
        self._entries: OrderedDict[CacheKey, CatalogMetadataAnswer] = OrderedDict()

    def __len__(self) -> int:
        return len(self._entries)

    def get(self, key: CacheKey) -> CatalogMetadataAnswer | None:
        answer = self._entries.get(key)
        if answer is None:
            return None
        self._entries.move_to_end(key)
        return answer

    def store(self, key: CacheKey, answer: CatalogMetadataAnswer) -> None:
        if not isinstance(answer, CatalogMetadataAnswer):
            raise CatalogMetadataError("invalid_cache_value")
        self._entries[key] = answer
        self._entries.move_to_end(key)
        while len(self._entries) > self._max_entries:
            self._entries.popitem(last=False)

    def invalidate(self, key: CacheKey) -> None:
        self._entries.pop(key, None)

    def clear(self) -> None:
        self._entries.clear()
