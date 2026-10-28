"""Real facade + extracted SQL builders; no engine, app bootstrap or DB access.

SQL compilation is not evidence of PostgreSQL locking/rollback or eager-load I/O.
"""

import ast
import asyncio
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

from sqlalchemy import ForeignKey, Integer, String, func
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from tests.shared.decomposition_source_loader import load_decomposed_source

ROOT = Path(__file__).resolve().parents[2]


class Base(DeclarativeBase):
    pass


class Parent(Base):
    __tablename__ = "p11_parent"
    __tenant_scoped__ = True
    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String)
    children: Mapped[list["Child"]] = relationship(back_populates="parent")


class Child(Base):
    __tablename__ = "p11_child"
    id: Mapped[int] = mapped_column(primary_key=True)
    parent_id: Mapped[int] = mapped_column(ForeignKey(Parent.id))
    name: Mapped[str] = mapped_column(String)
    parent: Mapped[Parent] = relationship(back_populates="children")


class TenantError(RuntimeError):
    pass


class Page:
    def __class_getitem__(cls, item):
        return cls


def sql(statement):
    return str(statement.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))


class ManagerBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.tenant = 7
        self.session_factory = AsyncMock(side_effect=AssertionError("compilation must not open a session"))
        self.module = load_decomposed_source(
            "app.models.managers._p11_boundary",
            ROOT / "app/models/managers/base_manager.py",
            {
                "fastapi": NS(HTTPException=RuntimeError),
                "app.core.database": NS(async_session_maker=self.session_factory, with_db_session=lambda f: f),
                "app.core.tenant_context": NS(
                    TenantContextError=TenantError,
                    current_tenant_id=lambda: self.tenant,
                    is_bypass=lambda: False,
                ),
                "app.schemas.common": NS(PaginationResult=Page),
            },
        )
        self.manager = self.module.BaseManager(Parent)

    def test_legacy_exports_are_the_same_descriptor_and_compiler(self):
        imports = self.module.__isolated_imports__
        relation = imports["app.models.managers.relation_loading"]
        lookup = imports["app.models.managers.lookup_compiler"]
        self.assertIs(self.module.Prefetch, relation.Prefetch)
        self.assertIs(self.module.prefetch, relation.prefetch)
        self.assertIs(self.module.LookupCompiler, lookup.LookupCompiler)
        self.assertIsInstance(self.module.prefetch("children"), self.module.Prefetch)

    def test_clone_exclude_and_pagination_preserve_tenant_filter(self):
        original = self.manager.filter(name__icontains="alpha")
        candidate = original.exclude(id__in=[1, 2]).order_by(Parent.id.desc()).offset(3).limit(4)
        statement = sql(candidate.to_select())
        self.assertIn("p11_parent.tenant_id = 7", statement)
        self.assertIn("ILIKE '%%alpha%%'", statement)
        self.assertIn("NOT IN (1, 2)", statement)
        self.assertIn("ORDER BY p11_parent.id DESC", statement)
        self.assertIn("LIMIT 4 OFFSET 3", statement)
        self.assertNotIn("LIMIT", sql(original.to_select()))
        self.assertEqual(original.kw_filters, {"name__icontains": "alpha"})
        self.session_factory.assert_not_called()

    def test_missing_tenant_still_fails_before_compilation(self):
        self.tenant = None
        with self.assertRaises(TenantError):
            self.manager.all()
        self.session_factory.assert_not_called()

    def test_joins_groups_having_values_and_sync_async_match(self):
        query = (
            self.manager.filter(id__gte=2)
            .outerjoin(Child, Parent.id == Child.parent_id)
            .group_by(Parent.id)
            .having(func.count(Child.id) > 0)
            .values(Parent.id, func.count(Child.id).label("children"))
            .limit(2)
        )
        statement = sql(query.to_select())
        self.assertIn("LEFT OUTER JOIN p11_child", statement)
        self.assertIn("GROUP BY p11_parent.id", statement)
        self.assertIn("HAVING count(p11_child.id) > 0", statement)
        self.assertEqual(statement, sql(asyncio.run(query._build_statement())))
        self.assertNotIn("LIMIT", sql(query._build_statement_sync(apply_pagination=False)))

    def test_relation_spellings_options_and_unknown_names(self):
        for spelling in ("children.parent", "children__parent"):
            self.assertIsNotNone(self.manager.build_eager_option(spelling))
            self.assertIsNotNone(self.manager.build_prefetch_option(spelling))
        for method in (self.manager.build_eager_option, self.manager.build_prefetch_option):
            with self.assertRaisesRegex(ValueError, "no such relation"):
                method("children.typo")
        with self.assertRaisesRegex(ValueError, "Filtered prefetch"):
            self.manager.build_prefetch_option(self.module.prefetch("children.parent", filters={"id": 1}))
        descriptor = self.module.prefetch("children", filters={"name__icontains": "beta"})
        query = self.manager.prefetch_related(descriptor).select_related("children")
        self.assertEqual(len(query.to_select()._with_options), 2)
        self.session_factory.assert_not_called()

    def test_lookup_errors_and_false_null_semantics_survive(self):
        compiler = self.module.LookupCompiler
        with self.assertRaisesRegex(ValueError, "requires list"):
            compiler.compile_lookup(Parent, "id__in", "1")
        with self.assertRaisesRegex(ValueError, "Unsupported lookup"):
            compiler.compile_lookup(Parent, "id__bad", 1)
        with self.assertRaises(AttributeError):
            compiler.compile_lookup(Parent, "absent", 1)
        self.assertIn("IS NOT NULL", str(compiler.compile_lookup(Parent, "name__isnull", False)))

    def test_inherited_lifecycle_and_inputs_remain_explicit(self):
        # Parse only: never initialize app models/config or a worker.
        for filename, facade, mixin in (
            ("job_manager.py", "JobManager", "JobLifecycleMixin"),
            ("agent_task_manager.py", "AgentTaskManager", "TaskInputsMixin"),
        ):
            tree = ast.parse((ROOT / "app/models/managers" / filename).read_text())
            cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == facade)
            self.assertIn(mixin, [ast.unparse(base) for base in cls.bases])


if __name__ == "__main__":
    unittest.main()
