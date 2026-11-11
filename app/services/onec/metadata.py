"""1C OData $metadata parser, entity-set classification, and per-connection allowlist (P17-T02).

Parses standard 1C:Enterprise 8.3+ OData EDMX/CSDL `$metadata` XML documents into
an immutable catalog of read-only entity sets (`Catalog_*`, `Document_*`,
`AccumulationRegister_*`, `InformationRegister_*`), their typed fields, and
per-connection administrator allowlists.

Security invariants:
- Rejects XML DTDs, external entities (`<!DOCTYPE`, `<!ENTITY`), processing
  instructions, and oversized payloads (> 2 MiB) before parsing.
- Only entity sets with approved read-only prefixes (`Catalog_`, `Document_`,
  `AccumulationRegister_`, `InformationRegister_`) are admitted into the catalog.
- Per-connection allowlist fails closed: an entity set or field not explicitly
  permitted by the connection's allowlist is rejected with a fixed error code.
"""

from __future__ import annotations

import hashlib
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping

from app.integrations.onec.transport import MAX_BODY_BYTES

ALLOWED_ENTITY_PREFIXES: dict[str, str] = {
    "Catalog_": "catalog",
    "Document_": "document",
    "AccumulationRegister_": "accumulation_register",
    "InformationRegister_": "information_register",
}

# Safe identifier for 1C OData entity sets and properties (Cyrillic, ASCII, digits, underscore).
_IDENTIFIER_RE = re.compile(r"^[A-Za-zА-Яа-яЁё_][A-Za-zА-Яа-яЁё0-9_]{0,127}$")

# Base EDM types supported in read-only queries and filters.
SUPPORTED_EDM_TYPES: frozenset[str] = frozenset(
    {
        "Edm.String",
        "Edm.Guid",
        "Edm.Boolean",
        "Edm.DateTime",
        "Edm.DateTimeOffset",
        "Edm.Decimal",
        "Edm.Double",
        "Edm.Single",
        "Edm.Int16",
        "Edm.Int32",
        "Edm.Int64",
    }
)

# Heuristic + explicit suffix hints for monetary Decimal fields in 1C configurations.
_MONETARY_FIELD_HINTS: tuple[str, ...] = (
    "сумма",
    "цена",
    "стоимость",
    "себестоимость",
    "остатоксумма",
    "оборотсумма",
    "amount",
    "price",
    "total",
    "cost",
    "balanceamount",
)


class OneCMetadataError(ValueError):
    """Fixed error code; raw XML fragments or sensitive names are never echoed."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def is_valid_identifier(name: Any) -> bool:
    """Return True if `name` is a safe 1C OData identifier without control or syntax chars."""
    return isinstance(name, str) and bool(_IDENTIFIER_RE.match(name))


def classify_entity_set_prefix(entity_set_name: str) -> str | None:
    """Return the normalized kind for an allowed 1C entity set prefix, or None."""
    if not is_valid_identifier(entity_set_name):
        return None
    for prefix, kind in ALLOWED_ENTITY_PREFIXES.items():
        if entity_set_name.startswith(prefix) and len(entity_set_name) > len(prefix):
            return kind
    return None


@dataclass(frozen=True)
class OneCFieldSpec:
    """Typed field descriptor extracted from an OData EntityType."""

    name: str
    edm_type: str
    nullable: bool = True
    precision: int | None = None
    scale: int | None = None
    is_key: bool = False
    is_monetary: bool = False
    currency_field: str | None = None

    @property
    def logical_type(self) -> str:
        if self.edm_type == "Edm.Decimal":
            return "decimal"
        if self.edm_type in {"Edm.Int16", "Edm.Int32", "Edm.Int64"}:
            return "integer"
        if self.edm_type in {"Edm.Double", "Edm.Single"}:
            return "numeric"
        if self.edm_type in {"Edm.DateTime", "Edm.DateTimeOffset"}:
            return "datetime"
        if self.edm_type == "Edm.Boolean":
            return "boolean"
        if self.edm_type == "Edm.Guid":
            return "guid"
        return "string"


@dataclass(frozen=True)
class OneCEntitySetSpec:
    """Typed descriptor for a read-only 1C OData EntitySet."""

    name: str
    kind: str
    entity_type_name: str
    fields: Mapping[str, OneCFieldSpec]
    key_fields: tuple[str, ...]
    supports_balance: bool = False
    period_field: str | None = None
    date_field: str | None = None
    counterparty_field: str | None = None
    item_field: str | None = None

    def get_field(self, field_name: str) -> OneCFieldSpec:
        if not is_valid_identifier(field_name) or field_name not in self.fields:
            raise OneCMetadataError("field_not_found")
        return self.fields[field_name]


@dataclass(frozen=True)
class OneCMetadataCatalog:
    """Immutable parsed 1C OData $metadata catalog with its SHA-256 fingerprint."""

    metadata_sha256: str
    namespace: str
    entity_sets: Mapping[str, OneCEntitySetSpec]

    def get_entity_set(self, name: str) -> OneCEntitySetSpec:
        if not is_valid_identifier(name) or name not in self.entity_sets:
            raise OneCMetadataError("entity_not_found")
        return self.entity_sets[name]


def _local_tag(tag: str) -> str:
    """Strip XML namespace URI from an ElementTree tag."""
    if "}" in tag:
        return tag.split("}", 1)[1]
    return tag


def _is_monetary_field(name: str, edm_type: str, scale: int | None, explicit_monetary: frozenset[str]) -> bool:
    if edm_type != "Edm.Decimal":
        return False
    if name in explicit_monetary:
        return True
    lowered = name.lower()
    if any(hint in lowered for hint in _MONETARY_FIELD_HINTS):
        return True
    return scale is not None and scale in (2, 4) and ("сумма" in lowered or "цена" in lowered)


def _detect_currency_field(field_names: set[str]) -> str | None:
    for candidate in ("Валюта_Key", "ВалютаДокумента_Key", "Currency_Key", "Валюта", "Currency"):
        if candidate in field_names:
            return candidate
    return None


def _detect_special_field(fields: Mapping[str, OneCFieldSpec], candidates: tuple[str, ...]) -> str | None:
    for candidate in candidates:
        if candidate in fields:
            return candidate
    return None


def _check_safe_xml_bytes(raw_bytes: bytes) -> str:
    if not isinstance(raw_bytes, (bytes, bytearray)) or not raw_bytes:
        raise OneCMetadataError("empty_metadata")
    if len(raw_bytes) > MAX_BODY_BYTES:
        raise OneCMetadataError("metadata_too_large")
    try:
        text = bytes(raw_bytes).decode("utf-8-sig")
    except UnicodeDecodeError:
        raise OneCMetadataError("invalid_metadata_xml") from None

    upper_head = text[:8192].upper()
    if "<!DOCTYPE" in upper_head or "<!ENTITY" in upper_head or "<?XML-STYLESHEET" in upper_head:
        raise OneCMetadataError("unsafe_xml_forbidden")
    return text


def parse_odata_metadata_xml(
    xml_payload: bytes | str,
    *,
    monetary_fields_override: Mapping[str, frozenset[str]] | None = None,
) -> OneCMetadataCatalog:
    """Parse 1C OData `$metadata` XML into a validated `OneCMetadataCatalog`."""
    if isinstance(xml_payload, str):
        raw_bytes = xml_payload.encode("utf-8")
    elif isinstance(xml_payload, (bytes, bytearray)):
        raw_bytes = bytes(xml_payload)
    else:
        raise OneCMetadataError("invalid_metadata_xml")

    xml_text = _check_safe_xml_bytes(raw_bytes)
    metadata_sha256 = hashlib.sha256(raw_bytes).hexdigest()

    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        raise OneCMetadataError("invalid_metadata_xml") from None

    if _local_tag(root.tag) != "Edmx":
        raise OneCMetadataError("invalid_metadata_xml")

    overrides = monetary_fields_override or {}
    schemas = [elem for elem in root.iter() if _local_tag(elem.tag) == "Schema"]
    if not schemas:
        raise OneCMetadataError("invalid_metadata_xml")

    namespace = "StandardODATA"
    entity_types: dict[str, tuple[tuple[str, ...], dict[str, dict[str, Any]]]] = {}
    raw_entity_sets: list[tuple[str, str]] = []
    balance_capable_sets: set[str] = set()

    for schema in schemas:
        ns = schema.attrib.get("Namespace", "").strip()
        if ns:
            namespace = ns

        for child in schema:
            tag = _local_tag(child.tag)
            if tag == "EntityType":
                type_name = child.attrib.get("Name", "").strip()
                if not is_valid_identifier(type_name):
                    continue
                keys: list[str] = []
                props: dict[str, dict[str, Any]] = {}
                for sub in child:
                    sub_tag = _local_tag(sub.tag)
                    if sub_tag == "Key":
                        for pref in sub:
                            if _local_tag(pref.tag) == "PropertyRef":
                                kname = pref.attrib.get("Name", "").strip()
                                if is_valid_identifier(kname):
                                    keys.append(kname)
                    elif sub_tag == "Property":
                        pname = sub.attrib.get("Name", "").strip()
                        ptype = sub.attrib.get("Type", "").strip()
                        if not is_valid_identifier(pname) or ptype not in SUPPORTED_EDM_TYPES:
                            continue
                        nullable = sub.attrib.get("Nullable", "true").strip().lower() != "false"
                        precision_raw = sub.attrib.get("Precision")
                        scale_raw = sub.attrib.get("Scale")
                        try:
                            precision = int(precision_raw) if precision_raw is not None else None
                            scale = int(scale_raw) if scale_raw is not None else None
                        except ValueError:
                            precision, scale = None, None
                        props[pname] = {
                            "edm_type": ptype,
                            "nullable": nullable,
                            "precision": precision,
                            "scale": scale,
                        }
                entity_types[type_name] = (tuple(keys), props)

            elif tag == "EntityContainer":
                for sub in child:
                    sub_tag = _local_tag(sub.tag)
                    if sub_tag == "EntitySet":
                        set_name = sub.attrib.get("Name", "").strip()
                        set_type = sub.attrib.get("EntityType", "").strip()
                        if "." in set_type:
                            set_type = set_type.rsplit(".", 1)[-1]
                        if is_valid_identifier(set_name) and is_valid_identifier(set_type):
                            raw_entity_sets.append((set_name, set_type))
                    elif sub_tag == "FunctionImport":
                        fn_name = sub.attrib.get("Name", "").strip()
                        entity_set_attr = sub.attrib.get("EntitySet", "").strip()
                        if fn_name in {"Balance", "Turnovers", "BalanceAndTurnovers", "SliceLast", "SliceFirst"}:
                            if is_valid_identifier(entity_set_attr):
                                balance_capable_sets.add(entity_set_attr)

    catalog_sets: dict[str, OneCEntitySetSpec] = {}
    for set_name, short_type_name in raw_entity_sets:
        # Strip suffixes like _RecordType when matching 1C register entity types if direct match is absent.
        kind = classify_entity_set_prefix(set_name)
        if kind is None:
            continue

        resolved_type_name = short_type_name
        if resolved_type_name not in entity_types and set_name in entity_types:
            resolved_type_name = set_name
        if resolved_type_name not in entity_types:
            continue

        key_tuple, raw_props = entity_types[resolved_type_name]
        if not raw_props:
            continue

        prop_names = set(raw_props)
        currency_field = _detect_currency_field(prop_names)
        explicit_monetary = overrides.get(set_name, frozenset())
        key_set = set(key_tuple)

        field_map: dict[str, OneCFieldSpec] = {}
        for pname, pinfo in raw_props.items():
            is_mon = _is_monetary_field(pname, pinfo["edm_type"], pinfo["scale"], explicit_monetary)
            field_map[pname] = OneCFieldSpec(
                name=pname,
                edm_type=pinfo["edm_type"],
                nullable=pinfo["nullable"],
                precision=pinfo["precision"],
                scale=pinfo["scale"],
                is_key=pname in key_set,
                is_monetary=is_mon,
                currency_field=currency_field if is_mon else None,
            )

        supports_balance = (
            kind in {"accumulation_register", "information_register"}
            or set_name in balance_capable_sets
            or f"{set_name}_Balance" in entity_types
        )
        period_field = _detect_special_field(field_map, ("Period", "Период"))
        date_field = _detect_special_field(field_map, ("Date", "Дата", "Period", "Период"))
        counterparty_field = _detect_special_field(
            field_map,
            ("Контрагент_Key", "Counterparty_Key", "Контрагент", "Counterparty", "Поставщик_Key", "Покупатель_Key"),
        )
        item_field = _detect_special_field(
            field_map,
            ("Номенклатура_Key", "Item_Key", "Товар_Key", "Номенклатура", "Item"),
        )

        catalog_sets[set_name] = OneCEntitySetSpec(
            name=set_name,
            kind=kind,
            entity_type_name=resolved_type_name,
            fields=MappingProxyType(field_map),
            key_fields=key_tuple or (("Ref_Key",) if "Ref_Key" in field_map else ()),
            supports_balance=supports_balance,
            period_field=period_field,
            date_field=date_field,
            counterparty_field=counterparty_field,
            item_field=item_field,
        )

    if not catalog_sets:
        raise OneCMetadataError("no_supported_entity_sets")

    return OneCMetadataCatalog(
        metadata_sha256=metadata_sha256,
        namespace=namespace,
        entity_sets=MappingProxyType(catalog_sets),
    )


def extract_connection_allowlist(config: Mapping[str, Any]) -> tuple[frozenset[str], Mapping[str, frozenset[str]]]:
    """Extract and validate per-connection administrator allowlist from `WorkspaceConnection.config`.

    Fails closed (returns empty allowlist) if `allowed_entity_sets` is missing or empty,
    and rejects malformed configuration structures with `OneCMetadataError("invalid_allowlist")`.
    """
    if not isinstance(config, Mapping):
        raise OneCMetadataError("invalid_allowlist")

    raw_sets = config.get("allowed_entity_sets")
    if raw_sets is None:
        return frozenset(), MappingProxyType({})
    if not isinstance(raw_sets, (list, tuple, set, frozenset)):
        raise OneCMetadataError("invalid_allowlist")

    validated_sets: set[str] = set()
    for item in raw_sets:
        if not isinstance(item, str) or classify_entity_set_prefix(item) is None:
            raise OneCMetadataError("invalid_allowlist")
        validated_sets.add(item)

    raw_fields = config.get("allowed_fields")
    validated_fields: dict[str, frozenset[str]] = {}
    if raw_fields is not None:
        if not isinstance(raw_fields, Mapping):
            raise OneCMetadataError("invalid_allowlist")
        for entity_name, field_list in raw_fields.items():
            if (
                not isinstance(entity_name, str)
                or entity_name not in validated_sets
                or not isinstance(field_list, (list, tuple, set, frozenset))
            ):
                raise OneCMetadataError("invalid_allowlist")
            fset: set[str] = set()
            for fname in field_list:
                if not is_valid_identifier(fname):
                    raise OneCMetadataError("invalid_allowlist")
                fset.add(fname)
            validated_fields[entity_name] = frozenset(fset)

    return frozenset(validated_sets), MappingProxyType(validated_fields)


def validate_entity_access(
    catalog: OneCMetadataCatalog,
    entity_set_name: str,
    requested_fields: tuple[str, ...] | list[str],
    *,
    allowed_entity_sets: frozenset[str],
    allowed_fields_by_entity: Mapping[str, frozenset[str]] | None = None,
    expected_metadata_sha256: str | None = None,
) -> tuple[OneCEntitySetSpec, tuple[OneCFieldSpec, ...]]:
    """Verify that `entity_set_name` and `requested_fields` are allowlisted and present in `$metadata`."""
    if expected_metadata_sha256 is not None and catalog.metadata_sha256 != expected_metadata_sha256:
        raise OneCMetadataError("metadata_hash_mismatch")

    if not is_valid_identifier(entity_set_name) or classify_entity_set_prefix(entity_set_name) is None:
        raise OneCMetadataError("entity_not_allowed")

    if not allowed_entity_sets or entity_set_name not in allowed_entity_sets:
        raise OneCMetadataError("entity_not_allowed")

    if entity_set_name not in catalog.entity_sets:
        raise OneCMetadataError("entity_not_found")

    entity_spec = catalog.entity_sets[entity_set_name]

    if not isinstance(requested_fields, (tuple, list)) or not requested_fields:
        raise OneCMetadataError("invalid_fields")

    per_entity_allowed = (allowed_fields_by_entity or {}).get(entity_set_name)
    resolved_fields: list[OneCFieldSpec] = []
    seen: set[str] = set()
    for fname in requested_fields:
        if not is_valid_identifier(fname):
            raise OneCMetadataError("invalid_fields")
        if per_entity_allowed is not None and fname not in per_entity_allowed:
            raise OneCMetadataError("field_not_allowed")
        if fname not in entity_spec.fields:
            raise OneCMetadataError("field_not_found")
        if fname not in seen:
            seen.add(fname)
            resolved_fields.append(entity_spec.fields[fname])

    return entity_spec, tuple(resolved_fields)
