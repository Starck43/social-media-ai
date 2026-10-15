"""Actual platform-role lookup source with only DB infrastructure doubled.

No app bootstrap, conftest, settings, database, providers or transports.
The enum and lookup implementation are real; query results are synthetic.
"""

import importlib.util
import unittest
from enum import Enum
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, Mock

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("_role_lookup_loader", ROOT / "tests/source_import_isolation.py")
_loader = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_loader)


def module_with(**values):
    module = ModuleType("role_lookup_test_dependency")
    module.__dict__.update(values)
    return module


class PlatformRoleLookupTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        class DatabaseEnum(Enum):
            pass

        self.enums = _loader.load_isolated_source(
            "_role_lookup_enums",
            ROOT / "app/types/enums/user_types.py",
            {"app.utils.db_enums": module_with(DatabaseEnum=DatabaseEnum, database_enum=lambda cls: cls)},
        )

        class BaseManager:
            def __class_getitem__(cls, item):
                return cls

            def __init__(self, model_cls):
                self.model_cls = model_cls

        source = _loader.load_isolated_source(
            "app.models.managers._isolated_role_lookup",
            ROOT / "app/models/managers/role_manager.py",
            {
                "app.models.managers.base_manager": module_with(BaseManager=BaseManager),
                "app.types": self.enums,
            },
        )
        self.manager = source.RoleManager(object)
        self.row = SimpleNamespace(id=7)
        self.first = AsyncMock(return_value=self.row)
        self.manager.filter = Mock(return_value=SimpleNamespace(first=self.first))

    async def assert_lookup(self, supplied, expected, *, by_name=False):
        self.manager.filter.reset_mock()
        self.first.reset_mock()
        method = self.manager.get_by_name if by_name else self.manager.get_by_codename
        result = await method(supplied)
        self.assertIs(result, self.first.return_value)
        self.manager.filter.assert_called_once_with(codename=expected)
        self.first.assert_awaited_once_with()

    async def test_all_platform_enum_codes_reach_the_query(self):
        for role in self.enums.PlatformRoleType:
            with self.subTest(role=role):
                await self.assert_lookup(role, role.value)

    async def test_strings_are_trimmed_and_case_normalized(self):
        for supplied, expected in ((" USER ", "user"), ("Operator", "operator"), ("ADMIN", "admin")):
            with self.subTest(supplied=supplied):
                await self.assert_lookup(supplied, expected)

    async def test_existing_aliases_keep_the_new_platform_catalog_mapping(self):
        for supplied, expected in ((" VIEWER ", "user"), ("Superuser", "admin")):
            with self.subTest(supplied=supplied):
                await self.assert_lookup(supplied, expected)

    async def test_unknown_code_returns_the_query_miss_without_creating_a_role(self):
        self.first.return_value = None
        await self.assert_lookup("not-a-role", "not-a-role")

    async def test_compatibility_name_lookup_uses_the_same_runtime_path(self):
        await self.assert_lookup("VIEWER", "user", by_name=True)

    async def test_arbitrary_platform_role_creation_stays_disabled(self):
        with self.assertRaisesRegex(ValueError, "Arbitrary platform role creation is disabled"):
            await self.manager.create_role(codename="new-role")
        self.manager.filter.assert_not_called()


if __name__ == "__main__":
    unittest.main()
