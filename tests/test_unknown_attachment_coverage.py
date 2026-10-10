"""NEW actual snapshot/classifier/analyzer/dedup/retirement checks, local doubles.

Run: python tests/test_unknown_attachment_coverage.py
No app bootstrap, DB/provider/schema/loaders, historical tests or root-path tricks.
"""
import asyncio
import copy
from pathlib import Path
from types import SimpleNamespace as NS
import unittest

from source_import_isolation import load_isolated_source
import test_media_skip_coverage as media_fixtures
import test_collector_staging_log_privacy as collector_fixtures
import test_staged_attachments_contract as staged_fixtures

ROOT = Path(__file__).resolve().parents[1]
SAFE = 'https://cdn.example.org/image.jpg'


class UnknownAttachmentCoverageTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.f = media_fixtures.MediaSkipCoverageTests(); self.f.setUp()  # setup only, no old test discovery
        self.utility = load_isolated_source('_unknown_attachment_vocabulary', ROOT/'app/utils/content_attachments.py')
        self.classifier = self.f.classifier.ContentClassifier
        self.dedup = self.f.dedup
        self.storage = collector_fixtures.CollectorStagingLogPrivacyTests(); self.storage.setUp()
        self.storage.source.params = {}; self.storage.source.platform = NS(code='vk')
        self.storage.module.__isolated_imports__['app.utils.content_attachments'] = self.utility
        self.storage.module.__isolated_imports__['app.services.ai.dedup'].item_hash = self.dedup.item_hash
        self.storage.module.__isolated_imports__['app.services.ai.dedup'].analysed_hashes = self.dedup.analysed_hashes

    def post(self, identity='unknown', attachments=None, text=True):
        item = {'external_id':identity, 'platform':'vk', 'text':'Useful social content about '+identity if text else ''}
        if attachments is not None:item['attachments']=attachments
        return item

    async def analyze(self, content, **kw):
        return await self.f.analyze(content, **kw)

    def assert_uncovered(self, content, row, covered=()):
        self.assertIsNotNone(row)
        expected={self.dedup.item_hash(item) for item in covered}
        self.assertEqual(set(row.summary_data['content_hashes']), expected)
        self.assertFalse(row.summary_data['analysis_metadata']['analysis_complete'])
        self.assertEqual(self.dedup.analysed_hashes([row]),expected)
        remaining,_=self.dedup._split(content,[row],self.f.f.source.id)
        self.assertEqual(remaining,[item for item in content if item not in covered])
        self.assertEqual(self.f.analyzer.reported_errors,0)

    def test_single_alias_vocabulary_matches_snapshot_and_classifier(self):
        for alias,kind in [('photo','image'),('PHOTO','image'),('image','image'),('Image','image'),
                           ('video','video'),('video_file','video'),('VIDEO_FILE','video')]:
            with self.subTest(alias=alias):
                self.assertEqual(self.utility.canonical_attachment_type(alias),kind)
                snapshot=self.utility.normalize_attachments([{'type':alias,'url':SAFE}])
                self.assertEqual(snapshot,[{'type':kind,'url':SAFE}])
                classified=self.classifier.classify_content([self.post(attachments=[{'type':alias,'url':SAFE}])])
                self.assertEqual(classified[kind][0]['media_url'],SAFE)

    def test_static_reasons_no_private_type_or_payload_echo(self):
        for entry in [{'type':'PRIVATE-document','url':'PRIVATE-token'}, {'url':'PRIVATE-token'},
                      None, 7, {'type':None}]:
            self.assertEqual(self.utility.attachment_coverage_reason(entry),'unsupported_attachment')
        self.assertEqual(self.utility.attachment_coverage_reason({'type':'photo','url':None}),'missing_media_url')
        self.assertIsNone(self.utility.attachment_coverage_reason({'type':'video_file','url':SAFE}))

    def test_unknown_snapshot_does_not_become_empty_or_dispatchable(self):
        snapshot=self.utility.normalize_attachments([{'type':'doc','url':SAFE,'body':'PRIVATE','token':'PRIVATE'}])
        self.assertEqual(snapshot,[{'type':'unknown','url':None}])
        item=self.post(attachments=snapshot);classified=self.classifier.classify_content([item])
        self.assertEqual(set(classified),{'text','image','video'})
        self.assertEqual(classified['text'],[item]);self.assertEqual(classified['image'],[]);self.assertEqual(classified['video'],[])
        self.assertEqual(self.classifier.uncovered_attachment_items([item]),[item])

    def test_parent_once_input_not_mutated_and_known_url_not_rewritten(self):
        item=self.post(attachments=[{'type':'unknown','url':None},{'type':'photo','url':None},{'type':'doc'}]);before=copy.deepcopy(item)
        self.assertEqual(self.classifier.uncovered_attachment_items([item]),[item])
        self.classifier.classify_content([item]);self.assertEqual(item,before)
        known=self.post(attachments=[{'type':'image','url':'existing-local-double-url'}])
        self.assertEqual(self.classifier.get_media_urls(self.classifier.classify_content([known])['image']),['existing-local-double-url'])

    def test_absent_null_and_empty_keep_legacy_contract(self):
        absent=self.post();null=self.post();null['attachments']=None;empty=self.post(attachments=[])
        self.assertEqual(self.classifier.uncovered_attachment_items([absent,null,empty]),[])
        self.assertEqual(self.classifier.classify_content([null])['text'],[null])

    async def test_useful_text_with_unknown_saved_without_parent_certification(self):
        item=self.post(attachments=[{'type':'unknown','url':None}]);row=await self.analyze([item])
        self.assert_uncovered([item],row);self.assertIsNone(row.content_hash)
        self.assertEqual(row.summary_data['multi_llm_analysis']['text_analysis'],self.f.f.good['parsed'])
        self.f.client.analyze.assert_awaited_once()

    async def test_unsupported_raw_type_not_silently_ignored(self):
        item=self.post(attachments=[{'type':'audio','url':SAFE}]);row=await self.analyze([item])
        self.assert_uncovered([item],row);self.f.client.analyze.assert_awaited_once()

    async def test_supported_media_sibling_cannot_certify_same_parent(self):
        item=self.post(attachments=[{'type':'photo','url':SAFE},{'type':'unknown','url':None}])
        row=await self.analyze([item]);self.assert_uncovered([item],row)
        self.assertEqual(row.summary_data['multi_llm_analysis']['image_analysis'],self.f.f.good['parsed'])
        self.assertEqual(self.f.client.analyze.await_count,2)

    async def test_mixed_posts_only_complete_siblings_retire(self):
        plain=self.post('plain');image=self.post('image',[{'type':'image','url':SAFE}]);unknown=self.post('unknown',[{'type':'unknown','url':None}])
        content=[plain,image,unknown];row=await self.analyze(content);self.assert_uncovered(content,row,[plain,image])
        self.assertEqual(row.content_hash,self.dedup.batch_hash([plain,image]))
        self.storage.delete.return_value=2
        self.assertEqual(await self.storage.collector._retire_staged(self.storage.source,[row]),2)
        args=self.storage.delete.await_args.args
        self.assertEqual(args[1],self.storage.source.id)
        self.assertEqual(set(args[2]),{self.dedup.item_hash(plain),self.dedup.item_hash(image)})
        self.assertNotIn(self.dedup.item_hash(unknown),args[2])

    async def test_unknown_parent_not_retired_even_if_useful_text_saved(self):
        item=self.post(attachments=[{'type':'unknown','url':None}]);row=await self.analyze([item])
        self.assertEqual(await self.storage.collector._retire_staged(self.storage.source,[row]),0)
        self.storage.delete.assert_not_awaited();self.storage.new_session.assert_not_called()

    async def test_unknown_media_only_stays_unsaved_no_provider_or_error(self):
        item=self.post(attachments=[{'type':'unknown','url':None}],text=False)
        self.assertIsNone(await self.analyze([item]));self.f.client.analyze.assert_not_awaited()
        self.f.f.create.assert_not_awaited();self.assertEqual(self.f.analyzer.reported_errors,0)

    async def test_concrete_malformed_entries_keep_text_but_no_hash(self):
        for attachments in [[None],[7],[{}],['photo'],{'type':'photo'},'photo',False]:
            with self.subTest(attachments=attachments):
                self.setUp()  # separate new fixture state, no old checks
                item=self.post(attachments=attachments);row=await self.analyze([item]);self.assert_uncovered([item],row)
                self.f.client.analyze.assert_awaited_once()

    async def test_missing_supported_url_uses_same_local_reason_and_gap(self):
        item=self.post(attachments=[{'type':'video','url':None}]);row=await self.analyze([item])
        self.assert_uncovered([item],row);self.f.client.analyze.assert_awaited_once()

    async def test_empty_attachments_text_can_keep_full_existing_coverage(self):
        item=self.post(attachments=[]);row=await self.analyze([item])
        self.assertEqual(row.summary_data['content_hashes'],[self.dedup.item_hash(item)])
        self.assertEqual(row.content_hash,self.dedup.batch_hash([item]))
        self.assertNotIn('analysis_complete',row.summary_data['analysis_metadata'])

    async def test_existing_daily_A_not_overwritten_by_unknown_B(self):
        old=self.f.f.old_row();before=copy.deepcopy(vars(old))
        unknown=self.post('new',[{'type':'unknown','url':None}])
        self.assertIsNone(await self.analyze([unknown]))
        self.assertEqual(vars(old),before);self.f.f.create.assert_not_awaited();self.f.f.update.assert_not_awaited()
        self.f.analyzer._price_usage.assert_not_awaited()
        self.assertNotIn(self.dedup.item_hash(unknown),self.dedup.analysed_hashes([old]))

    async def test_genuine_provider_failure_still_clears_all_new_coverage(self):
        async def response(*args,**kw):return copy.deepcopy(self.f.f.failed if kw.get('media_urls') else self.f.f.good)
        self.f.client.analyze.side_effect=response
        unknown=self.post(attachments=[{'type':'unknown','url':None},{'type':'image','url':SAFE}]);plain=self.post('plain')
        row=await self.analyze([plain,unknown]);self.assertEqual(row.summary_data['content_hashes'],[])
        self.assertIsNone(row.content_hash);self.assertEqual(self.f.analyzer.reported_errors,1)

    async def test_actual_cancellation_not_swallowed_or_certified(self):
        entered=asyncio.Event();blocked=asyncio.Event()
        async def response(*args,**kw):entered.set();await blocked.wait()
        self.f.client.analyze.side_effect=response
        task=asyncio.create_task(self.analyze([self.post(attachments=[{'type':'unknown','url':None}])]))
        await asyncio.wait_for(entered.wait(),1);task.cancel()
        with self.assertRaises(asyncio.CancelledError):await task
        self.f.f.create.assert_not_awaited();self.assertEqual(self.f.analyzer.reported_errors,0)

    async def test_stage_replay_unknown_snapshot_coverage_and_retirement(self):
        item=self.post(attachments=[{'type':'document','url':SAFE,'secret':'PRIVATE'}])
        await self.storage.collector._stage_items([item],self.storage.source,29)
        staged=self.storage.store.await_args.args[1][0]
        self.assertEqual(staged['attachments'],[{'type':'unknown','url':None}]);self.assertNotIn('PRIVATE',repr(staged['attachments']))
        restored=staged_fixtures.replay(NS(**staged));self.assertEqual(self.dedup.item_hash(restored),self.dedup.item_hash(item))
        row=await self.analyze([restored]);self.assert_uncovered([restored],row)
        self.assertEqual(await self.storage.collector._retire_staged(self.storage.source,[row]),0)
        self.storage.delete.assert_not_awaited()


if __name__ == '__main__':
    unittest.main()
