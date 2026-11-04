"""Focused protocol, cryptography, in-process channel, and gateway contracts."""

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.asymmetric.rsa import generate_private_key
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from app.api.v1.endpoints.files_connectors import Results
from app.api.v1.entry import router as api_router
from app.services.local_connector import channel as channel_module
from app.services.local_connector.crypto import ConnectorAuthority
from app.services.local_connector.gateway import ConnectorGateway, SearchDispatch, cloud_chat_references
from app.services.local_connector.wire import (
    MAX_CONTROL_BYTES,
    TIMEOUT,
    ConnectorUnavailable,
    FileReference,
    SearchArgs,
    SearchHit,
    SearchRequest,
    SearchResponse,
    canonical,
    decode,
    new_request,
    sign,
    verify,
)


def private_pem(key):
    return key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )


def make_authority(*, ca_is_ca=True, expired=False, pairing_key=b"p" * 32, wrong_ca_key=False, signer=None):
    ca_key = Ed25519PrivateKey.generate()
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "synthetic-test-ca")])
    current = datetime.now(timezone.utc)
    not_before = current - timedelta(days=2 if expired else 1)
    not_after = current - timedelta(days=1) if expired else current + timedelta(days=2)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(ca_key.public_key())
        .serial_number(1)
        .not_valid_before(not_before)
        .not_valid_after(not_after)
        .add_extension(x509.BasicConstraints(ca=ca_is_ca, path_length=None), critical=True)
        .sign(ca_key, algorithm=None)
    )
    supplied_ca_key = Ed25519PrivateKey.generate() if wrong_ca_key else ca_key
    return ConnectorAuthority(
        ca_pem=certificate.public_bytes(serialization.Encoding.PEM),
        ca_key_pem=private_pem(supplied_ca_key),
        signing_key_pem=private_pem(signer or Ed25519PrivateKey.generate()),
        pairing_key=pairing_key,
        key_id="gateway-test-v1",
    )


def make_csr(key=None, *, subject=None, extension=False):
    key = key or Ed25519PrivateKey.generate()
    builder = x509.CertificateSigningRequestBuilder()
    if subject is not None:
        builder = builder.subject_name(subject)
    if extension:
        builder = builder.add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
    algorithm = None if isinstance(key, Ed25519PrivateKey) else hashes.SHA256()
    return builder.sign(key, algorithm=algorithm).public_bytes(serialization.Encoding.PEM).decode("ascii")


def make_search_hit(*, object_id, root_id="root-a", rank=1, snippet="synthetic snippet"):
    return SearchHit(
        reference=FileReference(object_id=object_id, root_id=root_id, path=f"/synthetic/{object_id}.txt", page=1),
        rank=rank,
        snippet=snippet,
        data_boundary="company",
    )


def test_canonical_encoding_is_stable_utf8_and_rejects_unsupported_values():
    assert canonical({"z": 2, "a": "тест"}) == '{"a":"тест","z":2}'.encode("utf-8")
    assert canonical({"root": ["a", "b"], "enabled": True}) == b'{"enabled":true,"root":["a","b"]}'

    for invalid in ({"value": 1.5}, {"ключ": "value"}, {"value": float("nan")}):
        with pytest.raises(ConnectorUnavailable):
            canonical(invalid)


@pytest.mark.parametrize(
    "payload",
    [
        b"",
        b"[]",
        b'{"x":1,"x":2}',
        b'{"\xd0\xba\xd0\xbb\xd1\x8e\xd1\x87":1}',
        b'{"x":1.25}',
        b'{"x":NaN}',
        b"\xff",
        b"x" * (MAX_CONTROL_BYTES + 1),
        bytearray(b'{"x":1}'),
    ],
)
def test_decode_rejects_noncanonical_or_oversized_input(payload):
    with pytest.raises(ConnectorUnavailable):
        decode(payload)


def test_signed_request_round_trip_and_tampering_fail_closed():
    signing_key = Ed25519PrivateKey.generate()
    request = new_request(
        key_id="gateway-test-v1",
        tenant_id=7,
        owner_id=11,
        device_id=13,
        device_epoch=2,
        consent_epoch=3,
        policy_epoch=5,
        args=SearchArgs(query="synthetic query", roots=["root-a"]),
    )
    payload, digest = sign(request, signing_key)

    accepted = verify(payload, signing_key.public_key(), key_id="gateway-test-v1", now=request.issued_at)
    assert accepted == request
    assert len(digest) == 64
    assert "synthetic query" not in repr(accepted)

    changed = decode(payload)
    changed["args"]["query"] = "tampered query"
    with pytest.raises(ConnectorUnavailable):
        verify(canonical(changed), signing_key.public_key(), key_id="gateway-test-v1", now=request.issued_at)
    with pytest.raises(ConnectorUnavailable):
        verify(payload, signing_key.public_key(), key_id="different-key", now=request.issued_at)
    with pytest.raises(ConnectorUnavailable):
        verify(payload, signing_key.public_key(), key_id="gateway-test-v1", now=request.expires_at + 1)
    with pytest.raises(ConnectorUnavailable):
        verify(payload, signing_key.public_key(), key_id="gateway-test-v1", now=request.issued_at - 1)


def test_signed_request_rejects_actor_substitution_and_unknown_fields():
    signing_key = Ed25519PrivateKey.generate()
    args = SearchArgs(query="synthetic", roots=["root-a"])
    request = SearchRequest(
        key_id="gateway-test-v1",
        request_id="a" * 32,
        tenant_id=1,
        owner_id=2,
        actor_id=3,
        device_id=4,
        issued_at=100,
        expires_at=100 + TIMEOUT,
        device_epoch=1,
        consent_epoch=1,
        policy_epoch=1,
        args=args,
    )
    payload, _ = sign(request, signing_key)
    with pytest.raises(ConnectorUnavailable):
        verify(payload, signing_key.public_key(), key_id="gateway-test-v1", now=101)

    extra = decode(payload)
    extra["unrecognized"] = True
    with pytest.raises(ConnectorUnavailable):
        verify(canonical(extra), signing_key.public_key(), key_id="gateway-test-v1", now=101)


def test_search_arguments_enforce_utf8_byte_and_root_boundaries():
    with pytest.raises(ConnectorUnavailable):
        SearchArgs(query="я" * 1025, roots=["root-a"]).checked()
    with pytest.raises(ConnectorUnavailable):
        SearchArgs(query="q", roots=["root-a", "root-a"]).checked()
    with pytest.raises(ConnectorUnavailable):
        SearchArgs(query="q", roots=["../outside"]).checked()


def test_csr_proof_and_certificate_issuance_bind_the_public_key():
    authority = make_authority()
    device_key = Ed25519PrivateKey.generate()
    csr_pem = make_csr(device_key)

    fingerprint, public_pem = authority.csr(csr_pem)
    assert fingerprint == __import__("hashlib").sha256(device_key.public_key().public_bytes_raw()).hexdigest()

    before = datetime.now(timezone.utc)
    cert_pem = authority.issue(public_pem, tenant_id=7, owner_id=11, device_id=13)
    certificate = x509.load_pem_x509_certificate(cert_pem.encode("ascii"))
    assert certificate.public_key().public_bytes_raw() == device_key.public_key().public_bytes_raw()
    certificate.verify_directly_issued_by(authority.ca)
    assert certificate.subject.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value == "urn:local-connector:7:11:13"
    assert (
        ExtendedKeyUsageOID.CLIENT_AUTH in certificate.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
    )
    assert certificate.extensions.get_extension_for_class(x509.BasicConstraints).value.ca is False
    assert certificate.not_valid_after_utc <= before + timedelta(hours=1, seconds=1)


@pytest.mark.parametrize(
    "csr",
    [
        "not a certificate request",
        "x" * 8193,
        make_csr(subject=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "device-controlled")])),
        make_csr(extension=True),
    ],
)
def test_csr_rejects_bad_input_and_device_supplied_identity(csr):
    with pytest.raises(ConnectorUnavailable):
        ConnectorAuthority.csr(csr)


def test_csr_rejects_non_ed25519_key():
    rsa_key = generate_private_key(public_exponent=65537, key_size=2048)
    csr = make_csr(rsa_key)
    with pytest.raises(ConnectorUnavailable):
        ConnectorAuthority.csr(csr)


@pytest.mark.parametrize(
    "kwargs", [{"ca_is_ca": False}, {"expired": True}, {"pairing_key": b"short"}, {"wrong_ca_key": True}]
)
def test_authority_rejects_untrusted_or_unusable_signing_configuration(kwargs):
    with pytest.raises(ConnectorUnavailable):
        make_authority(**kwargs)


def test_peer_requires_verified_tls_extension_and_returns_certificate_binding():
    authority = make_authority()
    device_key = Ed25519PrivateKey.generate()
    _, public_pem = authority.csr(make_csr(device_key))
    cert_pem = authority.issue(public_pem, tenant_id=7, owner_id=11, device_id=13)
    certificate = x509.load_pem_x509_certificate(cert_pem.encode("ascii"))
    fingerprint = __import__("hashlib").sha256(device_key.public_key().public_bytes_raw()).hexdigest()

    assert authority.peer(
        {
            "scheme": "wss",
            "extensions": {"tls": {"client_cert_chain": [cert_pem]}},
            "headers": [(b"x-client-cert", b"untrusted-public-header")],
        }
    ) == (fingerprint, str(certificate.serial_number))

    bad_scopes = [
        {"scheme": "ws", "extensions": {"tls": {"client_cert_chain": [cert_pem]}}},
        {"scheme": "wss", "extensions": {"tls": {"client_cert_error": "invalid"}}},
        {"scheme": "wss", "extensions": {"tls": {"client_cert_chain": []}}},
        {"scheme": "wss", "extensions": {}},
    ]
    for scope in bad_scopes:
        with pytest.raises(ConnectorUnavailable):
            authority.peer(scope)


class MemorySocket:
    def __init__(self, on_send=None):
        self.on_send = on_send
        self.closed = []

    async def send_bytes(self, payload):
        if self.on_send is not None:
            result = self.on_send(payload)
            if asyncio.iscoroutine(result):
                await result

    async def close(self, *, code):
        self.closed.append(code)


def make_request_for_channel(device_id=5):
    request = new_request(
        key_id="gateway-test-v1",
        tenant_id=2,
        owner_id=3,
        device_id=device_id,
        device_epoch=1,
        consent_epoch=1,
        policy_epoch=1,
        args=SearchArgs(query="synthetic", roots=["root-a"]),
    )
    return request


@pytest.mark.asyncio
async def test_channel_capacity_and_duplicate_session_fail_closed():
    channels = channel_module.ConnectorChannels(maximum=1)
    channels.attach(1, MemorySocket(), 1)
    with pytest.raises(ConnectorUnavailable):
        channels.attach(1, MemorySocket(), 1)
    with pytest.raises(ConnectorUnavailable):
        channels.attach(2, MemorySocket(), 1)


@pytest.mark.asyncio
async def test_channel_bounds_pending_searches_per_device_and_checks_epoch():
    channels = channel_module.ConnectorChannels(maximum=2, per_device=1)
    request = make_request_for_channel()
    sent = asyncio.Event()
    session = channels.attach(5, MemorySocket(on_send=lambda _payload: sent.set()), request.device_epoch)

    pending_task = asyncio.create_task(channels.search(5, request, b"request", "d" * 64))
    await sent.wait()
    with pytest.raises(ConnectorUnavailable):
        await channels.search(5, request, b"request", "d" * 64)
    await channels.detach(5, session)
    with pytest.raises(ConnectorUnavailable):
        await pending_task

    wrong_epoch = channels.attach(6, MemorySocket(), request.device_epoch + 1)
    with pytest.raises(ConnectorUnavailable):
        await channels.search(6, request, b"request", "d" * 64)
    assert not wrong_epoch.pending


@pytest.mark.asyncio
async def test_channel_accepts_matching_reply_and_rejects_mismatch_and_replay():
    channels = channel_module.ConnectorChannels()
    request = make_request_for_channel()
    digest = "b" * 64
    session = None

    def reply(device_id=5, request_id=request.request_id, request_digest=digest):
        return canonical(
            SearchResponse(
                request_id=request_id,
                request_digest=request_digest,
                device_id=device_id,
                outcome="succeeded",
                hits=[make_search_hit(object_id="object-1")],
            ).model_dump()
        )

    def matching(_payload):
        channels.receive(5, session, reply())

    socket = MemorySocket(on_send=matching)
    session = channels.attach(5, socket, request.device_epoch)
    accepted = await channels.search(5, request, b"signed-request", digest)
    assert accepted.device_id == 5
    assert not session.pending

    def wrong_device(_payload):
        channels.receive(5, session, reply(device_id=99))

    socket.on_send = wrong_device
    with pytest.raises(ConnectorUnavailable):
        await channels.search(5, request, b"signed-request", digest)
    assert not session.pending

    def duplicate_reply(_payload):
        payload = reply()
        channels.receive(5, session, payload)
        channels.receive(5, session, payload)

    socket.on_send = duplicate_reply
    with pytest.raises(ConnectorUnavailable):
        await channels.search(5, request, b"signed-request", digest)
    assert not session.pending


@pytest.mark.asyncio
async def test_channel_rejects_oversized_unmatched_and_stale_session_messages():
    channels = channel_module.ConnectorChannels()
    request = make_request_for_channel()
    socket = MemorySocket()
    session = channels.attach(5, socket, request.device_epoch)
    with pytest.raises(ConnectorUnavailable):
        channels.receive(5, session, b"x" * (MAX_CONTROL_BYTES + 1))
    with pytest.raises(ConnectorUnavailable):
        channels.receive(5, session, b'{"bad":"response"}')
    with pytest.raises(ConnectorUnavailable):
        channels.receive(6, session, b"{}")


@pytest.mark.asyncio
async def test_channel_timeout_detach_and_revoke_clean_pending_state(monkeypatch):
    channels = channel_module.ConnectorChannels()
    request = make_request_for_channel()
    sent = asyncio.Event()
    socket = MemorySocket(on_send=lambda _payload: sent.set())
    session = channels.attach(5, socket, request.device_epoch)
    monkeypatch.setattr(channel_module, "TIMEOUT", 0.01)
    with pytest.raises(ConnectorUnavailable):
        await channels.search(5, request, b"request", "c" * 64)
    assert not session.pending

    monkeypatch.setattr(channel_module, "TIMEOUT", TIMEOUT)
    sent.clear()
    pending_task = asyncio.create_task(channels.search(5, request, b"request", "c" * 64))
    await sent.wait()
    await channels.detach(5, session)
    with pytest.raises(ConnectorUnavailable):
        await pending_task
    assert not session.pending

    another_socket = MemorySocket()
    channels.attach(6, another_socket, request.device_epoch)
    await channels.revoke(6)
    assert another_socket.closed == [1008]
    assert 6 not in channels.sessions


class FakeStore:
    def __init__(self, *, changed_snapshot=False, allowed_roots=None):
        self.calls = []
        self.snapshots = {}
        self.changed_snapshot = changed_snapshot
        self.allowed_roots = set(allowed_roots or {"root-a"})

    async def authorize(self, *, owner_id, device_id, roots, previous=None):
        self.calls.append((owner_id, device_id, tuple(roots), previous))
        if not set(roots) <= self.allowed_roots:
            raise ConnectorUnavailable()
        current = self.snapshots.setdefault(device_id, (1, owner_id, device_id, 1, 1, 1))
        if previous is not None and self.changed_snapshot:
            current = (*current[:3], current[3] + 1, *current[4:])
            self.snapshots[device_id] = current
        if previous is not None and previous != current:
            raise ConnectorUnavailable()
        return current

    async def reserve(self, request, digest):
        self.calls.append(("reserve", request.device_id, digest))

    async def finish(self, request, digest):
        self.calls.append(("finish", request.device_id, digest))


class FakeChannels:
    def __init__(self, replies=None, failures=None, on_search=None):
        self.replies = replies or {}
        self.failures = set(failures or ())
        self.on_search = on_search

    async def search(self, device_id, request, _payload, digest):
        if self.on_search is not None:
            self.on_search(device_id)
        if device_id in self.failures:
            raise ConnectorUnavailable()
        return SearchResponse(
            request_id=request.request_id,
            request_digest=digest,
            device_id=device_id,
            outcome="succeeded",
            hits=self.replies.get(device_id, []),
        )


@pytest.mark.asyncio
async def test_gateway_fans_out_ranks_and_bounds_company_search_results():
    store = FakeStore()
    channels = FakeChannels(
        replies={
            3: [make_search_hit(object_id="low", rank=2)],
            5: [make_search_hit(object_id="high", rank=9)],
        }
    )
    gateway = ConnectorGateway(
        store, SimpleNamespace(key_id="gateway-test-v1", signer=Ed25519PrivateKey.generate()), channels
    )

    result = await gateway.search(owner_id=17, device_ids=[3, 5], args=SearchArgs(query="q", roots=["root-a"], limit=1))

    payload = result.payload if isinstance(result, SearchDispatch) else result
    assert [hit["reference"]["object_id"] for hit in payload["hits"]] == ["high"]
    assert payload["partial"] is True
    assert all(hit["data_boundary"] == "company" for hit in payload["hits"])
    assert all(call[0] == 17 for call in store.calls if isinstance(call[0], int))


@pytest.mark.asyncio
@pytest.mark.parametrize("device_ids", [[3, 3], list(range(1, 10))])
async def test_gateway_rejects_duplicate_or_excess_devices_before_authorization(device_ids):
    store = FakeStore()
    gateway = ConnectorGateway(
        store, SimpleNamespace(key_id="gateway-test-v1", signer=Ed25519PrivateKey.generate()), FakeChannels()
    )
    with pytest.raises(ConnectorUnavailable):
        await gateway.search(owner_id=17, device_ids=device_ids, args=SearchArgs(query="q", roots=["root-a"]))
    assert store.calls == []


@pytest.mark.asyncio
async def test_gateway_marks_partial_when_one_device_is_unavailable():
    store = FakeStore()
    channels = FakeChannels(replies={3: [make_search_hit(object_id="available")]}, failures={5})
    gateway = ConnectorGateway(
        store, SimpleNamespace(key_id="gateway-test-v1", signer=Ed25519PrivateKey.generate()), channels
    )

    result = await gateway.search(owner_id=17, device_ids=[3, 5], args=SearchArgs(query="q", roots=["root-a"]))

    payload = result.payload if isinstance(result, SearchDispatch) else result
    assert payload["partial"] is True
    assert [hit["reference"]["object_id"] for hit in payload["hits"]] == ["available"]


@pytest.mark.asyncio
async def test_gateway_rechecks_snapshot_and_consented_roots_after_fanout():
    store = FakeStore(changed_snapshot=True)
    channels = FakeChannels(replies={3: [make_search_hit(object_id="must-not-release")]})
    gateway = ConnectorGateway(
        store, SimpleNamespace(key_id="gateway-test-v1", signer=Ed25519PrivateKey.generate()), channels
    )
    with pytest.raises(ConnectorUnavailable):
        await gateway.search(owner_id=17, device_ids=[3], args=SearchArgs(query="q", roots=["root-a"]))

    store = FakeStore()

    def revoke_root_after_response(_device_id):
        store.allowed_roots.clear()

    channels = FakeChannels(
        replies={3: [make_search_hit(object_id="must-not-release")]}, on_search=revoke_root_after_response
    )
    gateway = ConnectorGateway(
        store, SimpleNamespace(key_id="gateway-test-v1", signer=Ed25519PrivateKey.generate()), channels
    )
    with pytest.raises(ConnectorUnavailable):
        await gateway.search(owner_id=17, device_ids=[3], args=SearchArgs(query="q", roots=["root-a"]))


def test_chat_reference_projection_denies_noncompany_boundaries():
    hit = make_search_hit(object_id="company-file")
    assert cloud_chat_references([hit]) == [hit.reference.model_dump()]
    with pytest.raises(ConnectorUnavailable):
        cloud_chat_references([SimpleNamespace(data_boundary="local_only")])


def test_runtime_fails_closed_when_connector_is_not_enabled(monkeypatch):
    from app.core.config import settings
    from app.services.local_connector import runtime

    runtime.get_gateway.cache_clear()
    monkeypatch.setattr(settings, "LOCAL_CONNECTOR_ENABLED", False)
    monkeypatch.setattr(settings, "LOCAL_CONNECTOR_SECURITY_REVIEWED", False)
    try:
        with pytest.raises(ConnectorUnavailable):
            runtime.get_gateway()
    finally:
        runtime.get_gateway.cache_clear()


def test_route_wiring_exposes_connector_lifecycle_and_results_search_contract():
    routes = {(route.path, method): route for route in api_router.routes for method in getattr(route, "methods", ())}
    expected = {
        ("/files/connectors/pairings", "POST"),
        ("/files/connectors/register", "POST"),
        ("/files/connectors/pairings/{pairing_id}/confirm", "POST"),
        ("/files/connectors", "GET"),
        ("/files/connectors/{device_id}", "DELETE"),
        ("/files/connectors/{device_id}/roots", "PUT"),
        ("/files/search", "POST"),
    }
    assert expected <= routes.keys()
    assert routes[("/files/search", "POST")].response_model is Results
    assert {"hits", "partial"} <= set(Results.model_fields)
