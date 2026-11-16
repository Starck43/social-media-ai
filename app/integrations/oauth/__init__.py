"""Provider-neutral OAuth 2.0 workspace-connection framework (P13-T08)."""

from .credentials import (
    CREDENTIALS_UNAVAILABLE,
    INJECTED_APP_CREDENTIALS,
    resolve_app_credentials,
)
from .network import OAuthNetworkError, default_dns_resolver, post_oauth_form, validate_oauth_url
from .service import (
    RUNTIME_OAUTH_CLIENT_CREDENTIALS,
    RUNTIME_OAUTH_METHODS,
    OAuthFlowError,
    WorkspaceOAuthService,
    decode_secret_bundle,
    encode_secret_bundle,
    resolve_client_credentials,
    resolve_oauth_method,
    sanitize_connection_view,
    workspace_oauth,
)
from .state import (
    DEFAULT_STATE_TTL_SECONDS,
    OAuthPendingState,
    OAuthStateError,
    OAuthStateStore,
    generate_pkce_pair,
    oauth_state_store,
)

__all__ = [
    "CREDENTIALS_UNAVAILABLE",
    "DEFAULT_STATE_TTL_SECONDS",
    "INJECTED_APP_CREDENTIALS",
    "OAuthFlowError",
    "OAuthNetworkError",
    "OAuthPendingState",
    "OAuthStateError",
    "OAuthStateStore",
    "RUNTIME_OAUTH_CLIENT_CREDENTIALS",
    "RUNTIME_OAUTH_METHODS",
    "WorkspaceOAuthService",
    "decode_secret_bundle",
    "default_dns_resolver",
    "encode_secret_bundle",
    "generate_pkce_pair",
    "oauth_state_store",
    "post_oauth_form",
    "resolve_app_credentials",
    "resolve_client_credentials",
    "resolve_oauth_method",
    "sanitize_connection_view",
    "validate_oauth_url",
    "workspace_oauth",
]
