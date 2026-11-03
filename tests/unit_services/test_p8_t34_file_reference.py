"""Opaque file-selector checks with fake keys; no provider or DB."""

import base64
import json

import pytest
from cryptography.fernet import Fernet

from app.core.config import settings
from app.integrations.file_workspace import yandex_file_reference as refs
from app.integrations.file_workspace.policy import FileOperation, FolderRestriction, ReadOnlyFolderPolicy
from app.integrations.file_workspace.yandex_disk import DiskItem

__source_only__ = True


@pytest.fixture(autouse=True)
def fake_key_and_clock(monkeypatch):
    monkeypatch.setattr(settings, "CREDENTIALS_KEY", Fernet.generate_key().decode())
    monkeypatch.setattr(refs, "_clock", lambda: 1_700_000_000)


def context():
    return {"tenant_id": 1, "actor_id": 2, "connection_id": 3, "session_id": "fake-session-id-123456789"}


def file():
    return DiskItem("disk:/work/private report.pdf", "private report.pdf", "file")


def policy():
    return ReadOnlyFolderPolicy("/work")


def issue():
    return refs.issue_file_reference(**context(), item=file(), policy=policy())


def test_reference_is_opaque_bound_and_hides_path_in_repr():
    token = issue()
    assert "private report" not in token
    assert b"private report" not in base64.urlsafe_b64decode(token.encode())
    selected = refs.resolve_file_reference(**context(), token=token, policy=policy())
    assert selected.path == "disk:/work/private report.pdf"
    assert "private report" not in repr(selected)
    assert issue() != token


@pytest.mark.parametrize(
    "field,value",
    [("tenant_id", 2), ("actor_id", 3), ("connection_id", 4), ("session_id", "other-session-id-123456789")],
)
def test_reference_cannot_cross_context(field, value):
    token = issue()
    args = context()
    args[field] = value
    with pytest.raises(refs.FileReferenceError, match="^file_unavailable$"):
        refs.resolve_file_reference(**args, token=token, policy=policy())


def test_expiry_and_key_rotation_fail_closed(monkeypatch):
    token = issue()
    monkeypatch.setattr(refs, "_clock", lambda: 1_700_000_300)
    assert refs.resolve_file_reference(**context(), token=token, policy=policy()).path == file().path
    monkeypatch.setattr(refs, "_clock", lambda: 1_700_000_301)
    with pytest.raises(refs.FileReferenceError):
        refs.resolve_file_reference(**context(), token=token, policy=policy())
    monkeypatch.setattr(refs, "_clock", lambda: 1_700_000_000)
    monkeypatch.setattr(settings, "CREDENTIALS_KEY", Fernet.generate_key().decode())
    with pytest.raises(refs.FileReferenceError):
        refs.resolve_file_reference(**context(), token=token, policy=policy())


@pytest.mark.parametrize("operation", [FileOperation.LIST, FileOperation.READ])
def test_current_tighter_policy_denies_reference(operation):
    token = issue()
    restricted = ReadOnlyFolderPolicy("/work", (FolderRestriction("/work", frozenset({operation})),))
    with pytest.raises(refs.FileReferenceError):
        refs.resolve_file_reference(**context(), token=token, policy=restricted)
    with pytest.raises(refs.FileReferenceError):
        refs.issue_file_reference(**context(), item=file(), policy=restricted)


@pytest.mark.parametrize(
    "item",
    [
        DiskItem("disk:/work/sub", "sub", "dir"),
        DiskItem("disk:/work/a", "wrong-name", "file"),
        DiskItem("disk:/outside/a", "a", "file"),
        DiskItem("disk:/work/../a", "a", "file"),
        DiskItem("disk:/work/%2fa", "%2fa", "file"),
        DiskItem("disk:/", "", "file"),
        DiskItem("disk:/work/" + "x" * 4097, "x" * 4097, "file"),
    ],
)
def test_invalid_selection_is_denied(item):
    with pytest.raises(refs.FileReferenceError, match="^file_unavailable$"):
        refs.issue_file_reference(**context(), item=item, policy=policy())


@pytest.mark.parametrize(
    "field,value",
    [
        ("tenant_id", True),
        ("actor_id", 0),
        ("connection_id", -1),
        ("session_id", "short"),
        ("session_id", "wrong session id 1234"),
    ],
)
def test_invalid_context_is_denied(field, value):
    args = context()
    args[field] = value
    with pytest.raises(refs.FileReferenceError):
        refs.issue_file_reference(**args, item=file(), policy=policy())


@pytest.mark.parametrize("token", ["", "bad", "x" * 8193, "недопустимо"])
def test_invalid_token_does_not_expose_information(token):
    with pytest.raises(refs.FileReferenceError, match="^file_unavailable$") as exc:
        refs.resolve_file_reference(**context(), token=token, policy=policy())
    assert exc.value.__cause__ is None


def test_tamper_and_other_cipher_purpose_do_not_validate():
    token = issue()
    # Flip an authenticated ciphertext byte. The final base64 character of a
    # Fernet token only carries padding bits for ~1 key in 64, so mutating it
    # made this test order-dependent rather than a tamper check.
    raw = bytearray(base64.urlsafe_b64decode(token.encode()))
    raw[25] ^= 0x01
    altered = base64.urlsafe_b64encode(bytes(raw)).decode()
    with pytest.raises(refs.FileReferenceError):
        refs.resolve_file_reference(**context(), token=altered, policy=policy())
    other = (
        Fernet(settings.CREDENTIALS_KEY.encode())
        .encrypt_at_time(json.dumps({"path": file().path}).encode(), refs._clock())
        .decode()
    )
    with pytest.raises(refs.FileReferenceError):
        refs.resolve_file_reference(**context(), token=other, policy=policy())


def test_missing_or_invalid_key_fails_closed(monkeypatch):
    for key in (None, "not-a-key"):
        monkeypatch.setattr(settings, "CREDENTIALS_KEY", key)
        with pytest.raises(refs.FileReferenceError, match="^file_unavailable$"):
            refs.issue_file_reference(**context(), item=file(), policy=policy())


def test_reference_denied_when_root_changes():
    token = issue()
    with pytest.raises(refs.FileReferenceError, match="^file_unavailable$"):
        refs.resolve_file_reference(**context(), token=token, policy=ReadOnlyFolderPolicy("/other"))


def test_reference_denied_when_restrictions_change():
    """A restriction-only change must not leave older selectors usable."""
    token = issue()
    tightened = ReadOnlyFolderPolicy("/work", (FolderRestriction("/work/sub", frozenset({FileOperation.READ})),))
    with pytest.raises(refs.FileReferenceError, match="^file_unavailable$"):
        refs.resolve_file_reference(**context(), token=token, policy=tightened)
    assert tightened.binding() != policy().binding()


@pytest.mark.parametrize(
    "change",
    [
        {"purpose": "other"},
        {"tenant": True},
        {"path": "disk:/work/../escape"},
        {"extra": "unexpected"},
        {"session": "недопустимо"},
    ],
)
def test_authenticated_but_malformed_payloads_are_rejected(change):
    data = {
        "purpose": refs._PURPOSE,
        "tenant": 1,
        "actor": 2,
        "connection": 3,
        "session": context()["session_id"],
        "path": file().path,
    }
    data.update(change)
    token = refs._fernet(policy()).encrypt_at_time(json.dumps(data).encode(), refs._clock()).decode()
    with pytest.raises(refs.FileReferenceError, match="^file_unavailable$"):
        refs.resolve_file_reference(**context(), token=token, policy=policy())


def test_reference_from_far_future_is_rejected(monkeypatch):
    token = issue()
    monkeypatch.setattr(refs, "_clock", lambda: 1_699_999_939)
    with pytest.raises(refs.FileReferenceError, match="^file_unavailable$"):
        refs.resolve_file_reference(**context(), token=token, policy=policy())
