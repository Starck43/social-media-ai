"""NEW owner-guard compiler/privacy regressions, no app/DB/HTTP/old runs.

Run: python tests/test_telegram_admission_guard.py
Actual SQLAlchemy 2.0.54 compiler + actual owner callback via AST extraction.
Statement defaults are context-synthetic; this is NOT driver acceptance.
"""
import ast
from contextvars import ContextVar
from datetime import datetime,timedelta,timezone
from pathlib import Path
import re
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock,Mock

from sqlalchemy import Boolean,Column,Integer,MetaData,String,Table
from sqlalchemy.dialects.postgresql import dialect,insert
from sqlalchemy.exc import StatementError,IntegrityError
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.sql import operators
from telegram_admission_guard import FixtureGuardError,admission_diagnostic,insert_parameter_rows
from source_import_isolation import load_isolated_source

ROOT=Path(__file__).resolve().parents[1]
RAW=Table('collected_items',MetaData(),Column('tenant_id',Integer),Column('source_id',Integer),
          Column('external_id',String),Column('analyze_attempts',Integer,default=0),
          Column('give_up_after_attempts',Integer,default=3),schema='test_schema')


def compiled_rows(rows):
    command=insert(RAW).values(rows)
    compiled=command.compile(dialect=dialect())
    values=compiled.construct_params()
    # Real execution fills these prefetched scalar defaults. No engine needed.
    for key,value in list(values.items()):
        if value is None and key.startswith('analyze_attempts'):values[key]=0
        if value is None and key.startswith('give_up_after_attempts'):values[key]=3
    return command,compiled,values


def actual_guard():
    module=ast.parse((ROOT/'tests/test_telegram_durable_admission_db.py').read_text())
    callback=next(node for node in ast.walk(module) if isinstance(node,ast.FunctionDef) and node.name=='write_guard')
    globals={'backend_pids':set(),'re':re,'datetime':datetime,'timedelta':timedelta,'timezone':timezone,
             'phase':ContextVar('guard_unit_phase',default='admission'),'owned':{'tenants':{4},'sources':{7},'collected_items':set(),'jobs':set(),'ai_analytics':set()},
             'prefix':'fixture','source':NS(id=7,external_id='fixture-source'),'operators':operators,
             'FixtureGuardError':FixtureGuardError,'insert_parameter_rows':insert_parameter_rows}
    exec(compile(ast.Module(body=[callback],type_ignores=[]),str(ROOT/'tests/test_telegram_durable_admission_db.py'),'exec'),globals)
    return globals['write_guard']


def fenced(rows):
    command,compiled,values=compiled_rows(rows)
    connection=NS(connection=NS(driver_connection=NS(get_server_pid=lambda:123)))
    actual_guard()(connection,None,str(compiled),None,NS(compiled=compiled,compiled_parameters=[values]),False)
    return insert_parameter_rows(command,values,compiled=compiled)


class BindGuardTests(unittest.TestCase):
    def row(self,identity='90',tenant=4,source=7):return {'tenant_id':tenant,'source_id':source,'external_id':'fixture-source_'+identity}
    def test_actual_single_values_insert_accepts_m0_binds_and_unsuffixed_defaults(self):
        rows=fenced([self.row()]);self.assertEqual(rows,[{**self.row(),'analyze_attempts':0,'give_up_after_attempts':3}])
    def test_actual_two_values_insert_preserves_each_row_and_defaults(self):
        rows=fenced([self.row(),self.row('91')]);self.assertEqual([row['external_id'] for row in rows],['fixture-source_90','fixture-source_91'])
        self.assertEqual([row['analyze_attempts'] for row in rows],[0,0])
    def test_foreign_second_tenant_cannot_borrow_first_row_fence(self):
        with self.assertRaises(FixtureGuardError):fenced([self.row(),self.row('91',tenant=999)])
    def test_foreign_second_source_cannot_borrow_first_row_fence(self):
        with self.assertRaises(FixtureGuardError):fenced([self.row(),self.row('91',source=999)])
    def test_missing_row_fence_is_refused(self):
        command,compiled,values=compiled_rows([self.row(),self.row('91')]);del values['tenant_id_m1']
        with self.assertRaises(FixtureGuardError):insert_parameter_rows(command,values,compiled=compiled)
    def test_unindexed_ownership_and_unknown_or_outside_binds_refused(self):
        command,compiled,original=compiled_rows([self.row()])
        for key in ['tenant_id','tenant_id_m2','unknown_m0','tenant_id_m00']:
            with self.subTest(key=key),self.assertRaises(FixtureGuardError):
                insert_parameter_rows(command,{**original,key:4},compiled=compiled)
    def test_nonbatch_plain_parameter_mapping_unchanged(self):
        command=insert(RAW);compiled=command.compile(dialect=dialect());values=self.row()
        self.assertEqual(insert_parameter_rows(command,values,compiled=compiled),[values])
    def test_foreign_plain_orm_parameters_still_refused(self):
        command=insert(RAW);compiled=command.compile(dialect=dialect())
        with self.assertRaises(FixtureGuardError):
            actual_guard()(NS(connection=NS(driver_connection=NS(get_server_pid=lambda:1))),None,str(compiled),None,
                           NS(compiled=compiled,compiled_parameters=[self.row(tenant=999)]),False)


class DiagnosticTests(unittest.IsolatedAsyncioTestCase):
    def test_suppressed_guard_context_remains_safely_identifiable(self):
        try:
            try:raise FixtureGuardError('batch_insert_bind')
            except Exception:raise RuntimeError('public_static_error') from None
        except RuntimeError as error:
            result=admission_diagnostic(error,root=ROOT)
            self.assertEqual(result['kind'],'fixture_guard');self.assertEqual(result['code'],'batch_insert_bind')
            self.assertTrue(error.__suppress_context__)
    def test_sqlalchemy_wrapper_does_not_hide_guard_or_print_private_statement(self):
        guard=FixtureGuardError('batch_insert_row')
        error=StatementError('PRIVATE-message','PRIVATE-sql',{'password':'PRIVATE'},guard)
        result=admission_diagnostic(error,root=ROOT)
        self.assertEqual(result['kind'],'fixture_guard');self.assertNotIn('PRIVATE',repr(result))
    def test_production_sql_error_classified_without_message_or_parameters(self):
        error=IntegrityError('PRIVATE-sql',{'password':'PRIVATE'},RuntimeError('PRIVATE-original'))
        result=admission_diagnostic(error,root=ROOT)
        self.assertEqual(result,{'kind':'production_error','code':'integrity'});self.assertNotIn('PRIVATE',repr(result))
    def test_hostile_error_string_and_repr_are_never_called(self):
        class PrivateError(Exception):
            def __str__(self):raise AssertionError('Do not format')
            def __repr__(self):raise AssertionError('Do not repr')
        self.assertEqual(admission_diagnostic(PrivateError(),root=ROOT),{'kind':'production_error','code':'unclassified'})
    async def test_actual_production_boundary_preserves_from_none_and_runner_finds_guard(self):
        class Base(DeclarativeBase):pass
        class Source(Base):
            __tablename__='sources';id=Column(Integer,primary_key=True);tenant_id=Column(Integer)
            external_id=Column(String);is_active=Column(Boolean)
        class Transaction:
            async def __aenter__(self):return self
            async def __aexit__(self,*args):return False
        session=NS(begin=lambda:Transaction(),execute=AsyncMock(side_effect=FixtureGuardError('batch_insert_bind')),close=AsyncMock())
        module=load_isolated_source('_admission_guard_privacy',ROOT/'app/services/monitoring/staging.py',{
            'app.core.tenant_context':NS(current_tenant_id=lambda:4,is_bypass=lambda:False),
            'app.utils.collected_content':NS(build_staged_rows=lambda *args:[]),
            'app.core.database':NS(new_session=lambda:session),'app.models':NS(Source=Source,CollectedItem=Mock(),Job=Mock())})
        source=NS(id=7,tenant_id=4,external_id='fixture-source')
        with self.assertRaises(module.ContentAdmissionError) as caught:
            await module.admit_telegram_items([{'id':'90','external_id':'fixture-source_90','platform':'telegram'}],source)
        self.assertTrue(caught.exception.__suppress_context__);self.assertIsNone(caught.exception.__cause__)
        self.assertEqual(admission_diagnostic(caught.exception,root=ROOT)['kind'],'fixture_guard')
        session.close.assert_awaited_once()


if __name__=='__main__':unittest.main(verbosity=2)
