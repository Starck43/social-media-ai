"""Injected synthetic authority/streams only; no DB, network or pytest-asyncio."""

import ast
import asyncio
import inspect
import time
import traceback
from dataclasses import FrozenInstanceError, fields, replace

import pytest

from app.integrations.file_workspace.content_read import PlainTextReadUnavailable
from app.services.file_workspace import plain_text_read as service
from tests.shared.file_content_read import SyntheticContentAuthority, SyntheticContentReader

__source_only__ = True
IDS = dict(tenant_id=1, actor_id=2, connection_id=3, object_id=4)


def invoke(authority, reader, **kwargs):
    return asyncio.run(service.read_plain_text(**(IDS | kwargs), authority=authority, reader=reader))


def rejected(authority, reader, **kwargs):
    with pytest.raises(PlainTextReadUnavailable) as caught:
        invoke(authority, reader, **kwargs)
    error = caught.value
    assert str(error) == "plain_text_unavailable"
    assert error.args == ("plain_text_unavailable",)
    assert error.__cause__ is None and error.__context__ is None
    assert "private-account-token" not in "".join(traceback.format_exception(error))
    return error


def test_order_binding_closed_before_recheck_and_no_inferred_version():
    events = []
    authority = SyntheticContentAuthority(events)
    reader = SyntheticContentReader(events, chunks=(b"\xef\xbb", b"\xbf\xd0", b"\xaf\r", b"\n"))
    result = invoke(authority, reader)
    assert events == ["authorize", "open", "chunk", "chunk", "chunk", "chunk", "close", "authorize"]
    assert reader.closed and reader.limits == [262144]
    assert authority.requests[0] == authority.requests[1] == result.context.request
    assert reader.contexts == [result.context]
    assert result.extraction.text == "Я\n" and result.extraction.line_count == 2
    assert result.provider_version is None
    assert result.extraction.raw_sha256 != result.extraction.text_sha256


def test_supplied_version_is_opaque_adapter_assertion_not_hash_or_catalog_revision():
    reader = SyntheticContentReader()
    reader.stream_updates["provider_version"] = "opaque-provider-revision-9"
    result = invoke(SyntheticContentAuthority(), reader)
    assert result.provider_version == "opaque-provider-revision-9"
    assert result.provider_version not in {result.extraction.raw_sha256, result.extraction.text_sha256}


@pytest.mark.parametrize("chunks", [(), (b"",), (b"\xef\xbb\xbf",)])
def test_empty_complete_text_is_valid_not_a_missing_file_claim(chunks):
    reader = SyntheticContentReader(chunks=chunks)
    result = invoke(SyntheticContentAuthority(), reader)
    assert result.extraction.text == "" and result.extraction.line_count == 0
    assert reader.closed


@pytest.mark.parametrize("missing", ["authority", "reader", "both"])
def test_no_defaults_or_fallback(missing):
    authority = SyntheticContentAuthority() if missing == "reader" else None
    reader = SyntheticContentReader() if missing == "authority" else None
    rejected(authority, reader)
    if authority:
        assert not authority.requests
    if reader:
        assert not reader.contexts


@pytest.mark.parametrize("key", list(IDS))
@pytest.mark.parametrize("value", [None, 0, -1, True, "1", 1.0])
def test_invalid_ids_fail_before_authority_or_provider(key, value):
    authority, reader = SyntheticContentAuthority(), SyntheticContentReader()
    rejected(authority, reader, **{key: value})
    assert not authority.requests and not reader.contexts


@pytest.mark.parametrize("at", [1, 2])
def test_authority_denial_before_read_or_before_release(at):
    authority, reader = SyntheticContentAuthority(), SyntheticContentReader()
    authority.deny_at = at
    rejected(authority, reader)
    assert len(reader.contexts) == (at - 1)
    assert reader.closed == (at == 2)


@pytest.mark.parametrize("boundary", ["local_only", "company", "", "unknown", None, True])
def test_non_cloud_or_unknown_processing_denied_before_bytes(boundary):
    authority, reader = SyntheticContentAuthority(), SyntheticContentReader()
    authority.initial_updates["processing_boundary"] = boundary
    rejected(authority, reader)
    assert not reader.contexts


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("processing_policy_key", ""),
        ("processing_policy_key", None),
        ("policy_key", ""),
        ("credential_key", ""),
        ("root_key", ""),
        ("observation_number", 0),
        ("observation_number", True),
        ("path", ""),
        ("path", "a\x00"),
        ("path", "a" * 2049),
        ("path", "я" * 1025),
        ("path", "\ud800"),
        ("name", "file.pdf"),
        ("name", "folder/file.txt"),
        ("name", None),
    ],
)
def test_malformed_or_unsupported_initial_context_never_opens_reader(key, value):
    authority, reader = SyntheticContentAuthority(), SyntheticContentReader()
    authority.initial_updates[key] = value
    rejected(authority, reader)
    assert not reader.contexts


@pytest.mark.parametrize("field", ["tenant_id", "actor_id", "connection_id", "object_id", "nonce"])
def test_foreign_initial_request_is_denied(field):
    authority, reader = SyntheticContentAuthority(), SyntheticContentReader()
    original = authority.authorize

    async def foreign(request):
        context = await original(request)
        return replace(context, request=replace(request, **{field: "other" if field == "nonce" else 999}))

    authority.authorize = foreign
    rejected(authority, reader)
    assert not reader.contexts


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("path", "disk:/other/file.txt"),
        ("name", "renamed.txt"),
        ("root_key", "changed"),
        ("policy_key", "changed"),
        ("credential_key", "rotated"),
        ("observation_number", 2),
        ("processing_policy_key", "changed"),
        ("processing_boundary", "local_only"),
    ],
)
def test_fresh_context_changes_deny_release_after_stream_closed(key, value):
    authority, reader = SyntheticContentAuthority(), SyntheticContentReader()
    authority.fresh_updates[key] = value
    rejected(authority, reader)
    assert reader.closed and len(authority.requests) == 2


def test_cross_call_stream_replay_is_rejected_by_fresh_nonce():
    authority, reader = SyntheticContentAuthority(), SyntheticContentReader()
    first = invoke(authority, reader)
    replay = SyntheticContentReader()
    replay.stream_updates["context"] = first.context
    rejected(SyntheticContentAuthority(), replay)
    assert replay.contexts[0].request.nonce != first.context.request.nonce
    assert replay.consumed == 0 and replay.closed


@pytest.mark.parametrize("field", ["tenant_id", "actor_id", "connection_id", "object_id", "nonce"])
def test_foreign_stream_request_is_rejected_before_consumption(field):
    authority, reader = SyntheticContentAuthority(), SyntheticContentReader()

    async def change_stream():
        context = reader.contexts[0]
        reader.stream_updates["context"] = replace(
            context, request=replace(context.request, **{field: "other" if field == "nonce" else 999})
        )

    reader.before_yield = change_stream
    rejected(authority, reader)
    assert reader.consumed == 0 and reader.closed


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("complete", False),
        ("complete", 1),
        ("complete", None),
        ("expected_bytes", -1),
        ("expected_bytes", True),
        ("expected_bytes", 262145),
        ("expected_bytes", None),
        ("expected_bytes", "7"),
        ("provider_version", ""),
        ("provider_version", "a" * 513),
        ("provider_version", "private\x00"),
        ("provider_version", 1),
        ("context", None),
    ],
)
def test_invalid_representation_and_version_metadata_denied_before_consumption(key, value):
    authority, reader = SyntheticContentAuthority(), SyntheticContentReader()
    reader.stream_updates[key] = value
    rejected(authority, reader)
    assert reader.consumed == 0 and reader.closed and len(authority.requests) == 1


@pytest.mark.parametrize("expected", [0, 6, 8])
def test_declared_size_mismatch_denies_partial_or_overlong_transfer(expected):
    authority, reader = SyntheticContentAuthority(), SyntheticContentReader()
    reader.stream_updates["expected_bytes"] = expected
    rejected(authority, reader)
    assert reader.closed and len(authority.requests) == 1


@pytest.mark.parametrize("bad", ["private", bytearray(b"private"), None, 42])
def test_wrong_chunk_types_are_not_coerced(bad):
    authority = SyntheticContentAuthority()
    reader = SyntheticContentReader(chunks=(bad, b"never"))
    rejected(authority, reader)
    assert reader.consumed == 1 and reader.closed


def test_running_size_stops_before_consuming_remaining_chunks():
    reader = SyntheticContentReader(chunks=(b"a" * 262144, b"b", b"never"))
    reader.stream_updates["expected_bytes"] = 262144
    rejected(SyntheticContentAuthority(), reader)
    assert reader.consumed == 2 and reader.closed


def test_chunk_budget_includes_empty_chunks():
    result = invoke(SyntheticContentAuthority(), SyntheticContentReader(chunks=(b"",) * 1024))
    assert result.extraction.text == ""
    reader = SyntheticContentReader(chunks=(b"",) * 1026)
    rejected(SyntheticContentAuthority(), reader)
    assert reader.consumed == 1025 and reader.closed


@pytest.mark.parametrize("payload", [b"\xff", b"private\x00", b"a" * 32769, b"\n" * 1000])
def test_parser_failure_never_releases_partial_text(payload):
    authority, reader = SyntheticContentAuthority(), SyntheticContentReader(chunks=(payload,))
    rejected(authority, reader)
    assert reader.closed and len(authority.requests) == 1


@pytest.mark.parametrize("phase", ["before_yield", "on_chunk", "on_close"])
def test_provider_exception_sanitized_and_stream_closed(phase, caplog, capsys):
    async def fail():
        raise RuntimeError("private-account-token https://private.invalid/file")

    reader = SyntheticContentReader()
    setattr(reader, phase, fail)
    error = rejected(SyntheticContentAuthority(), reader)
    assert "private.invalid" not in "".join(traceback.format_exception(error))
    assert reader.closed and not caplog.records and capsys.readouterr() == ("", "")


@pytest.mark.parametrize("phase", ["prepare", "read", "close", "recheck"])
def test_total_cooperative_timeout_covers_all_async_phases(phase, monkeypatch):
    monkeypatch.setattr(service, "READ_TIMEOUT", 0.01)
    authority, reader = SyntheticContentAuthority(), SyntheticContentReader()

    async def delay():
        await asyncio.sleep(1)

    if phase == "prepare":
        authority.hook = delay
    elif phase == "recheck":

        async def recheck_delay():
            if len(authority.requests) == 2:
                await delay()

        authority.hook = recheck_delay
    else:
        setattr(reader, "on_chunk" if phase == "read" else "on_close", delay)
    rejected(authority, reader)
    assert reader.closed or not reader.contexts


@pytest.mark.parametrize("phase", ["prepare", "read", "close", "recheck"])
def test_cancellation_propagates_and_stream_closes(phase):
    authority, reader = SyntheticContentAuthority(), SyntheticContentReader()

    async def cancel():
        raise asyncio.CancelledError("cancel-marker")

    if phase == "prepare":
        authority.hook = cancel
    elif phase == "recheck":

        async def cancel_recheck():
            if len(authority.requests) == 2:
                await cancel()

        authority.hook = cancel_recheck
    else:
        setattr(reader, "on_chunk" if phase == "read" else "on_close", cancel)
    with pytest.raises(asyncio.CancelledError, match="cancel-marker"):
        invoke(authority, reader)
    assert reader.closed or not reader.contexts


def test_synchronous_overrun_is_denied_after_adapter_returns(monkeypatch):
    monkeypatch.setattr(service, "READ_TIMEOUT", 0.005)
    reader = SyntheticContentReader()

    async def block():
        time.sleep(0.02)

    reader.before_yield = block
    rejected(SyntheticContentAuthority(), reader)
    assert reader.consumed == 0 and reader.closed


def test_adapter_swallowing_timeout_cannot_release_late_text(monkeypatch):
    monkeypatch.setattr(service, "READ_TIMEOUT", 0.005)
    reader = SyntheticContentReader()

    async def swallow():
        try:
            await asyncio.sleep(1)
        except asyncio.CancelledError:
            pass

    reader.on_chunk = swallow
    rejected(SyntheticContentAuthority(), reader)
    assert reader.closed


def test_expiry_during_sync_parser_is_rejected_before_postcheck(monkeypatch):
    monkeypatch.setattr(service, "READ_TIMEOUT", 0.005)
    original = service.extract_plain_text

    def delayed(payload, **kwargs):
        if payload:
            time.sleep(0.02)
        return original(payload, **kwargs)

    monkeypatch.setattr(service, "extract_plain_text", delayed)
    authority, reader = SyntheticContentAuthority(), SyntheticContentReader()
    rejected(authority, reader)
    assert reader.closed and len(authority.requests) == 1


def test_all_lineage_result_fields_are_immutable_and_repr_hidden():
    result = invoke(SyntheticContentAuthority(), SyntheticContentReader())
    for value in [result, result.context, result.context.request, result.extraction]:
        assert type(value).__repr__ is object.__repr__
        for field in fields(value):
            with pytest.raises((FrozenInstanceError, AttributeError)):
                setattr(value, field.name, None)
            with pytest.raises((FrozenInstanceError, AttributeError)):
                delattr(value, field.name)
        assert not hasattr(value, "__dict__")


def test_service_has_no_concrete_db_network_model_or_settings_imports():
    modules = set()
    for node in ast.walk(ast.parse(inspect.getsource(service))):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            modules.add(node.module)
    assert modules == {
        "__future__",
        "asyncio",
        "unicodedata",
        "uuid",
        "app.integrations.file_workspace.content_read",
        "app.integrations.file_workspace.plain_text",
    }


@pytest.mark.parametrize("phase", ["prepare", "read", "recheck"])
def test_swallowed_external_cancellation_cannot_release_result(phase):
    async def scenario():
        reached = asyncio.Event()
        authority, reader = SyntheticContentAuthority(), SyntheticContentReader()

        async def swallow():
            reached.set()
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                pass

        if phase == "prepare":
            authority.hook = swallow
        elif phase == "recheck":

            async def only_recheck():
                if len(authority.requests) == 2:
                    await swallow()

            authority.hook = only_recheck
        else:
            reader.on_chunk = swallow
        task = asyncio.create_task(service.read_plain_text(**IDS, authority=authority, reader=reader))
        await asyncio.wait_for(reached.wait(), 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert reader.closed or not reader.contexts

    asyncio.run(scenario())


@pytest.mark.parametrize("phase", ["prepare", "recheck"])
@pytest.mark.parametrize("bad", [None, {}, True])
def test_malformed_authority_result_is_never_a_permission_grant(phase, bad):
    authority, reader = SyntheticContentAuthority(), SyntheticContentReader()
    original = authority.authorize

    async def malformed(request):
        context = await original(request)
        if phase == "prepare" or len(authority.requests) == 2:
            return bad
        return context

    authority.authorize = malformed
    rejected(authority, reader)
    assert reader.closed or not reader.contexts


def test_equal_content_hashes_do_not_merge_requests_or_owners():
    first = invoke(SyntheticContentAuthority(), SyntheticContentReader())
    second = invoke(SyntheticContentAuthority(), SyntheticContentReader(), actor_id=99)
    assert first.extraction == second.extraction
    assert first.context.request.actor_id != second.context.request.actor_id
    assert first.context.request.nonce != second.context.request.nonce


def test_fresh_authority_cannot_substitute_another_request():
    authority, reader = SyntheticContentAuthority(), SyntheticContentReader()
    original = authority.authorize

    async def wrong_request(request):
        context = await original(request)
        if len(authority.requests) == 2:
            return replace(context, request=replace(request, actor_id=99))
        return context

    authority.authorize = wrong_request
    rejected(authority, reader)
    assert reader.closed


def test_preflight_expiry_does_not_start_reader(monkeypatch):
    monkeypatch.setattr(service, "READ_TIMEOUT", 0.005)
    original = service.extract_plain_text

    def slow_preflight(payload, **kwargs):
        time.sleep(0.02)
        return original(payload, **kwargs)

    monkeypatch.setattr(service, "extract_plain_text", slow_preflight)
    reader = SyntheticContentReader()
    rejected(SyntheticContentAuthority(), reader)
    assert not reader.contexts


def test_final_validation_overrun_cannot_release_result(monkeypatch):
    monkeypatch.setattr(service, "READ_TIMEOUT", 0.005)
    original = service._context_valid
    calls = 0

    def slow_final(context, request):
        nonlocal calls
        calls += 1
        if calls == 3:
            time.sleep(0.02)
        return original(context, request)

    monkeypatch.setattr(service, "_context_valid", slow_final)
    reader = SyntheticContentReader()
    rejected(SyntheticContentAuthority(), reader)
    assert calls == 3 and reader.closed
