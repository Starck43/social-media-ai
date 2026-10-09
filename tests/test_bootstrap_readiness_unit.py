"""Standalone actual-source tests, with infrastructure imports mocked.

Run: python tests/test_bootstrap_readiness_unit.py. No DB or HTTP acceptance.
"""

import asyncio
import importlib.util
import io
import os
import sys
import types
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]


def load_source(name, relative_path):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def module_with(**fields):
    module = types.ModuleType("mock_infrastructure")
    module.__dict__.update(fields)
    return module


class BootstrapTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        imports = {
            "dotenv": module_with(load_dotenv=lambda: None),
            "sqlalchemy": module_with(text=lambda value: value),
        }
        with patch.dict(sys.modules, imports):
            self.bootstrap = load_source("_test_bootstrap", "scripts/setup_test_db.py")
        self.env = patch.dict(os.environ, {"POSTGRES_URL": "postgresql+asyncpg://db/shared"}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_common_url_default_test_schema_allowed(self):
        working, test, schema = self.bootstrap.resolve_and_redirect()
        self.assertEqual(working, test)
        self.assertEqual(schema, "test_schema")
        self.assertEqual(os.environ["DB_SCHEMA"], schema)

    def test_unset_working_schema_refuses_public(self):
        os.environ["DB_TEST_SCHEMA"] = "public"
        before = dict(os.environ)
        with self.assertRaisesRegex(RuntimeError, "working data"):
            self.bootstrap.resolve_and_redirect()
        self.assertEqual(dict(os.environ), before)

    def test_blank_working_schema_refuses_public_conservatively(self):
        os.environ.update(DB_SCHEMA="  ", DB_TEST_SCHEMA="public")
        with self.assertRaises(RuntimeError):
            self.bootstrap.resolve_and_redirect()

    def test_explicit_working_schema_refuses_same_schema(self):
        os.environ.update(DB_SCHEMA="business", DB_TEST_SCHEMA="business")
        with self.assertRaises(RuntimeError):
            self.bootstrap.resolve_and_redirect()

    def test_default_test_schema_also_checked_against_working(self):
        os.environ["DB_SCHEMA"] = "test_schema"
        with self.assertRaises(RuntimeError):
            self.bootstrap.resolve_and_redirect()

    def test_common_url_distinct_custom_schemas_allowed(self):
        os.environ.update(DB_SCHEMA="business", DB_TEST_SCHEMA="checks")
        self.assertEqual(self.bootstrap.resolve_and_redirect()[2], "checks")

    def test_explicit_separate_database_still_optional_and_supported(self):
        os.environ.update(TEST_POSTGRES_URL="postgresql://db/other", DB_TEST_SCHEMA="public")
        self.assertEqual(self.bootstrap.resolve_and_redirect()[1:], ("postgresql://db/other", "public"))

    def test_other_credentials_do_not_bypass_same_database_schema(self):
        os.environ.update(TEST_POSTGRES_URL="postgresql://other:fake@db/shared", DB_TEST_SCHEMA="public")
        with self.assertRaises(RuntimeError):
            self.bootstrap.resolve_and_redirect()

    def test_same_database_name_on_other_host_is_conservatively_blocked(self):
        os.environ.update(TEST_POSTGRES_URL="postgresql://other-host/shared", DB_TEST_SCHEMA="public")
        with self.assertRaises(RuntimeError):
            self.bootstrap.resolve_and_redirect()

    def test_missing_url_fails_before_redirect(self):
        os.environ.clear()
        with self.assertRaisesRegex(RuntimeError, "optional"):
            self.bootstrap.resolve_and_redirect()
        self.assertEqual(dict(os.environ), {})

    def test_redact_removes_all_userinfo_query_and_fragment(self):
        url = "postgresql+asyncpg://fake-user:FAKE%40SECRET@db:5432/shared?password=FAKE-QUERY#FAKE-FRAGMENT"
        value = self.bootstrap.redact(url)
        self.assertEqual(value, "postgresql+asyncpg://***@db:5432/shared")
        self.assertNotIn("FAKE", value)
        self.assertNotIn("fake-user", value)

    def test_redact_preserves_ipv6_endpoint(self):
        self.assertEqual(self.bootstrap.redact("postgresql://fake:fake@[::1]:5432/shared"),
                         "postgresql://***@[::1]:5432/shared")

    def test_redact_invalid_urls_fail_without_echo(self):
        for url in ("FAKE-SECRET", "postgresql://[FAKE-SECRET", "postgresql://db:FAKE-SECRET/shared"):
            self.assertEqual(self.bootstrap.redact(url), "(invalid URL)")
        self.assertEqual(self.bootstrap.redact(""), "(unset)")

    async def test_check_loads_dotenv_before_working_display(self):
        os.environ.clear()
        def dotenv():
            os.environ.setdefault("POSTGRES_URL", "postgresql://fake:FAKE-PASS@db/shared?password=FAKE-Q")
        self.bootstrap.load_dotenv = dotenv
        output = io.StringIO()
        with patch.object(sys, "argv", ["setup_test_db", "--check"]), redirect_stdout(output):
            self.assertEqual(await self.bootstrap._main(), 0)
        self.assertEqual(output.getvalue().count("postgresql://***@db/shared"), 2)
        self.assertIn("schema=public", output.getvalue())
        self.assertNotIn("FAKE", output.getvalue())

    async def test_check_with_reset_does_not_redirect_or_touch_database(self):
        self.bootstrap.ensure_test_database = AsyncMock(side_effect=AssertionError("must not run"))
        self.bootstrap.resolve_and_redirect = lambda: self.fail("must not redirect")
        before = dict(os.environ)
        with patch.object(sys, "argv", ["setup_test_db", "--check", "--reset"]), redirect_stdout(io.StringIO()):
            self.assertEqual(await self.bootstrap._main(), 0)
        self.assertEqual(dict(os.environ), before)
        self.bootstrap.ensure_test_database.assert_not_awaited()


class FakeRouter:
    def __init__(self, **kwargs):
        self.paths = {}

    def get(self, path, **kwargs):
        def decorator(fn):
            self.paths[path] = fn
            return fn
        return decorator


class FakeResponse:
    def __init__(self, status_code, content, headers):
        self.status_code, self.content, self.headers = status_code, content, headers


class HealthTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.count = AsyncMock(return_value=0)
        imports = {
            "fastapi": module_with(APIRouter=FakeRouter),
            "fastapi.responses": module_with(JSONResponse=FakeResponse),
            "app.models": module_with(Permission=SimpleNamespace(objects=SimpleNamespace(count=self.count))),
        }
        with patch.dict(sys.modules, imports):
            self.health = load_source("_test_api_health", "app/core/health.py")

    async def test_liveness_never_probes_database(self):
        self.count.side_effect = RuntimeError("offline")
        result = await self.health.liveness()
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.content["status"], "ok")
        self.assertNotIn("database", result.content)
        self.count.assert_not_awaited()

    async def test_readiness_success_even_with_empty_permission_table(self):
        result = await self.health.readiness()
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.content["database"], "connected")
        self.count.assert_awaited_once()

    async def test_database_failure_returns_503_without_exception_text(self):
        self.count.side_effect = RuntimeError("PRIVATE-DSN-CONTENT")
        result = await self.health.readiness()
        self.assertEqual(result.status_code, 503)
        self.assertEqual(result.content["status"], "error")
        self.assertEqual(result.content["database"], "disconnected")
        self.assertNotIn("PRIVATE", str(result.content))

    async def test_timeout_returns_503_and_cancels_cooperative_probe(self):
        cancelled = asyncio.Event()
        async def blocked():
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()
        self.count.side_effect = blocked
        self.health.DB_PROBE_TIMEOUT_SECONDS = 0.001
        result = await self.health.readiness()
        self.assertEqual(result.status_code, 503)
        self.assertTrue(cancelled.is_set())

    async def test_request_cancellation_propagates(self):
        self.count.side_effect = asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await self.health.readiness()

    async def test_responses_are_not_cached_and_timestamps_are_utc(self):
        for result in (await self.health.liveness(), await self.health.readiness()):
            self.assertEqual(result.headers["Cache-Control"], "no-store")
            self.assertTrue(result.content["timestamp"].endswith("+00:00"))

    def test_legacy_health_alias_and_explicit_probe_routes_registered(self):
        self.assertEqual(set(self.health.router.paths), {"/livez", "/readyz", "/health"})
        self.assertIs(self.health.router.paths["/health"], self.health.router.paths["/readyz"])


class MainRegistrationTests(unittest.TestCase):
    def setUp(self):
        class FakeApplication(FakeRouter):
            def __init__(self, **kwargs):
                super().__init__()
                self.state = SimpleNamespace()
            def include_router(self, router, prefix="", **kwargs):
                self.paths.update({prefix + path: fn for path, fn in router.paths.items()})
            def add_middleware(self, *args, **kwargs):
                pass
        health = module_with(router=FakeRouter())
        health.router.paths = {"/health": object(), "/readyz": object(), "/livez": object()}
        response_type = type("Response", (), {})
        imports = {
            "fastapi": module_with(FastAPI=FakeApplication, Request=object),
            "fastapi.middleware.cors": module_with(CORSMiddleware=object),
            "fastapi.responses": module_with(JSONResponse=FakeResponse, RedirectResponse=object),
            "fastapi.templating": module_with(Jinja2Templates=lambda **kwargs: object()),
            "fastapi_pagination": module_with(add_pagination=lambda app: None),
            "starlette.middleware.sessions": module_with(SessionMiddleware=object),
            "starlette.responses": module_with(Response=response_type),
            "starlette.websockets": module_with(WebSocket=object),
            "app.admin.csrf": module_with(CSRFTokenManager=lambda **kwargs: object()),
            "app.api.v1": module_with(entry=SimpleNamespace(router=FakeRouter())),
            "app.core.api_scope": module_with(ApiScopeMiddleware=object),
            "app.core.config": module_with(settings=SimpleNamespace(
                DEBUG=False, SECRET_KEY="fake-unit-key", BACKEND_CORS_ORIGINS=[], ADMIN_ENABLED=False)),
            "app.core.database": module_with(async_engine=object(), init_db=AsyncMock()),
            "app.core.health": health,
            "app.core.tenant_context": module_with(PlatformScopeMiddleware=object),
            "app.web": module_with(web_router=FakeRouter()),
            "app.web.middleware": module_with(TenantUIMiddleware=object),
        }
        with patch.dict(sys.modules, imports):
            self.main = load_source("_test_main_registration", "app/main.py")

    def test_singleton_registers_all_probe_paths(self):
        self.assertTrue({"/health", "/readyz", "/livez"}.issubset(self.main.app.paths))

    def test_application_factory_registers_all_probe_paths(self):
        self.assertTrue({"/health", "/readyz", "/livez"}.issubset(self.main.create_application().paths))


if __name__ == "__main__":
    unittest.main()
