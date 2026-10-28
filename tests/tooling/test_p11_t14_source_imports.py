"""Stdlib-only Source import guard. No application/ORM/bootstrap imports."""

from __future__ import annotations

import ast
import importlib.util
import inspect
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LEGACY = "app.models.source"
CANONICAL = "app.models.collection.source"


def legacy_imports(path: Path, source: str, *, dynamic: bool = True) -> list[int]:
    package = ".".join(path.parent.parts)
    failures = []
    for node in ast.walk(ast.parse(source)):
        names = []
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            name = node.module or ""
            if node.level:
                name = importlib.util.resolve_name("." * node.level + name, package)
            names = [name]
        elif dynamic and isinstance(node, ast.Call) and node.args:
            function = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            if function in {"import_module", "__import__"} and isinstance(node.args[0], ast.Constant):
                names = [node.args[0].value]
        if LEGACY in names:
            failures.append(node.lineno)
    return failures


class ImportOnlyNormalizer(ast.NodeTransformer):
    """Allow only the selected path replacement, import ordering and doc formatting."""

    def visit_ImportFrom(self, node):
        if node.module == LEGACY:
            node.module = CANONICAL
        elif node.level == 2 and node.module == "source" and any(a.name == "Source" for a in node.names):
            node.module = "collection.source"
        return self.generic_visit(node)

    def visit_Constant(self, node):
        if node.value == LEGACY:
            node.value = CANONICAL
        return node

    def generic_visit(self, node):
        super().generic_visit(node)
        body = getattr(node, "body", None)
        if isinstance(body, list):
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                if isinstance(body[0].value.value, str):
                    body[0].value.value = inspect.cleandoc(body[0].value.value)
            ordered = []
            group = []
            for child in body:
                if isinstance(child, (ast.Import, ast.ImportFrom)):
                    group.append(child)
                else:
                    ordered.extend(sorted(group, key=ast.dump))
                    group = []
                    ordered.append(child)
            ordered.extend(sorted(group, key=ast.dump))
            node.body = ordered
        return node


def import_only_ast(source: str) -> str:
    return ast.dump(ImportOnlyNormalizer().visit(ast.parse(source)), include_attributes=False)


class SourceImportGuardTests(unittest.TestCase):
    def test_consumers_do_not_import_legacy_leaf(self):
        failures = []
        for top in ("app", "cli", "scripts", "tests"):
            for path in (ROOT / top).rglob("*.py"):
                relative = path.relative_to(ROOT)
                # Compatibility tests intentionally resolve old paths dynamically.
                lines = legacy_imports(relative, path.read_text(), dynamic=top != "tests")
                failures.extend(f"{relative}:{line}" for line in lines)
        self.assertEqual(failures, [])

    def test_guard_catches_alias_relative_and_dynamic_imports(self):
        for source in (
            "from app.models.source import Source as S",
            "import app.models.source as old",
            "from .source import Source",
            "from ..models.source import Source",
            "importlib.import_module('app.models.source')",
            "__import__('app.models.source')",
        ):
            with self.subTest(source=source):
                self.assertEqual(legacy_imports(Path("app/models/example.py"), source), [1])
        self.assertEqual(legacy_imports(Path("app/models/collection/__init__.py"), "from .source import Source"), [])
        self.assertEqual(legacy_imports(Path("app/example.py"), "from app.models import Source"), [])

    def test_compatibility_leaf_remains_an_import_only_reexport(self):
        tree = ast.parse((ROOT / "app/models/source.py").read_text())
        self.assertFalse(any(isinstance(n, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) for n in tree.body))
        imports = {
            (node.module, alias.name) for node in tree.body if isinstance(node, ast.ImportFrom) for alias in node.names
        }
        self.assertIn(("collection.source", "Source"), imports)
        self.assertIn(("managers.source_manager", "SourceManager"), imports)
        declarations = []
        for path in (ROOT / "app/models").rglob("*.py"):
            declarations.extend(
                str(path.relative_to(ROOT))
                for n in ast.walk(ast.parse(path.read_text()))
                if isinstance(n, ast.ClassDef) and n.name == "Source"
            )
        self.assertEqual(declarations, ["app/models/collection/source.py"])

    def test_public_and_domain_exports_still_route_to_canonical_source(self):
        public = ast.parse((ROOT / "app/models/__init__.py").read_text())
        domain = ast.parse((ROOT / "app/models/collection/__init__.py").read_text())
        self.assertTrue(
            any(
                isinstance(n, ast.ImportFrom) and n.module == "collection" and any(a.name == "Source" for a in n.names)
                for n in public.body
            )
        )
        self.assertTrue(any(isinstance(n, ast.FunctionDef) and n.name == "__getattr__" for n in domain.body))
        modules = next(
            n.value
            for n in domain.body
            if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "_MODULES" for t in n.targets)
        )
        self.assertEqual(ast.literal_eval(modules)["Source"], "source")

    def test_registration_is_after_facade_source_export(self):
        tree = ast.parse((ROOT / "app/models/__init__.py").read_text())
        source_index = next(
            i for i, n in enumerate(tree.body) if isinstance(n, ast.ImportFrom) and n.module == "collection"
        )
        bind_indexes = [
            i
            for i, n in enumerate(tree.body)
            if isinstance(n, ast.Expr)
            and isinstance(n.value, ast.Call)
            and getattr(n.value.func, "id", "") == "_bind_managers"
        ]
        self.assertEqual(len(bind_indexes), 1)
        self.assertLess(source_index, bind_indexes[0])

    def test_import_only_normalizer_does_not_hide_body_changes(self):
        before = "from app.models.source import Source\ndef f():\n return Source\n"
        after = "from app.models.collection.source import Source\ndef f():\n return Source\n"
        self.assertEqual(import_only_ast(before), import_only_ast(after))
        self.assertNotEqual(import_only_ast(before), import_only_ast(after.replace("return Source", "return None")))


if __name__ == "__main__":
    unittest.main()
