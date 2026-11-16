"""Explicit default-off wiring; no keys, CA or TLS identity are fabricated."""

from functools import lru_cache

from .crypto import ConnectorAuthority
from .gateway import ConnectorGateway
from .wire import ConnectorUnavailable


@lru_cache(maxsize=1)
def get_gateway():
    from app.core.config import settings

    from .store import LocalConnectorStore

    if not settings.LOCAL_CONNECTOR_ENABLED or not settings.LOCAL_CONNECTOR_SECURITY_REVIEWED:
        raise ConnectorUnavailable()
    try:
        authority = ConnectorAuthority(
            ca_pem=settings.LOCAL_CONNECTOR_CA_CERT.encode(),
            ca_key_pem=settings.LOCAL_CONNECTOR_CA_KEY.get_secret_value().encode(),
            signing_key_pem=settings.LOCAL_CONNECTOR_SIGNING_KEY.get_secret_value().encode(),
            pairing_key=settings.LOCAL_CONNECTOR_PAIRING_KEY.get_secret_value().encode(),
            key_id=settings.LOCAL_CONNECTOR_KEY_ID,
        )
    except Exception:
        raise ConnectorUnavailable() from None
    return ConnectorGateway(LocalConnectorStore(authority), authority)
