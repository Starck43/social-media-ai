"""Mock-only tests: no provider network, account, file or database access."""

import json

import pytest

from app.integrations.file_workspace.yandex_download_target import DownloadTargetError, parse_download_target

__source_only__ = True


def response(url, method="GET", templated=False):
    return json.dumps({"href": url, "method": method, "templated": templated}).encode()


def test_documented_host_and_opaque_url_are_accepted_without_repr_leak():
    url = "https://downloader.dst.yandex.ru/disk/opaque%3D%3D?uid=123&filename=private.txt&hash=temporary-secret"
    target = parse_download_target(response(url))
    assert target._url == url
    assert "private.txt" not in repr(target)
    assert "temporary-secret" not in repr(target)
    assert "downloader" not in repr(target)


@pytest.mark.parametrize(
    "url",
    [
        "http://downloader.dst.yandex.ru/disk/a",
        "https://downloader.dst.yandex.ru.evil.invalid/disk/a",
        "https://evil.invalid/disk/a",
        "https://user@downloader.dst.yandex.ru/disk/a",
        "https://downloader.dst.yandex.ru:444/disk/a",
        "https://downloader.dst.yandex.ru./disk/a",
        "https://127.0.0.1/disk/a",
        "https://[::1]/disk/a",
        "https://downloader.dst.yandex.ru/elsewhere/a",
        "https://downloader.dst.yandex.ru/disk/../private",
        "https://downloader.dst.yandex.ru/disk/%2e%2e/private",
        "https://downloader.dst.yandex.ru/disk/%252e%252e/private",
        "https://downloader.dst.yandex.ru/disk/a%2fb",
        "https://downloader.dst.yandex.ru/disk/a%5cb",
        "https://downloader.dst.yandex.ru/disk/a%00b",
        "https://downloader.dst.yandex.ru/disk/a#fragment",
        "https://downloader.dst.yandex.ru/disk/a#",
        "https://downloader.dst.yandex.ru/disk/a?x=%0d%0aheader",
        "https://downloader.dst.yandex.ru/disk/a?x=%zz",
        "https://downloader.dst.yandex.ru/disk/a?access_token=fake-secret",
        "https://downloader.dst.yandex.ru/disk/a?%6f%61%75%74%68_token=fake-secret",
        "https://downloader.dst.yandex.ru/disk/a?%2561ccess_token=fake-secret",
        "https://downloader.dst.yandex.ru/disk/a\nextra",
        "https://downloader.dst.yandex.ru/disk/неаски",
        "https://downloader.dst.yandex.ru/disk/" + "x" * 13000,
    ],
)
def test_invalid_targets_are_rejected_without_leaking(url):
    with pytest.raises(DownloadTargetError, match="^download_unavailable$") as exc:
        parse_download_target(response(url))
    assert "fake-secret" not in str(exc.value) and "downloader" not in str(exc.value)
    assert exc.value.__cause__ is None


@pytest.mark.parametrize(
    "body",
    [
        b"{}",
        b"not-json fake-secret",
        b"[]",
        b'{"href":"https://downloader.dst.yandex.ru/disk/a","href":"https://evil.invalid","method":"GET","templated":false}',
        response("https://downloader.dst.yandex.ru/disk/a", method="POST"),
        response("https://downloader.dst.yandex.ru/disk/a", templated=True),
        response("https://downloader.dst.yandex.ru/disk/a", templated=0),
        b"x" * (16 * 1024 + 1),
    ],
)
def test_bad_link_objects_fail_closed(body):
    with pytest.raises(DownloadTargetError, match="^download_unavailable$"):
        parse_download_target(body)
