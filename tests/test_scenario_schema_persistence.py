"""NEW statement/event checks; execution deferred. No engine/connect/app/DB.

Run later: python tests/test_scenario_schema_persistence.py
Requires existing SQLAlchemy/Pydantic. Not real transaction/admin acceptance.
"""
from pathlib import Path
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock
from sqlalchemy import Boolean, Column, Integer, JSON, MetaData, Table, bindparam, insert, select, update
from sqlalchemy.sql import func
from source_import_isolation import load_isolated_source
from test_scenario_output_contract import load_contract
ROOT = Path(__file__).resolve().parents[1]


class PersistenceFenceTests(unittest.TestCase):
    def setUp(self):
        self.schema, _ = load_contract()
        self.table = Table('agent_scenarios', MetaData(), Column('id', Integer), Column('tenant_id', Integer),
                           Column('output_schema', JSON), Column('analysis_types', JSON), Column('scope', JSON),
                           Column('is_active', Boolean), schema='test_schema')
        self.model = type('SyntheticScenario', (), {'__table__': self.table,
                         **{name: self.table.c[name] for name in self.schema.SCHEMA_FIELDS}})
        self.events, self.inspect = {}, Mock()
        event = NS(listen=lambda target, name, callback: self.events.__setitem__(name, callback))
        module = load_isolated_source('_new_schema_persistence', ROOT / 'app/models/scenario_schema_guard.py', {
            'sqlalchemy': NS(event=event, inspect=self.inspect, select=select),
            'app.services.ai.scenario_schema': self.schema})
        self.module = module
        module.install_scenario_schema_guard(self.model)
        self.row = {'output_schema': {'type': 'object'}, 'analysis_types': [], 'scope': {}}
        self.execute = Mock(return_value=NS(mappings=lambda: [self.row]))
        self.session = NS(execute=self.execute, commit=Mock())

    def invoke(self, statement, parameters=None, many=False):
        self.events['do_orm_execute'](NS(is_update=statement.is_update, is_insert=statement.is_insert,
            statement=statement, parameters=parameters, is_executemany=many, session=self.session))
        self.session.commit.assert_not_called()

    def test_model_registration_does_not_import_ai_service_package(self):
        class UnavailableContract:
            def __getattribute__(self, name):
                raise AssertionError("AI service contract imported during model registration")
        self.module.__isolated_imports__['app.services.ai.scenario_schema'] = UnavailableContract()
        self.module.install_scenario_schema_guard(self.model)
        self.assertIn('before_insert', self.events)
        self.assertIn('before_update', self.events)
        self.assertIn('do_orm_execute', self.events)

    def test_literal_update_preserves_predicates_and_row_lock(self):
        statement = update(self.table).where(self.table.c.id == bindparam('target_id'),
            self.table.c.tenant_id == bindparam('tenant')).values(output_schema={'type': 'object'})
        before = str(statement.compile())
        self.invoke(statement, {'target_id': 7, 'tenant': 3})
        query, parameters = self.execute.call_args.args
        self.assertIsNotNone(query._for_update_arg)
        self.assertEqual(list(query._where_criteria), list(statement._where_criteria))
        self.assertEqual(parameters, {'target_id': 7, 'tenant': 3})
        self.assertEqual(str(statement.compile()), before)

    def test_required_bind_values_are_resolved_or_rejected(self):
        statement = update(self.table).values(output_schema=bindparam('new_schema'))
        self.invoke(statement, {'new_schema': {'type': 'object'}})
        with self.assertRaises(self.schema.ScenarioSchemaError):
            self.invoke(statement)

    def test_invalid_update_schema_is_rejected(self):
        with self.assertRaises(self.schema.ScenarioSchemaError):
            self.invoke(update(self.table).values(output_schema={'type': 'unknown'}))

    def test_expression_update_is_rejected(self):
        with self.assertRaises(self.schema.ScenarioSchemaError):
            self.invoke(update(self.table).values(output_schema=func.json_build_object('type', 'object')))

    def test_executemany_configuration_update_is_rejected(self):
        with self.assertRaises(self.schema.ScenarioSchemaError):
            self.invoke(update(self.table), [{'output_schema': {'type': 'object'}}], many=True)

    def test_literal_insert_parameters_are_checked(self):
        self.invoke(insert(self.table), {**self.row, 'id': 1, 'tenant_id': 1})
        self.execute.assert_not_called()
        with self.assertRaises(self.schema.ScenarioSchemaError):
            self.invoke(insert(self.table), {'output_schema': {'type': 'unknown'}})

    def test_multi_values_insert_is_rejected(self):
        with self.assertRaises(self.schema.ScenarioSchemaError):
            self.invoke(insert(self.table).values([self.row, self.row]))

    def test_insert_from_select_is_rejected(self):
        with self.assertRaises(self.schema.ScenarioSchemaError):
            self.invoke(insert(self.table).from_select(['id'], select(self.table.c.id)))

    def test_foreign_table_is_not_intercepted(self):
        other = Table('other', MetaData(), Column('output_schema', JSON), schema='test_schema')
        self.invoke(update(other).values(output_schema={'type': 'unknown'}))
        self.execute.assert_not_called()

    def test_disable_and_unrelated_update_do_not_revalidate_legacy_schema(self):
        self.row['output_schema'] = {'type': 'unknown'}
        self.invoke(update(self.table).values(is_active=False))
        self.invoke(update(self.table).values(tenant_id=1))
        self.execute.assert_not_called()

    def test_reactivation_revalidates_existing_schema(self):
        self.row['output_schema'] = {'type': 'unknown'}
        with self.assertRaises(self.schema.ScenarioSchemaError):
            self.invoke(update(self.table).values(is_active=True))

    def test_orm_insert_update_hooks_cover_admin_flush(self):
        target = NS(**self.row, is_active=True)
        self.events['before_insert'](None, None, target)
        target.output_schema = {'type': 'unknown'}
        with self.assertRaises(self.schema.ScenarioSchemaError):
            self.events['before_insert'](None, None, target)
        self.inspect.return_value = NS(attrs={name: NS(history=NS(has_changes=lambda: True))
                       for name in self.schema.SCHEMA_FIELDS | {'is_active'}})
        with self.assertRaises(self.schema.ScenarioSchemaError):
            self.events['before_update'](None, None, target)


if __name__ == '__main__':
    unittest.main()
