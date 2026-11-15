"""P18-T06 wire widening: ``file.fetch`` and ``index.delete`` inside the closed envelope."""

from __future__ import annotations

import time

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from pydantic import ValidationError

from app.services.local_connector.wire import (
    MAX_FETCH_BYTES,
    ConnectorUnavailable,
    DeleteArgs,
    DeleteResponse,
    FetchArgs,
    FetchResponse,
    SearchArgs,
    SearchRequest,
    canonical,
    new_request,
    sign,
    verify,
)

KEY_ID = "device-key-1"
DIGEST = "a" * 64


def request_for(args, operation=None, *, key_id=KEY_ID, expires_at=None):
    now = int(time.time())
    body = {
        "key_id": key_id,
        "request_id": "b" * 32,
        "tenant_id": 1,
        "owner_id": 2,
        "actor_id": 2,
        "device_id": 3,
        "issued_at": now,
        "expires_at": now + 30 if expires_at is None else expires_at,
        "device_epoch": 1,
        "consent_epoch": 1,
        "policy_epoch": 1,
        "args": args.model_dump() if hasattr(args, "model_dump") else args,
    }
    if operation is not None:
        body["operation"] = operation
    return SearchRequest(**body)


def test_fetch_and_delete_requests_round_trip_inside_version_one() -> None:
    key = Ed25519PrivateKey.generate()
    args = FetchArgs(object_id="object-1", root_id="root-a", relative_path="docs/report.pdf")
    request = request_for(args, "file.fetch")
    payload, digest = sign(request, key)

    accepted = verify(payload, key.public_key(), key_id=KEY_ID)

    assert accepted.operation == "file.fetch"
    assert isinstance(accepted.typed_args(), FetchArgs)
    assert accepted.typed_args().relative_path == "docs/report.pdf"
    assert accepted.typed_args().max_bytes == MAX_FETCH_BYTES
    assert accepted.model_dump()["operation"] == "file.fetch"

    delete = request_for(DeleteArgs(scope="index", root_ids=["root-a"]), "index.delete")
    delete_payload, delete_digest = sign(delete, key)
    accepted_delete = verify(delete_payload, key.public_key(), key_id=KEY_ID)
    assert accepted_delete.typed_args().scope == "index"
    assert accepted_delete.typed_args().root_ids == ["root-a"]


def test_search_requests_still_round_trip_and_keep_their_typed_args() -> None:
    key = Ed25519PrivateKey.generate()
    request = request_for(SearchArgs(query="needle", roots=["root-a"]), "search")
    payload, _digest = sign(request, key)
    accepted = verify(payload, key.public_key(), key_id=KEY_ID)
    assert isinstance(accepted.typed_args(), SearchArgs)
    assert accepted.typed_args().query == "needle"


@pytest.mark.parametrize("operation", ["file.delete", "shell", "remove_all", "search ", "FILES"])
def test_unknown_operations_are_rejected_by_the_closed_enum(operation: str) -> None:
    with pytest.raises(ValidationError):
        request_for(SearchArgs(query="q", roots=["root-a"]), operation)


def test_operation_and_argument_shape_must_agree() -> None:
    with pytest.raises(Exception):
        request_for(SearchArgs(query="q", roots=["root-a"]), "index.delete")
    with pytest.raises(Exception):
        request_for(DeleteArgs(scope="index", root_ids=["root-a"]), "file.fetch")


@pytest.mark.parametrize(
    "bad_path", ["/etc/passwd", "../secrets.txt", "docs/../secrets.txt", "docs//file.txt", "docs/file\x00.txt"]
)
def test_fetch_arguments_reject_path_escape_attempts(bad_path: str) -> None:
    with pytest.raises(Exception):
        request_for(FetchArgs(object_id="object-1", root_id="root-a", relative_path=bad_path), "file.fetch")


def test_fetch_arguments_reject_oversized_and_batched_input() -> None:
    with pytest.raises(ValidationError):
        FetchArgs(object_id="object-1", root_id="root-a", relative_path="a.txt", max_bytes=MAX_FETCH_BYTES + 1)
    with pytest.raises(ValidationError):
        FetchArgs(object_id="object-1", root_id="root-a", relative_path="a.txt", destination="cloud_catalog")
    with pytest.raises(ValidationError):
        FetchArgs(object_id="object-1", root_id="root-a", relative_path="a.txt", object_ids=["object-1"])


def test_delete_arguments_reject_duplicates_and_unknown_roots() -> None:
    with pytest.raises(Exception):
        request_for(DeleteArgs(scope="index", root_ids=["root-a", "root-a"]), "index.delete")
    with pytest.raises(Exception):
        request_for(DeleteArgs(scope="index", root_ids=["../root"]), "index.delete")
    with pytest.raises(ValidationError):
        DeleteArgs(scope="files", root_ids=["root-a"])


def test_fetch_response_must_bind_the_request_object_and_carry_an_assertion() -> None:
    request = request_for(FetchArgs(object_id="object-1", root_id="root-a", relative_path="a.txt"), "file.fetch")
    good = FetchResponse(
        request_id=request.request_id,
        request_digest=DIGEST,
        device_id=request.device_id,
        outcome="succeeded",
        object_id="object-1",
        content_hash="c" * 64,
        size=10,
        version_assertion="exact-version-1",
    )
    assert good.checked(request, DIGEST) is good

    with pytest.raises(ConnectorUnavailable):
        good.model_copy(update={"object_id": "object-2"}).checked(request, DIGEST)
    with pytest.raises(ConnectorUnavailable):
        FetchResponse(
            request_id=request.request_id,
            request_digest=DIGEST,
            device_id=request.device_id,
            outcome="succeeded",
            object_id="object-1",
            content_hash="c" * 64,
            size=10,
        ).checked(request, DIGEST)
    with pytest.raises(Exception):
        good.model_copy(update={"size": MAX_FETCH_BYTES + 1}).checked(request, DIGEST)
    with pytest.raises(Exception):
        good.model_copy(update={"request_digest": "d" * 64}).checked(request, DIGEST)


def test_delete_response_must_match_scope_and_stay_empty_on_failure() -> None:
    request = request_for(DeleteArgs(scope="index", root_ids=["root-a"]), "index.delete")
    good = DeleteResponse(
        request_id=request.request_id,
        request_digest=DIGEST,
        device_id=request.device_id,
        outcome="succeeded",
        scope="index",
        generation=3,
        removed=4,
    )
    assert good.checked(request, DIGEST) is good

    with pytest.raises(Exception):
        good.model_copy(update={"scope": "all"}).checked(request, DIGEST)
    with pytest.raises(ConnectorUnavailable):
        DeleteResponse(
            request_id=request.request_id,
            request_digest=DIGEST,
            device_id=request.device_id,
            outcome="denied",
            scope="index",
            generation=3,
            removed=4,
        ).checked(request, DIGEST)


def test_new_request_helper_carries_operation_specific_arguments() -> None:
    args = FetchArgs(object_id="object-1", root_id="root-a", relative_path="a.txt")
    request = new_request(
        key_id=KEY_ID,
        tenant_id=1,
        owner_id=2,
        device_id=3,
        device_epoch=1,
        consent_epoch=1,
        policy_epoch=1,
        args=args,
        operation="file.fetch",
    )
    assert request.operation == "file.fetch"
    assert canonical({"a": 1}) == b'{"a":1}'
    assert request.typed_args().object_id == "object-1"


def test_request_roots_and_audit_action_support_fetch_and_delete() -> None:
    from app.services.local_connector.store import _AUDIT_ACTION_BY_OPERATION, _request_roots

    fetch_req = request_for(FetchArgs(object_id="object-1", root_id="root-a", relative_path="a.txt"), "file.fetch")
    del_req = request_for(DeleteArgs(scope="index", root_ids=["root-a"]), "index.delete")
    search_req = request_for(SearchArgs(query="q", roots=["root-a"]), "search")
    assert _request_roots(fetch_req) == ["root-a"]
    assert _request_roots(del_req) == ["root-a"]
    assert _request_roots(search_req) == ["root-a"]
    assert _AUDIT_ACTION_BY_OPERATION["file.fetch"] == "files.connector.fetch"
    assert _AUDIT_ACTION_BY_OPERATION["index.delete"] == "files.connector.index_delete"
