"""Actual VK normalization -> staging/replay/classifier, local doubles only.

Run: python tests/test_vk_direct_photo_snapshots.py
No app bootstrap, historical test execution, DB, provider/network or downloads.
"""
import copy
import logging
from pathlib import Path
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock

from source_import_isolation import load_isolated_source
import test_collector_staging_log_privacy as collector_fixtures
import test_staged_attachments_contract as staged_fixtures

ROOT = Path(__file__).resolve().parents[1]
SAFE = 'https://cdn.example.org/photo.jpg'
OTHER = 'https://cdn.example.org/large.jpg'


def forbidden(*args, **kwargs):
    raise AssertionError('Out-of-scope dependency called')


class VKDirectPhotoSnapshotTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.util = load_isolated_source('_vk_photo_snapshot_utility', ROOT/'app/utils/content_attachments.py')
        imports = {
            'app.core.config': NS(settings=NS()), 'app.models': NS(Source=object),
            'app.services.social.base': NS(BaseClient=object),
            'app.services.social.credentials': NS(AuthorizationRequired=RuntimeError, resolve_token=forbidden),
            'app.services.social.owner': NS(resolve_source_owner=forbidden),
            'app.types': NS(SourceType=object),
            'app.utils.date_parsing': NS(to_unix_timestamp=forbidden, universal_date_parser=forbidden),
            'app.utils.enum_helpers': NS(get_enum_value=lambda value: value),
            'app.utils.content_attachments': NS(normalize_attachments=self.util.normalize_attachments),
        }
        self.module = load_isolated_source('_vk_direct_photo_client', ROOT/'app/services/social/vk_client.py', imports)
        self.module.logger = Mock(spec=logging.Logger)
        self.client = self.module.VKClient()
        self.raw = {'id': 42, 'owner_id': -5, 'text': 'A useful caption in a social post', 'date': 1700000000,
            'likes': {'count': 2}, 'comments': {'count': 3}, 'reposts': {'count': 4}, 'views': {'count': 10}}
        self.dedup = load_isolated_source('_vk_photo_hashes', ROOT/'app/services/ai/dedup.py')
        media_type = type('MediaType', (), {k: NS(db_value=v) for k,v in [('TEXT','text'),('IMAGE','image'),('VIDEO','video')]})
        self.classifier = load_isolated_source('_vk_photo_classifier', ROOT/'app/services/ai/content_classifier.py',
            {'app.types.enums.llm_types': NS(MediaType=media_type),
             'app.utils.content_attachments': self.util}).ContentClassifier

    def normalize(self, attachments=(), **kw):
        item = {**self.raw, **kw}
        if attachments != ():
            item['attachments'] = attachments
        return self.client._normalize_response({'response': {'items': [item]}}, 'group')[0]

    def photo(self, sizes=None):
        return {'type': 'photo', 'photo': {'sizes': [{'url': SAFE, 'width': 100, 'height': 100}] if sizes is None else sizes,
            'access_key': 'PRIVATE', 'owner_id': -5}, 'provider_extra': 'PRIVATE'}

    def test_largest_valid_photo_size_not_last_or_highest_width(self):
        item = self.normalize([self.photo([{'url': OTHER,'width': 400,'height': 300},
            {'url': SAFE,'width': 900,'height': 50}, {'url': 'https://cdn.example.org/small.jpg','width': 10,'height': 10}])])
        self.assertEqual(item['attachments'], [{'type': 'image', 'url': OTHER}])
        self.assertEqual(item['attachment_types'], ['photo'])
        self.assertTrue(item['has_attachments'])

    def test_rejected_largest_falls_back_to_safe_smaller(self):
        item = self.normalize([self.photo([{'url': SAFE,'width': 1,'height': 1},
            {'url': OTHER+'?access_token=PRIVATE','width': 1000,'height': 1000}])])
        self.assertEqual(item['attachments'][0]['url'], SAFE)

    def test_same_area_first_wins(self):
        item = self.normalize([self.photo([{'url': SAFE,'width': 20,'height': 10},
            {'url': OTHER,'width': 10,'height': 20}])])
        self.assertEqual(item['attachments'][0]['url'], SAFE)

    def test_unknown_dimensions_lower_rank_and_safe_fallback(self):
        for bad in [None, '1000', True, -1, float('nan')]:
            with self.subTest(dimension=bad):
                item = self.normalize([self.photo([{'url': OTHER,'width': bad,'height': 10000},
                    {'url': SAFE,'width': 1,'height': 1}])])
                self.assertEqual(item['attachments'][0]['url'], SAFE)
        self.assertEqual(self.normalize([self.photo([{'url': SAFE}])])['attachments'][0]['url'], SAFE)

    def test_bad_size_entries_do_not_drop_post(self):
        item = self.normalize([self.photo([None, 7, {'url': None}, {'url': SAFE}])])
        self.assertEqual(item['external_id'], '-5_42')
        self.assertEqual(item['attachments'][0]['url'], SAFE)
        self.module.logger.error.assert_not_called()

    def test_missing_or_malformed_photo_payload_is_image_placeholder(self):
        for photo in [None, 'invalid', {}, {'sizes': None}, {'sizes': {}}, {'sizes': []}]:
            with self.subTest(photo=photo):
                self.assertEqual(self.normalize([{'type':'photo','photo':photo}])['attachments'], [{'type':'image','url':None}])

    def test_signed_local_token_and_invalid_urls_remain_missing_media(self):
        for url in ['http://cdn.example.org/p.jpg', SAFE+'?sig=PRIVATE', SAFE+'#secret',
                    'https://user:secret@cdn.example.org/p', 'https://127.0.0.1/p',
                    'https://api.telegram.org/file/botPRIVATE/p', None]:
            with self.subTest(url=url):
                item = self.normalize([self.photo([{'url':url,'width':100,'height':100}])])
                classified = self.classifier.classify_content([item])
                self.assertEqual(len(classified['image']), 1)
                self.assertEqual(self.classifier.get_media_urls(classified['image']), [])

    def test_empty_absent_and_malformed_attachment_collection_distinct(self):
        self.assertNotIn('attachments', self.normalize())
        self.assertEqual(self.normalize([])['attachments'], [])
        for bad in [None, {}, 'photo', 7]:
            self.assertNotIn('attachments', self.normalize(bad))
        self.module.logger.error.assert_not_called()

    def test_mixed_order_and_video_preview_not_promoted(self):
        raw = [self.photo(), {'type':'video','video':{'image':[{'url':OTHER}], 'player':OTHER}},
            {'type':'doc','doc':{'url':OTHER}}, None]
        item = self.normalize(raw)
        self.assertEqual(item['attachments'], [{'type':'image','url':SAFE},{'type':'video','url':None},
            {'type':'unknown','url':None},{'type':'unknown','url':None}])
        self.assertEqual(item['attachment_types'], ['photo','video','doc',None])

    def test_raw_payload_not_mutated_and_private_fields_not_copied(self):
        raw = [self.photo()]; original = copy.deepcopy(raw)
        item = self.normalize(raw)
        self.assertEqual(raw, original)
        self.assertNotIn('PRIVATE', repr(item))
        item['attachments'][0]['url'] = OTHER
        self.assertEqual(raw, original)

    def test_nonattachment_fields_and_stable_hash_unchanged(self):
        old = self.normalize(); new = self.normalize([self.photo()])
        self.assertEqual({k:v for k,v in new.items() if k not in {'attachments','has_attachments','attachment_types'}}, old)
        self.assertEqual(self.dedup.item_hash(old), self.dedup.item_hash(new))
        self.assertEqual(new['reactions'], 9)
        self.assertEqual(new['metric_availability'], {'reactions':True,'comments':True,'views':True})

    def test_copy_history_not_traversed(self):
        item = self.normalize([], copy_history=[{'attachments':[self.photo()]}])
        self.assertEqual(item['attachments'], [])

    def test_multiple_direct_photos_are_all_classified(self):
        item = self.normalize([self.photo(), self.photo([{'url':OTHER,'width':10,'height':20}])])
        classified = self.classifier.classify_content([item])
        self.assertEqual(self.classifier.get_media_urls(classified['image']), [SAFE,OTHER])
        self.assertEqual(classified['text'], [item])

    async def stage_replay(self, item):
        f = collector_fixtures.CollectorStagingLogPrivacyTests(); f.setUp()  # setup only, no old checks
        f.source.params = {}; f.source.platform = NS(code='vkontakte')
        f.module.__isolated_imports__['app.utils.content_attachments'] = NS(normalize_attachments=self.util.normalize_attachments)
        f.module.__isolated_imports__['app.services.ai.dedup'].item_hash = self.dedup.item_hash
        await f.collector._stage_items([item], f.source, 29)
        row = f.store.await_args.args[1][0]
        f.transaction.enter.assert_awaited_once(); f.transaction.exit.assert_awaited_once(); f.session.close.assert_awaited_once()
        return row, staged_fixtures.replay(NS(**row))

    async def test_actual_vk_collector_replay_classifier_photo_roundtrip(self):
        item = self.normalize([self.photo()]); row, restored = await self.stage_replay(item)
        self.assertEqual(row['attachments'], item['attachments'])
        self.assertEqual(self.dedup.item_hash(item), self.dedup.item_hash(restored))
        self.assertEqual(self.classifier.get_media_urls(self.classifier.classify_content([restored])['image']), [SAFE])
        self.assertEqual(row['source_id'], 17)

    async def test_rejected_vk_photo_remains_incomplete_after_stage_replay(self):
        item = self.normalize([self.photo([{'url':SAFE+'?secret=PRIVATE'}])])
        _, restored = await self.stage_replay(item)
        classified = self.classifier.classify_content([restored])
        self.assertEqual(len(classified['image']), 1)
        self.assertEqual(self.classifier.get_media_urls(classified['image']), [])
        self.assertEqual(restored['attachments'], [{'type':'image','url':None}])


if __name__ == '__main__':
    unittest.main()
