"""NEW standalone contracts; prepared but execution deferred by the owner.

Run later: python tests/test_scenario_output_contract.py
Real Pydantic/source; module-local application/provider doubles. No DB/bootstrap.
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, Mock, patch

from source_import_isolation import load_isolated_source

ROOT = Path(__file__).resolve().parents[1]


def load_contract():
    schema = load_isolated_source('_new_scenario_schema', ROOT / 'app/services/ai/scenario_schema.py')
    builder = load_isolated_source('_new_scenario_builder', ROOT / 'app/services/ai/json_schema_builder.py', {
        'app.services.ai.scenario_schema': schema,
    })
    schema.__isolated_imports__['app.services.ai.json_schema_builder'] = builder
    return schema, builder


def scenario(raw=None, **changes):
    return NS(output_schema=raw, analysis_types=[], scope={}, max_tokens=None, id=11, **changes)


def object_schema(properties=None, **changes):
    return {'type': 'object', 'properties': properties or {}, **changes}


def load_analyzer(schema, builder):
    media = type('MediaType', (), {'TEXT': NS(db_value='text'),
                                   'IMAGE': NS(db_value='image'), 'VIDEO': NS(db_value='video')})
    classifier = NS(classify_content=Mock(return_value={'text': [], 'image': [], 'video': []}),
                    uncovered_attachment_items=Mock(return_value=[]),
                    select_text_content=Mock(side_effect=lambda items: items),
                    prepare_text_content=Mock(return_value='input'),
                    get_media_urls=Mock(side_effect=lambda items: [x['media_url'] for x in items if x.get('media_url')]))
    imports = {
        'app.core.analysis_constants': NS(DEFAULT_ANALYSIS_PARAMS={'min_confidence': .5}),
        'app.core.config': NS(settings=NS(DEBUG=False)),
        'app.models': NS(AgentScenario=NS(objects=NS(get_default_scenario=AsyncMock(return_value=None))),
                         AIAnalytics=NS(objects=Mock()), LLMModel=NS(), Source=NS()),
        'app.services.ai.chain_resolver': NS(_normalize=lambda value: value, _token_set_ratio=Mock(),
                                             resolve_chain_async=AsyncMock()),
        'app.services.ai.content_classifier': NS(ContentClassifier=classifier),
        'app.services.ai.dedup': NS(batch_hash=Mock(return_value='batch'), hashes_hash=Mock(return_value='hashes'),
                                   item_hash=lambda item: str(item['id']), filter_analyzed=AsyncMock()),
        'app.services.ai.json_schema_builder': builder,
        'app.services.ai.scenario_schema': schema,
        'app.services.ai.llm_client': NS(LLMClientFactory=NS(create=Mock())),
        'app.services.ai.prompts': NS(PromptBuilder=NS(get_prompt=Mock(return_value='prompt'))),
        'app.services.ai.theme_matcher': NS(ThemeMatcher=Mock()),
        'app.types': NS(PeriodType=NS(DAY='day')),
        'app.types.enums.llm_types': NS(MediaType=media),
        'app.utils.date_parsing': NS(universal_date_parser=Mock()),
        'app.utils.enum_helpers': NS(get_enum_value=lambda value: value),
        'app.utils.translit': NS(translit_slug=lambda value: value),
    }
    return load_isolated_source('_new_schema_analyzer', ROOT / 'app/services/ai/analyzer.py', imports)


class CompiledSchemaTests(unittest.TestCase):
    def setUp(self):
        self.schema, self.builder = load_contract()

    def compile(self, raw):
        return self.schema.compile_scenario_output(scenario(raw))

    def validate(self, raw, value):
        return self.builder.validate_with_pydantic(value, self.compile(raw).model, strict=True)

    def test_absent_scenario_preserves_generic_path(self):
        self.assertIsNone(self.schema.compile_scenario_output(None))

    def test_derived_schema_is_snapshot_not_orm_mutation(self):
        row = scenario()
        contract = self.schema.compile_scenario_output(row)
        self.assertIsNone(row.output_schema)
        self.assertIn('summary', contract.schema['required'])
        self.assertEqual(contract.model.model_validate({'summary': 'useful'}).summary, 'useful')

    def test_nested_required_closed_objects_and_arrays(self):
        raw = object_schema({'rows': {'type': 'array', 'minItems': 1, 'items': {
            'type': 'object', 'properties': {'score': {'type': 'integer', 'minimum': 0}},
            'required': ['score'], 'additionalProperties': False}}}, required=['rows'], additionalProperties=False)
        self.assertEqual(self.validate(raw, {'rows': [{'score': 2}]}), {'rows': [{'score': 2}]})
        for value in ({}, {'rows': []}, {'rows': [{'score': True}]}, {'rows': [{'score': -1}]},
                      {'rows': [{'score': 1, 'extra': 1}]}, {'rows': [{'score': 1}], 'extra': 1}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.validate(raw, value)

    def test_unknown_types_and_assertions_fail_closed(self):
        for field in ({'type': 'datetime'}, {'type': 'string', 'format': 'email'}, {'$ref': '#/thing'},
                      {'anyOf': [{'type': 'string'}]}, {'type': 'array', 'items': []}):
            with self.subTest(field=field), self.assertRaises(self.schema.ScenarioSchemaError):
                self.compile(object_schema({'value': field}))

    def test_scalar_types_do_not_coerce(self):
        for kind, value in [('integer', '2'), ('integer', True), ('boolean', 1), ('string', 2), ('number', True)]:
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                self.validate(object_schema({'value': {'type': kind}}, required=['value']), {'value': value})

    def test_scalar_enum_boolean_is_not_integer(self):
        raw = object_schema({'value': {'type': ['boolean', 'integer'], 'enum': [True, 2]}}, required=['value'])
        self.assertEqual(self.validate(raw, {'value': True}), {'value': True})
        self.assertEqual(self.validate(raw, {'value': 2}), {'value': 2})
        with self.assertRaises(ValueError):
            self.validate(raw, {'value': 1})

    def test_nullable_union_keeps_branch_constraints(self):
        raw = object_schema({'value': {'type': ['null', 'string'], 'minLength': 2}}, required=['value'])
        for value in (None, 'ok'):
            self.assertEqual(self.validate(raw, {'value': value}), {'value': value})
        with self.assertRaises(ValueError):
            self.validate(raw, {'value': 'x'})

    def test_bounds_pattern_and_length_are_enforced(self):
        raw = object_schema({'value': {'type': 'string', 'pattern': '^ok', 'minLength': 2, 'maxLength': 4}}, required=['value'])
        self.assertEqual(self.validate(raw, {'value': 'okay'}), {'value': 'okay'})
        for value in ('bad', 'oklong'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.validate(raw, {'value': value})

    def test_reserved_field_names_roundtrip_as_json_aliases(self):
        names = ['_private', 'model_validate', 'model_dump', 'model_special', 'schema_field_0']
        raw = object_schema({name: {'type': 'string'} for name in names}, required=names)
        value = {name: name for name in names}
        self.assertEqual(self.validate(raw, value), value)

    def test_optional_omission_does_not_invent_nulls(self):
        raw = object_schema({'optional': {'type': 'integer'}, 'value': {'type': 'string'}}, required=['value'])
        self.assertEqual(self.validate(raw, {'value': 'ok'}), {'value': 'ok'})
        with self.assertRaises(ValueError):
            self.validate(raw, {'value': 'ok', 'optional': None})

    def test_extra_properties_policy_is_explicit(self):
        self.assertEqual(self.validate(object_schema(), {'extra': {'nested': [1]}}), {'extra': {'nested': [1]}})
        with self.assertRaises(ValueError):
            self.validate(object_schema(additionalProperties=False), {'extra': 1})

    def test_snapshot_and_model_are_per_invocation(self):
        raw = object_schema({'value': {'type': 'string'}}, required=['value'])
        first, second = self.compile(raw), self.compile(raw)
        raw['properties']['value']['type'] = 'integer'
        detached = first.schema
        detached['properties'].clear()
        self.assertIsNot(first.model, second.model)
        self.assertEqual(first.schema['properties']['value']['type'], 'string')
        self.assertEqual(self.builder.validate_with_pydantic({'value': 'safe'}, first.model, strict=True), {'value': 'safe'})

    def test_complexity_and_size_limits(self):
        for raw in (object_schema({str(i): {'type': 'string'} for i in range(257)}),
                    object_schema(description='x' * (self.schema.MAX_SCHEMA_BYTES + 1))):
            with self.subTest(size=len(str(raw))), self.assertRaises(ValueError):
                self.compile(raw)
        node = {'type': 'string'}
        for _ in range(self.schema.MAX_SCHEMA_DEPTH + 2):
            node = {'type': 'array', 'items': node}
        with self.assertRaises(ValueError):
            self.compile(object_schema({'deep': node}))

    def test_non_json_and_nonfinite_values_rejected(self):
        for value in (float('nan'), float('inf'), object()):
            with self.subTest(value_type=type(value).__name__), self.assertRaises(ValueError):
                self.compile(object_schema(default=value))

    def test_inconsistent_numeric_and_required_contracts_rejected(self):
        for raw in (object_schema({'x': {'type': 'number', 'minimum': 1, 'exclusiveMaximum': 1}}),
                    object_schema(required=['undeclared']),
                    object_schema({'x': {'type': 'string', 'enum': ['a', 'a']}})):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                self.compile(raw)

    def test_regex_compilation_error_is_static(self):
        with self.assertRaises(self.schema.ScenarioSchemaError) as caught:
            self.compile(object_schema({'value': {'type': 'string', 'pattern': '('}}))
        self.assertEqual(caught.exception.code, 'schema_compilation_failed')

    def test_strict_error_never_formats_private_output(self):
        model = self.compile(object_schema({'value': {'type': 'integer'}}, required=['value'])).model
        with self.assertRaises(ValueError) as caught:
            self.builder.validate_with_pydantic({'value': 'PRIVATE-PROVIDER-OUTPUT'}, model, strict=True)
        self.assertNotIn('PRIVATE-PROVIDER-OUTPUT', str(caught.exception))

    def test_compatibility_non_strict_wrapper_still_returns_raw(self):
        model = self.compile(object_schema({'value': {'type': 'integer'}})).model
        raw = {'value': 'bad'}
        with patch('logging.Logger.warning'):
            self.assertIs(self.builder.validate_with_pydantic(raw, model), raw)

    def test_invalid_derived_configuration_is_rejected(self):
        row = scenario()
        row.analysis_types = 'sentiment'
        with self.assertRaises(ValueError):
            self.schema.compile_scenario_output(row)

    def test_non_boolean_activation_cannot_bypass_revalidation(self):
        for value in (1, "true", 0):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.schema.schema_write_requires_validation({"is_active": value})

    def test_non_utf8_schema_string_has_static_error(self):
        with self.assertRaises(self.schema.ScenarioSchemaError) as caught:
            self.compile(object_schema(description="\ud800"))
        self.assertEqual(caught.exception.code, "non_json_value")

    def test_write_policy_permits_disable_not_reactivation(self):
        predicate = self.schema.schema_write_requires_validation
        self.assertFalse(predicate({'is_active': False}))
        self.assertFalse(predicate({'name': 'rename'}))
        self.assertTrue(predicate({'is_active': True}))
        self.assertTrue(predicate({'output_schema': None}))
        self.assertTrue(predicate({}, creating=True))


class AnalyzerContractTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.schema, self.builder = load_contract()
        self.module = load_analyzer(self.schema, self.builder)
        self.analyzer = self.module.AIAnalyzer()
        self.source = NS(id=1, tenant_id=1, source_type=None, name='source')
        self.row = scenario(object_schema({'summary': {'type': 'string'}}, required=['summary']))
        self.contract = self.schema.compile_scenario_output(self.row)

    def test_invalid_parsed_envelope_preserves_usage_without_mutation(self):
        result = {'parsed': {'summary': 9}, 'response': {'usage': {'prompt_tokens': 7}},
                  'request': {'model': 'synthetic'}}
        original = deepcopy(result)
        rejected = self.analyzer._validate_stage_result(result, self.contract)
        self.assertEqual(result, original)
        self.assertEqual(rejected['parsed'], {})
        self.assertEqual(rejected['response']['usage'], original['response']['usage'])
        self.assertEqual(rejected['request'], original['request'])
        self.assertEqual(rejected['response']['error'], 'analysis_output_validation_failed')

    def test_existing_client_error_is_not_reinterpreted(self):
        result = {'parsed': {}, 'response': {'error': 'synthetic'}}
        self.assertIs(self.analyzer._validate_stage_result(result, self.contract), result)

    async def test_bad_stored_schema_stops_before_model_and_save(self):
        self.row.output_schema = object_schema({'x': {'type': 'unsupported'}})
        self.analyzer._get_llm_model = AsyncMock()
        self.analyzer._save_analysis = AsyncMock()
        result = await self.analyzer.base_analyze_content([{'id': 1, 'text': 'input'}], self.source,
                            force_reanalyze=True, agent_scenario=self.row)
        self.assertIsNone(result)
        self.analyzer._get_llm_model.assert_not_awaited()
        self.analyzer._save_analysis.assert_not_awaited()
        self.assertEqual(self.analyzer.reported_errors, 1)

    async def test_one_contract_is_forwarded_to_all_stages_and_save(self):
        content = [{'id': 1, 'text': 'input', 'media_url': 'https://example.invalid/synthetic'}]
        self.module.ContentClassifier.classify_content.return_value = {key: content for key in ('text', 'image', 'video')}
        self.analyzer._calculate_content_stats = Mock(return_value={})
        self.analyzer._get_platform_name = AsyncMock(return_value='synthetic')
        methods = [AsyncMock(return_value={'parsed': {'summary': 'useful'}, 'response': {}}) for _ in range(3)]
        self.analyzer._analyze_text, self.analyzer._analyze_images, self.analyzer._analyze_videos = methods
        self.analyzer._create_unified_summary = AsyncMock(return_value=None)
        self.analyzer._save_analysis = AsyncMock(return_value=NS(id=2))
        compile_spy = Mock(wraps=self.module.compile_scenario_output)
        with patch.object(self.module, 'compile_scenario_output', compile_spy):
            result = await self.analyzer.base_analyze_content(content, self.source, force_reanalyze=True,
                                agent_scenario=self.row, topic_chain_id='chain')
        self.assertEqual(result.id, 2)
        self.assertEqual(compile_spy.call_count, 1)
        forwarded = self.analyzer._save_analysis.call_args.kwargs['output_contract']
        for method in methods:
            self.assertIs(method.call_args.kwargs['output_contract'], forwarded)

    async def test_actual_modalities_share_model_and_validate_returns(self):
        self.analyzer._get_llm_model = AsyncMock(return_value=NS(name='synthetic'))
        client = NS(analyze=AsyncMock(return_value={'parsed': {'summary': 'valid'}, 'response': {}}))
        self.module.LLMClientFactory.create.return_value = client
        items = [{'id': 1, 'text': 'input', 'media_url': 'https://example.invalid/synthetic'}]
        text = await self.analyzer._analyze_text(items, self.row, {}, 'synthetic', self.source, output_contract=self.contract)
        image = await self.analyzer._analyze_images(items, self.row, 'synthetic', output_contract=self.contract)
        video = await self.analyzer._analyze_videos(items, self.row, 'synthetic', output_contract=self.contract)
        for result in (text, image, video):
            self.assertEqual(result['parsed'], {'summary': 'valid'})
        self.assertEqual(client.analyze.await_count, 3)
        for call in client.analyze.call_args_list:
            self.assertIs(call.kwargs['pydantic_model'], self.contract.model)

    async def test_direct_modality_call_rejects_schema_before_selection(self):
        self.row.output_schema = object_schema({'x': {'type': 'unsupported'}})
        self.analyzer._get_llm_model = AsyncMock()
        for method, args in ((self.analyzer._analyze_text, ([], self.row, {}, 'synthetic', self.source)),
                             (self.analyzer._analyze_images, ([], self.row, 'synthetic')),
                             (self.analyzer._analyze_videos, ([], self.row, 'synthetic'))):
            self.assertIsNone(await method(*args))
        self.analyzer._get_llm_model.assert_not_awaited()

    async def test_cancellation_is_not_swallowed(self):
        self.analyzer._get_llm_model = AsyncMock(side_effect=asyncio.CancelledError())
        with self.assertRaises(asyncio.CancelledError):
            await self.analyzer._analyze_images([], self.row, 'synthetic', output_contract=self.contract)

    async def test_existing_partial_row_guard_precedes_compilation(self):
        self.module.AIAnalytics.objects.filter.return_value = NS(first=AsyncMock(return_value=NS(id=7)))
        self.row.output_schema = object_schema({'x': {'type': 'unsupported'}})
        compile_spy = Mock(side_effect=AssertionError('must not reach schema compilation'))
        with patch.object(self.module, 'compile_scenario_output', compile_spy):
            result = await self.analyzer._save_analysis({}, None, self.source, {}, 'synthetic', self.row,
                                                       reported_partial=True)
        self.assertIsNone(result)
        compile_spy.assert_not_called()

    async def test_direct_save_rejects_invalid_parsed_before_pricing(self):
        results = {'text_analysis': {'parsed': {'summary': 9}, 'response': {'usage': {'prompt_tokens': 3}}}}
        original = deepcopy(results)
        self.analyzer._price_usage = AsyncMock(side_effect=AssertionError('pricing must not run'))
        with self.assertRaises(ValueError):
            await self.analyzer._save_analysis(results, None, self.source, {}, 'synthetic', self.row,
                                              output_contract=self.contract)
        self.assertEqual(results, original)
        self.analyzer._price_usage.assert_not_awaited()

    async def test_direct_save_without_valid_semantic_result_is_rejected(self):
        with self.assertRaises(ValueError):
            await self.analyzer._save_analysis({"text_analysis": {"parsed": {}}}, None,
                self.source, {}, "synthetic", self.row, output_contract=self.contract)

    def test_audit_uses_compiled_schema_after_original_mutation(self):
        self.row.output_schema['properties']['summary']['type'] = 'integer'
        audit = self.analyzer._build_request_snapshot({}, self.source, self.row, output_contract=self.contract)
        self.assertEqual(audit['configuration']['output_schema'], self.contract.schema)
        self.assertEqual(audit['configuration']['output_schema']['properties']['summary']['type'], 'string')


if __name__ == '__main__':
    unittest.main()
