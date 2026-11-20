"""State binding and fixed WebDAV response boundaries without provider access."""

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from app.integrations.file_workspace.mailru_webdav import _href, _items, credentials
from app.integrations.file_workspace.policy import ReadOnlyFolderPolicy
from app.integrations.file_workspace.yandex_disk import MetadataError
from app.services.file_workspace.folder_picker import PendingFolder, PickerExpired, get, issue

__source_only__ = True


async def test_picker_handle_cannot_cross_actor_tenant_or_session_and_is_single_use():
    expected = "yandex-browse:browser:1:2"
    pending = PendingFolder(
        expected, "yandex_disk", "synthetic-token", datetime.now(timezone.utc) + timedelta(seconds=60)
    )
    handle = issue(pending)
    for binding in (
        "yandex-browse:other:1:2",
        "yandex-browse:browser:3:2",
        "yandex-browse:browser:1:3",
    ):
        with pytest.raises(PickerExpired):
            get(handle, binding, consume=True)
    assert get(handle, expected) is pending
    assert get(handle, expected, consume=True) is pending
    with pytest.raises(PickerExpired):
        get(handle, expected)
    assert pending.credential not in repr(pending)


async def test_picker_expiry_discards_the_pending_token():
    binding = "browser:1:2"
    pending = PendingFolder(
        binding, "yandex_disk", "synthetic-token", datetime.now(timezone.utc) + timedelta(milliseconds=10)
    )
    handle = issue(pending)
    await asyncio.sleep(0.02)
    with pytest.raises(PickerExpired):
        get(handle, binding)
    with pytest.raises(PickerExpired):
        issue(pending)


def test_mailru_credentials_strip_surrounding_whitespace():
    assert credentials(" example@mail.ru ", "\t app-password \r\n") == ("example@mail.ru", "app-password")


@pytest.mark.parametrize("password", [" \t\n", "app password", "app\npassword"])
def test_mailru_credentials_reject_empty_passwords_and_internal_whitespace(password):
    with pytest.raises(ValueError, match="invalid_credentials"):
        credentials("example@mail.ru", password)


@pytest.mark.parametrize(
    "href",
    [
        "",
        "/work/../x",
        "/work/%2e%2e",
        "/work/%252e%252e",
        "/work/%2Fetc",
        "/work/%5Cetc",
        "/work/%00x",
        "/work/%FF",
        "/work/%ZZ",
        "/work//",
        "//webdav.cloud.mail.ru/work",
        "http://webdav.cloud.mail.ru/work",
        "https://elsewhere.test/work",
        "https://webdav.cloud.mail.ru@elsewhere.test/work",
        "https://webdav.cloud.mail.ru/work?other=1",
    ],
)
def test_mailru_href_refuses_ambiguous_or_external_paths(href):
    with pytest.raises(ValueError):
        _href(href)


def test_mailru_href_decodes_each_component_once():
    assert _href("https://webdav.cloud.mail.ru:443/work/My%20files/") == ("work", "My files")


def _folder(href):
    return (
        f"<response><href>{href}</href><propstat><prop><resourcetype><collection/></resourcetype></prop>"
        "<status>HTTP/1.1 200 OK</status></propstat></response>"
    )


@pytest.mark.parametrize(
    "xml",
    [
        '<!DOCTYPE a [<!ENTITY bad "text">]><multistatus xmlns="DAV:"/>',
        '<multistatus xmlns="DAV:"/>',
        f'<multistatus xmlns="DAV:">{_folder("/work/")}{_folder("/workspace/")}</multistatus>',
        f'<multistatus xmlns="DAV:">{_folder("/work/")}{_folder("/work/a/b/")}</multistatus>',
        f'<multistatus xmlns="DAV:">{_folder("/work/")}{_folder("/work/")}</multistatus>',
    ],
)
def test_mailru_listing_requires_a_valid_self_collection_and_direct_children(xml):
    with pytest.raises(MetadataError, match="invalid_provider_response"):
        _items(xml.encode(), ("work",), ReadOnlyFolderPolicy("/work"))
