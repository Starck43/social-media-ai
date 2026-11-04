"""CRM services package (Bitrix24 incremental sync and contact cards, P14-T02..T03)."""

from .cards import (
    PORTAL_ENTITY_PATHS,
    CrmContactCardService,
    build_portal_entity_url,
    crm_contact_card_service,
)
from .sync import (
    DEFAULT_ANNIVERSARY_FIELD,
    DEFAULT_BIRTHDAY_FIELD,
    ENTITY_IDENTITY_KINDS,
    ENTITY_LIST_METHODS,
    PAGE_SIZE,
    SYNC_INTERVAL_SECONDS,
    BitrixSyncService,
    bitrix_sync_service,
    build_entity_title,
    extract_allowlisted_custom_fields,
    extract_multifield_values,
    get_field_mapping_config,
    parse_bitrix_timestamp,
    parse_crm_date_field,
)

__all__ = [
    "BitrixSyncService",
    "CrmContactCardService",
    "DEFAULT_ANNIVERSARY_FIELD",
    "DEFAULT_BIRTHDAY_FIELD",
    "ENTITY_IDENTITY_KINDS",
    "ENTITY_LIST_METHODS",
    "PAGE_SIZE",
    "PORTAL_ENTITY_PATHS",
    "SYNC_INTERVAL_SECONDS",
    "bitrix_sync_service",
    "build_entity_title",
    "build_portal_entity_url",
    "crm_contact_card_service",
    "extract_allowlisted_custom_fields",
    "extract_multifield_values",
    "get_field_mapping_config",
    "parse_bitrix_timestamp",
    "parse_crm_date_field",
]
