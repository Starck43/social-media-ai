from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from local_connector.index import IndexEngine, IndexStore
from local_connector.paths import SelectedRoot
from local_connector.search import (
    FolderRuleSet,
    LocalSearchService,
    RootRegistration,
    SearchError,
    SearchFilters,
    SearchProtocolError,
    SearchQuery,
    extract_snippet,
)


def _setup_store(tmp_path: Path):
    store = IndexStore(tmp_path / "cache" / "index.sqlite")
    engine = IndexEngine(store)
    return store, engine


def test_bm25_ranking_orders_stronger_matches_first_and_deduplicates_chunks(tmp_path: Path) -> None:
    root = tmp_path / "docs"
    root.mkdir()
    (root / "low.txt").write_text("budget summary with miscellaneous appendix notes", encoding="utf-8")
    (root / "high.txt").write_text(
        "budget forecast budget allocation budget review budget target",
        encoding="utf-8",
    )
    # Multi-chunk file where second chunk matches stronger than first.
    (root / "multi.txt").write_text(
        ("filler text " * 400) + "\nbudget audit budget control budget ledger",
        encoding="utf-8",
    )

    store, engine = _setup_store(tmp_path)
    try:
        selected = SelectedRoot(root)
        engine.scan([selected])
        service = LocalSearchService(
            store,
            [RootRegistration(root_id="work", selected=selected, boundary="local_only")],
        )
        result = service.search(SearchQuery(query="budget", limit=10))
        paths = [hit.reference.relative_path for hit in result.hits]
        assert len(paths) == 3
        assert paths[0] == "high.txt"
        assert paths[-1] == "low.txt"
        assert result.hits[0].bm25 <= result.hits[-1].bm25
        assert result.hits[0].score >= result.hits[-1].score
        assert result.hits[0].rank >= result.hits[-1].rank
        assert result.hits[0].reference.content_hash
        assert result.hits[0].reference.mtime_ns > 0
        assert result.hits[0].reference.absolute_path is None
    finally:
        store.close()


def test_query_parser_handles_punctuation_keywords_unicode_and_bounds(tmp_path: Path) -> None:
    root = tmp_path / "docs"
    root.mkdir()
    (root / "notes.txt").write_text("C++ alpha-beta report_v2 not only отчёт квартал", encoding="utf-8")

    store, engine = _setup_store(tmp_path)
    try:
        selected = SelectedRoot(root)
        engine.scan([selected])
        service = LocalSearchService(
            store,
            [RootRegistration(root_id="work", selected=selected)],
        )
        for q in ('"alpha-beta"', "C++", "report_v2", "not only", "отчёт квартал"):
            res = service.search(SearchQuery(query=q))
            assert [h.reference.relative_path for h in res.hits] == ["notes.txt"], f"failed for {q}"

        # Punctuation-only queries do not raise sqlite3 OperationalError.
        assert service.search(SearchQuery(query="!!! ---")).hits == ()

        with pytest.raises(SearchError):
            SearchQuery(query="   ")
        with pytest.raises(SearchError):
            SearchQuery(query="я" * 2049)
        with pytest.raises(SearchError):
            SearchQuery(query="alpha", limit=51)
        with pytest.raises(SearchError):
            SearchQuery(query="alpha", max_snippet_chars=513)
        with pytest.raises(SearchError):
            SearchQuery(query="alpha", max_snippets=9)
    finally:
        store.close()


def test_filters_by_type_date_size_folder_and_root(tmp_path: Path) -> None:
    root_a = tmp_path / "root_a"
    root_b = tmp_path / "root_b"
    (root_a / "reports" / "2026").mkdir(parents=True)
    (root_a / "reports" / "2025").mkdir(parents=True)
    root_b.mkdir()

    target = root_a / "reports" / "2026" / "q1.md"
    target.write_text("quarterly target metrics " + ("x" * 120), encoding="utf-8")

    old_file = root_a / "reports" / "2025" / "old.txt"
    old_file.write_text("quarterly target metrics", encoding="utf-8")
    now_ns = time.time_ns()
    os.utime(old_file, ns=(now_ns - 10 * 86400 * 10**9, now_ns - 10 * 86400 * 10**9))
    os.utime(target, ns=(now_ns, now_ns))

    other_root_file = root_b / "other.md"
    other_root_file.write_text("quarterly target metrics " + ("y" * 120), encoding="utf-8")

    store, engine = _setup_store(tmp_path)
    try:
        sel_a, sel_b = SelectedRoot(root_a), SelectedRoot(root_b)
        engine.scan([sel_a, sel_b])
        service = LocalSearchService(
            store,
            [
                RootRegistration(root_id="alpha", selected=sel_a),
                RootRegistration(root_id="beta", selected=sel_b),
            ],
        )

        # File type filter (.md / md)
        res_type = service.search(SearchQuery(query="quarterly", filters=SearchFilters(file_types=("md",))))
        assert {h.reference.relative_path for h in res_type.hits} == {"reports/2026/q1.md", "other.md"}

        # Folder filter
        res_folder = service.search(SearchQuery(query="quarterly", filters=SearchFilters(folders=("reports/2026",))))
        assert [h.reference.relative_path for h in res_folder.hits] == ["reports/2026/q1.md"]

        # Size filter
        res_size = service.search(
            SearchQuery(query="quarterly", filters=SearchFilters(min_size=100, max_size=500, roots=("alpha",)))
        )
        assert [h.reference.relative_path for h in res_size.hits] == ["reports/2026/q1.md"]

        # Modified date filter (last 2 days)
        res_date = service.search(
            SearchQuery(
                query="quarterly",
                filters=SearchFilters(modified_after_ns=now_ns - 2 * 86400 * 10**9),
            )
        )
        assert {h.reference.relative_path for h in res_date.hits} == {"reports/2026/q1.md", "other.md"}

        # Live mtime change out of filter window is excluded immediately
        os.utime(target, ns=(now_ns - 20 * 86400 * 10**9, now_ns - 20 * 86400 * 10**9))
        res_after_touch = service.search(
            SearchQuery(
                query="quarterly",
                filters=SearchFilters(
                    roots=("alpha",),
                    modified_after_ns=now_ns - 2 * 86400 * 10**9,
                ),
            )
        )
        assert res_after_touch.hits == ()
    finally:
        store.close()


def test_snippet_extraction_centers_on_match_and_enforces_response_caps(tmp_path: Path) -> None:
    long_text = ("prefix filler words " * 120) + "UNIQUE_MARKER_TOKEN vital context here" + (" trailing noise" * 120)
    snippet = extract_snippet(long_text, "UNIQUE_MARKER_TOKEN", max_chars=200)
    assert snippet is not None
    assert "UNIQUE_MARKER_TOKEN" in snippet
    assert len(snippet) <= 200
    assert len(snippet) < len(long_text)

    root = tmp_path / "docs"
    root.mkdir()
    for idx in range(12):
        (root / f"doc_{idx:02d}.txt").write_text(
            f"preamble {idx} " + ("padding " * 80) + "signal phrase target",
            encoding="utf-8",
        )

    store, engine = _setup_store(tmp_path)
    try:
        selected = SelectedRoot(root)
        engine.scan([selected])
        service = LocalSearchService(
            store,
            [RootRegistration(root_id="work", selected=selected, allow_absolute_paths=True)],
        )
        res = service.search(SearchQuery(query="signal", limit=12, max_snippet_chars=120, allow_absolute_paths=True))
        assert len(res.hits) == 12
        # At most 8 snippets per response
        with_snippets = [h for h in res.hits if h.snippet is not None]
        without_snippets = [h for h in res.hits if h.snippet is None]
        assert len(with_snippets) == 8
        assert len(without_snippets) == 4
        assert all(len(h.snippet) <= 120 for h in with_snippets if h.snippet is not None)
        assert all("signal" in h.snippet for h in with_snippets if h.snippet is not None)
        assert res.hits[0].reference.absolute_path is not None

        # allow_absolute_paths=False keeps absolute_path stripped
        res_rel = service.search(SearchQuery(query="signal", limit=5, allow_absolute_paths=False))
        assert all(h.reference.absolute_path is None for h in res_rel.hits)
    finally:
        store.close()


def test_deleted_unreadable_symlinked_excluded_and_policy_denied_files_are_omitted(tmp_path: Path) -> None:
    root = tmp_path / "docs"
    (root / "allowed").mkdir(parents=True)
    (root / "restricted" / "child").mkdir(parents=True)
    outside = tmp_path / "outside.txt"
    outside.write_text("confidential outside needle", encoding="utf-8")

    kept = root / "allowed" / "kept.txt"
    removed = root / "allowed" / "removed.txt"
    unreadable = root / "allowed" / "unreadable.txt"
    write_only = root / "allowed" / "write_only.txt"
    swapped = root / "allowed" / "swapped.txt"
    policy_denied = root / "restricted" / "child" / "hidden.txt"

    for path in (kept, removed, unreadable, write_only, swapped, policy_denied):
        path.write_text("needle compliance audit", encoding="utf-8")

    store, engine = _setup_store(tmp_path)
    try:
        selected = SelectedRoot(root)
        engine.scan([selected])

        # Mutate filesystem state AFTER indexing
        removed.unlink()
        unreadable.chmod(0o000)
        write_only.chmod(0o200)
        swapped.unlink()
        swapped.symlink_to(outside)

        policy = FolderRuleSet(
            allowed_folders={"work": ("allowed", "restricted/child")},
            denied_folders={"work": ("restricted",)},
        )
        service = LocalSearchService(
            store,
            [RootRegistration(root_id="work", selected=selected)],
            folder_policy=policy,
        )
        res = service.search(SearchQuery(query="needle"))
        assert [h.reference.relative_path for h in res.hits] == ["allowed/kept.txt"]

        # Corrupt / raising folder policy fails closed
        def broken_policy(_root_id: str, _rel: str) -> bool:
            raise RuntimeError("corrupt policy")

        service_broken = LocalSearchService(
            store,
            [RootRegistration(root_id="work", selected=selected)],
            folder_policy=broken_policy,
        )
        assert service_broken.search(SearchQuery(query="needle")).hits == ()

        # Custom OS ACL hook denial removes kept.txt too
        service_acl_deny = LocalSearchService(
            store,
            [RootRegistration(root_id="work", selected=selected)],
            os_access_check=lambda _p, _st: False,
        )
        assert service_acl_deny.search(SearchQuery(query="needle")).hits == ()
    finally:
        for p in (unreadable, write_only):
            if p.exists():
                p.chmod(0o600)
        store.close()


def test_local_only_root_never_read_or_exposed_on_cloud_egress(tmp_path: Path) -> None:
    local_root = tmp_path / "private_local"
    local_root.mkdir()
    (local_root / "secret.txt").write_text("top secret cloudforbidden", encoding="utf-8")

    store, engine = _setup_store(tmp_path)
    try:
        selected = SelectedRoot(local_root)
        engine.scan([selected])
        acl_calls: list[Path] = []
        service = LocalSearchService(
            store,
            [RootRegistration(root_id="priv", selected=selected, boundary="local_only", cloud_consented=False)],
            os_access_check=lambda path, _st: acl_calls.append(path) or True,
        )
        with pytest.raises(SearchProtocolError) as exc_info:
            service.search(
                SearchQuery(query="cloudforbidden", filters=SearchFilters(roots=("priv",))), cloud_egress=True
            )
        assert exc_info.value.code == "denied"
        assert acl_calls == []
    finally:
        store.close()


def test_document_anchors_projected_from_indexed_records(tmp_path: Path) -> None:
    from local_connector.index import Chunk, FileRecord

    root = tmp_path / "docs"
    root.mkdir()
    doc_file = root / "sheet.xlsx"
    doc_file.write_bytes(b"placeholder")
    pdf_file = root / "deck.pdf"
    pdf_file.write_bytes(b"placeholder")

    store = IndexStore(tmp_path / "cache" / "index.sqlite")
    try:
        selected = SelectedRoot(root)
        root_hash = IndexEngine._root_hash(selected)
        st_x = doc_file.stat()
        st_p = pdf_file.stat()
        store.replace_file(
            FileRecord(
                path_hash=store.path_hash(f"{root_hash}\x00sheet.xlsx"),
                root_hash=root_hash,
                relative_path="sheet.xlsx",
                size=st_x.st_size,
                mtime_ns=st_x.st_mtime_ns,
                content_hash="a" * 64,
                file_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                document_modified=1_700_000_000,
            ),
            [Chunk(text="quarterly revenue projection", sheet_anchor="Revenue!C14")],
        )
        store.replace_file(
            FileRecord(
                path_hash=store.path_hash(f"{root_hash}\x00deck.pdf"),
                root_hash=root_hash,
                relative_path="deck.pdf",
                size=st_p.st_size,
                mtime_ns=st_p.st_mtime_ns,
                content_hash="b" * 64,
                file_type="application/pdf",
            ),
            [Chunk(text="quarterly executive deck", page_anchor="4")],
        )

        service = LocalSearchService(
            store,
            [RootRegistration(root_id="corp", selected=selected, boundary="company", cloud_consented=True)],
        )
        res = service.search(SearchQuery(query="quarterly", filters=SearchFilters(roots=("corp",))), cloud_egress=True)
        by_path = {h.reference.relative_path: h for h in res.hits}

        xlsx_ref = by_path["sheet.xlsx"].reference
        assert xlsx_ref.sheet_anchor == "Revenue"
        assert xlsx_ref.cell_anchor == "C14"
        assert xlsx_ref.document_modified == 1_700_000_000
        assert xlsx_ref.to_wire_dict()["sheet"] == "Revenue"

        pdf_ref = by_path["deck.pdf"].reference
        assert pdf_ref.page_anchor == "4"
        assert pdf_ref.wire_page() == 4
        assert pdf_ref.to_wire_dict()["page"] == 4
    finally:
        store.close()


def test_mime_filters_unix_dates_partial_limit_zero_snippet_and_delete(tmp_path: Path) -> None:
    root = tmp_path / "docs"
    root.mkdir()
    for i in range(4):
        (root / f"note_{i}.txt").write_text("telemetry metric signal", encoding="utf-8")

    store, engine = _setup_store(tmp_path)
    try:
        selected = SelectedRoot(root)
        engine.scan([selected])
        service = LocalSearchService(store)
        service.register_root(RootRegistration(root_id="work", selected=selected))

        now_s = int(time.time())
        res = service.search(
            SearchQuery(
                query="telemetry",
                limit=2,
                max_snippet_chars=0,
                filters=SearchFilters(
                    file_types=("text/*", "text/plain"),
                    modified_after=now_s - 60,
                    modified_before=now_s + 60,
                ),
            )
        )
        assert len(res.hits) == 2
        assert res.partial is True
        assert all(h.snippet is None for h in res.hits)

        # delete_all disables hits durably
        store.delete_all(pause=True)
        assert service.search(SearchQuery(query="telemetry")).hits == ()
    finally:
        store.close()


def test_acl_locked_directory_excluded_prefix_and_permits_policy_adapter(tmp_path: Path) -> None:
    root = tmp_path / "docs"
    locked_dir = root / "locked"
    secret_dir = root / "skipme"
    open_dir = root / "open"
    for d in (locked_dir, secret_dir, open_dir):
        d.mkdir(parents=True)
        (d / "item.txt").write_text("beacon findme", encoding="utf-8")
    (root / "open" / "denied_by_permits.txt").write_text("beacon findme", encoding="utf-8")

    store, engine = _setup_store(tmp_path)
    try:
        selected = SelectedRoot(root)
        engine.scan([selected])
        locked_dir.chmod(0o000)

        class PermitsPolicy:
            def permits(self, path: str, operation: str) -> bool:
                return operation == "read" and not path.endswith("denied_by_permits.txt")

        service = LocalSearchService(
            store,
            [RootRegistration(root_id="work", selected=selected)],
            excluded_paths=("skipme",),
            folder_policy=PermitsPolicy(),
        )
        res = service.search(SearchQuery(query="beacon"))
        assert [h.reference.relative_path for h in res.hits] == ["open/item.txt"]
    finally:
        locked_dir.chmod(0o700)
        store.close()
