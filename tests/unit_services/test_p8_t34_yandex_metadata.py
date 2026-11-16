"""Mock-only metadata tests: never calls a provider or a database."""

import httpx
import pytest

from app.integrations.file_workspace.policy import FileOperation, FolderRestriction, ReadOnlyFolderPolicy
from app.integrations.file_workspace.yandex_disk import MetadataError, list_folder

__source_only__ = True


def response(items, *, path="disk:/work", offset=0, kind="dir"):
    return {"path": path, "type": kind, "_embedded": {"items": items, "offset": offset}}


def item(path, kind="file"):
    return {"path": path, "name": path.rsplit("/", 1)[-1], "type": kind}


@pytest.mark.asyncio
async def test_fixed_metadata_endpoint_and_filtering():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=response([item("disk:/work/hidden", "dir"), item("disk:/work/report.txt")]))

    policy = ReadOnlyFolderPolicy(
        "/work", (FolderRestriction("/work/hidden", frozenset({FileOperation.LIST, FileOperation.READ})),)
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        page = await list_folder(client, token="test-token", folder="/work", policy=policy, limit=2)
    assert [(x.name, x.kind) for x in page.items] == [("report.txt", "file")]
    assert page.next_offset == 2
    request = requests[0]
    assert request.method == "GET"
    assert request.url.host == "cloud-api.yandex.net"
    assert request.url.path == "/v1/disk/resources"
    assert request.url.params["path"] == "disk:/work"
    assert request.url.params["limit"] == "2"
    assert request.headers["authorization"] == "OAuth test-token"


@pytest.mark.asyncio
@pytest.mark.parametrize("folder", ["/work/private", "/workspace", "/work/../private", "/work/%2e%2e/private"])
async def test_denied_path_never_calls_provider(folder):
    calls = []
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda req: calls.append(req))) as client:
        policy = ReadOnlyFolderPolicy(
            "/work", (FolderRestriction("/work/private", frozenset({FileOperation.LIST, FileOperation.READ})),)
        )
        with pytest.raises(MetadataError, match="^access_denied$"):
            await list_folder(client, token="test-token", folder=folder, policy=policy)
    assert not calls


@pytest.mark.asyncio
@pytest.mark.parametrize("offset,limit", [(-1, 1), (0, 0), (0, 101), (1_000_001, 1), (True, 1), (0, True)])
async def test_bad_paging_never_calls_provider(offset, limit):
    calls = []
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda req: calls.append(req))) as client:
        with pytest.raises(MetadataError, match="^invalid_page$"):
            await list_folder(
                client,
                token="test-token",
                folder="/work",
                policy=ReadOnlyFolderPolicy("/work"),
                offset=offset,
                limit=limit,
            )
    assert not calls


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,code",
    [
        (301, "provider_unavailable"),
        (401, "reauthorization_required"),
        (403, "reauthorization_required"),
        (429, "rate_limited"),
        (503, "provider_unavailable"),
    ],
)
async def test_statuses_are_sanitized_and_redirects_not_followed(status, code):
    calls = []

    def handler(req):
        calls.append(req)
        return httpx.Response(status, headers={"Location": "https://attacker.invalid/"}, text="sensitive provider body")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True) as client:
        with pytest.raises(MetadataError, match=f"^{code}$") as exc:
            await list_folder(client, token="test-token", folder="/work", policy=ReadOnlyFolderPolicy("/work"))
    assert len(calls) == 1
    assert "test-token" not in str(exc.value) and "/work" not in str(exc.value)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        response([item("disk:/workspace/secret")]),
        response([item("disk:/work/dir/nested")]),
        response([item("disk:/work/../secret")]),
        response([item("disk:/work/x"), item("disk:/work/x")]),
        response([{"path": "disk:/work/x", "name": "other", "type": "file"}]),
        response([item("disk:/work/x", "link")]),
        response([], path="disk:/other"),
        response([], offset=5),
        response([], kind="file"),
        {"path": "disk:/work", "type": "dir", "_embedded": {"offset": 0, "items": "bad"}},
    ],
)
async def test_invalid_provider_responses_fail_closed(payload):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda req: httpx.Response(200, json=payload))
    ) as client:
        with pytest.raises(MetadataError, match="^invalid_provider_response$"):
            await list_folder(client, token="test-token", folder="/work", policy=ReadOnlyFolderPolicy("/work"))


@pytest.mark.asyncio
async def test_body_limit_and_empty_folder():
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda req: httpx.Response(200, content=b"x" * (256 * 1024 + 1)))
    ) as client:
        with pytest.raises(MetadataError, match="^response_too_large$"):
            await list_folder(client, token="test-token", folder="/work", policy=ReadOnlyFolderPolicy("/work"))
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda req: httpx.Response(200, json={"path": "disk:/work", "type": "dir"}))
    ) as client:
        assert (
            await list_folder(client, token="test-token", folder="/work", policy=ReadOnlyFolderPolicy("/work"))
        ).items == ()


@pytest.mark.asyncio
async def test_offset_cap_is_reported_as_truncated_not_complete():
    payload = response([item("disk:/work/x")], offset=1_000_000)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda req: httpx.Response(200, json=payload))
    ) as client:
        page = await list_folder(
            client, token="test-token", folder="/work", policy=ReadOnlyFolderPolicy("/work"), offset=1_000_000, limit=1
        )
    assert page.next_offset is None
    assert page.truncated is True


@pytest.mark.asyncio
async def test_transport_failure_does_not_leak_request_or_token():
    def handler(req):
        raise httpx.ConnectError("secret test-token /work", request=req)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(MetadataError, match="^provider_unavailable$") as exc:
            await list_folder(client, token="test-token", folder="/work", policy=ReadOnlyFolderPolicy("/work"))
    assert "test-token" not in str(exc.value) and "/work" not in str(exc.value)
