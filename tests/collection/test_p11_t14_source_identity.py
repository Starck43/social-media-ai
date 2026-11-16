"""Source-only metadata/canonical package/bootstrap contract; run directly, never DB conftest.

Real SQLAlchemy, Source declaration, enum helpers, canonical/lazy exports and binding
logic and project Base/mixins; synthetic settings/managers. No project app or DB bootstrap,
engine, connection, SQL, migration, provider or network is permitted.
"""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import sqlalchemy.ext.asyncio
from sqlalchemy import MetaData

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("_source_identity_loader", ROOT / "tests/source_import_isolation.py")
LOADER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(LOADER)


def load(name, relative, imports=None):
    return LOADER.load_isolated_source(name, ROOT / relative, imports)


class SourceIdentityTests(unittest.TestCase):
    def setUp(self):
        self.before = {name for name in sys.modules if name == "app" or name.startswith("app.")}
        for target in (
            "socket.socket.connect",
            "socket.getaddrinfo",
            "sqlalchemy.create_engine",
            "sqlalchemy.ext.asyncio.create_async_engine",
        ):
            guard = patch(target, side_effect=AssertionError("No DB/network in source-only test"))
            guard.start()
            self.addCleanup(guard.stop)
        for method in ("create_all", "drop_all"):
            guard = patch.object(MetaData, method, side_effect=AssertionError("No DDL"))
            guard.start()
            self.addCleanup(guard.stop)

        settings = SimpleNamespace(DB_SCHEMA="p11_t14_isolated")
        config = SimpleNamespace(settings=settings)
        bases = load("app.models.base", "app/models/base.py", {"app.core.config": config})
        self.bases = bases
        self.base = bases.Base
        decorators = load("_source_identity_decorators", "app/core/decorators.py")
        enums = load("_source_identity_enums", "app/utils/db_enums.py", {"app.core.config": config})
        types = load(
            "_source_identity_platform_types", "app/types/enums/platform_types.py", {"app.utils.db_enums": enums}
        )
        self.types = types
        self.leaf = load(
            "app.models.collection.source",
            "app/models/collection/source.py",
            {
                "app.core.config": config,
                "app.core.decorators": decorators,
                "app.types": types,
                "app.models.base": bases,
            },
        )
        self.manager_calls = []

        def manager():
            value = SimpleNamespace(model=self.leaf.Source)
            self.manager_calls.append(value)
            return value

        self.managers = SimpleNamespace(SourceManager=manager)
        self.domain = load("app.models.collection", "app/models/collection/__init__.py")
        self.domain.import_module = lambda relative, package: (
            self.leaf if relative == ".source" else self.fail(relative)
        )

    def tearDown(self):
        after = {name for name in sys.modules if name == "app" or name.startswith("app.")}
        self.assertEqual(after, self.before, "No application import/cache pollution")
        self.base.registry.dispose()

    def test_canonical_leaf_and_lazy_domain_export_share_one_real_mapped_class(self):
        cls = self.leaf.Source
        self.assertIs(self.domain.Source, cls)
        self.assertIs(self.domain.__dict__["Source"], cls)
        self.assertIn("Source", self.domain.__all__)
        self.assertIs(self.leaf.Base, self.base)
        self.assertIs(self.leaf.SourceType, self.types.SourceType)
        self.assertIs(self.leaf.TenantScopedMixin, self.bases.TenantScopedMixin)
        self.assertIs(self.leaf.TimestampMixin, self.bases.TimestampMixin)
        self.assertEqual(cls.__module__, "app.models.collection.source")
        self.assertEqual(cls._app_label, "collection")
        self.assertEqual(list(self.base.metadata.tables), ["p11_t14_isolated.sources"])
        self.assertIs(cls.__table__, self.base.metadata.tables["p11_t14_isolated.sources"])
        self.assertEqual([mapper.class_ for mapper in self.base.registry.mappers], [cls])

    def test_actual_bootstrap_binds_the_same_class_once(self):
        class Models(SimpleNamespace):
            def __getattr__(self, name):
                value = type(name, (), {})
                setattr(self, name, value)
                return value

        def generic(model=None):
            return SimpleNamespace(model=model)

        class ManagerModule:
            def __getattr__(self, name):
                return self.managers.SourceManager if name == "SourceManager" else generic

        manager_module = ManagerModule()
        manager_module.managers = self.managers
        bootstrap = load(
            "app.models.bootstrap",
            "app/models/bootstrap.py",
            {
                "app.models.managers.base_manager": SimpleNamespace(BaseManager=generic),
                "app.models.managers.role_manager": SimpleNamespace(RoleManager=generic),
                "app.models.base": self.bases,
            },
        )
        bootstrap.import_module = lambda name: manager_module
        models = Models(Source=self.leaf.Source)
        bootstrap.bind_managers(models)
        self.assertEqual(len(self.manager_calls), 1)
        self.assertIs(models.Source.objects.model, self.leaf.Source)
        self.assertIs(self.domain.Source.objects, models.Source.objects)
        self.assertIs(self.domain.__dict__["Source"].objects, models.Source.objects)
        self.assertEqual(len(list(self.base.registry.mappers)), 1)

    def test_source_helper_edges_are_not_normalized_by_import_migration(self):
        clean = self.leaf.Source._clean_external_id
        for value, expected in ((None, None), ("", ""), (False, False), (0, 0), ("/a/b/", "b"), ("plain", "plain")):
            with self.subTest(value=value):
                self.assertEqual(clean(value), expected)
        # Existing truthy non-string behavior stays TypeError/AttributeError,
        # not a newly shared positive-ID/string-coercing helper.
        for value in (True, 1):
            with self.subTest(value=value), self.assertRaises(AttributeError):
                clean(value)


if __name__ == "__main__":
    unittest.main()
