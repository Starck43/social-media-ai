"""NEW actual-source admission/SQL/transport checks; module-local doubles only.

Run: python tests/test_telegram_durable_admission.py
No app imports/bootstrap, DB, HTTP, Telethon, providers or historical test runs.
Doubles prove control flow and statement contracts, NOT PostgreSQL acceptance.
"""
import asyncio
import copy
from contextlib import nullcontext
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, Mock

from source_import_isolation import load_isolated_source

ROOT = Path(__file__).resolve().parents[1]


def load(name, path, imports=None):
    bindings = dict(imports or {})
    module_imports = {'httpx': bindings.pop('httpx')} if 'httpx' in bindings else None
    alias = 'app.models.managers._l1_manager' if name == 'manager' else '_l1_' + name
    return load_isolated_source(alias, ROOT / path, bindings, module_imports=module_imports)


class Col:
    def __init__(self, name): self.name = name
    def __eq__(self, value): return ('eq', self.name, value)
    def is_(self, value): return ('is', self.name, value)
    def in_(self, value): return ('in', self.name, value)
    def __getitem__(self, key): return Col(self.name + '.' + key)
    def contains(self, value): return ('contains', self.name, value)
    def has_any(self, value): return ('has_any', self.name, value)


class SQL:
    def __init__(self, *columns):
        self.columns = columns; self.criteria = []; self.locked = False
    def where(self, *criteria): self.criteria += list(criteria); return self
    def with_for_update(self): self.locked = True; return self
    def limit(self, value): self.limit_value = value; return self
    def values(self, value): self.rows = value; return self
    def on_conflict_do_nothing(self, **kw): self.conflict = kw; return self
    def returning(self, column): self.returned = column; return self


SA = NS(select=SQL, cast=lambda column, kind: column)
SRC = type('Source', (), {name: Col(name) for name in ('id','tenant_id','external_id','is_active')})
RAW = type('Collected', (), {name: Col(name) for name in ('id','tenant_id','source_id','external_id','content_hash')})
AI = type('Analytics', (), {name: Col(name) for name in ('id','tenant_id','source_id','summary_data')})


class Result:
    def __init__(self, scalar=None, rows=()): self.scalar = scalar; self.rows = list(rows)
    def scalar_one_or_none(self): return self.scalar
    def all(self): return self.rows
    def scalars(self): return self


class Transaction:
    def __init__(self, fixture): self.f = fixture
    async def __aenter__(self):
        self.before = copy.deepcopy(vars(self.f.locked)); self.f.events.append('begin'); return self
    async def __aexit__(self, kind, error, traceback):
        self.f.events.append('commit' if kind is None else 'rollback')
        if kind is not None or self.f.commit_error:
            vars(self.f.locked).clear(); vars(self.f.locked).update(self.before)
        if self.f.commit_error: raise self.f.commit_error
        return False


class AdmissionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.events=[]; self.commit_error=None
        self.source=NS(id=7, tenant_id=4, external_id='-10077', params={}, last_item_id='50')
        self.locked=copy.deepcopy(self.source)
        self.utility=load('attachments','app/utils/content_attachments.py')
        self.dedup=load('dedup','app/services/ai/dedup.py')
        self.builder=load('rows','app/utils/collected_content.py', {
            'app.services.ai.dedup':self.dedup,
            'app.utils.date_parsing':NS(universal_date_parser=lambda value:value),
            'app.utils.content_attachments':self.utility})
        self.execute=AsyncMock(return_value=Result(self.locked))
        self.session=NS(begin=lambda:Transaction(self), execute=self.execute, close=AsyncMock())
        async def store(session, rows): self.events.append('raw'); self.rows=rows; return self.written
        async def enqueue(*args,**kwargs): self.events.append('queue'); self.enqueue_args=(args,kwargs); return NS(id=9)
        self.written=1; self.store=AsyncMock(side_effect=store); self.enqueue=AsyncMock(side_effect=enqueue)
        self.context=NS(current_tenant_id=lambda:4,is_bypass=lambda:False,tenant_scope=lambda *a,**kw:nullcontext())
        self.models=NS(Source=SRC,CollectedItem=NS(objects=NS(admit_items=self.store)),Job=NS(objects=NS(enqueue=self.enqueue)))
        self.staging=load('staging','app/services/monitoring/staging.py',{
            'app.core.tenant_context':self.context,'app.utils.collected_content':self.builder,
            'sqlalchemy':SA,'app.core.database':NS(new_session=lambda:self.session),'app.models':self.models})
        self.ingest=load('ingest','app/services/monitoring/ingest.py',{
            'app.services.monitoring.staging':self.staging,'app.utils.content_attachments':self.utility,
            'app.core.tenant_context':self.context,'app.models':NS(Source=NS(objects=NS(
                select_related=lambda *a:NS(get=AsyncMock(return_value=self.source)))) )})
        self.ingest.logger=Mock()
    def inbound(self, **fields):
        return NS(channel='telegram',chat_id='-10077',is_channel_post=True,raw={'channel_post':{
            'message_id':20,'date':1791720000,'chat':{'id':-10077},**fields}})
    def item(self, identity='20', **fields):
        return {'id':identity,'external_id':f'-10077_{identity}','platform':'telegram','text':'Useful text',**fields}
    async def admit(self, **kwargs): return await self.staging.admit_telegram_items([self.item()],self.source,**kwargs)
    def test_caption_snapshot_excludes_file_secrets(self):
        item=self.ingest.normalize_channel_post(self.inbound(caption='Useful text',photo=[{'file_id':'PRIVATE'}]))
        self.assertEqual(item['text'],'Useful text'); self.assertEqual(item['attachments'],[{'type':'image','url':None}])
        self.assertNotIn('PRIVATE',repr(item)); self.assertEqual(self.dedup.item_hash(item),self.dedup.item_hash(self.item()))
    def test_media_only_and_unknown_are_not_discarded(self):
        for field,kind in [('video','video'),('document','unknown'),('audio','unknown'),('photo','image')]:
            with self.subTest(field=field):
                item=self.ingest.normalize_channel_post(self.inbound(**{field:{'file_id':'PRIVATE'}}))
                self.assertEqual(item['text'],''); self.assertEqual(item['attachments'],[{'type':kind,'url':None}])
    def test_text_only_and_service_update_distinction(self):
        self.assertEqual(self.ingest.normalize_channel_post(self.inbound(text='Useful'))['attachments'],[])
        self.assertIsNone(self.ingest.normalize_channel_post(self.inbound(new_chat_title='Title')))
    def test_invalid_identity_fails_closed(self):
        for identity in [None,False,0,-1,'20']:
            with self.subTest(identity=identity),self.assertRaises(ValueError):
                self.ingest.normalize_channel_post(self.inbound(message_id=identity,text='Useful'))
    async def test_l1_lower_id_admits_before_monotonic_cursor_and_job_commit(self):
        self.assertEqual(await self.admit(wake_analysis=True),1)
        self.assertEqual(self.events,['begin','raw','queue','commit'])
        self.assertEqual(self.locked.last_item_id,'50')
        self.assertEqual(self.locked.params['telegram_l2_last_item_id'],'50')
        args,kw=self.enqueue_args
        self.assertEqual(args,('analyze',{'source_ids':[7]}));self.assertEqual(kw,{'session':self.session})
        stmt=self.execute.call_args.args[0];self.assertTrue(stmt.locked)
        self.assertIn(('eq','tenant_id',4),stmt.criteria);self.assertIn(('is','is_active',True),stmt.criteria)
    async def test_l1_does_not_advance_l2_cursor(self):
        item=self.item('90')
        await self.staging.admit_telegram_items([item],self.source,wake_analysis=True)
        self.assertEqual(self.locked.last_item_id,'90');self.assertEqual(self.locked.params['telegram_l2_last_item_id'],'50')
    async def test_l2_own_cursor_advances_without_regressing_shared_max(self):
        self.locked.params={'telegram_l2_last_item_id':'10','keep':True}
        await self.admit()
        self.assertEqual(self.locked.last_item_id,'50'); self.assertEqual(self.locked.params,{'telegram_l2_last_item_id':'20','keep':True})
        self.enqueue.assert_not_awaited()
    async def test_duplicate_receipt_creates_no_job(self):
        self.written=0; self.assertEqual(await self.admit(wake_analysis=True),0)
        self.enqueue.assert_not_awaited();self.assertEqual(self.events,['begin','raw','commit'])
    async def test_commit_failure_has_no_success_and_rolls_cursor_back(self):
        self.commit_error=RuntimeError('PRIVATE-db');before=copy.deepcopy(vars(self.locked))
        with self.assertRaisesRegex(self.staging.ContentAdmissionError,'content_admission_unconfirmed') as cm:
            await self.staging.admit_telegram_items([self.item('90')],self.source,wake_analysis=True)
        self.assertEqual(vars(self.locked),before);self.assertTrue(cm.exception.__suppress_context__)
        self.session.close.assert_awaited_once()
    async def test_queue_failure_rolls_back_raw_and_cursor(self):
        self.enqueue.side_effect=RuntimeError('PRIVATE-queue');before=copy.deepcopy(vars(self.locked))
        with self.assertRaises(self.staging.ContentAdmissionError): await self.admit(wake_analysis=True)
        self.assertEqual(vars(self.locked),before);self.assertEqual(self.events[-1],'rollback')
    async def test_invalid_receipt_and_missing_source_cannot_ack(self):
        self.written=True
        with self.assertRaisesRegex(self.staging.ContentAdmissionError,'content_receipt_invalid'):await self.admit()
        self.execute.return_value=Result(None);self.store.reset_mock()
        with self.assertRaisesRegex(self.staging.ContentAdmissionError,'content_source_unavailable'):await self.admit()
        self.store.assert_not_awaited()
    async def test_missing_tenant_and_bypass_refused_before_session_write(self):
        self.staging.current_tenant_id=lambda:None
        with self.assertRaisesRegex(self.staging.ContentAdmissionError,'content_tenant_invalid'):await self.admit()
        self.staging.current_tenant_id=lambda:4;self.staging.is_bypass=lambda:True
        with self.assertRaises(self.staging.ContentAdmissionError):await self.admit()
        self.execute.assert_not_awaited()
    def test_malformed_cursors_are_not_zero_or_truncated(self):
        for value in [False,True,1.2,{},[],"not-a-cursor",-1]:
            with self.subTest(value=value),self.assertRaises(self.staging.ContentAdmissionError):
                self.staging.cursor_value(value)
        self.assertEqual(self.staging.cursor_value(None),0)
        self.assertEqual(self.staging.cursor_value("50"),50)
    async def test_actual_cancellation_rolls_back_and_propagates(self):
        self.store.side_effect=asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):await self.admit(wake_analysis=True)
        self.enqueue.assert_not_awaited();self.assertEqual(self.events[-1],'rollback')
    async def test_ingest_reuses_admission_not_analyzer_or_watermark_skip(self):
        self.ingest._find_source=AsyncMock(return_value=(7,4,'9999'))
        self.ingest._is_digest_target=AsyncMock(return_value=False)
        self.ingest.admit_telegram_items=AsyncMock(return_value=0)
        self.assertTrue(await self.ingest.ingest_channel_post(self.inbound(text='Useful')))
        self.ingest.admit_telegram_items.assert_awaited_once()
        self.assertEqual(self.ingest.admit_telegram_items.call_args.kwargs,{'wake_analysis':True})
    async def test_intentional_digest_skip_has_no_storage(self):
        self.ingest._find_source=AsyncMock(return_value=(7,4,'50'));self.ingest._is_digest_target=AsyncMock(return_value=True)
        self.ingest.admit_telegram_items=AsyncMock()
        self.assertFalse(await self.ingest.ingest_channel_post(self.inbound(text='Useful')))
        self.ingest.admit_telegram_items.assert_not_awaited()


class ManagerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        class Base:
            def __class_getitem__(cls,item):return cls
            async def _apply_tenant(self,session,row):row['tenant_id']=4;return row
        self.context=NS(current_tenant_id=lambda:4,is_bypass=lambda:False)
        self.utility=load('manager_attachments','app/utils/content_attachments.py')
        self.module=load('manager','app/models/managers/collected_item_manager.py',{
            'sqlalchemy':NS(JSON=object,bindparam=lambda *a,**kw:None,text=lambda *a:None,select=SQL,cast=lambda c,t:c),
            'sqlalchemy.dialects.postgresql':NS(JSONB=object,insert=SQL,array=lambda values:values),
            'app.core.config':NS(settings=NS()),'app.utils.content_attachments':self.utility,
            'app.models.managers.base_manager':NS(BaseManager=Base),
            'app.core.tenant_context':self.context,'app.models':NS(AIAnalytics=AI)})
        self.manager=self.module.CollectedItemManager();self.manager.model=RAW
        self.row={'source_id':7,'external_id':'-10077_20','content_hash':'a'*64,'attachments':[{'type':'document','url':'PRIVATE'}]}
    def session(self,results):return NS(execute=AsyncMock(side_effect=results))
    async def test_strict_insert_returning_and_exact_receipt_scope(self):
        session=self.session([Result(rows=[]),Result(),Result(rows=['-10077_20']),Result(rows=[('-10077_20','a'*64)])])
        self.assertEqual(await self.manager.admit_items(session,[self.row]),1)
        insert=session.execute.call_args_list[2].args[0]
        self.assertEqual(insert.conflict['index_elements'],[RAW.source_id,RAW.external_id])
        self.assertEqual(insert.rows[0]['tenant_id'],4)
        self.assertEqual(insert.rows[0]['attachments'],[{'type':'unknown','url':None}])
        for index in [0,3]:
            stmt=session.execute.call_args_list[index].args[0]
            self.assertTrue(stmt.locked);self.assertIn(('eq','tenant_id',4),stmt.criteria);self.assertIn(('eq','source_id',7),stmt.criteria)
    async def test_duplicate_staged_does_not_reset_or_write(self):
        session=self.session([Result(rows=[('-10077_20','a'*64)])])
        self.assertEqual(await self.manager.admit_items(session,[self.row]),0);self.assertEqual(session.execute.await_count,1)
    async def test_retired_parent_hash_receipt_avoids_restage(self):
        session=self.session([Result(rows=[]),Result(rows=[{'content_hashes':['a'*64]}])])
        self.assertEqual(await self.manager.admit_items(session,[self.row]),0)
        stmt=session.execute.call_args_list[1].args[0]
        self.assertIn(('eq','tenant_id',4),stmt.criteria)
        self.assertIn(('has_any','summary_data.content_hashes',['a'*64]),stmt.criteria)
    async def test_missing_and_conflicting_receipts_refuse_admission(self):
        session=self.session([Result(rows=[('-10077_20','b'*64)])])
        with self.assertRaisesRegex(ValueError,'content_identity_conflict'):await self.manager.admit_items(session,[self.row])
        session=self.session([Result(rows=[]),Result(),Result(rows=[]),Result(rows=[])])
        with self.assertRaisesRegex(ValueError,'content_receipt_missing'):await self.manager.admit_items(session,[self.row])
    async def test_batch_coverage_lookup_is_one_query_not_n_plus_one(self):
        second={**self.row,'external_id':'-10077_21','content_hash':'b'*64}
        session=self.session([Result(rows=[]),Result(rows=[]),Result(rows=['-10077_20','-10077_21']),
                              Result(rows=[('-10077_20','a'*64),('-10077_21','b'*64)])])
        self.assertEqual(await self.manager.admit_items(session,[self.row,second]),2)
        self.assertEqual(session.execute.await_count,4)
        self.assertIn(('has_any','summary_data.content_hashes',['a'*64,'b'*64]),session.execute.call_args_list[1].args[0].criteria)
    async def test_malformed_saved_hash_ledger_cannot_certify_a_post(self):
        session=self.session([Result(rows=[]),Result(rows=[{'content_hashes':{'a'*64:'not-an-array'}}]),
                              Result(rows=['-10077_20']),Result(rows=[('-10077_20','a'*64)])])
        self.assertEqual(await self.manager.admit_items(session,[self.row]),1)
    async def test_manager_refuses_bypass_before_SQL(self):
        self.context.is_bypass=lambda:True;session=self.session([])
        with self.assertRaisesRegex(ValueError,'content_tenant_invalid'):await self.manager.admit_items(session,[self.row])
        session.execute.assert_not_awaited()


class TransportTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.base=load('channel_base','app/channels/base.py')
        self.updates=[{'update_id':100,'channel_post':{'message_id':20,'caption':'Useful','photo':[{'file_id':'PRIVATE'}],'chat':{'id':-10077}}},
                      {'update_id':101,'channel_post':{'message_id':21,'video':{'file_id':'PRIVATE'},'chat':{'id':-10077}}}]
        fixture=self
        class Client:
            async def __aenter__(self):fixture.client_closed=False;return self
            async def __aexit__(self,*args):fixture.client_closed=True
            async def get(self,*args,**kw):return NS(json=lambda:{'ok':True,'result':fixture.updates})
        self.poll=load('poll','app/channels/telegram.py',{
            'httpx':NS(AsyncClient=lambda **kw:Client(),HTTPError=type('HTTPError',(Exception,),{})),
            'app.channels.base':self.base,'app.core.config':NS(settings=NS(TELEGRAM_API_BASE_URL='https://fake.invalid',TELEGRAM_BOT_TOKEN='PRIVATE')),
            'app.channels.delivery_parts':NS(REJECTED_HTTP_STATUSES=set(),part_result=Mock(),valid_part=Mock())})
        self.channel=self.poll.TelegramChannel()
        self.ingest=AsyncMock(side_effect=RuntimeError('PRIVATE-db-payload'))
        self.listener=load('listener','app/channels/listener.py',{
            'app.channels.base':self.base,'app.channels.registry':NS(enabled_channels=lambda:[]),
            'app.services.monitoring.ingest':NS(ingest_channel_post=self.ingest)})
        self.listener.logger=Mock()
    async def test_poll_caption_and_media_only_ack_only_after_resume(self):
        updates=self.channel.poll();first=await anext(updates)
        self.assertEqual(first.text,'Useful');self.assertEqual(self.channel._offset,0)
        second=await anext(updates)
        self.assertEqual(second.text,'');self.assertEqual(self.channel._offset,101)
        await updates.aclose();self.assertEqual(self.channel._offset,101);self.assertTrue(self.client_closed)
    async def test_close_first_yield_does_not_ack_failed_admission(self):
        updates=self.channel.poll();await anext(updates);await updates.aclose()
        self.assertEqual(self.channel._offset,0);self.assertTrue(self.client_closed)
    async def test_listener_failclosed_and_iterator_drained(self):
        async def backoff(*args):raise asyncio.CancelledError()
        self.listener.asyncio=NS(CancelledError=asyncio.CancelledError,sleep=backoff)
        # Cancellation of the backoff is outside the poll try and must propagate.
        with self.assertRaises(asyncio.CancelledError):await self.listener._consume_channel(self.channel)
        self.assertEqual(self.channel._offset,0);self.assertTrue(self.client_closed)
        self.assertNotIn('PRIVATE',repr(self.listener.logger.call_args_list))
    async def test_new_caption_media_ingest_does_not_activate_agent_but_legacy_text_route_stays(self):
        for legacy_text in (False, True):
            with self.subTest(legacy_text=legacy_text):
                fixture=self
                raw={'channel_post':{'caption':'Useful'}}
                if legacy_text:raw['channel_post']['text']='Existing text'
                inbound=self.base.Inbound('telegram','-10077','1','Useful',True,raw)
                class Channel:
                    name='telegram'
                    async def poll(self):
                        yield inbound
                        raise asyncio.CancelledError()
                self.ingest.side_effect=None
                self.listener._handle_safely=AsyncMock(return_value=None)
                await self.listener._consume_channel(Channel())
                self.assertEqual(self.listener._handle_safely.await_count, int(legacy_text))
    async def test_ingest_error_is_static_and_actual_cancel_propagates(self):
        inbound=self.base.Inbound('telegram','-10077','1','Useful',True)
        with self.assertRaisesRegex(RuntimeError,'ingest_admission_unconfirmed') as cm:await self.listener._ingest_safely(inbound)
        self.assertTrue(cm.exception.__suppress_context__)
        self.ingest.side_effect=asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):await self.listener._ingest_safely(inbound)


class PullTests(unittest.IsolatedAsyncioTestCase):
    async def test_l2_fetch_oldest_first_has_no_cursor_write(self):
        seen={}
        class Client:
            connect=AsyncMock();disconnect=AsyncMock();is_user_authorized=AsyncMock(return_value=True)
            async def iter_messages(self,entity,**kwargs):
                seen.update(kwargs);yield NS(id=21)
        client=Client()
        module=load('pull','app/services/social/tg_client.py',{
            'app.core.config':NS(settings=NS()),'app.models':NS(Source=object),
            'app.services.social.base':NS(BaseClient=object),'app.types':NS(SourceType=object),
            'app.services.social.credentials':NS(AuthorizationRequired=type('AuthError',(Exception,),{})),
            'app.services.social.owner':NS(resolve_source_owner=AsyncMock(return_value=3)),
            'app.services.social.tg_session':NS(build_client=lambda session:client,load_session=AsyncMock(return_value='not-real'))})
        pull=module.TelegramClient();pull._resolve_entity=AsyncMock(return_value='chat');pull._normalize_message=lambda m,s:{'id':str(m.id)}
        source=NS(id=7,params={'telegram_l2_last_item_id':'20'},last_item_id='999')
        items=await pull._collect_mtproto(source)
        self.assertEqual(items,[{'id':'21'}]);self.assertEqual(source.last_item_id,'999')
        self.assertEqual(seen['min_id'],20);self.assertIs(seen['reverse'],True)
        self.assertIn('offset_date',seen);self.assertNotIn('min_date',seen)
        client.disconnect.assert_awaited_once()


if __name__ == '__main__': unittest.main(verbosity=2)
