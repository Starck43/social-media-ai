"""Actual Telegram L2 normalization -> staging/replay/coverage, local doubles.

Run: python tests/test_telegram_media_placeholders.py
No Telethon/app bootstrap, sessions, downloads, DB, providers or old checks.
L1 push/cursor logic is deliberately unchanged.
"""
import copy
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock

from source_import_isolation import load_isolated_source
import test_collector_staging_log_privacy as collector_fixtures
import test_staged_attachments_contract as staged_fixtures
import test_media_skip_coverage as coverage_fixtures

ROOT = Path(__file__).resolve().parents[1]


def tl(name, **fields):
    obj = type(name, (), {})()
    vars(obj).update(fields)
    return obj


class TelegramMediaPlaceholderTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.module = load_isolated_source('_tg_media_placeholder_client', ROOT/'app/services/social/tg_client.py', {
            'app.core.permissions': NS(service_permission_scope=Mock(side_effect=AssertionError('No permission action'))),
            'app.core.config': NS(settings=NS()), 'app.models': NS(Source=object),
            'app.services.social.base': NS(BaseClient=object), 'app.types': NS(SourceType=object)})
        self.module.logger = Mock()
        self.client = self.module.TelegramClient()
        self.source = NS(id=17, tenant_id=4, external_id='-100777', source_type=NS(value='channel'), params={})
        self.date = datetime(2026,10,10,tzinfo=timezone.utc)
        self.photo = tl('MessageMediaPhoto', photo=NS(id=7, access_hash='PRIVATE', file_reference=b'PRIVATE'))
        self.video = tl('MessageMediaDocument', document=NS(mime_type='video/mp4', attributes=[], file_reference=b'PRIVATE'))
        self.dedup = load_isolated_source('_tg_media_placeholder_hashes', ROOT/'app/services/ai/dedup.py')

    def message(self, text='A useful Telegram caption with content', media=None):
        return NS(id=42, text=text, message=text, media=media, chat_id=-100777, date=self.date,
            views=10, forwards=2, reactions=NS(results=[NS(count=3)]), replies=NS(replies=5),
            is_channel=True, sender_id=9, pinned=False, edit_date=None)

    def normalized(self, text='A useful Telegram caption with content', media=None):
        return self.client._normalize_message(self.message(text, media), self.source)

    def test_photo_caption_preserves_fields_and_missing_reference(self):
        old=self.normalized();new=self.normalized(media=self.photo)
        self.assertEqual({k:v for k,v in new.items() if k not in {'attachments','has_media','media_type'}}, old)
        self.assertEqual(new['attachments'], [{'type':'image','url':None}])
        self.assertEqual(new['external_id'], '-100777_42')
        self.assertEqual(self.dedup.item_hash(old), self.dedup.item_hash(new))

    def test_media_only_photo_is_retained(self):
        item=self.normalized(text=None,media=self.photo)
        self.assertEqual(item['text'], '')
        self.assertEqual(item['attachments'], [{'type':'image','url':None}])
        self.assertEqual(item['id'], '42')

    def test_media_only_video_is_retained(self):
        self.assertEqual(self.normalized(text='',media=self.video)['attachments'], [{'type':'video','url':None}])

    def test_video_caption_preserves_metrics(self):
        item=self.normalized(media=self.video)
        self.assertEqual(item['attachments'], [{'type':'video','url':None}])
        self.assertEqual((item['views'],item['forwards'],item['reactions'],item['comments']), (10,2,3,5))
        self.assertEqual(item['metric_availability'], {'reactions':True,'comments':True,'views':True})

    def test_video_attribute_recognized_without_mime(self):
        media=tl('MessageMediaDocument',document=NS(attributes=[tl('DocumentAttributeVideo')]))
        self.assertEqual(self.normalized(media=media)['attachments'], [{'type':'video','url':None}])

    def test_missing_photo_payload_stays_known_image(self):
        self.assertEqual(self.normalized(media=tl('MessageMediaPhoto'))['attachments'], [{'type':'image','url':None}])

    def test_plain_text_not_given_fake_attachments(self):
        item=self.normalized()
        self.assertNotIn('attachments',item);self.assertNotIn('has_media',item)

    def test_empty_service_message_still_skipped(self):
        self.assertIsNone(self.normalized(text=None))

    def test_unsupported_media_does_not_become_image_or_video(self):
        for name,doc in [('MessageMediaWebPage',NS(photo=self.photo)),
                         ('MessageMediaDocument',NS(mime_type='audio/mpeg',attributes=[])),
                         ('MessageMediaDocument',NS(mime_type='application/pdf',attributes=[])),
                         ('MessageMediaDocument',None)]:
            with self.subTest(name=name,document=doc):
                media=tl(name,document=doc)
                self.assertNotIn('attachments',self.normalized(media=media))
                self.assertIsNone(self.normalized(text=None,media=media))

    def test_malformed_document_attributes_not_assumed_video(self):
        for attributes in [None,{},'DocumentAttributeVideo',[None,{},7]]:
            media=tl('MessageMediaDocument',document=NS(attributes=attributes))
            self.assertNotIn('attachments',self.normalized(media=media))

    def test_file_tokens_url_and_private_payload_never_copied(self):
        vars(self.photo)['url']='https://api.telegram.org/file/botPRIVATE/p';vars(self.photo)['extra']='PRIVATE'
        before=copy.deepcopy(vars(self.photo))
        item=self.normalized(media=self.photo)
        self.assertNotIn('PRIVATE',repr(item));self.assertEqual(vars(self.photo),before)
        self.assertEqual(vars(self.photo)['url'],before['url'])
        item['attachments'][0]['type']='video'
        self.assertEqual(self.normalized(media=self.photo)['attachments'][0]['type'],'image')

    def dict_item(self, media):
        return {'id':42,'message':'A useful dictionary Telegram caption','date':self.date,'peer_id':{'channel_id':777},
            'post':True,'media':media,'views':10,'reactions':{'results':[{'count':3}]},'replies':{'replies':5}}

    def test_dict_compatibility_photo_placeholder(self):
        raw=self.dict_item({'_':'MessageMediaPhoto','photo':{'file_reference':'PRIVATE'}});before=copy.deepcopy(raw)
        item=self.client._normalize_response({'messages':[raw]},self.source.source_type)[0]
        self.assertEqual(item['attachments'], [{'type':'image','url':None}]);self.assertEqual(raw,before)
        self.assertEqual(item['external_id'], "{'channel_id': 777}_42")  # legacy identity unchanged
        self.assertNotIn('PRIVATE',repr(item))

    def test_dict_video_mime_and_attribute(self):
        for doc in [{'mime_type':'VIDEO/MP4'},{'attributes':[{'_':'DocumentAttributeVideo'}]}]:
            item=self.client._normalize_response({'messages':[self.dict_item({'_':'MessageMediaDocument','document':doc})]},self.source.source_type)[0]
            self.assertEqual(item['attachments'], [{'type':'video','url':None}])

    def test_to_dict_compatibility_and_media_only(self):
        raw=self.dict_item({'_':'MessageMediaPhoto'});raw['message']=''
        msg=NS(to_dict=lambda:copy.deepcopy(raw))
        item=self.client._normalize_response([msg],self.source.source_type)[0]
        self.assertEqual(item['text'],'');self.assertEqual(item['attachments'], [{'type':'image','url':None}])

    async def replay(self,item):
        f=collector_fixtures.CollectorStagingLogPrivacyTests();f.setUp()  # setup only
        f.source.params={};f.source.platform=NS(code='telegram')
        f.module.__isolated_imports__['app.utils.content_attachments']=NS(normalize_attachments=staged_fixtures.NORMALIZE)
        f.module.__isolated_imports__['app.services.ai.dedup'].item_hash=self.dedup.item_hash
        await f.collector._stage_items([item],f.source,29)
        row=f.store.await_args.args[1][0]
        self.assertEqual(row['source_id'],17)
        f.transaction.enter.assert_awaited_once();f.transaction.exit.assert_awaited_once();f.session.close.assert_awaited_once()
        return staged_fixtures.replay(NS(**row))

    async def test_actual_collect_replay_preserves_caption_and_photo_marker(self):
        item=self.normalized(media=self.photo);restored=await self.replay(item)
        self.assertEqual(restored['attachments'],item['attachments']);self.assertEqual(restored['text'],item['text'])
        self.assertEqual(self.dedup.item_hash(restored),self.dedup.item_hash(item))

    async def test_actual_caption_analyzer_does_not_certify_missing_photo(self):
        restored=await self.replay(self.normalized(media=self.photo))
        f=coverage_fixtures.MediaSkipCoverageTests();f.setUp()  # setup only, no historical tests
        row=await f.analyze([restored])
        self.assertIsNotNone(row);self.assertEqual(row.summary_data['content_hashes'],[])
        self.assertFalse(row.summary_data['analysis_metadata']['analysis_complete'])
        self.assertIsNone(row.content_hash);self.assertEqual(f.analyzer.reported_errors,0)
        f.client.analyze.assert_awaited_once()  # caption only, local double

    async def test_actual_media_only_analyzer_does_not_call_provider_or_save(self):
        restored=await self.replay(self.normalized(text=None,media=self.video))
        f=coverage_fixtures.MediaSkipCoverageTests();f.setUp()
        self.assertIsNone(await f.analyze([restored]));f.client.analyze.assert_not_awaited()
        f.f.create.assert_not_awaited();self.assertEqual(f.analyzer.reported_errors,0)


if __name__ == '__main__':
    unittest.main()
