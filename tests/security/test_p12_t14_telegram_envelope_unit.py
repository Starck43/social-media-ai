"""DB-exempt envelope/SQL contracts; all key material is synthetic."""

import json
import socket
from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet
from sqlalchemy.dialects import postgresql

from app.core.config import settings
from app.core.tenant_context import tenant_scope
from app.services.social import connector_authorizations as accounts
from app.services.social import telegram_grant_access as access
from app.services.social.connector_authorizations import AuthorizationUnavailable
from app.utils.crypto import decrypt_secret, encrypt_secret

MATERIAL = {"api_id": 123, "api_hash": "SYNTHETIC-HASH", "session": "SYNTHETIC-NOT-A-SESSION"}
PIN = access.SourceGrantSelection(11, 12, 13, "synthetic-target", 14, 15, 2)


@pytest.fixture(autouse=True)
def synthetic_boundaries(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("DB/provider forbidden in envelope unit tests")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(access, "async_session_maker", forbidden)
    monkeypatch.setattr(accounts, "async_session_maker", forbidden)
    monkeypatch.setattr(settings, "CREDENTIALS_KEY", Fernet.generate_key().decode())


def account(**changes):
    values = dict(
        id=99,
        owner_user_id=14,
        provider_id="telegram",
        auth_method="mtproto",
        subject_key="90013",
        revision=2,
        state="active",
    )
    values.update(changes)
    row = SimpleNamespace(**values)
    row.secret_encrypted = encrypt_secret(json.dumps({"identity": accounts._identity(row), "material": MATERIAL}))
    return row


def rewrite(row, change):
    payload = json.loads(decrypt_secret(row.secret_encrypted))
    change(payload)
    row.secret_encrypted = encrypt_secret(json.dumps(payload))


def test_real_synthetic_crypto_uses_one_decoder_but_reuse_never_returns_plaintext():
    row = account()
    assert accounts._decode_telegram_material(row) == MATERIAL
    assert accounts._check_reusable(row) is None


@pytest.mark.parametrize(
    "key", ["purpose", "format", "id", "owner_user_id", "provider_id", "auth_method", "subject_key", "revision"]
)
def test_every_envelope_identity_field_is_bound_to_the_row(key):
    row = account()
    rewrite(row, lambda payload: payload["identity"].update({key: "SYNTHETIC-WRONG"}))
    with pytest.raises(AuthorizationUnavailable):
        accounts._decode_telegram_material(row)


@pytest.mark.parametrize(
    "key,value", [("format", True), ("format", 1.0), ("revision", 2.0), ("id", 99.0), ("owner_user_id", 14.0)]
)
def test_boolean_and_float_identity_pins_are_not_integer_pins(key, value):
    row = account()
    rewrite(row, lambda payload: payload["identity"].update({key: value}))
    with pytest.raises(AuthorizationUnavailable):
        accounts._decode_telegram_material(row)


@pytest.mark.parametrize(
    "key,value", [("provider_id", "vk"), ("auth_method", "bot"), ("state", "revoked"), ("state", "reauth")]
)
def test_non_reusable_accounts_never_decrypt(monkeypatch, key, value):
    row = account(**{key: value})

    def forbidden(*args):
        pytest.fail("capability/state gate must precede decryption")

    monkeypatch.setattr(accounts, "decrypt_secret", forbidden)
    with pytest.raises(AuthorizationUnavailable):
        accounts._decode_telegram_material(row)


@pytest.mark.parametrize(
    "key,value",
    [
        ("api_id", True),
        ("api_id", 0),
        ("api_id", -1),
        ("api_id", "123"),
        ("api_id", 1.0),
        ("api_hash", ""),
        ("api_hash", "x" * 257),
        ("api_hash", None),
        ("api_hash", "bad\nvalue"),
        ("session", ""),
        ("session", "x" * 16385),
        ("session", []),
        ("session", "bad\x00value"),
    ],
)
def test_invalid_material_is_never_returned(key, value):
    row = account()
    rewrite(row, lambda payload: payload["material"].update({key: value}))
    with pytest.raises(AuthorizationUnavailable):
        accounts._decode_telegram_material(row)


@pytest.mark.parametrize("payload", [None, [], {}, {"identity": {}}, {"identity": {}, "material": {}, "extra": 1}])
def test_malformed_envelope_is_opaque(payload):
    row = account()
    row.secret_encrypted = encrypt_secret(json.dumps(payload))
    with pytest.raises(AuthorizationUnavailable, match="^connection_unavailable$"):
        accounts._decode_telegram_material(row)


@pytest.mark.parametrize(
    "payload_key,value",
    [
        ("identity", []),
        ("identity", None),
        ("material", []),
        ("material", None),
        ("material", {**MATERIAL, "extra": 1}),
        ("material", {"api_id": 123}),
    ],
)
def test_envelope_sections_and_exact_material_shape(payload_key, value):
    row = account()
    rewrite(row, lambda payload: payload.update({payload_key: value}))
    with pytest.raises(AuthorizationUnavailable):
        accounts._decode_telegram_material(row)


@pytest.mark.parametrize("failure", ["ciphertext", "key", "missing_key", "json"])
def test_unreadable_envelopes_are_opaque_without_logs(monkeypatch, caplog, failure):
    row = account()
    if failure == "ciphertext":
        row.secret_encrypted = "SYNTHETIC-CORRUPT-CIPHERTEXT"
    elif failure == "json":
        row.secret_encrypted = encrypt_secret("SYNTHETIC-NOT-JSON")
    else:
        monkeypatch.setattr(
            settings, "CREDENTIALS_KEY", None if failure == "missing_key" else Fernet.generate_key().decode()
        )
    with pytest.raises(AuthorizationUnavailable) as error:
        accounts._decode_telegram_material(row)
    assert "SYNTHETIC" not in str(error.value) + repr(error.value) + caplog.text


class Session:
    def __init__(self, row):
        self.row = row
        self.statements = []

    async def execute(self, statement):
        self.statements.append(statement)
        return self

    def one_or_none(self):
        return self.row


def source():
    return SimpleNamespace(
        id=11,
        tenant_id=12,
        platform_id=13,
        external_id="synthetic-target",
        owner_user_id=14,
        params={"mode": "user", "telegram_authorization": {"grant_id": 15, "authorization_revision": 2}},
    )


@pytest.mark.asyncio
async def test_material_query_checks_all_bindings_in_same_snapshot_and_refreshes_identity_map():
    row = account()
    session = Session((source(), row, "viewer"))
    with tenant_scope(12):
        snapshot, material = await access._resolve_source_grant_material(session, access.SourceGrantSnapshot(PIN, 99))
    assert snapshot == access.SourceGrantSnapshot(PIN, 99) and material == MATERIAL
    assert len(session.statements) == 1
    statement = session.statements[0]
    assert statement.get_execution_options()["populate_existing"] is True
    sql = str(statement.compile(dialect=postgresql.dialect()))
    assert "secret_encrypted" in sql.split("\nFROM", 1)[0]
    assert "FOR UPDATE" not in sql and sql.count("is_active IS true") == 6
    assert "personal_connector_authorizations.id =" in sql
    assert 99 in statement.compile(dialect=postgresql.dialect()).params.values()
    for predicate in (
        "workspace_authorization_grants.authorization_revision =",
        "personal_connector_authorizations.revision =",
        "personal_connector_authorizations.owner_user_id = test_schema.workspace_authorization_grants.owner_user_id",
        "tenant_users.channel =",
        "platforms.platform_type =",
        "personal_connector_authorizations.state =",
    ):
        assert predicate in sql


@pytest.mark.asyncio
@pytest.mark.parametrize("rejection", ["missing", "role", "binding", "tenant"])
async def test_no_decryption_before_source_and_rights_validation(monkeypatch, rejection):
    current = source()
    row = account()
    if rejection == "binding":
        current.params = {}
    session = Session(
        None if rejection == "missing" else (current, row, "superuser" if rejection == "role" else "viewer")
    )

    def forbidden(*args):
        pytest.fail("invalid selection must never decrypt")

    monkeypatch.setattr(accounts, "decrypt_secret", forbidden)
    with tenant_scope(13 if rejection == "tenant" else 12), pytest.raises(AuthorizationUnavailable):
        await access._resolve_source_grant_material(session, access.SourceGrantSnapshot(PIN, 99))
    assert len(session.statements) == (0 if rejection == "tenant" else 1)


@pytest.mark.asyncio
async def test_rebound_account_is_rejected_before_decryption_even_for_forged_sql_result(monkeypatch):
    row = account(id=100)
    session = Session((source(), row, "viewer"))

    def forbidden(*args):
        pytest.fail("another account must not decrypt under old account lock")

    monkeypatch.setattr(accounts, "decrypt_secret", forbidden)
    with tenant_scope(12), pytest.raises(AuthorizationUnavailable):
        await access._resolve_source_grant_material(session, access.SourceGrantSnapshot(PIN, 99))


class AdmissionSession(Session):
    def __init__(self, missing=None):
        super().__init__((source(), 99, "viewer"))
        self.missing = missing

    def scalar_one_or_none(self):
        return None if len(self.statements) == self.missing else 99


@pytest.mark.asyncio
async def test_admission_locks_account_grant_source_then_owner_rights_without_ciphertext(monkeypatch):
    def forbidden(*args):
        pytest.fail("admission never decrypts")

    monkeypatch.setattr(accounts, "decrypt_secret", forbidden)
    session = AdmissionSession()
    expected = access.SourceGrantSnapshot(PIN, 99)
    with tenant_scope(12):
        assert await access._lock_source_grant_for_admission(session, expected) == expected
    compiled = [str(s.compile(dialect=postgresql.dialect())) for s in session.statements]
    assert len(compiled) == 4
    assert "personal_connector_authorizations" in compiled[0] and compiled[0].endswith("FOR SHARE")
    assert "workspace_authorization_grants" in compiled[1] and compiled[1].endswith("FOR SHARE")
    assert "sources" in compiled[2] and compiled[2].endswith("FOR UPDATE")
    assert compiled[3].endswith("FOR SHARE OF platforms, tenants, users, tenant_users, workspace_roles")
    for sql in compiled:
        assert "secret_encrypted" not in sql.split("\nFROM", 1)[0]
    assert "workspace_authorization_grants.authorization_id =" in compiled[1]


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", [1, 2, 3])
async def test_missing_admission_row_never_yields_or_continues_to_later_locks(missing):
    session = AdmissionSession(missing=missing)
    with tenant_scope(12), pytest.raises(AuthorizationUnavailable):
        await access._lock_source_grant_for_admission(session, access.SourceGrantSnapshot(PIN, 99))
    assert len(session.statements) == missing


@pytest.mark.asyncio
async def test_admission_lock_ids_are_not_a_replacement_for_full_pin_validation():
    session = AdmissionSession()
    session.row = (source(), 100, "viewer")
    with tenant_scope(12), pytest.raises(AuthorizationUnavailable):
        await access._lock_source_grant_for_admission(session, access.SourceGrantSnapshot(PIN, 99))
