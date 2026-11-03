"""Assembled app/auth middleware and persisted catalog; synthetic data only."""

import re

import httpx
import pytest
from bs4 import BeautifulSoup
from sqlalchemy import update

from app.core.config import settings
from app.core.database import async_session_maker
from app.core.hashing import get_password_hash
from app.main import create_application
from app.models import User, WorkspaceConnection
from tests.shared.file_catalog_fixtures import actor, admit, configure, data  # noqa: F401

pytestmark = pytest.mark.tenancy


@pytest.fixture
async def web(data, monkeypatch):
    monkeypatch.setattr(settings, "FILE_CATALOG_UI_ENABLED", True)
    password = "catalog-synthetic-password"
    async with async_session_maker() as session, session.begin():
        await session.execute(
            update(User)
            .where(User.id.in_([u.id for u in data.users]))
            .values(hashed_password=get_password_hash(password))
        )
    app = create_application()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver", follow_redirects=False) as client:

        async def login(index):
            client.cookies.clear()
            page = await client.get("/app/login")
            csrf = re.search(r'name="_csrf" value="([^\"]+)"', page.text).group(1)
            response = await client.post(
                "/app/login",
                data={
                    "username": data.users[index].username,
                    "password": password,
                    "_csrf": csrf,
                },
            )
            assert response.status_code in (302, 303)

        await login(0)
        yield client, login, transport


async def test_list_detail_escape_and_privacy_headers(data, web):
    client, _, _ = web
    with actor(data) as args:
        await admit(args, "<img src=x onerror=alert(1)>.txt", "report.txt")
    response = await client.get("/app/knowledge/files")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["referrer-policy"] == "no-referrer"
    html = BeautifulSoup(response.text, "html.parser")
    assert html.select_one("[data-file-catalog]") and not html.select("#page-content img")
    assert "<img src=x onerror=alert(1)>.txt" in html.get_text()
    links = html.select(".file-catalog-name")
    assert len(links) == 2 and all(link["href"].startswith("/app/knowledge/files/") for link in links)
    detail = await client.get(links[0]["href"])
    assert detail.status_code == 200 and detail.headers["cache-control"] == "no-store"
    dom = BeautifulSoup(detail.text, "html.parser")
    assert dom.select_one("[data-file-catalog-detail]") and not dom.select("#page-content img")
    assert "не резервные копии" in dom.get_text()
    assert "Открытие оригинала пока недоступно" in dom.get_text()
    assert not dom.select(
        '#page-content a[href^="http"], #page-content a[href^="file:"], #page-content a[href^="javascript:"]'
    )
    assert "p12t12-synthetic-token" not in response.text + detail.text


async def test_feature_off_hides_routes_and_legacy_links(data, web, monkeypatch):
    client, _, _ = web
    monkeypatch.setattr(settings, "FILE_CATALOG_UI_ENABLED", False)
    assert (await client.get("/app/knowledge/files")).status_code == 404
    for path in ("/app/knowledge", "/app/documents"):
        page = await client.get(path)
        assert page.status_code == 200
        assert not BeautifulSoup(page.text, "html.parser").select('#page-actions a[href="/app/knowledge/files"]')


async def test_enabled_links_cloud_only_preserve_legacy_pages(data, web):
    client, _, _ = web
    for path in ("/app/knowledge", "/app/documents"):
        for boundary in ("cloud", "company", "local_only"):
            page = await client.get(path, params={"boundary": boundary})
            assert page.status_code == 200
            link = BeautifulSoup(page.text, "html.parser").select_one('#page-actions a[href="/app/knowledge/files"]')
            assert bool(link) == (boundary == "cloud")


async def test_forged_owner_and_missing_ids_use_same_response(data, web):
    client, login, _ = web
    with actor(data) as args:
        await admit(args, "owner-secret.txt")
    page = await client.get("/app/knowledge/files")
    detail_url = BeautifulSoup(page.text, "html.parser").select_one(".file-catalog-name")["href"]
    await login(1)
    foreign = await client.get(detail_url)
    missing = await client.get("/app/knowledge/files/2147483647", params={"connection_id": data.connections[1]})
    assert foreign.status_code == missing.status_code == 404 and foreign.text == missing.text
    assert "owner-secret.txt" not in (await client.get("/app/knowledge/files")).text
    assert (await client.get("/app/knowledge/files", params={"connection_id": data.connections[0]})).status_code == 404


async def test_revoke_closes_rendered_detail(data, web):
    client, _, _ = web
    with actor(data) as args:
        await admit(args, "report.txt")
    page = await client.get("/app/knowledge/files")
    href = BeautifulSoup(page.text, "html.parser").select_one(".file-catalog-name")["href"]
    async with async_session_maker() as session, session.begin():
        await session.execute(
            update(WorkspaceConnection).where(WorkspaceConnection.id == data.connections[0]).values(is_active=False)
        )
    response = await client.get(href)
    assert response.status_code == 404 and "report.txt" not in response.text


async def test_restriction_applies_before_html_search(data, web):
    client, _, _ = web
    with actor(data) as args:
        await admit(args, "secret.txt", folder="disk:/work/private")
        await admit(args, "visible.txt")
    await configure(data, restrictions=[{"path": "disk:/work/private", "deny": ["list"]}])
    page = await client.get("/app/knowledge/files", params={"q": "secret"})
    assert page.status_code == 200 and "secret.txt" not in page.text
    assert "Ничего не найдено" in page.text


async def test_mount_selector_and_empty_states_do_not_claim_live_sync(data, web):
    client, _, _ = web
    page = await client.get("/app/knowledge/files")
    assert "В каталоге пока нет записей" in page.text and "не означает, что папка пуста" in page.text
    options = BeautifulSoup(page.text, "html.parser").select("#file-mount option")
    assert [int(o["value"]) for o in options] == [data.connections[0]]
    await configure(data, extra={"boundary": "local_only"})
    page = await client.get("/app/knowledge/files")
    assert "Нет доступных подключённых папок" in page.text
    assert "disk:/work" not in page.text


async def test_query_limits_paging_and_no_mutation_routes(data, web):
    client, _, _ = web
    with actor(data) as args:
        await admit(args, *[f"report-{n:02}.txt" for n in range(51)])
    first = await client.get("/app/knowledge/files")
    dom = BeautifulSoup(first.text, "html.parser")
    assert len(dom.select(".file-catalog-name")) == 50
    next_link = dom.select_one('a[rel="next"]')["href"]
    second = await client.get(next_link)
    assert len(BeautifulSoup(second.text, "html.parser").select(".file-catalog-name")) == 1
    assert (await client.get("/app/knowledge/files", params={"q": "x" * 201})).status_code == 422
    assert (await client.get("/app/knowledge/files", params={"after": -1})).status_code == 422
    assert (await client.post("/app/knowledge/files", json={"path": "/fake"})).status_code == 405


async def test_unauthenticated_redirects_before_catalog(data, web):
    _, _, transport = web
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as guest:
        response = await guest.get("/app/knowledge/files")
        assert response.status_code == 302 and response.headers["location"].startswith("/app/login")


async def test_cross_workspace_connection_and_unknown_body_are_not_authority(data, web):
    client, _, _ = web
    assert (await client.get("/app/knowledge/files", params={"connection_id": data.connections[2]})).status_code == 404
    with actor(data) as args:
        await admit(args, "safe.txt")
    page = await client.get("/app/knowledge/files", params={"q": "<script>alert(1)</script>"})
    assert page.status_code == 200 and "<script>alert(1)</script>" not in page.text
    assert (await client.post("/app/knowledge/files", json={"source_ref": "https://evil.invalid"})).status_code == 405


async def test_catalog_unavailable_is_503_not_fake_empty(data, web, monkeypatch):
    from sqlalchemy.exc import DBAPIError

    from app.web import file_catalog

    async def broken(**kwargs):
        raise DBAPIError("not-rendered", {}, Exception("synthetic-db-failure"))

    monkeypatch.setattr(file_catalog.catalog, "list_page", broken)
    response = await web[0].get("/app/knowledge/files")
    assert response.status_code == 503 and "В каталоге пока нет записей" not in response.text
    assert "synthetic-db-failure" not in response.text
