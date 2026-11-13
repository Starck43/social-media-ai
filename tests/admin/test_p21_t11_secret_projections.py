"""Source-only admin and legacy API omission guards; no DB/provider traffic."""

from types import SimpleNamespace as NS

from app.admin.view_modules.llm_catalog import LLMProviderAdmin
from app.api.v1.endpoints.llm_providers import _provider_response
from app.services.ai.provider_egress import endpoint_path_metadata, provider_url_metadata

__source_only__ = True


def test_admin_details_export_allowlists_exclude_secret_material():
    secret_names = {"encrypted_api_key", "auth_header", "managed_credential_ref"}
    assert not secret_names.intersection(LLMProviderAdmin.column_details_list)
    assert not secret_names.intersection(LLMProviderAdmin.column_export_list)


def test_legacy_api_and_admin_do_not_echo_credentialed_endpoint():
    provider = NS(
        id=1,
        owner_user_id=2,
        uid="synthetic",
        name="Synthetic",
        description=None,
        api_format="openai",
        base_url="https://user:SYNTHETIC_KEY@example.org/v1?api_key=SYNTHETIC_KEY",
        auth_header="Bearer SYNTHETIC_KEY",
        encrypted_api_key="SYNTHETIC_CIPHERTEXT",
        is_active=True,
        created_at=None,
        updated_at=None,
    )
    response = _provider_response(provider).model_dump()
    assert "SYNTHETIC_KEY" not in repr(response)
    assert "SYNTHETIC_CIPHERTEXT" not in repr(response)
    assert response["auth_header"] is None
    assert LLMProviderAdmin.column_formatters["base_url"](provider, None) == ""


def test_safe_metadata_is_shape_only_without_dns():
    assert provider_url_metadata("https://example.org/v1") == "https://example.org/v1"
    assert provider_url_metadata("https://example.org/v1#SYNTHETIC_KEY") == ""
    assert endpoint_path_metadata("/chat/completions") == "/chat/completions"
    assert endpoint_path_metadata("/chat?api_key=SYNTHETIC_KEY") is None
