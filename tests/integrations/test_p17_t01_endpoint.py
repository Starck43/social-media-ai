"""Offline 1C endpoint contract; synthetic DNS only, never a real provider."""

import socket

import pytest

from app.integrations.onec.endpoint import OneCEndpointError, resolve_endpoint, validate_endpoint

ALLOW = frozenset({"erp.example.com"})


def answer(address):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 443))]


def test_valid_endpoints_build_only_fixed_health_paths():
    odata = validate_endpoint("https://ERP.example.com/base/", "odata_standard", ALLOW)
    service = validate_endpoint("https://erp.example.com/custom/service", "http_service", ALLOW)
    assert odata.health_path == "/base/odata/standard.odata/$metadata"
    assert service.health_path == "/custom/service/ping"
    assert odata.host == "erp.example.com"
    assert odata.port == 443


@pytest.mark.parametrize(
    "url",
    [
        "http://erp.example.com/base",
        "https://user:pass@erp.example.com/base",
        "https://127.0.0.1/base",
        "https://erp.example.com/base?token=secret",
        "https://erp.example.com/base#x",
        "https://erp.example.com/a/../b",
        "https://erp.example.com/a/%2e%2e/b",
        "https://erp.example.com/a%5cb",
        "https://erp.example.com/a%252e%252e/b",
        "https://erp.example.com/a%2fb",
        "https://erp.example.com:99999/",
        "https://erp.example.com:0/",
        "https://erp.example.com/line\nfeed",
    ],
)
def test_unsafe_url_is_rejected(url):
    with pytest.raises(OneCEndpointError) as exc:
        validate_endpoint(url, "odata_standard", ALLOW)
    assert exc.value.code == "invalid_endpoint"
    assert "secret" not in str(exc.value)


def test_exact_admin_allowlist_is_required():
    with pytest.raises(OneCEndpointError, match="host_not_allowed"):
        validate_endpoint("https://evil-erp.example.com", "odata_standard", ALLOW)
    with pytest.raises(OneCEndpointError, match="invalid_interface"):
        validate_endpoint("https://erp.example.com", "unsupported", ALLOW)


@pytest.mark.parametrize("ip", ["127.0.0.1", "10.0.0.1", "169.254.169.254", "::1", "fc00::1", "224.0.0.1", "ff02::1"])
def test_non_public_dns_rejected_even_when_mixed_with_public(ip):
    ep = validate_endpoint("https://erp.example.com", "odata_standard", ALLOW)
    with pytest.raises(OneCEndpointError):
        resolve_endpoint(ep, resolver=lambda host, port: answer("8.8.8.8") + answer(ip))


def test_dns_is_rechecked_each_attempt_to_reject_rebinding():
    ep = validate_endpoint("https://erp.example.com", "odata_standard", ALLOW)
    responses = iter([answer("8.8.8.8"), answer("127.0.0.1")])
    resolve = lambda host, port: next(responses)
    assert resolve_endpoint(ep, resolver=resolve).addresses == ("8.8.8.8",)
    with pytest.raises(OneCEndpointError):
        resolve_endpoint(ep, resolver=resolve)
