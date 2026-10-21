"""Source-pinned roadmap observations, not HTTP/DB/release acceptance.

Run directly: python tests/test_roadmap_reconciliation.py -v.
Expected failures are still-open contracts, never completed-feature evidence.
"""

from __future__ import annotations

import ast
import asyncio
import unittest
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock

from source_import_isolation import load_isolated_source

ROOT = Path(__file__).resolve().parents[1]


class HTTPError(Exception):
    def __init__(self, status_code, detail):
        self.status_code = status_code
        self.detail = detail


def function(path, name, namespace=None, bindings=None):
    """Execute the exact function AST, removing only registration decorators."""
    tree = ast.parse((ROOT / path).read_text())
    node = next(n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name)
    node.decorator_list = []
    scope = dict(namespace or {})
    import builtins

    def isolated_import(module, globals=None, locals=None, fromlist=(), level=0):
        if module in (bindings or {}):
            return bindings[module]
        if module == "app" or module.startswith("app."):
            raise ImportError("Undeclared application dependency: " + module)
        return builtins.__import__(module, globals, locals, fromlist, level)

    scope["__builtins__"] = dict(vars(builtins), __import__=isolated_import)
    body = [ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), node]
    exec(compile(ast.fix_missing_locations(ast.Module(body=body, type_ignores=[])), path, "exec"), scope)
    return scope[name]


class SecurityReconciliation(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.action = NS(UPDATE="update")
        self.roles = Enum("PlatformRoleType", {"ADMIN": "admin", "OPERATOR": "operator", "USER": "user"}, type=str)
        self.schema = load_isolated_source(
            "_roadmap_user_schema", ROOT / "app/schemas/user.py", {"app.types": NS(PlatformRoleType=self.roles)}
        )
        self.actor = NS(id=7, is_active=True, is_platform_admin=False)
        self.fresh = NS(get=AsyncMock(return_value=self.actor))
        self.users = NS(objects=NS(prefetch_related=Mock(return_value=self.fresh)))
        self.manager_module = load_isolated_source(
            "_roadmap_user_manager",
            ROOT / "app/models/managers/user_manager.py",
            {
                "fastapi": NS(HTTPException=HTTPError, status=NS(HTTP_403_FORBIDDEN=403, HTTP_400_BAD_REQUEST=400)),
                "passlib.context": NS(CryptContext=lambda **kw: NS()),
                "app.core.hashing": NS(get_password_hash=Mock(), verify_password=Mock()),
                "app.models.managers.base_manager": NS(BaseManager=object),
                "app.types": NS(ActionType=self.action, PlatformRoleType=self.roles),
                "app.core.permissions": NS(get_current_user=lambda: None),
                "app.core.tenant_context": NS(is_bypass=lambda: False),
                "app.models": NS(User=self.users, PlatformRole=NS(objects=NS())),
            },
        )
        self.manager = object.__new__(self.manager_module.UserManager)
        self.manager.get = AsyncMock(return_value=NS(id=7))
        self.manager.update_by_id = AsyncMock(return_value=NS(id=7))

    async def test_public_reset_is_inert_before_any_lookup_or_write(self):
        reset = function(
            "app/admin/endpoints.py",
            "reset_password",
            {
                "Form": lambda value: value,
                "HTTPException": HTTPError,
                "status": NS(HTTP_410_GONE=410),
            },
        )
        with self.assertRaises(HTTPError) as error:
            await reset(NS(), "fixture@example.invalid", "fictitious-csrf")
        self.assertEqual(error.exception.status_code, 410)

    async def test_response_model_drops_password_hash(self):
        now = datetime.now(timezone.utc)
        result = self.schema.UserInDB.model_validate(
            {
                "id": 7,
                "username": "fixture",
                "email": "f@example.com",
                "created_at": now,
                "updated_at": now,
                "hashed_password": "PRIVATE_SENTINEL",
            }
        )
        self.assertNotIn("hashed_password", result.model_dump())
        self.assertNotIn("PRIVATE_SENTINEL", result.model_dump_json())
        self.assertNotIn("hashed_password", self.schema.UserInDB.model_json_schema()["properties"])

    async def test_self_profile_schema_ignores_privileged_fields(self):
        result = self.schema.UserUpdate.model_validate(
            {
                "username": "fixture",
                "is_active": True,
                "is_superuser": True,
                "role_id": 1,
                "hashed_password": "PRIVATE_SENTINEL",
            }
        )
        self.assertEqual(result.model_dump(exclude_unset=True), {"username": "fixture"})

    async def test_manager_rejects_privileged_fields_even_from_admin(self):
        self.actor.is_platform_admin = True
        for field in ("is_active", "is_superuser", "role_id", "hashed_password"):
            with self.subTest(field=field), self.assertRaises(HTTPError) as error:
                await self.manager.update_user(7, {field: True}, current_user=self.actor)
            self.assertEqual(error.exception.status_code, 400)
        self.manager.update_by_id.assert_not_awaited()

    async def test_own_profile_uses_fresh_actor_before_write(self):
        await self.manager.update_user(7, {"username": "fixture"}, current_user=self.actor)
        self.fresh.get.assert_awaited_once_with(id=7, is_active=True)
        self.manager.update_by_id.assert_awaited_once_with(7, username="fixture")

    async def test_cross_user_profile_and_revoked_actor_are_denied(self):
        for fresh in (self.actor, None):
            self.fresh.get.return_value = fresh
            with self.assertRaises(HTTPError) as error:
                await self.manager.update_user(8, {"username": "fixture"}, current_user=self.actor)
            self.assertEqual(error.exception.status_code, 403)
        self.manager.update_by_id.assert_not_awaited()

    async def test_anonymous_manager_profile_update_is_denied(self):
        with self.assertRaises(HTTPError) as error:
            await self.manager.update_user(7, {"username": "fixture"})
        self.assertEqual(error.exception.status_code, 403)
        self.manager.update_by_id.assert_not_awaited()

    async def test_role_update_rechecks_admin_and_rejects_self(self):
        with self.assertRaises(HTTPError):
            await self.manager.set_platform_role(8, self.roles.USER, 7)
        self.actor.is_platform_admin = True
        with self.assertRaises(HTTPError):
            await self.manager.set_platform_role(7, self.roles.USER, 7)
        self.manager.update_by_id.assert_not_awaited()

    @unittest.expectedFailure
    async def test_legacy_test_db_redacts_exception(self):
        probe = function(
            "app/api/v1/entry.py",
            "test_database",
            {
                "Permission": NS(objects=NS(count=AsyncMock(side_effect=RuntimeError("PRIVATE_SENTINEL")))),
                "HTTPException": HTTPError,
            },
        )
        try:
            await probe()
        except HTTPError as error:
            self.assertNotIn("PRIVATE_SENTINEL", error.detail)
        else:
            self.fail("Failure was not reported")

    @unittest.expectedFailure
    async def test_legacy_health_redacts_exception(self):
        probe = function(
            "app/api/v1/entry.py",
            "health_check",
            {
                "Permission": NS(objects=NS(count=AsyncMock(side_effect=RuntimeError("PRIVATE_SENTINEL")))),
                "HTTPException": HTTPError,
            },
        )
        try:
            await probe()
        except HTTPError as error:
            self.assertNotIn("PRIVATE_SENTINEL", error.detail)
        else:
            self.fail("Failure was not reported")

    @unittest.expectedFailure
    async def test_ordinary_bad_login_is_counted(self):
        module = load_isolated_source(
            "_roadmap_admin_auth",
            ROOT / "app/admin/auth.py",
            {
                "fastapi": NS(HTTPException=HTTPError, Request=object),
                "sqladmin.authentication": NS(AuthenticationBackend=object),
                "app.admin.csrf": NS(CSRFTokenManager=object),
                "app.core.config": NS(settings=NS()),
                "app.core.hashing": NS(verify_password=Mock(return_value=False)),
                "app.models": NS(User=NS(objects=NS(get_by_username_or_email=AsyncMock(return_value=None)))),
                "app.services.user.auth": NS(get_authenticated_user=AsyncMock()),
                "app.utils.token": NS(create_access_token=Mock()),
            },
        )
        backend = object.__new__(module.AdminAuthBackend)
        backend.csrf_manager = NS(verify_token=lambda token: True)
        backend.check_login_lockout = AsyncMock()
        backend.track_login_attempt = AsyncMock()
        request = NS(
            form=AsyncMock(return_value={"username": "fixture", "password": "not-a-secret", "csrf_token": "fake"}),
            app=NS(state=NS()),
            session={},
        )
        self.assertFalse(await backend.login(request))
        backend.track_login_attempt.assert_awaited_once()


class PipelineReconciliation(unittest.IsolatedAsyncioTestCase):
    async def pipeline(self, offer):
        self.staged = NS(count=AsyncMock(return_value=10))
        self.analytics = NS(count=AsyncMock(return_value=20))
        self.jobs = NS(
            order_by=Mock(
                return_value=NS(
                    limit=AsyncMock(
                        return_value=[
                            NS(status="pending", job_type="collect"),
                            NS(status="running", job_type="prune"),
                            NS(status="running", job_type="analyze"),
                        ]
                    )
                )
            )
        )
        models = lambda query: NS(objects=NS(filter=Mock(return_value=query)))
        fn = function(
            "app/web/dashboard.py",
            "_pipeline",
            bindings={
                "app.models.collected_item": NS(CollectedItem=models(self.staged)),
                "app.models.ai_analytics": NS(AIAnalytics=models(self.analytics)),
                "app.models.job": NS(
                    Job=NS(objects=NS(filter=Mock(return_value=self.jobs)), created_at=NS(desc=lambda: None))
                ),
            },
        )
        return await fn(7, False, offer)

    @unittest.expectedFailure
    async def test_task_only_view_does_not_query_analytics(self):
        await self.pipeline({"tasks": True, "analytics": False, "sources": False})
        self.analytics.count.assert_not_awaited()

    async def test_job_count_is_only_three_row_preview(self):
        result = await self.pipeline({"tasks": True})
        self.assertEqual(result["job_count"], 3)
        self.jobs.order_by.return_value.limit.assert_awaited_once_with(3)
        self.assertEqual(result["queued_count"], 1)

    async def test_prune_and_analyze_have_same_collect_false_label(self):
        result = await self.pipeline({"tasks": True})
        self.assertEqual(
            result["running"], [{"job_type": "prune", "collect": False}, {"job_type": "analyze", "collect": False}]
        )


class AnalysisReconciliation(unittest.TestCase):
    def setUp(self):
        self.context = load_isolated_source("_roadmap_context", ROOT / "app/services/ai/analysis_context.py")
        utility = load_isolated_source("_roadmap_attachments", ROOT / "app/utils/content_attachments.py")
        media = NS(TEXT=NS(db_value="text"), IMAGE=NS(db_value="image"), VIDEO=NS(db_value="video"))
        self.classifier = load_isolated_source(
            "_roadmap_classifier",
            ROOT / "app/services/ai/content_classifier.py",
            {
                "app.types.enums.llm_types": NS(MediaType=media),
                "app.utils.content_attachments": utility,
            },
        ).ContentClassifier

    def test_fingerprint_is_stable_under_dict_order(self):
        self.assertEqual(
            self.context.analysis_fingerprint(NS(id=1, scope={"a": 1, "b": 2})),
            self.context.analysis_fingerprint(NS(id=1, scope={"b": 2, "a": 1})),
        )

    def test_fingerprint_changes_for_scenario_and_model(self):
        original = self.context.analysis_fingerprint(NS(id=1, text_llm_model_id=2))
        self.assertNotEqual(original, self.context.analysis_fingerprint(NS(id=3, text_llm_model_id=2)))
        self.assertNotEqual(original, self.context.analysis_fingerprint(NS(id=1, text_llm_model_id=4)))

    def test_fingerprint_changes_for_prompt_and_output_contract(self):
        original = self.context.analysis_fingerprint(NS(id=1, base_prompt="a"))
        self.assertNotEqual(original, self.context.analysis_fingerprint(NS(id=1, base_prompt="b")))
        self.assertNotEqual(
            original, self.context.analysis_fingerprint(NS(id=1, base_prompt="a"), output_schema={"type": "object"})
        )

    def test_url_less_media_is_now_text_only(self):
        post = {"text": "fixture", "attachments": [{"type": "image", "url": None}]}
        result = self.classifier.classify_content([post])
        self.assertEqual(result, {"text": [post], "image": [], "video": []})
        self.assertEqual(self.classifier.uncovered_attachment_items([post]), [])

    def test_real_media_still_reaches_media_stage(self):
        post = {"text": "fixture", "attachments": [{"type": "image", "url": "https://fixture.invalid/a.jpg"}]}
        result = self.classifier.classify_content([post])
        self.assertEqual(len(result["image"]), 1)
        self.assertEqual(result["image"][0]["media_url"], "https://fixture.invalid/a.jpg")

    def test_malformed_collection_still_counts_as_unknown(self):
        post = {"text": "fixture", "attachments": "malformed"}
        self.assertEqual(self.classifier.uncovered_attachment_items([post]), [post])

    @unittest.expectedFailure
    def test_malformed_attachment_member_does_not_crash_classifier(self):
        # Unknown members should not crash all sibling input.
        self.classifier.classify_content([{"text": "fixture", "attachments": [None]}])


class SimilaritySQLReconciliation(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        from contextlib import asynccontextmanager

        from sqlalchemy import select, text

        self.module = load_isolated_source(
            "app.models.managers._roadmap_sql",
            ROOT / "app/models/managers/ai_analytics_manager.py",
            {
                "sqlalchemy": NS(select=select, text=text),
                "app.types": NS(PeriodType=NS(DAY="day")),
                "app.models.managers.base_manager": NS(BaseManager=object),
                "app.core.config": NS(settings=NS(DB_SCHEMA="offline_audit_schema")),
            },
        )
        self.manager = object.__new__(self.module.AIAnalyticsManager)
        self.execute = AsyncMock(return_value=NS(first=lambda: (7, 1.0)))

        @asynccontextmanager
        async def session():
            yield NS(execute=self.execute)

        self.manager._get_owned_session = session
        self.first = AsyncMock(return_value=NS(id=7))
        self.manager.filter = Mock(return_value=NS(first=self.first))

    async def test_actual_sqlalchemy_binds_json_expansion_and_scoped_readback(self):
        result = await self.manager.most_similar_by_topics(3, ["fixture"], threshold=0.3)
        statement, params = self.execute.await_args.args
        self.assertEqual(set(statement.compile().params), {"source_id", "topics", "threshold"})
        self.assertEqual(params, {"source_id": 3, "topics": ["fixture"], "threshold": 0.3})
        sql = str(statement)
        for part in (
            "offline_audit_schema.ai_analytics",
            "json_array_elements_text",
            "json_typeof",
            "INTERSECT",
            "UNION",
            "CAST(:topics AS text[])",
        ):
            self.assertIn(part, sql)
        self.assertNotIn("{settings.DB_SCHEMA}", sql)
        self.manager.filter.assert_called_once_with(id=7)
        self.assertEqual(result.id, 7)

    async def test_empty_topics_do_not_query(self):
        self.assertIsNone(await self.manager.most_similar_by_topics(3, []))
        self.execute.assert_not_awaited()

    async def test_no_ranked_result_does_not_load_foreign_record(self):
        self.execute.return_value = NS(first=lambda: None)
        self.assertIsNone(await self.manager.most_similar_by_topics(3, ["fixture"]))
        self.manager.filter.assert_not_called()


class RawCleanupReconciliation(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        from contextlib import asynccontextmanager, contextmanager

        self.csrf = True
        self.admin = True
        self.busy = False
        self.get = AsyncMock(return_value=NS(id=3, tenant_id=7))
        self.delete = AsyncMock(return_value=2)
        self.lock = AsyncMock()
        self.close = AsyncMock()

        @asynccontextmanager
        async def transaction():
            yield

        @contextmanager
        def tenant_scope(tenant_id):
            self.assertEqual(tenant_id, 7)
            yield

        self.session = NS(begin=transaction, close=self.close)
        self.new_session = Mock(return_value=self.session)

        async def running(source_id):
            return {"collect": self.busy, "analyze": False}

        self.fn = function(
            "app/web/sources.py",
            "source_delete_unprocessed",
            {
                "Form": lambda value=None, **kw: value,
                "ensure_csrf": lambda *a: self.csrf,
                "add_flash": Mock(),
                "RedirectResponse": lambda url, status_code: NS(url=url, status_code=status_code),
                "action_tenant_id": lambda *a: 7,
                "Source": NS(objects=NS(get=self.get)),
                "CollectedItem": NS(objects=NS(delete_unprocessed_for_source=self.delete)),
                "_source_has_running_job": running,
            },
            {
                "app.web.tasks": NS(_diagnostics_admin=lambda request: self.admin),
                "app.core.database": NS(new_session=self.new_session),
                "app.core.tenant_context": NS(tenant_scope=tenant_scope),
                "app.models.managers.job_manager": NS(JobManager=NS(lock_source_admission=self.lock)),
            },
        )

    async def test_csrf_and_nonadmin_denial_do_not_open_session(self):
        for csrf, admin in ((False, True), (True, False)):
            self.csrf, self.admin = csrf, admin
            result = await self.fn(NS(), 3, "fictitious-csrf", 7)
            self.assertEqual(result.status_code, 302)
        self.new_session.assert_not_called()
        self.delete.assert_not_awaited()

    async def test_busy_source_is_locked_but_not_deleted(self):
        self.busy = True
        await self.fn(NS(), 3, "fictitious-csrf", 7)
        self.lock.assert_awaited_once_with(self.session, 7)
        self.delete.assert_not_awaited()
        self.close.assert_awaited_once()

    async def test_missing_source_is_not_deleted(self):
        self.get.return_value = None
        await self.fn(NS(), 3, "fictitious-csrf", 7)
        self.delete.assert_not_awaited()
        self.close.assert_awaited_once()

    async def test_cleanup_passes_explicit_source_and_tenant_in_existing_transaction(self):
        await self.fn(NS(), 3, "fictitious-csrf", 7)
        self.get.assert_awaited_once_with(id=3, tenant_id=7, session=self.session)
        self.lock.assert_awaited_once_with(self.session, 7)
        self.delete.assert_awaited_once_with(self.session, 3, 7)
        self.close.assert_awaited_once()


class ScopeReconciliation(unittest.IsolatedAsyncioTestCase):
    @unittest.expectedFailure
    async def test_digest_statistics_receive_same_source_and_scenario_scope_as_brief(self):
        from datetime import date

        agg = NS(
            get_sentiment_trends=AsyncMock(return_value=[]),
            get_top_topics=AsyncMock(return_value=[]),
            get_content_mix=AsyncMock(return_value={}),
            get_engagement_metrics=AsyncMock(return_value={}),
            get_llm_provider_stats=AsyncMock(return_value={}),
            generate_digest_brief=AsyncMock(return_value={}),
        )
        aggregate = function(
            "app/services/digest/builder.py",
            "aggregate",
            {
                "normalize_digest_axis": lambda value: value,
                "period_bounds": lambda period: (date(2026, 10, 1), date(2026, 10, 1)),
                "ReportAggregator": lambda: agg,
                "job_coverage_note": AsyncMock(return_value=None),
            },
        )
        await aggregate("day", source_ids=[3], scenario_id=7)
        self.assertEqual(agg.generate_digest_brief.await_args.kwargs["source_ids"], [3])
        self.assertEqual(agg.generate_digest_brief.await_args.kwargs["scenario_id"], 7)
        for method in (
            agg.get_sentiment_trends,
            agg.get_top_topics,
            agg.get_content_mix,
            agg.get_engagement_metrics,
            agg.get_llm_provider_stats,
        ):
            self.assertEqual(method.await_args.kwargs.get("source_ids"), [3])
            self.assertEqual(method.await_args.kwargs.get("scenario_id"), 7)

    @unittest.expectedFailure
    def test_removing_last_link_must_not_expand_to_other_active_sources(self):
        activity = load_isolated_source("_roadmap_source_activity", ROOT / "app/jobs/source_activity.py")
        self.assertEqual(activity.job_source_ids({}, 1, {1: {3}}, {3, 4}), {3})
        # Model the documented CASCADE removal of the last selected source.
        after = activity.job_source_ids({}, 1, {1: set()}, {4})
        self.assertNotIn(4, after)


if __name__ == "__main__":
    unittest.main()
