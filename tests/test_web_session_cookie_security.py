"""Source-isolated application wiring checks; no app, database or network imports.

Run directly with python tests/test_web_session_cookie_security.py.
These verify middleware options, not live proxy/TLS or deployment acceptance.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]


class ApplicationDouble:
    def __init__(self, **options):
        self.options = options
        self.state = SimpleNamespace()
        self.routers = []
        self.middleware = []

    def include_router(self, router, **options):
        self.routers.append((router, options))

    def add_middleware(self, middleware, **options):
        self.middleware.append((middleware, options))


def application_for(environment, *, debug=False, cors=False):
    path = ROOT / "app/main.py"
    tree = ast.parse(path.read_text())
    selected = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "create_application"]
    if len(selected) != 1:
        raise RuntimeError("Expected one application factory")
    settings = SimpleNamespace(
        ENVIRONMENT=environment, DEBUG=debug, SECRET_KEY="synthetic-secret",
        BACKEND_CORS_ORIGINS=["http://localhost:8000"] if cors else [], ADMIN_ENABLED=False,
    )
    tokens = {name: object() for name in (
        "SessionMiddleware", "TenantUIMiddleware", "ApiScopeMiddleware", "PlatformScopeMiddleware",
        "CORSMiddleware", "health_router", "web_router", "lifespan",
    )}
    namespace = {
        **tokens, "FastAPI": ApplicationDouble, "settings": settings,
        "entry": SimpleNamespace(router=object()), "add_pagination": lambda app: None,
        "CSRFTokenManager": lambda **options: options,
    }
    module = ast.Module(body=selected, type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), str(path), "exec"), namespace)
    application = namespace["create_application"]()
    options = [kwargs for middleware, kwargs in application.middleware if middleware is tokens["SessionMiddleware"]]
    if len(options) != 1:
        raise RuntimeError("Expected one session middleware")
    return application, tokens, options[0]


class WebSessionCookieSecurityTests(unittest.TestCase):
    def test_production_cookie_requires_https(self):
        self.assertIs(application_for("production")[2].get("https_only"), True)

    def test_staging_cookie_requires_https(self):
        self.assertIs(application_for("staging")[2].get("https_only"), True)

    def test_development_preserves_local_http(self):
        self.assertIs(application_for("development")[2].get("https_only"), False)

    def test_environment_normalization(self):
        self.assertIs(application_for("  DeVeLoPmEnT  ")[2].get("https_only"), False)
        self.assertIs(application_for("  PRODUCTION  ")[2].get("https_only"), True)

    def test_unknown_or_empty_environment_fails_secure(self):
        for environment in ("", " ", "unknown", "development-extra"):
            with self.subTest(environment=environment):
                self.assertIs(application_for(environment)[2].get("https_only"), True)

    def test_debug_does_not_weaken_production_cookie(self):
        self.assertIs(application_for("production", debug=True)[2].get("https_only"), True)

    def test_existing_cookie_name_secret_and_lifetime_are_preserved(self):
        options = application_for("production")[2]
        self.assertEqual(options["session_cookie"], "session")
        self.assertEqual(options["secret_key"], "synthetic-secret")
        self.assertEqual(options["max_age"], 86400)

    def test_middleware_registration_order_is_preserved(self):
        application, tokens, _ = application_for("production", cors=True)
        self.assertEqual([kind for kind, _ in application.middleware], [tokens[name] for name in (
            "TenantUIMiddleware", "ApiScopeMiddleware", "PlatformScopeMiddleware", "SessionMiddleware", "CORSMiddleware",
        )])

    def test_existing_health_router_remains_registered_once(self):
        application, tokens, _ = application_for("production")
        matches = [options for router, options in application.routers if router is tokens["health_router"]]
        self.assertEqual(matches, [{}])


if __name__ == "__main__":
    unittest.main()
