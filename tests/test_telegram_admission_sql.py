"""NEW actual SQLAlchemy/PostgreSQL compilation and Telethon signature checks.

Run: python tests/test_telegram_admission_sql.py
Uses installed project libraries, minimal local mappings, no engines/app/bootstrap,
DB, HTTP, Telegram clients, sessions or old tests. Compile != driver acceptance.
"""
import inspect
from pathlib import Path
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock

from sqlalchemy import Column, Integer, JSON, String
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import DeclarativeBase
from telethon import TelegramClient
from source_import_isolation import load_isolated_source

ROOT=Path(__file__).resolve().parents[1]


class Base(DeclarativeBase): pass
class Raw(Base):
    __tablename__='collected_items';__table_args__={'schema':'test_schema'}
    id=Column(Integer,primary_key=True);tenant_id=Column(Integer);source_id=Column(Integer)
    external_id=Column(String);content_hash=Column(String);attachments=Column(JSON(none_as_null=True))
class Analytics(Base):
    __tablename__='ai_analytics';__table_args__={'schema':'test_schema'}
    id=Column(Integer,primary_key=True);tenant_id=Column(Integer);source_id=Column(Integer);summary_data=Column(JSON)
class ManagerBase:
    def __class_getitem__(cls,item):return cls
    async def _apply_tenant(self,session,row):row['tenant_id']=4;return row
class Result:
    def __init__(self,value=None,rows=()):self.value=value;self.rows=list(rows)
    def scalar_one_or_none(self):return self.value
    def all(self):return self.rows
    def scalars(self):return self


class SQLCompilationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        utility=load_isolated_source('_l1_sql_snapshots',ROOT/'app/utils/content_attachments.py')
        module=load_isolated_source('app.models.managers._l1_sql',ROOT/'app/models/managers/collected_item_manager.py',{
            'app.models.managers.base_manager':NS(BaseManager=ManagerBase),'app.core.config':NS(settings=NS()),
            'app.core.tenant_context':NS(current_tenant_id=lambda:4,is_bypass=lambda:False),
            'app.utils.content_attachments':utility,'app.models':NS(AIAnalytics=Analytics)})
        self.manager=module.CollectedItemManager();self.manager.model=Raw
        self.row={'source_id':7,'external_id':'-10077_20','content_hash':'a'*64,'attachments':[{'type':'video','url':None}]}
        self.session=NS(execute=AsyncMock(side_effect=[Result(rows=[]),Result(),Result(rows=['-10077_20']),Result(rows=[('-10077_20','a'*64)])]))
    async def statements(self):
        self.assertEqual(await self.manager.admit_items(self.session,[self.row]),1)
        return [call.args[0] for call in self.session.execute.call_args_list]
    async def test_real_pg_insert_returning_json_binding(self):
        statements=await self.statements();stmt=statements[2];compiled=stmt.compile(dialect=postgresql.dialect())
        self.assertIn('ON CONFLICT (source_id, external_id) DO NOTHING RETURNING',str(compiled))
        self.assertEqual(compiled.params['attachments_m0'],[{'type':'video','url':None}])
        processor=Raw.attachments.type.bind_processor(postgresql.dialect())
        self.assertEqual(processor(compiled.params['attachments_m0']),'[{"type": "video", "url": null}]')
    async def test_real_pg_tenant_source_lock_and_exact_identity_probe(self):
        statements=await self.statements()
        for index in (0,3):
            compiled=statements[index].compile(dialect=postgresql.dialect())
            self.assertIn('FOR UPDATE',str(compiled));self.assertIn('tenant_id =',str(compiled));self.assertIn('source_id =',str(compiled))
            self.assertIn(4,compiled.params.values());self.assertIn(7,compiled.params.values())
            self.assertIn(['-10077_20'],compiled.params.values())
    async def test_real_pg_saved_hash_uses_jsonb_array_membership_not_batch_hash(self):
        statements=await self.statements();compiled=statements[1].compile(dialect=postgresql.dialect())
        self.assertIn('AS JSONB',str(compiled));self.assertIn('?| ARRAY[',str(compiled))
        self.assertIn('a'*64,compiled.params.values());self.assertIn('content_hashes',compiled.params.values())
    def test_installed_telethon_accepts_bounded_oldest_first_parameters(self):
        signature=inspect.signature(TelegramClient.iter_messages)
        signature.bind(None,'fake-entity',limit=10,min_id=50,offset_date=None,reverse=True)
        self.assertNotIn('min_date',signature.parameters)


if __name__=='__main__':unittest.main(verbosity=2)
