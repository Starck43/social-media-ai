from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from local_connector.index import IndexEngine, IndexStore
from local_connector.paths import SelectedRoot
from local_connector.search import FolderRuleSet
from local_connector.transfer import (
    MAX_FETCH_BYTES,
    FetchConsent,
    FetchConsentError,
    FetchError,
    FileVersion,
    LocalFetcher,
    compare_versions,
)

REQUEST_ID = "a" * 32


class _Issuer:
    def __init__(self, token: str | None = "exact-version-1") -> None:
        self.token = token
        self.calls: list[dict] = []

    def assert_exact_version(self, *, object_id, root_id, relative_path, version):
        self.calls.append({"object_id": object_id, "root_id": root_id, "path": relative_path, "version": version})
        return self.token


def _indexed(tmp_path: Path, *, content: str = "confidential payload needle"):
    root = tmp_path / "docs"
    root.mkdir(exist_ok=True)
    (root / "file.txt").write_text(content, encoding="utf-8")
    store = IndexStore(tmp_path / "cache" / "index.sqlite")
    engine = IndexEngine(store)
    selected = SelectedRoot(root)
    engine.scan([selected])
    record = store.list_files()[0]
    return store, selected, record


def _consent(record, *, request_id: str = REQUEST_ID, **overrides) -> FetchConsent:
    payload = {
        "request_id": request_id,
        "object_id": record.path_hash,
        "root_id": "work",
        "relative_path": record.relative_path,
        "destination": "private_storage",
        "confirmed_by_user_id": 7,
        "confirmed_at": 1_700_000_000,
    }
    payload.update(overrides)
    return FetchConsent(**payload)


def test_fetch_without_user_confirmation_cannot_be_constructed() -> None:
    with pytest.raises(FetchConsentError):
        FetchConsent(
            request_id=REQUEST_ID,
            object_id="object",
            root_id="work",
            relative_path="file.txt",
            confirmed_by_user_id=0,
            confirmed_at=1_700_000_000,
        )
    with pytest.raises(FetchConsentError):
        FetchConsent(
            request_id=REQUEST_ID,
            object_id="object",
            root_id="work",
            relative_path="file.txt",
            confirmed_by_user_id=7,
            confirmed_at=0,
        )
    with pytest.raises(FetchConsentError):
        FetchConsent(
            request_id=REQUEST_ID,
            object_id="object",
            root_id="work",
            relative_path="file.txt",
            destination="cloud_catalog",  # type: ignore[arg-type]
            confirmed_by_user_id=7,
            confirmed_at=1_700_000_000,
        )


def test_consent_binds_one_object_root_and_path_only() -> None:
    consent = FetchConsent(
        request_id=REQUEST_ID,
        object_id="object_a",
        root_id="work",
        relative_path="file.txt",
        confirmed_by_user_id=7,
        confirmed_at=1_700_000_000,
    )
    assert consent.binds(object_id="object_a", root_id="work", relative_path="file.txt")
    assert not consent.binds(object_id="object_b", root_id="work", relative_path="file.txt")
    assert not consent.binds(object_id="object_a", root_id="other", relative_path="file.txt")
    assert not consent.binds(object_id="object_a", root_id="work", relative_path="other.txt")


def test_fetch_is_ineligible_without_a_reviewed_exact_version_issuer(tmp_path: Path) -> None:
    store, selected, record = _indexed(tmp_path)
    fetcher = LocalFetcher(store, {"work": selected})
    result = fetcher.fetch(_consent(record))
    assert result.state == "ineligible"
    assert result.bytes_read == 0
    assert result.complete is False

    result = fetcher.fetch(_consent(record), issuer=_Issuer(token=None))
    assert result.state == "ineligible"
    assert result.sha256 is None


def test_fetch_streams_one_object_with_hash_and_version_evidence(tmp_path: Path) -> None:
    store, selected, record = _indexed(tmp_path)
    issuer = _Issuer()
    fetcher = LocalFetcher(store, {"work": selected})
    staged: list[bytes] = []

    result = fetcher.fetch(_consent(record), issuer=issuer, sink=staged.append)

    assert result.state == "complete"
    assert result.complete is True
    assert result.bytes_read == record.size == sum(len(block) for block in staged)
    assert result.sha256 == record.content_hash
    assert result.version_assertion == "exact-version-1"
    assert result.evidence_kind == "local_fetch"
    assert hashlib.sha256(b"".join(staged)).hexdigest() == record.content_hash
    assert issuer.calls and issuer.calls[0]["version"].content_hash == record.content_hash


def test_fetch_denies_unknown_object_and_mismatched_consent(tmp_path: Path) -> None:
    store, selected, record = _indexed(tmp_path)
    fetcher = LocalFetcher(store, {"work": selected})

    unknown = _consent(record, object_id="missing_object")
    assert fetcher.fetch(unknown, issuer=_Issuer()).state == "denied"

    other_path = _consent(record, relative_path="other.txt")
    assert fetcher.fetch(other_path, issuer=_Issuer()).state == "denied"

    other_root = _consent(record, root_id="elsewhere")
    assert fetcher.fetch(other_root, issuer=_Issuer()).state == "denied"


def test_fetch_refuses_when_the_file_changed_after_indexing(tmp_path: Path) -> None:
    store, selected, record = _indexed(tmp_path)
    (selected.path / "file.txt").write_text("rewritten after indexing", encoding="utf-8")
    issuer = _Issuer()
    result = LocalFetcher(store, {"work": selected}).fetch(_consent(record), issuer=issuer)

    assert result.state == "stale"
    assert result.version is not None and result.version.content_hash == record.content_hash
    assert issuer.calls == []


def test_fetch_refuses_replaced_root(tmp_path: Path) -> None:
    store, selected, record = _indexed(tmp_path)
    moved = tmp_path / "docs-moved"
    selected.path.rename(moved)
    selected.path.mkdir()
    (selected.path / "file.txt").write_text("different root", encoding="utf-8")

    result = LocalFetcher(store, {"work": selected}).fetch(_consent(record), issuer=_Issuer())
    assert result.state == "stale"


def test_fetch_enforces_the_byte_ceiling_without_reporting_partial_as_complete(tmp_path: Path) -> None:
    store, selected, record = _indexed(tmp_path, content="x" * 4096)
    fetcher = LocalFetcher(store, {"work": selected}, max_bytes=1024)
    staged: list[bytes] = []

    result = fetcher.fetch(_consent(record), issuer=_Issuer(), sink=staged.append)

    assert result.state == "limit_exceeded"
    assert result.complete is False
    assert result.sha256 is None
    assert result.bytes_read == 1024


def test_fetch_enforces_the_deadline(tmp_path: Path) -> None:
    store, selected, record = _indexed(tmp_path)
    ticks = iter([0.0, 0.0, 99.0, 99.0, 99.0])
    fetcher = LocalFetcher(store, {"work": selected}, clock=lambda: next(ticks, 99.0))

    assert fetcher.fetch(_consent(record), issuer=_Issuer()).state == "limit_exceeded"


def test_fetch_denies_when_folder_policy_excludes_the_path(tmp_path: Path) -> None:
    store, selected, record = _indexed(tmp_path)
    policy = FolderRuleSet(denied_folders={"work": ("file.txt",)})
    result = LocalFetcher(store, {"work": selected}, folder_policy=policy).fetch(_consent(record), issuer=_Issuer())
    assert result.state == "denied"


def test_fetch_limits_are_bounded_by_the_protocol_ceiling(tmp_path: Path) -> None:
    store, selected, _ = _indexed(tmp_path)
    with pytest.raises(FetchError):
        LocalFetcher(store, {"work": selected}, max_bytes=MAX_FETCH_BYTES + 1)
    with pytest.raises(FetchError):
        LocalFetcher(store, {"work": selected}, timeout_seconds=0)


def test_version_evidence_never_upgrades_an_unknown_observation() -> None:
    indexed = FileVersion("0" * 64, 10, 1)
    same = FileVersion("0" * 64, 10, 1)
    changed = FileVersion("1" * 64, 10, 1)
    resized = FileVersion("0" * 64, 11, 1)

    assert compare_versions(indexed, same).state == "current"
    assert compare_versions(indexed, changed).state == "changed"
    assert compare_versions(indexed, resized).state == "changed"
    assert compare_versions(indexed, None).state == "unknown"
    assert compare_versions(None, same).state == "unknown"
