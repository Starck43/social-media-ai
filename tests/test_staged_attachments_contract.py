"""Prepared staged-media schema contract; actual methods + local doubles only.

Run: python tests/test_staged_attachments_contract.py
No app/SQLAlchemy/alembic bootstrap, DB, DDL, downloads or provider calls.
"""
import ast
import asyncio
import builtins
import copy
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Sequence
import unittest
from unittest.mock import Mock

from source_import_isolation import load_isolated_source
import test_collector_staging_log_privacy as collector_fixtures
import test_staged_count_scope as sql_fixtures
import test_media_skip_coverage as media_fixtures

ROOT = Path(__file__).resolve().parents[1]
UTILITY = load_isolated_source('_staged_media_utils', ROOT/'app/utils/content_attachments.py')
NORMALIZE = UTILITY.normalize_attachments
SAFE_URL = 'https://cdn.example.org/image.jpg'


def method(path, owner, name, scope):
    tree = ast.parse((ROOT/path).read_text())
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == owner)
    node = next(n for n in cls.body if getattr(n, 'name', None) == name)
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(ROOT/path), 'exec'), scope)
    return scope[name]


def local_import(name, *args, **kwargs):
    if name == 'app.utils.content_attachments':
        return SimpleNamespace(normalize_attachments=NORMALIZE)
    return builtins.__import__(name, *args, **kwargs)


def replay(row):
    scope = {'Any': Any, '__builtins__': {**vars(builtins), '__import__': local_import}}
    return method('app/models/collection/collected_item.py', 'CollectedItem', 'as_agent_item', scope)(row)


def manager(stamp=17, reject=False):
    scope = {'Any': Any, 'Sequence': Sequence, 'JSON': sql_fixtures.JsonType,
        'sa_text': sql_fixtures.Statement, 'bindparam': lambda name, **kw: (name, kw),
        'settings': SimpleNamespace(DB_SCHEMA='unit_schema'), 'normalize_attachments': NORMALIZE}
    store = method('app/models/managers/collected_item_manager.py', 'CollectedItemManager', 'store_items', scope)
    obj = sql_fixtures.Manager(stamp=stamp, reject=reject)
    obj.store_items = store.__get__(obj)
    return obj


class StagedAttachmentsContractTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        f = collector_fixtures.CollectorStagingLogPrivacyTests()
        f.setUp()  # Setup only: no imported TestCase in discovery or old checks.
        f.source.params = {}
        f.source.platform = SimpleNamespace(code='vk')
        f.module.__isolated_imports__['app.utils.content_attachments'] = SimpleNamespace(normalize_attachments=NORMALIZE)
        self.f = f
        self.dedup = load_isolated_source('_staged_attachment_hashes', ROOT/'app/services/ai/dedup.py')
        f.module.__isolated_imports__['app.services.ai.dedup'].item_hash = self.dedup.item_hash
        media = type('MediaType', (), {k: SimpleNamespace(db_value=v) for k,v in
            [('TEXT','text'), ('IMAGE','image'), ('VIDEO','video')]})
        self.classifier = load_isolated_source('_staged_attachments_classifier', ROOT/'app/services/ai/content_classifier.py',
            {'app.types.enums.llm_types': SimpleNamespace(MediaType=media),
             'app.utils.content_attachments': UTILITY}).ContentClassifier
        self.item = {'external_id':'post','platform':'vk','text':'A useful social post with attachments',
            'date':None,'permalink':'https://example.org/post','metrics':{'views':1},
            'author':{'id':2}, 'attachments':[{'type':'photo','url':SAFE_URL}, {'type':'video_file','url':'https://cdn.example.org/video.mp4'}]}

    async def stage(self, item=None):
        await self.f.collector._stage_items([self.item if item is None else item], self.f.source, 29)
        return self.f.store.await_args.args[1][0]

    async def test_collector_replay_preserves_canonical_image_and_video(self):
        original = copy.deepcopy(self.item)
        row = await self.stage()
        restored = replay(SimpleNamespace(**row))
        self.assertEqual(restored['attachments'], NORMALIZE(original['attachments']))
        media = self.classifier.classify_content([restored])
        self.assertEqual(self.classifier.get_media_urls(media['image']), [SAFE_URL])
        self.assertEqual(self.classifier.get_media_urls(media['video']), ['https://cdn.example.org/video.mp4'])
        self.assertEqual(self.dedup.item_hash(original), self.dedup.item_hash(restored))
        self.assertEqual(self.item, original)
        self.f.transaction.enter.assert_awaited_once()
        self.f.transaction.exit.assert_awaited_once()
        self.f.session.close.assert_awaited_once()

    async def test_collector_keeps_absent_unknown_distinct_from_empty(self):
        unknown = {k:v for k,v in self.item.items() if k != 'attachments'}
        row = await self.stage(unknown)
        self.assertIsNone(row['attachments'])
        self.assertNotIn('attachments', replay(SimpleNamespace(**row)))
        row = await self.stage({**self.item, 'attachments': []})
        self.assertEqual(row['attachments'], [])
        self.assertEqual(replay(SimpleNamespace(**row))['attachments'], [])

    async def test_manager_binds_three_json_columns_without_stringifying(self):
        row = await self.stage()
        session = sql_fixtures.Session(rowcount=1)
        incoming = copy.deepcopy(row)
        self.assertEqual(await manager().store_items(session, [row]), 1)
        stmt, params = session.calls[0]
        self.assertIn('CAST(:attachments AS jsonb)', stmt.text)
        self.assertIn('ON CONFLICT (source_id, external_id) DO NOTHING', stmt.text)
        binds = dict(stmt.binds)
        self.assertEqual(set(binds), {'metrics','author','attachments'})
        self.assertTrue(all(v['type_'].kwargs == {'none_as_null':True} for v in binds.values()))
        self.assertIsInstance(params[0]['attachments'], list)
        self.assertEqual(json.loads(json.dumps(params[0]['attachments'])), NORMALIZE(self.item['attachments']))
        self.assertEqual(params[0]['tenant_id'], 17)
        self.assertEqual(row, incoming)

    async def test_manager_missing_payload_gets_sql_null_binding(self):
        row = await self.stage()
        row.pop('attachments')
        session = sql_fixtures.Session(rowcount=1)
        await manager().store_items(session, [row])
        self.assertIsNone(session.calls[0][1][0]['attachments'])
        self.assertNotIn('attachments', row)

    async def test_manager_keeps_explicit_empty_payload(self):
        row = await self.stage({**self.item, 'attachments':[]})
        session = sql_fixtures.Session(rowcount=1)
        await manager().store_items(session, [row])
        self.assertEqual(session.calls[0][1][0]['attachments'], [])

    async def test_manager_normalizes_direct_caller_payload(self):
        row = await self.stage()
        row['attachments'] = [{'type':'IMAGE','url':SAFE_URL,'provider_body':'private','api_key':'private'}]
        session = sql_fixtures.Session(rowcount=1)
        await manager().store_items(session, [row])
        self.assertEqual(session.calls[0][1][0]['attachments'], [{'type':'image','url':SAFE_URL}])
        self.assertIn('provider_body', row['attachments'][0])

    async def test_tenant_failure_still_happens_before_sql(self):
        row = await self.stage()
        session = sql_fixtures.Session(rowcount=1)
        with self.assertRaises(RuntimeError):
            await manager(reject=True).store_items(session, [row])
        self.assertEqual(session.calls, [])

    async def test_scoped_count_fallback_is_not_widened(self):
        row = await self.stage()
        neighbours = [{'tenant_id':17,'source_id':self.f.source.id,'run_id':29},
            {'tenant_id':18,'source_id':self.f.source.id,'run_id':29}, {'tenant_id':17,'source_id':999,'run_id':29}]
        session = sql_fixtures.Session(persisted=neighbours, rowcount=-1)
        self.assertEqual(await manager().store_items(session, [row]), 1)
        self.assertEqual(len(session.calls), 2)
        self.assertEqual(session.calls[1][1], {'run_id':29,'tenant_id_0':17,'source_id_0':self.f.source.id})

    async def test_storage_exception_keeps_zero_and_close_contract(self):
        self.f.store.side_effect = RuntimeError('local storage failure')
        self.assertEqual(await self.f.collector._stage_items([self.item], self.f.source, 29), 0)
        self.f.session.close.assert_awaited_once()

    async def test_actual_cancellation_still_propagates(self):
        error = asyncio.CancelledError()
        self.f.store.side_effect = error
        with self.assertRaises(asyncio.CancelledError) as caught:
            await self.stage()
        self.assertIs(caught.exception, error)
        self.f.session.close.assert_awaited_once()

    def test_copy_is_allowlisted_and_idempotent(self):
        raw = [{'type':'photo','url':SAFE_URL,'caption':'private','nested':{'token':'private'}}]
        before = copy.deepcopy(raw)
        normalized = NORMALIZE(raw)
        self.assertEqual(normalized, [{'type':'image','url':SAFE_URL}])
        self.assertEqual(NORMALIZE(normalized), normalized)
        self.assertEqual(raw, before)
        normalized[0]['url'] = None
        self.assertEqual(raw, before)

    def test_malformed_payload_is_unknown_not_empty(self):
        for value in (None, True, {}, 'private', ('image',)):
            with self.subTest(value=value):
                self.assertIsNone(NORMALIZE(value))
        self.assertEqual(NORMALIZE([]), [])
        self.assertEqual(NORMALIZE([None, {'type':'audio','url':SAFE_URL}]),
            [{'type':'unknown','url':None}, {'type':'unknown','url':None}])

    def test_rejected_reference_keeps_classified_missing_media(self):
        for url in ('https://api.telegram.org/file/botredacted/photo', 'https://user:password@cdn.example.org/photo',
                    SAFE_URL+'?token=redacted', SAFE_URL+'#secret', 'http://cdn.example.org/photo'):
            with self.subTest(url=url):
                normalized = NORMALIZE([{'type':'photo','url':url}])
                self.assertEqual(normalized, [{'type':'image','url':None}])
                self.assertEqual(len(self.classifier.classify_content([{'attachments':normalized}])['image']),1)

    def test_private_local_and_malformed_url_references_are_not_stored(self):
        values = ['https://127.0.0.1/x','https://[::1]/x','https://localhost./x',
            'https://host.internal/x','https://singlehost/x','https://cdn.example.org:8443/x',
            'https://cdn.example.org:notaport/x', 'https://cdn.example.org/has space',
            'https://cdn.example.org/\x00', 'https://cdn.example.org\\x', True, 123]
        for value in values:
            with self.subTest(value=value):
                self.assertEqual(NORMALIZE([{'type':'video','url':value}]), [{'type':'video','url':None}])

    def test_replay_copies_json_and_never_exposes_extra_fields(self):
        row = SimpleNamespace(platform='vk',external_id='post',text='body',published_at=None,
            media_type='image',metrics={},author={},permalink=None,content_hash='hash',
            attachments=[{'type':'photo','url':SAFE_URL,'raw':'private'}])
        restored = replay(row)
        self.assertEqual(restored['attachments'], [{'type':'image','url':SAFE_URL}])
        restored['attachments'][0]['url'] = None
        self.assertEqual(row.attachments[0]['url'], SAFE_URL)
        row.attachments = None
        self.assertNotIn('attachments', replay(row))
        del row.attachments
        self.assertNotIn('attachments', replay(row))

    async def test_rejected_media_reference_remains_pending_under_actual_analyzer(self):
        staged = await self.stage({**self.item, 'attachments':[{'type':'photo','url':SAFE_URL+'?token=redacted'}]})
        session = sql_fixtures.Session(rowcount=1)
        await manager().store_items(session, [staged])
        restored = replay(SimpleNamespace(**session.calls[0][1][0]))
        f = media_fixtures.MediaSkipCoverageTests()
        f.setUp()  # Fixture setup only; no historical suite execution.
        saved = await f.analyzer.base_analyze_content([restored], f.f.source, agent_scenario=f.f.scenario)
        self.assertIsNotNone(saved)
        self.assertEqual(saved.summary_data['content_hashes'], [])
        self.assertFalse(saved.summary_data['analysis_metadata']['analysis_complete'])
        self.assertEqual(f.analyzer.reported_errors, 0)
        f.client.analyze.assert_awaited_once()

    def test_model_nullable_json_has_no_default_or_backfill(self):
        tree = ast.parse((ROOT/'app/models/collection/collected_item.py').read_text())
        cls = next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='CollectedItem')
        field = next(n for n in cls.body if isinstance(n,ast.AnnAssign) and n.target.id=='attachments')
        self.assertEqual(field.value.func.id, 'Column')
        self.assertEqual(field.value.args[0].func.id, 'JSON')
        self.assertEqual({k.arg:ast.literal_eval(k.value) for k in field.value.keywords}, {'nullable':True})
        self.assertEqual({k.arg:ast.literal_eval(k.value) for k in field.value.args[0].keywords}, {'none_as_null':True})

    def test_migration_prepared_with_only_one_scoped_add_column(self):
        path = ROOT/'migrations/versions/0089_add_staged_attachments.py'
        tree = ast.parse(path.read_text())
        fields = {n.targets[0].id:ast.literal_eval(n.value) for n in tree.body if isinstance(n,ast.Assign)}
        self.assertEqual(fields['revision'],'0089')
        self.assertEqual(fields['down_revision'],'0088')
        upgrade = next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='upgrade')
        self.assertEqual(len(upgrade.body),1)
        call = upgrade.body[0].value
        self.assertEqual(ast.unparse(call.func),'op.add_column')
        self.assertEqual(ast.literal_eval(call.args[0]),'collected_items')
        self.assertEqual(ast.literal_eval(call.args[1].args[0]),'attachments')
        self.assertEqual(ast.unparse(call.keywords[0].value),'settings.DB_SCHEMA')
        self.assertEqual(call.args[1].keywords[0].arg,'nullable')
        self.assertIs(ast.literal_eval(call.args[1].keywords[0].value),True)
        self.assertFalse(any(isinstance(n,ast.Call) and ast.unparse(n.func) in
            ('op.execute','op.bulk_insert','op.get_bind') for n in ast.walk(tree)))


if __name__ == '__main__':
    unittest.main()
