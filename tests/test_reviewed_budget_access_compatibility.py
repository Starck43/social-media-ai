"""No-DB compatibility checks against authoritative access-model dev.

Actual source + explicit configuration/import/catalog doubles only. No app
startup, settings/env, engine, DBAPI, migration execution or external calls.
"""

import ast
import subprocess
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import sqlalchemy as sa
import test_reviewed_budget_baselines as harness
from source_import_isolation import load_isolated_source

ROOT = Path(__file__).resolve().parents[1]
AUTHORITATIVE_DEV = "eb6cad6c56e78768b6482eba312082b849d24712"
CONFIG = SimpleNamespace(settings=SimpleNamespace(DB_SCHEMA="baseline_review"))
DB_ENUMS = load_isolated_source(
    "_baseline_db_enums",
    ROOT / "app/utils/db_enums.py",
    imports={"app.core.config": CONFIG},
)
TYPES = load_isolated_source(
    "app.types.enums._baseline_access_types",
    ROOT / "app/types/enums/user_types.py",
    imports={"app.utils.db_enums": DB_ENUMS},
)
CATALOG = load_isolated_source(
    "_baseline_resource_catalog",
    ROOT / "app/core/access_catalog.py",
    imports={"app.types": TYPES},
)
CONTEXT = load_isolated_source(
    "_baseline_real_tenant_context",
    ROOT / "app/core/tenant_context.py",
    imports={
        "starlette.types": SimpleNamespace(
            ASGIApp=Any, Receive=Any, Scope=Any, Send=Any
        )
    },
)
POLICY = load_isolated_source(
    "_baseline_current_access_policy",
    ROOT / "app/core/permissions.py",
    imports={
        "app.types": TYPES,
        "app.core.access_catalog": CATALOG,
        "app.core.tenant_context": CONTEXT,
    },
)


class AccessCompatibilityTests(unittest.TestCase):
    def test_authoritative_policy_registration_bootstrap_and_access_migration_unchanged(
        self,
    ):
        paths = (
            "app/core/access_catalog.py",
            "app/core/permissions.py",
            "app/types/enums/user_types.py",
            "app/models/bootstrap.py",
            "app/models/tenancy/workspace_role.py",
            "app/models/identity/role.py",
            "app/models/managers/role_manager.py",
            "app/models/managers/permission_manager.py",
            "scripts/migrations/register_model_types.py",
            "scripts/migrations/deps.py",
            "migrations/versions/0096_split_access_roles_and_resources.py",
            "AGENTS.md",
            ".agent/rules.md",
        )
        for path in paths:
            with self.subTest(path=path):
                self.assertEqual(
                    (ROOT / path).read_bytes(),
                    subprocess.check_output(
                        ["git", "show", AUTHORITATIVE_DEV + ":" + path], cwd=ROOT
                    ),
                )

    def test_one_unique_access0096_then_baseline0097_no_colliding_old_definition(self):
        versions = {}
        for path in (ROOT / "migrations/versions").glob("*.py"):
            values = {}
            for node in ast.parse(path.read_text()).body:
                if isinstance(node, ast.Assign) and isinstance(
                    node.value, ast.Constant
                ):
                    for target in node.targets:
                        if isinstance(target, ast.Name) and target.id in (
                            "revision",
                            "down_revision",
                        ):
                            values[target.id] = node.value.value
            if "revision" in values:
                self.assertNotIn(values["revision"], versions, str(path))
                versions[values["revision"]] = (path.name, values.get("down_revision"))
        self.assertEqual(
            versions["0096"], ("0096_split_access_roles_and_resources.py", "0095")
        )
        self.assertEqual(
            versions["0097"], ("0097_add_reviewed_budget_baselines.py", "0096")
        )
        self.assertFalse(
            (
                ROOT / "migrations/versions/0096_add_reviewed_budget_baselines.py"
            ).exists()
        )

    def test_internal_baseline_has_no_resource_or_implicit_permission_key(self):
        for name in (
            "LLMBudgetBaseline",
            "llmbudgetbaseline",
            "llm_budget_baselines",
            "llm.budget_baseline",
        ):
            self.assertIsNone(CATALOG.resource_for(name))
        self.assertIsNone(CATALOG.resource_for(harness.MODELS.LLMBudgetBaseline))
        for action in TYPES.ActionType:
            self.assertIsNone(
                CATALOG.resolve_permission_key("llm.budget_baseline." + action.db_value)
            )
            self.assertIsNone(
                CATALOG.resolve_permission_key(
                    "llm.llmbudgetbaseline." + action.db_value
                )
            )

    def test_no_workspace_role_gains_baseline_crud_or_provision_grants(self):
        for role in TYPES.WorkspaceRoleType:
            self.assertFalse(
                any(
                    name == "llmbudgetbaseline"
                    for name, _ in CATALOG.workspace_grants(role.value)
                )
            )
        self.assertFalse(CATALOG.workspace_grants(None))
        self.assertFalse(CATALOG.workspace_grants("SUPERUSER"))

    def test_unknown_baseline_denied_even_for_platform_or_workspace_owner_and_bypass(
        self,
    ):
        actor = SimpleNamespace(
            id=7,
            is_active=True,
            has_admin_access=lambda: True,
            has_perm_for=lambda *args: True,
        )
        for role in TYPES.WorkspaceRoleType:
            with CONTEXT.tenant_scope(7), POLICY.permission_scope(
                actor,
                is_owner=role.value == "owner",
                member_grants=CATALOG.workspace_grants(role.value),
            ):
                for action in TYPES.ActionType:
                    self.assertFalse(
                        POLICY.has_permission(actor, "LLMBudgetBaseline", action)
                    )
        with CONTEXT.tenant_scope(bypass=True):
            self.assertFalse(
                POLICY.has_permission(None, "llm_budget_baselines", "create")
            )
            with POLICY.permission_scope(actor):
                self.assertFalse(
                    POLICY.has_permission(actor, "llm_budget_baselines", "view")
                )

    def test_baseline_facades_are_additive_no_old_manager_side_effect(self):
        paths = {
            "app/models/__init__.py": "from .llm.llm_budget_baseline import LLMBudgetBaseline\n",
            "app/models/llm/__init__.py": "from .llm_budget_baseline import LLMBudgetBaseline\n",
        }
        for path, added_import in paths.items():
            source = (
                (ROOT / path)
                .read_text()
                .replace(added_import, "")
                .replace('    "LLMBudgetBaseline",\n', "")
            )
            self.assertEqual(
                source,
                subprocess.check_output(
                    ["git", "show", AUTHORITATIVE_DEV + ":" + path], cwd=ROOT, text=True
                ),
            )
        source = (ROOT / "app/models/llm_budget.py").read_text()
        self.assertNotIn(".objects =", source)
        self.assertIsNone(harness.MODELS.LLMBudgetBaseline.objects)
        self.assertNotIn(
            "LLMBudgetBaseline", (ROOT / "app/models/bootstrap.py").read_text()
        )

    def test_compatibility_alias_does_not_redeclare_orm_model(self):
        path = ROOT / "app/models/analysis/llm_budget_baseline.py"
        self.assertFalse(
            any(
                isinstance(node, ast.ClassDef)
                for node in ast.parse(path.read_text()).body
            )
        )
        alias = load_isolated_source(
            "app.models.analysis._baseline_compat",
            path,
            imports={"app.models.llm.llm_budget_baseline": harness.MODELS},
        )
        self.assertIs(alias.LLMBudgetBaseline, harness.MODELS.LLMBudgetBaseline)

    def test_post_access_registration_does_not_register_internal_baseline_table(self):
        captured = []
        model_table = sa.Table(
            "model_types",
            sa.MetaData(schema="baseline_review"),
            sa.Column("id", sa.Integer),
            sa.Column("resource_key", sa.String),
        )
        model = SimpleNamespace(__table__=model_table, id=model_table.c.id)
        inspector = SimpleNamespace(
            has_table=lambda *a, **kw: True,
            get_columns=lambda *a, **kw: [{"name": "resource_key"}],
            get_table_names=lambda **kw: ["model_types", "llm_budget_baselines"],
        )

        class Connection:
            def execute(self, statement):
                captured.append(statement)
                return [SimpleNamespace(resource_key="identity.model_type", id=1)]

        permission_calls = []
        module = load_isolated_source(
            "_baseline_post_access_registration",
            ROOT / "scripts/migrations/register_model_types.py",
            imports={
                "sqlalchemy": SimpleNamespace(
                    inspect=lambda *args: inspector, text=sa.text, select=sa.select
                ),
                "app.core.config": CONFIG,
                "app.core.access_catalog": CATALOG,
                "app.models": SimpleNamespace(ModelType=model),
                "app.services.crud": SimpleNamespace(
                    create_permissions_for_model=lambda *a: permission_calls.append(a)
                ),
                "scripts.migrations.deps": SimpleNamespace(
                    get_app_label_from_model=None, get_model_class_by_table_name=None
                ),
            },
        )
        module.register_model_types(Connection())
        self.assertEqual(len(captured), 1)
        self.assertTrue(captured[0].is_select)
        self.assertEqual([call[3] for call in permission_calls], ["modeltype"])

    def test_admission_method_unchanged_except_new_provisioning_method(self):
        def admission(source):
            node = next(
                n
                for n in ast.walk(ast.parse(source))
                if isinstance(n, ast.AsyncFunctionDef) and n.name == "reserve_receipt"
            )
            return ast.dump(node, include_attributes=False)

        path = "app/models/managers/llm_budget_manager.py"
        self.assertEqual(
            admission((ROOT / path).read_text()),
            admission(
                subprocess.check_output(
                    ["git", "show", AUTHORITATIVE_DEV + ":" + path], cwd=ROOT, text=True
                )
            ),
        )
