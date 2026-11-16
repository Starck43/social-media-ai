"""Native target syntax/lifetime; synthetic .invalid URLs, no provider or DB."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from app.integrations.file_workspace.original_open import (
    OPEN_TTL,
    NativeLinkPolicy,
    NativeOriginalTarget,
    OriginalOpenContext,
    OriginalOpenUnavailable,
    context_live,
    validate_target,
)

__source_only__ = True
POLICY = NativeLinkPolicy("native.example.invalid", "/client/files/")
URL = "https://native.example.invalid/client/files/opaque-file"


def sample():
    now = datetime.now(timezone.utc)
    context = OriginalOpenContext(1, 2, 3, 4, "disk:/work/private.txt", "root", "policy", "credential", 1, now)
    return context, NativeOriginalTarget(context, URL, now, now + OPEN_TTL)


def test_current_target_does_not_claim_content_version_or_browser_success():
    context, target = sample()
    result = validate_target(context, target, POLICY)
    assert result.url == URL
    assert result.version_scope == "current" and result.content_version_known is False
    assert URL not in repr(result) and URL not in repr(target)
    assert context.path not in repr(context)
    assert "credential" not in repr(context)
    assert context_live(context)


@pytest.mark.parametrize(
    "url",
    [
        "http://native.example.invalid/client/files/a",
        "https://native.example.invalid.evil.invalid/client/files/a",
        "https://native.example.invalid@evil.invalid/client/files/a",
        "https://user:password@native.example.invalid/client/files/a",
        "https://native.example.invalid:443/client/files/a",
        "https://NATIVE.example.invalid/client/files/a",
        "https://native.example.invalid/client/files/a?token=private",
        "https://native.example.invalid/client/files/a?",
        "https://native.example.invalid/client/files/a#fragment",
        "https://native.example.invalid/client/files/a#",
        "https://native.example.invalid/client/files/",
        "https://native.example.invalid/client/files-other/a",
        "https://native.example.invalid/client/files/../a",
        "https://native.example.invalid/client/files/%2e%2e/a",
        "https://native.example.invalid/client/files/%2fa",
        "https://native.example.invalid/client/files/%5ca",
        "https://native.example.invalid/client/files/%252fa",
        "https://native.example.invalid/client/files/a%3ftoken=secret",
        "https://native.example.invalid/client/files/a%23fragment",
        "https://native.example.invalid/client/files/a%00",
        "https://native.example.invalid/client/files/a%0a",
        "https://native.example.invalid/client/files/a%EF%BB%BF",
        "https://native.example.invalid/client/files/%ff",
        "https://native.example.invalid/client/files/%xx",
        "https://native.example.invalid/client/files//a",
        "https://native.example.invalid/client/files/a/",
        "https://native.example.invalid/client/files/a\\b",
        "https://native.example.invalid/client/files/а",
        " https://native.example.invalid/client/files/a",
        "https://native.example.invalid/client/files/a\n",
        "https://native.example.invalid/client/files/" + "a" * 2048,
        "file:///private",
        "javascript:alert(1)",
        "https://downloader.dst.yandex.ru/disk/bearer-download",
        "https://yadi.sk/public-share",
        None,
    ],
)
def test_untrusted_or_download_targets_are_never_normalized_into_native_links(url):
    context, target = sample()
    with pytest.raises(OriginalOpenUnavailable, match="^original_unavailable$"):
        validate_target(context, replace(target, url=url), POLICY)


@pytest.mark.parametrize(
    "field",
    [
        "tenant_id",
        "actor_id",
        "connection_id",
        "object_id",
        "path",
        "policy_key",
        "credential_key",
        "observation_number",
    ],
)
def test_target_must_bind_exact_request_context(field):
    context, target = sample()
    value = getattr(context, field)
    changed = replace(context, **{field: value + 1 if type(value) is int else value + "changed"})
    with pytest.raises(OriginalOpenUnavailable):
        validate_target(context, replace(target, context=changed), POLICY)


@pytest.mark.parametrize(
    "case",
    [
        "expired",
        "future_check",
        "old_check",
        "long_lived",
        "naive_check",
        "naive_expiry",
        "naive_context",
        "future_context",
        "overflow_context",
        "wrong_type",
    ],
)
def test_expiry_fresh_provider_check_and_types_are_required(case):
    context, target = sample()
    now = context.issued_at
    if case == "expired":
        target = replace(target, expires_at=now)
    elif case == "future_check":
        target = replace(target, verified_at=now + timedelta(seconds=5))
    elif case == "old_check":
        target = replace(target, verified_at=now - timedelta(seconds=1))
    elif case == "long_lived":
        target = replace(target, expires_at=now + OPEN_TTL + timedelta(seconds=1))
    elif case == "naive_check":
        target = replace(target, verified_at=now.replace(tzinfo=None))
    elif case == "naive_expiry":
        target = replace(target, expires_at=now.replace(tzinfo=None))
    elif case == "naive_context":
        context = replace(context, issued_at=now.replace(tzinfo=None))
    elif case == "future_context":
        context = replace(context, issued_at=now + timedelta(seconds=10))
    elif case == "overflow_context":
        context = replace(context, issued_at=datetime.max.replace(tzinfo=timezone.utc))
    else:
        target = {"url": URL}
    with pytest.raises(OriginalOpenUnavailable):
        validate_target(context, target, POLICY)


@pytest.mark.parametrize(
    "policy",
    [
        None,
        NativeLinkPolicy("*", "/client/"),
        NativeLinkPolicy("https://native.example.invalid", "/client/"),
        NativeLinkPolicy("native.example.invalid", "/"),
        NativeLinkPolicy("native.example.invalid", "/../"),
        NativeLinkPolicy("native.example.invalid", "/client"),
    ],
)
def test_registration_is_explicit_exact_and_narrow(policy):
    context, target = sample()
    with pytest.raises(OriginalOpenUnavailable):
        validate_target(context, target, policy)


def test_encoded_unicode_filename_is_supported_without_inventing_a_path_mapping():
    context, target = sample()
    url = URL + "/%D0%A2%D0%B5%D1%81%D1%82.txt"
    assert validate_target(context, replace(target, url=url), POLICY).url == url
