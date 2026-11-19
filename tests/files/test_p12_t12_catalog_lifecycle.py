"""P12-T12 S1: Catalog lifecycle honesty on completed scans (test_schema, mocked provider)."""

import re
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest
from alembic import command
from alembic.config import Config
from bs4 import BeautifulSoup
from cryptography.fernet import Fernet
from sqlalchemy import delete, inspect, text, update

from app.core.config import settings
from app.core.database import async_session_maker, engine
from app.core.hashing import get_password_hash
from app.integrations.file_workspace.content_read import ReadPlainText
from app.integrations.file_workspace.insight_candidates import InsightCandidate, InsightCandidateBatch
from app.integrations.file_workspace.line_evidence import LineRange, build_line_evidence
from app.integrations.file_workspace.original_open import (
    OPEN_TTL,
    NativeLinkPolicy,
    NativeOriginalTarget,
    OriginalOpenUnavailable,
)
from app.integrations.file_workspace.plain_text import extract_plain_text
from app.integrations.file_workspace.private_admission import ExactVersionAssertion
from app.integrations.file_workspace.private_cipher import PrivateCipher
from app.integrations.file_workspace.yandex_disk import DiskItem, DiskPage
from app.main import create_application
from app.models import User
from app.models.file_workspace.private_insights import FileInsightMount
from app.models.managers.file_catalog_manager import CatalogInputError, FileCatalogManager
from app.models.managers.file_content_manager import FileContentManager
from app.models.managers.file_insight_manager import POLICY_REVISION, FileInsightManager
from app.models.managers.workspace_connection_manager import ConnectionDenied
from app.services.file_workspace import catalog_scan as scan
from app.services.file_workspace import original_open as original_service
from app.services.file_workspace.private_insights import PrivateInsightWorkflow
from tests.shared.file_catalog_fixtures import actor, admit, configure, data  # noqa: F401

pytestmark = pytest.mark.tenancy
manager = FileCatalogManager()
SCHEMA = "test_schema"


def _provider_page(items, *, path="disk:/work", offset=0, limit=100):
    return {
        "path": path,
        "type": "dir",
        "_embedded": {
            "items": [{"path": item_path, "name": name, "type": kind} for item_path, name, kind in items],
            "offset": offset,
            "limit": limit,
        },
    }


def _mock_client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def _run_exhausted_scan(args, *, folder, handler, page_size=10, recursive=False):
    await scan.start_catalog_scan(**args, folder=folder, page_size=page_size, recursive=recursive)
    async with _mock_client(handler) as client:
        while True:
            result = await scan.resume_catalog_scan(**args, client=client)
            if result["state"] != "partial":
                return result


async def test_single_folder_exhausted_scan_marks_missing_and_stale_honestly(data):
    with actor(data) as args:
        await admit(args, "kept.txt", "gone.txt", folder="disk:/work", kind="file")
        await admit(args, "kept_dir", "gone_dir", folder="disk:/work", kind="dir")
        await admit(args, "nested_kept.txt", folder="disk:/work/kept_dir", kind="file")
        await admit(args, "nested_stale.txt", folder="disk:/work/gone_dir", kind="file")
        await admit(args, "deep_stale.txt", folder="disk:/work/gone_dir/sub", kind="file")

        def handle(request):
            return httpx.Response(
                200,
                json=_provider_page(
                    [
                        ("disk:/work/kept.txt", "kept.txt", "file"),
                        ("disk:/work/kept_dir", "kept_dir", "dir"),
                    ]
                ),
            )

        status = await _run_exhausted_scan(args, folder="/work", handler=handle)
        assert status["state"] == "exhausted"

        items = {item["path"]: item for item in (await manager.list_page(**args))["items"]}
        assert items["disk:/work/kept.txt"]["lifecycle_state"] == "present"
        assert items["disk:/work/kept.txt"]["freshness"] == "metadata_observed"
        assert items["disk:/work/kept.txt"]["missing_since"] is None
        assert items["disk:/work/kept.txt"]["reconciled_at"] is not None

        assert items["disk:/work/kept_dir"]["lifecycle_state"] == "present"
        # Present subfolder's children were not visited by a shallow scan; must remain present.
        assert items["disk:/work/kept_dir/nested_kept.txt"]["lifecycle_state"] == "present"
        assert items["disk:/work/kept_dir/nested_kept.txt"]["reconciled_at"] is None

        for gone_path in ("disk:/work/gone.txt", "disk:/work/gone_dir"):
            assert items[gone_path]["lifecycle_state"] == "missing"
            assert items[gone_path]["freshness"] == "missing_in_last_scan"
            assert items[gone_path]["missing_since"] == items[gone_path]["reconciled_at"]
            assert items[gone_path]["observation_number"] == 1

        for stale_path in ("disk:/work/gone_dir/nested_stale.txt", "disk:/work/gone_dir/sub/deep_stale.txt"):
            assert items[stale_path]["lifecycle_state"] == "stale"
            assert items[stale_path]["freshness"] == "stale_parent_missing"
            assert items[stale_path]["missing_since"] == items[stale_path]["reconciled_at"]


async def test_recursive_exhausted_scan_marks_absent_subtree_missing_and_upgrades_stale(data):
    with actor(data) as args:
        await admit(args, "kept.txt", folder="disk:/work", kind="file")
        await admit(args, "kept_dir", "gone_dir", folder="disk:/work", kind="dir")
        await admit(args, "sub_kept.txt", "sub_gone.txt", folder="disk:/work/kept_dir", kind="file")
        await admit(args, "orphan.txt", folder="disk:/work/gone_dir", kind="file")

        # First run shallow scan where gone_dir vanished -> orphan.txt becomes stale.
        def shallow_handle(request):
            return httpx.Response(
                200,
                json=_provider_page(
                    [
                        ("disk:/work/kept.txt", "kept.txt", "file"),
                        ("disk:/work/kept_dir", "kept_dir", "dir"),
                    ]
                ),
            )

        assert (await _run_exhausted_scan(args, folder="/work", handler=shallow_handle, recursive=False))[
            "state"
        ] == "exhausted"
        before = {item["path"]: item for item in (await manager.list_page(**args))["items"]}
        assert before["disk:/work/gone_dir/orphan.txt"]["lifecycle_state"] == "stale"
        first_missing_since = before["disk:/work/gone_dir/orphan.txt"]["missing_since"]

        # Recursive exhausted scan sees kept_dir -> sub_kept.txt, missing sub_gone.txt & gone_dir/orphan.txt.
        def recursive_handle(request):
            path = request.url.params["path"]
            if path == "disk:/work":
                return httpx.Response(
                    200,
                    json=_provider_page(
                        [
                            ("disk:/work/kept.txt", "kept.txt", "file"),
                            ("disk:/work/kept_dir", "kept_dir", "dir"),
                        ]
                    ),
                )
            if path == "disk:/work/kept_dir":
                return httpx.Response(
                    200,
                    json=_provider_page(
                        [("disk:/work/kept_dir/sub_kept.txt", "sub_kept.txt", "file")], path="disk:/work/kept_dir"
                    ),
                )
            raise AssertionError(f"unexpected folder {path}")

        assert (await _run_exhausted_scan(args, folder="/work", handler=recursive_handle, recursive=True))[
            "state"
        ] == "exhausted"

        after = {item["path"]: item for item in (await manager.list_page(**args))["items"]}
        assert after["disk:/work/kept.txt"]["lifecycle_state"] == "present"
        assert after["disk:/work/kept_dir/sub_kept.txt"]["lifecycle_state"] == "present"
        assert after["disk:/work/kept_dir/sub_gone.txt"]["lifecycle_state"] == "missing"
        assert after["disk:/work/gone_dir/orphan.txt"]["lifecycle_state"] == "missing"
        assert after["disk:/work/gone_dir/orphan.txt"]["missing_since"] == first_missing_since


async def test_partial_errored_limited_and_adhoc_pages_never_infer_absence(data):
    with actor(data) as args:
        await admit(args, "existing.txt", folder="disk:/work", kind="file")
        await admit(args, "sub", folder="disk:/work", kind="dir")
        await admit(args, "child.txt", folder="disk:/work/sub", kind="file")

        # 1) Ad-hoc begin_page + admit_page does not reconcile absence.
        ticket = await manager.begin_page(**args, folder="disk:/work")
        await manager.admit_page(
            **args,
            ticket=ticket,
            page=DiskPage((DiskItem("disk:/work/adhoc.txt", "adhoc.txt", "file"),), None),
        )
        items = {item["path"]: item for item in (await manager.list_page(**args))["items"]}
        assert items["disk:/work/existing.txt"]["lifecycle_state"] == "present"
        assert items["disk:/work/adhoc.txt"]["reconciled_at"] is None

        # 2) Partial page followed by provider 429 error leaves older rows present.
        await scan.start_catalog_scan(**args, folder="/work", page_size=1)
        step = 0

        def partial_then_error(request):
            nonlocal step
            step += 1
            if step == 1:
                return httpx.Response(
                    200,
                    json=_provider_page([("disk:/work/adhoc.txt", "adhoc.txt", "file")], offset=0, limit=1),
                )
            return httpx.Response(429)

        async with _mock_client(partial_then_error) as client:
            r1 = await scan.resume_catalog_scan(**args, client=client)
            assert r1["state"] == "partial"
            r2 = await scan.resume_catalog_scan(**args, client=client)
            assert r2["state"] == "error"

        items = {item["path"]: item for item in (await manager.list_page(**args))["items"]}
        assert all(item["lifecycle_state"] == "present" for item in items.values())

        # 3) Limited scan (truncated=True) leaves older rows present.
        from app.models.managers.file_catalog_manager import CatalogScanClaim

        await scan.start_catalog_scan(**args, folder="/work", page_size=1)
        scan_token, lease_token, folder, offset, limit = await scan._claim(**args)
        lim_ticket = await manager.begin_page(**args, folder=folder)
        lim_result = await manager.admit_page(
            **args,
            ticket=lim_ticket,
            page=DiskPage((DiskItem("disk:/work/adhoc.txt", "adhoc.txt", "file"),), None, truncated=True),
            _scan_claim=CatalogScanClaim(scan_token, lease_token, offset, limit),
        )
        assert lim_result["scan"]["state"] == "limited"

        items = {item["path"]: item for item in (await manager.list_page(**args))["items"]}
        assert all(item["lifecycle_state"] == "present" for item in items.values())


async def test_list_denied_paths_and_outside_subfolders_are_never_marked_missing(data):
    with actor(data) as args:
        await admit(args, "visible.txt", "gone.txt", folder="disk:/work/sub", kind="file")
        await admit(args, "outside.txt", folder="disk:/work", kind="file")
        await admit(args, "sub", "secret", folder="disk:/work", kind="dir")
        await admit(args, "hidden.txt", folder="disk:/work/secret", kind="file")
        await configure(data, restrictions=[{"path": "disk:/work/secret", "deny": ["list"]}])

        # Exhausted scan on /work/sub leaves /work/outside.txt and /work/sub untouched.
        def sub_handle(request):
            return httpx.Response(
                200,
                json=_provider_page([("disk:/work/sub/visible.txt", "visible.txt", "file")], path="disk:/work/sub"),
            )

        assert (await _run_exhausted_scan(args, folder="/work/sub", handler=sub_handle))["state"] == "exhausted"
        items = {item["path"]: item for item in (await manager.list_page(**args))["items"]}
        assert items["disk:/work/sub/visible.txt"]["lifecycle_state"] == "present"
        assert items["disk:/work/sub/gone.txt"]["lifecycle_state"] == "missing"
        assert items["disk:/work/outside.txt"]["lifecycle_state"] == "present"
        assert items["disk:/work/outside.txt"]["reconciled_at"] is None

        # Exhausted recursive scan on /work never marks list-denied secret objects absent.
        def root_handle(request):
            path = request.url.params["path"]
            if path == "disk:/work":
                return httpx.Response(200, json=_provider_page([("disk:/work/sub", "sub", "dir")]))
            if path == "disk:/work/sub":
                return httpx.Response(
                    200,
                    json=_provider_page([("disk:/work/sub/visible.txt", "visible.txt", "file")], path="disk:/work/sub"),
                )
            raise AssertionError(f"unexpected folder {path}")

        assert (await _run_exhausted_scan(args, folder="/work", handler=root_handle, recursive=True))[
            "state"
        ] == "exhausted"

        # Lift restriction and inspect DB rows directly: secret and hidden.txt stayed present!
        await configure(data, restrictions=[])
        all_items = {item["path"]: item for item in (await manager.list_page(**args))["items"]}
        assert all_items["disk:/work/secret"]["lifecycle_state"] == "present"
        assert all_items["disk:/work/secret/hidden.txt"]["lifecycle_state"] == "present"
        assert all_items["disk:/work/secret/hidden.txt"]["missing_since"] is None
        assert all_items["disk:/work/sub"]["lifecycle_state"] == "present"
        assert all_items["disk:/work/outside.txt"]["lifecycle_state"] == "missing"


async def test_reappearance_and_empty_folder_and_dir_replaced_by_file(data):
    with actor(data) as args:
        await admit(args, "phoenix.txt", folder="disk:/work", kind="file")
        await admit(args, "morph", folder="disk:/work", kind="dir")
        await admit(args, "inside.txt", folder="disk:/work/morph", kind="file")

        # Empty exhausted scan marks phoenix.txt and morph missing, morph/inside.txt stale.
        assert (
            await _run_exhausted_scan(
                args, folder="/work", handler=lambda req: httpx.Response(200, json=_provider_page([]))
            )
        )["state"] == "exhausted"

        items = {item["path"]: item for item in (await manager.list_page(**args))["items"]}
        assert items["disk:/work/phoenix.txt"]["lifecycle_state"] == "missing"
        assert items["disk:/work/morph"]["lifecycle_state"] == "missing"
        assert items["disk:/work/morph/inside.txt"]["lifecycle_state"] == "stale"

        # Now phoenix.txt reappears unchanged, and morph reappears as a file!
        def reappear_handle(request):
            return httpx.Response(
                200,
                json=_provider_page(
                    [
                        ("disk:/work/phoenix.txt", "phoenix.txt", "file"),
                        ("disk:/work/morph", "morph", "file"),
                    ]
                ),
            )

        assert (await _run_exhausted_scan(args, folder="/work", handler=reappear_handle))["state"] == "exhausted"
        after = {item["path"]: item for item in (await manager.list_page(**args))["items"]}
        assert after["disk:/work/phoenix.txt"]["lifecycle_state"] == "present"
        assert after["disk:/work/phoenix.txt"]["missing_since"] is None
        assert after["disk:/work/phoenix.txt"]["observation_number"] == 1
        assert after["disk:/work/morph"]["lifecycle_state"] == "present"
        assert after["disk:/work/morph"]["kind"] == "file"
        assert after["disk:/work/morph"]["observation_number"] == 2
        assert after["disk:/work/morph/inside.txt"]["lifecycle_state"] == "stale"


async def test_original_open_and_content_denied_while_retained_insights_remain_accessible(data):
    class FakeResolver:
        link_policy = NativeLinkPolicy("native.example.invalid", "/client/files/")

        def __init__(self, hook=None):
            self.calls = 0
            self.hook = hook

        async def resolve(self, context):
            self.calls += 1
            if self.hook:
                await self.hook(context)
            return NativeOriginalTarget(
                context,
                "https://native.example.invalid/client/files/x",
                datetime.now(timezone.utc),
                context.issued_at + OPEN_TTL,
            )

    class SyntheticVersion:
        async def verify(self, source, receipt):
            return ExactVersionAssertion(
                source.context.request,
                receipt.raw_sha256,
                receipt.provider_version,
                receipt.version_semantics,
                receipt.provider_object_id,
                datetime.now(timezone.utc),
            )

    store = FileInsightManager(enabled=True, cipher=PrivateCipher(Fernet.generate_key().decode()))
    try:
        with actor(data) as args:
            await store.set_consent(**args, processing=True, retention=True, policy_revision=POLICY_REVISION)
            await admit(args, "note.txt", "raced.txt", folder="disk:/work", kind="file")
            await admit(args, "gone_dir", folder="disk:/work", kind="dir")
            await admit(args, "stale.txt", folder="disk:/work/gone_dir", kind="file")

            items = {item["path"]: item for item in (await manager.list_page(**args))["items"]}
            note_id = items["disk:/work/note.txt"]["id"]
            raced_id = items["disk:/work/raced.txt"]["id"]
            stale_id = items["disk:/work/gone_dir/stale.txt"]["id"]

            # Save a private insight before note.txt vanishes.
            req = await store.begin_read(**args, object_id=note_id)
            ctx = await store.read_context(req)
            src = ReadPlainText(ctx, extract_plain_text(b"hello world\n", name=ctx.name), "v1")
            receipt = await store.complete_read(src, version_semantics="synthetic-v1")
            evidence = build_line_evidence(src, ranges=(LineRange(1, 1),))
            batch = InsightCandidateBatch(req.nonce, evidence, (InsightCandidate("Remembered note", (1,)),))
            saved = await PrivateInsightWorkflow(store).save(src, batch, receipt, versions=SyntheticVersion())

            # Mid-flight disappearance between prepare_original and check_original blocks release.
            async def vanish_during_resolve(_context):
                await _run_exhausted_scan(
                    args,
                    folder="/work",
                    handler=lambda req: httpx.Response(200, json=_provider_page([])),
                )

            raced_resolver = FakeResolver(hook=vanish_during_resolve)
            with pytest.raises(ConnectionDenied):
                await original_service.resolve_original(**args, object_id=raced_id, resolver=raced_resolver)
            assert raced_resolver.calls == 1

            # Already missing & stale files are denied before adapter is called.
            resolver = FakeResolver()
            for blocked_id in (note_id, stale_id):
                with pytest.raises(ConnectionDenied):
                    await original_service.resolve_original(**args, object_id=blocked_id, resolver=resolver)
                with pytest.raises(ConnectionDenied):
                    await FileContentManager(enabled=True).authorize(**args, object_id=blocked_id)
            assert resolver.calls == 0

            # Retained private insights for the missing file remain readable and forgettable.
            listed = await store.list_results(**args, object_id=note_id)
            assert len(listed) == 1 and listed[0]["id"] == saved.id
            loaded = await store.get(**args, object_id=note_id, result_id=saved.id)
            assert b"Remembered note" in loaded.payload
            await store.forget_object(**args, object_id=note_id)
            assert await store.list_results(**args, object_id=note_id) == []
    finally:
        async with async_session_maker() as session, session.begin():
            await session.execute(delete(FileInsightMount).where(FileInsightMount.connection_id.in_(data.connections)))


async def test_web_lifecycle_filter_pagination_and_workspace_link_gating(data, monkeypatch):
    monkeypatch.setattr(settings, "FILE_CATALOG_UI_ENABLED", True)
    monkeypatch.setattr(settings, "FILE_WORKSPACE_ACTIONS_ENABLED", True)
    user_pass = "catalog-lifecycle-" + "check"
    async with async_session_maker() as session, session.begin():
        await session.execute(
            __import__("sqlalchemy")
            .update(User)
            .where(User.id == data.users[0].id)
            .values(
                hashed_password=__import__("app.core.hashing", fromlist=["get_password_hash"]).get_password_hash(
                    user_pass
                )
            )
        )

    with actor(data) as args:
        await admit(args, "stay.txt", *[f"miss-{i:02d}.txt" for i in range(55)], folder="disk:/work", kind="file")
        await admit(args, "vanished_dir", folder="disk:/work", kind="dir")
        await admit(args, "stale_child.txt", folder="disk:/work/vanished_dir", kind="file")

        with pytest.raises(CatalogInputError):
            await manager.list_page(**args, lifecycle="deleted")

        assert (
            await _run_exhausted_scan(
                args,
                folder="/work",
                handler=lambda req: httpx.Response(
                    200, json=_provider_page([("disk:/work/stay.txt", "stay.txt", "file")])
                ),
            )
        )["state"] == "exhausted"

        items = {item["path"]: item for item in (await manager.list_page(**args, limit=100))["items"]}
        stay_id = items["disk:/work/stay.txt"]["id"]
        miss_id = items["disk:/work/miss-00.txt"]["id"]
        stale_id = items["disk:/work/vanished_dir/stale_child.txt"]["id"]

    app = create_application()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://testserver", follow_redirects=False
    ) as client:
        login_page = await client.get("/app/login")
        csrf = re.search(r'name="_csrf" value="([^\"]+)"', login_page.text).group(1)
        resp = await client.post(
            "/app/login", data={"username": data.users[0].username, "password": user_pass, "_csrf": csrf}
        )
        assert resp.status_code in (302, 303)

        page1 = await client.get(f"/app/knowledge/files?connection_id={data.connections[0]}&lifecycle=missing")
        assert page1.status_code == 200
        dom1 = BeautifulSoup(page1.text, "html.parser")
        assert len(dom1.select(".file-catalog-list li")) == 50
        assert all(tag["data-file-lifecycle"] == "missing" for tag in dom1.select("[data-file-lifecycle]"))
        assert "Отсутствует на диске" in dom1.get_text()
        next_link = dom1.select_one('a[rel="next"]')
        assert next_link is not None and "lifecycle=missing" in next_link["href"]

        stale_page = await client.get(f"/app/knowledge/files?connection_id={data.connections[0]}&lifecycle=stale")
        assert stale_page.status_code == 200
        assert "Устарело" in stale_page.text
        assert "stale_child.txt" in stale_page.text

        present_detail = await client.get(f"/app/knowledge/files/{stay_id}?connection_id={data.connections[0]}")
        assert f"/app/knowledge/files/{stay_id}/workspace" in present_detail.text

        for non_present_id, expected_tag in ((miss_id, "Отсутствует на диске"), (stale_id, "Устарело")):
            detail = await client.get(f"/app/knowledge/files/{non_present_id}?connection_id={data.connections[0]}")
            assert detail.status_code == 200
            assert expected_tag in detail.text
            assert "Открытие оригинала недоступно" in detail.text
            assert f"/app/knowledge/files/{non_present_id}/workspace" not in detail.text


async def test_s1_migration_one_hop_round_trip_preserves_catalog_rows(data):
    assert settings.DB_SCHEMA == SCHEMA
    cfg = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    cfg.set_main_option("script_location", str(Path(__file__).resolve().parents[2] / "migrations"))

    with actor(data) as args:
        await admit(args, "before_downgrade.txt", folder="disk:/work", kind="file")
        before_items = (await manager.list_page(**args))["items"]
        assert len(before_items) == 1
        obj_id = before_items[0]["id"]

    try:
        with engine.begin() as connection:
            cfg.attributes["connection"] = connection
            command.downgrade(cfg, "p8t51_job_type_width")
            cols = {col["name"] for col in inspect(connection).get_columns("file_catalog_objects", schema=SCHEMA)}
            assert not {"lifecycle_state", "reconciled_at", "missing_since", "last_scan_token"} & cols
            assert (
                connection.execute(
                    text(f"SELECT count(*) FROM {SCHEMA}.file_catalog_objects WHERE id = :id"), {"id": obj_id}
                ).scalar_one()
                == 1
            )
    finally:
        with engine.begin() as connection:
            cfg.attributes["connection"] = connection
            command.upgrade(cfg, "p12t12_s1_lifecycle")
            cols = {col["name"] for col in inspect(connection).get_columns("file_catalog_objects", schema=SCHEMA)}
            assert {"lifecycle_state", "reconciled_at", "missing_since", "last_scan_token"} <= cols
            ver = connection.execute(text(f"SELECT version_num FROM {SCHEMA}.alembic_version")).scalar_one()
            assert ver == "p12t12_s1_lifecycle"

    with actor(data) as args:
        restored = await manager.detail(**args, object_id=obj_id)
        assert restored["lifecycle_state"] == "present"
        assert restored["missing_since"] is None
        assert restored["reconciled_at"] is None
        assert len(restored["observations"]) == 1
