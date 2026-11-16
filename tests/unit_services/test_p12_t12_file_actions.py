"""Synthetic no-DB/no-network tests for private file actions, not production approval."""

import asyncio
import hashlib
import json
from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import urlencode

import pytest
from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

from app.integrations.file_workspace.original_open import OriginalOpenContext
from app.integrations.file_workspace.yandex_content import DownloadedText, FileDownloadUnavailable, YandexTextClient
from app.services.file_workspace.action_forms import action_form, action_token
from app.services.file_workspace.file_content import FileContentService

SCOPE = dict(tenant_id=1, actor_id=2, connection_id=3, object_id=4)


def run(coro):
    return asyncio.run(coro)


def response(data, status=200, extra=b""):
    return (
        b"HTTP/1.1 "
        + str(status).encode()
        + b" OK\r\nContent-Length: "
        + str(len(data)).encode()
        + b"\r\n"
        + extra
        + b"\r\n"
        + data
    )


class Reader:
    def __init__(self, raw):
        self.raw = raw

    async def read(self, maximum):
        chunk, self.raw = self.raw[:maximum], self.raw[maximum:]
        return chunk


class Writer:
    def __init__(self):
        self.request = b""
        self.closed = False

    def write(self, data):
        self.request += data

    async def drain(self):
        pass

    def close(self):
        self.closed = True

    async def wait_closed(self):
        pass


def stream_client(payload=b"hello\nworld", *, link=None, second=None, body=None):
    metadata = dict(
        path="disk:/root/file.txt",
        name="file.txt",
        type="file",
        size=len(payload),
        md5=hashlib.md5(payload, usedforsecurity=False).hexdigest(),
        modified="2026-01-01",
    )
    link = link or dict(href="https://downloader.dst.yandex.ru/disk/opaque?x=1", method="GET", templated=False)
    raws = [
        response(json.dumps(metadata).encode()),
        response(json.dumps(link).encode()),
        body or response(payload),
        response(json.dumps(second or metadata).encode()),
    ]
    calls, writers = [], []

    async def open_(host):
        calls.append(host)
        w = Writer()
        writers.append(w)
        return Reader(raws.pop(0)), w

    return YandexTextClient(_opener=open_), calls, writers


def test_download_is_pinned_bounded_and_has_no_version():
    client, calls, writers = stream_client()
    result = run(client.download(path="disk:/root/file.txt", name="file.txt", token="SYNTHETIC"))
    assert result.payload == b"hello\nworld" and result.provider_version is None
    assert calls == ["cloud-api.yandex.net", "cloud-api.yandex.net", "downloader.dst.yandex.ru", "cloud-api.yandex.net"]
    assert all(w.closed and b"Authorization: OAuth SYNTHETIC" in w.request for w in writers)
    assert "SYNTHETIC" not in repr(result)


@pytest.mark.parametrize(
    "link",
    [
        dict(href="https://evil.example/disk/key", method="GET", templated=False),
        dict(href="https://downloader.dst.yandex.ru/disk/%2e%2e/a", method="GET", templated=False),
        dict(href="https://downloader.dst.yandex.ru/disk/a?token=secret", method="GET", templated=False),
        dict(href="https://downloader.dst.yandex.ru/disk/a", method="POST", templated=False),
    ],
)
def test_download_rejects_untrusted_link_without_opening_it(link):
    client, calls, writers = stream_client(link=link)
    with pytest.raises(FileDownloadUnavailable) as err:
        run(client.download(path="disk:/root/file.txt", name="file.txt", token="SYNTHETIC"))
    assert str(err.value) == "file_download_unavailable" and len(calls) == 2
    assert all(w.closed for w in writers)


@pytest.mark.parametrize(
    "body",
    [
        response(b"", 302, b"Location: https://evil.example/\r\n"),
        response(b"a", 200, b"Content-Encoding: gzip\r\n"),
        response(b"a", 200, b"Content-Range: bytes 0-0/1\r\n"),
        b"HTTP/1.1 200 OK\r\nContent-Length: 9999999\r\n\r\n",
    ],
)
def test_http_failures_close_streams(body):
    client, calls, writers = stream_client(body=body)
    with pytest.raises(FileDownloadUnavailable):
        run(client.download(path="disk:/root/file.txt", name="file.txt", token="SYNTHETIC"))
    assert len(calls) == 3 and all(w.closed for w in writers)


def test_checksum_and_metadata_change_fail_closed():
    for client in (
        stream_client(payload=b"hi", body=response(b"no"))[0],
        stream_client(
            second=dict(
                path="disk:/root/file.txt", name="file.txt", type="file", size=11, md5="0" * 32, modified="changed"
            )
        )[0],
    ):
        with pytest.raises(FileDownloadUnavailable):
            run(client.download(path="disk:/root/file.txt", name="file.txt", token="SYNTHETIC"))


def access(revision="consent"):
    context = OriginalOpenContext(
        1, 2, 3, 4, "disk:/root/file.txt", "root", "policy", "credential", 1, datetime.now(timezone.utc)
    )
    return SimpleNamespace(context=context, name="file.txt", token="SYNTHETIC", processing_revision=revision)


def test_content_reauthorizes_and_preview_uses_same_consent():
    a = access()
    manager = SimpleNamespace(authorize=AsyncMock(return_value=a))
    client = SimpleNamespace(download=AsyncMock(return_value=DownloadedText(b"first\nsecond")))
    service = FileContentService(manager, client=client)
    evidence = run(service.preview(**SCOPE, start=2, end=2))
    assert evidence.excerpts[0].text == "second"
    assert evidence.context.processing_policy_key == "consent" and evidence.provider_version is None
    assert manager.authorize.await_count == 2
    assert manager.authorize.await_args_list[1].kwargs["previous"] is a
    assert all(c.kwargs["processing"] is True for c in manager.authorize.await_args_list)


@pytest.mark.parametrize(
    "download", [DownloadedText(b"x" * 262145), SimpleNamespace(payload=b"bad"), DownloadedText("not bytes")]
)
def test_untrusted_client_result_never_released(download):
    manager = SimpleNamespace(authorize=AsyncMock(return_value=access()))
    client = SimpleNamespace(download=AsyncMock(return_value=download))
    with pytest.raises(FileDownloadUnavailable):
        run(FileContentService(manager, client=client).original(**SCOPE))


def test_changed_authority_denied_and_range_checked_before_fetch():
    manager = SimpleNamespace(authorize=AsyncMock(side_effect=[access(), access("changed")]))
    client = SimpleNamespace(download=AsyncMock(return_value=DownloadedText(b"hi")))
    service = FileContentService(manager, client=client)
    with pytest.raises(FileDownloadUnavailable):
        run(service.original(**SCOPE))
    with pytest.raises(FileDownloadUnavailable):
        run(service.preview(**SCOPE, start=1, end=33))
    assert client.download.await_count == 1


class Request:
    def __init__(self, body, headers=None):
        self.body = body
        self.headers = headers if headers is not None else {"content-type": "application/x-www-form-urlencoded"}

    async def stream(self):
        yield self.body


def test_action_tokens_bind_session_and_all_scope_fields():
    session = {}
    token = action_token(session, "key", **SCOPE)
    assert token == action_token(session, "key", **SCOPE)
    for field in SCOPE:
        changed = dict(SCOPE)
        changed[field] += 1
        assert token != action_token(session, "key", **changed)
    assert token != action_token({}, "key", **SCOPE)
    with pytest.raises(ValueError):
        action_token({}, "key", **dict(SCOPE, actor_id=True))


def test_strict_action_form():
    token = "a" * 64
    assert (
        run(
            action_form(Request(urlencode({"_csrf": token, "confirm": "forget"}).encode()), {"_csrf", "confirm"}, token)
        )["confirm"]
        == "forget"
    )
    for body, headers in [
        (("_csrf=" + token + "&confirm=forget&confirm=forget").encode(), None),
        (("_csrf=" + token + "&confirm=%ZZ").encode(), None),
        (("_csrf=" + "b" * 64 + "&confirm=forget").encode(), None),
        (b"x" * 2049, None),
        (("_csrf=" + token + "&confirm=forget").encode(), {"content-type": "application/json"}),
        (
            ("_csrf=" + token + "&confirm=forget").encode(),
            {"content-type": "application/x-www-form-urlencoded", "content-length": "1"},
        ),
    ]:
        with pytest.raises(ValueError):
            run(action_form(Request(body, headers), {"_csrf", "confirm"}, token))


def test_template_escapes_inert_text_and_has_no_implicit_consent_or_scripts():
    env = Environment(
        loader=FileSystemLoader("app/web/templates"), autoescape=select_autoescape(["html"]), undefined=StrictUndefined
    )
    env.globals["static_v"] = lambda p: "/static/" + p
    preview = SimpleNamespace(
        line_count=1, excerpts=[SimpleNamespace(start=1, end=1, text="<script>attack()</script>")]
    )
    rendered = env.get_template("web/file_workspace.html").render(
        file={"id": 4, "name": "<img src=x>"},
        connection_id=3,
        action_url="/app/knowledge/files/4/workspace?connection_id=3",
        file_csrf="a" * 64,
        consent={"processing": False, "retention": False},
        records=[],
        results_enabled=True,
        preview=preview,
        result=None,
    )
    assert "<script>" not in rendered and "&lt;script&gt;attack()&lt;/script&gt;" in rendered
    assert "&lt;img src=x&gt;" in rendered and rendered.count('value="" selected') == 2
    assert "<script " not in rendered and "checked" not in rendered


def test_content_authority_checks_scope_policy_and_reauthorization():
    from contextlib import asynccontextmanager

    from sqlalchemy import Column, Integer, MetaData, String, Table
    from sqlalchemy.orm import registry

    from app.integrations.file_workspace.original_open import OriginalOpenContext, context_live
    from app.integrations.file_workspace.policy import FileOperation
    from tests.shared.file_private_storage import source_module

    meta = MetaData()
    objects = Table(
        "objects",
        meta,
        *[
            Column(n, Integer if n in ("id", "tenant_id", "connection_id") else String, primary_key=n == "id")
            for n in ("id", "tenant_id", "connection_id", "root_key", "kind")
        ],
    )
    mounts = Table(
        "mounts",
        meta,
        *[Column(n, Integer, primary_key=n == "connection_id") for n in ("tenant_id", "connection_id", "owner_id")],
    )

    class FileCatalogObject:
        pass

    class FileInsightMount:
        pass

    mapper = registry()
    mapper.map_imperatively(FileCatalogObject, objects)
    mapper.map_imperatively(FileInsightMount, mounts)

    class ConnectionDenied(Exception):
        pass

    row = SimpleNamespace(path="disk:/root/file.txt", name="file.txt", observation_number=1)
    mount = SimpleNamespace(processing_allowed=True, policy_revision="policy-v1", consent_revision="revision1")
    secret = SimpleNamespace(secret_encrypted="encrypted")
    session = SimpleNamespace(
        scalar=AsyncMock(side_effect=lambda q: mount if "mounts" in str(q) else row), get=AsyncMock(return_value=secret)
    )
    connection = SimpleNamespace(config={}, account_key="account")
    policy = SimpleNamespace(permits=lambda path, operation: True)

    @asynccontextmanager
    async def owned(*args):
        yield session, connection, policy, "root"

    mod = source_module(
        "app/models/managers/file_content_manager.py",
        "_file_content_manager_tests",
        dict(
            OriginalOpenContext=OriginalOpenContext,
            context_live=context_live,
            FileOperation=FileOperation,
            FileCatalogObject=FileCatalogObject,
            WorkspaceConnectionSecret=object,
            FileInsightMount=FileInsightMount,
            _key=lambda x: x,
            _owned=owned,
            _policy_key=lambda x: "policy",
            POLICY_REVISION="policy-v1",
            ConnectionDenied=ConnectionDenied,
            decrypt_secret=lambda x: "SYNTHETIC",
        ),
        ("app.",),
    )
    manager = mod.FileContentManager(enabled=True)
    first = run(manager.authorize(**SCOPE, processing=True))
    assert first.processing_revision == "revision1"
    assert run(manager.authorize(**SCOPE, processing=True, previous=first)) == first
    mount.consent_revision = "revision2"
    with pytest.raises(ConnectionDenied):
        run(manager.authorize(**SCOPE, processing=True, previous=first))
    mount.processing_allowed = False
    with pytest.raises(ConnectionDenied):
        run(manager.authorize(**SCOPE, processing=True))
    assert run(manager.authorize(**SCOPE, processing=False)).processing_revision is None
    policy.permits = lambda path, operation: False
    with pytest.raises(ConnectionDenied):
        run(manager.authorize(**SCOPE, processing=False))
    with pytest.raises(ConnectionDenied):
        run(mod.FileContentManager().authorize(**SCOPE))


def test_private_routes_are_post_csrf_scoped_and_recheck_consent():
    import re

    import httpx
    from fastapi import FastAPI
    from fastapi.responses import HTMLResponse
    from starlette.middleware.sessions import SessionMiddleware

    from tests.shared.file_private_storage import source_module

    class ConnectionDenied(Exception):
        pass

    class PrivateInsightUnavailable(Exception):
        pass

    settings = SimpleNamespace(
        FILE_CATALOG_UI_ENABLED=True,
        FILE_WORKSPACE_ACTIONS_ENABLED=True,
        FILE_PRIVATE_RESULTS_UI_ENABLED=True,
        SECRET_KEY="synthetic-session-key",
        CREDENTIALS_KEY="not-used",
    )
    consent = {"processing": True, "retention": True, "revision": "rev1"}
    manager = SimpleNamespace(
        consent_state=AsyncMock(side_effect=lambda **k: dict(consent)),
        list_results=AsyncMock(return_value=[]),
        set_consent=AsyncMock(),
        forget_object=AsyncMock(),
        get=AsyncMock(),
    )
    catalog = SimpleNamespace(detail=AsyncMock(return_value={"id": 4, "name": "test.txt", "kind": "file"}))
    preview = SimpleNamespace(
        context=SimpleNamespace(processing_policy_key="rev1"),
        line_count=1,
        excerpts=[SimpleNamespace(start=1, end=1, text="quote")],
    )
    content = SimpleNamespace(
        original=AsyncMock(return_value=SimpleNamespace(payload=b"hi")), preview=AsyncMock(return_value=preview)
    )
    env = Environment(
        loader=FileSystemLoader("app/web/templates"), autoescape=select_autoescape(["html"]), undefined=StrictUndefined
    )
    env.globals["static_v"] = lambda p: "/static/" + p

    def render(request, name, **values):
        return HTMLResponse(env.get_template(name).render(**values))

    mod = source_module(
        "app/web/file_workspace.py",
        "_file_workspace_routes_tests",
        dict(
            settings=settings,
            PrivateCipher=object,
            FileDownloadUnavailable=FileDownloadUnavailable,
            FileContentManager=object,
            FileInsightManager=object,
            POLICY_REVISION="p",
            PrivateInsightUnavailable=PrivateInsightUnavailable,
            ConnectionDenied=ConnectionDenied,
            action_form=action_form,
            action_token=action_token,
            FileContentService=object,
            render=render,
            catalog=catalog,
        ),
        ("app.",),
    )
    mod._manager = lambda: manager
    mod._content = lambda: content
    app = FastAPI()
    app.include_router(mod.router, prefix="/app")
    app.add_middleware(SessionMiddleware, secret_key="synthetic-session-key", https_only=True)

    @app.middleware("http")
    async def actor(request, call_next):
        request.state.web_user = SimpleNamespace(id=2)
        request.state.tenant_id = 1
        return await call_next(request)

    async def exercise():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://test.local") as c:
            url = "/app/knowledge/files/4/workspace?connection_id=3"
            page = await c.get(url)
            assert page.status_code == 200 and page.headers["cache-control"] == "no-store"
            assert "default-src 'none'" in page.headers["content-security-policy"]
            assert content.original.await_count == content.preview.await_count == 0
            token = re.search(r'name="_csrf" value="([a-f0-9]+)"', page.text).group(1)
            bad = await c.post(url.replace("/workspace?", "/workspace/original?"), data={"_csrf": "bad"})
            assert bad.status_code == 403 and content.original.await_count == 0
            ok = await c.post(url.replace("/workspace?", "/workspace/original?"), data={"_csrf": token})
            assert ok.status_code == 200 and ok.content == b"hi" and "attachment" in ok.headers["content-disposition"]
            assert "sandbox" in ok.headers["content-security-policy"]
            result = await c.post(
                url.replace("/workspace?", "/workspace/preview?"), data={"_csrf": token, "start": "1", "end": "1"}
            )
            assert result.status_code == 200 and "quote" in result.text
            consent["revision"] = "rev2"
            result = await c.post(
                url.replace("/workspace?", "/workspace/preview?"), data={"_csrf": token, "start": "1", "end": "1"}
            )
            assert result.status_code == 404 and "quote" not in result.text
            assert (await c.get(url + "&connection_id=3")).status_code == 404
            assert (await c.get(url + "&path=secret")).status_code == 404
            settings.FILE_WORKSPACE_ACTIONS_ENABLED = False
            assert (await c.get(url)).status_code == 404

    run(exercise())
