"""1C read-only metadata catalog and typed OData query extraction (P17-T02).

Builds on the P17-T01 connection, host-allowlist, secret-vault, and IP-pinned
HTTPS transport contracts without enabling any write operation.
"""

from app.services.onec.metadata import (
    ALLOWED_ENTITY_PREFIXES,
    OneCEntitySetSpec,
    OneCFieldSpec,
    OneCMetadataCatalog,
    OneCMetadataError,
    parse_odata_metadata_xml,
    validate_entity_access,
)
from app.services.onec.query import (
    MAX_ROWS_PER_REQUEST,
    TIMEOUT_SECONDS,
    MonetaryValue,
    OneCFilterPredicate,
    OneCQueryCache,
    OneCQueryError,
    OneCQueryHistoryMetadata,
    OneCQueryPlan,
    OneCQueryResult,
    OneCReadQueryService,
    OneCSortSpec,
    build_odata_query_target,
    decode_page_token,
    encode_page_token,
)

__all__ = [
    "ALLOWED_ENTITY_PREFIXES",
    "MAX_ROWS_PER_REQUEST",
    "MonetaryValue",
    "OneCEntitySetSpec",
    "OneCFieldSpec",
    "OneCFilterPredicate",
    "OneCMetadataCatalog",
    "OneCMetadataError",
    "OneCQueryCache",
    "OneCQueryError",
    "OneCQueryHistoryMetadata",
    "OneCQueryPlan",
    "OneCQueryResult",
    "OneCReadQueryService",
    "OneCSortSpec",
    "TIMEOUT_SECONDS",
    "build_odata_query_target",
    "decode_page_token",
    "encode_page_token",
    "parse_odata_metadata_xml",
    "validate_entity_access",
]
