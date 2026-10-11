"""Real typed validation across legacy coverage fixtures, no app/DB/providers.

Run: python tests/test_coverage_output_contract_integration.py
Historical modules supply setup only; no historical suite discovery.
"""
import copy
import unittest
from unittest.mock import patch

import test_media_skip_coverage as coverage_fixtures


class CoverageOutputContractIntegrationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.f = coverage_fixtures.MediaSkipCoverageTests()
        self.f.setUp()
        self.f.f.scenario.output_schema = {
            "type": "object", "properties": {"analysis": {"type": "string"}},
            "required": ["analysis"], "additionalProperties": False,
        }

    async def test_real_validator_reaches_stage_and_save_without_mock_payload(self):
        real_validator = self.f.module.validate_with_pydantic
        content = [coverage_fixtures.post("valid")]
        with patch.object(self.f.module, "validate_with_pydantic", wraps=real_validator) as validator:
            row = await self.f.analyze(content)
        self.f.assert_coverage(content, row, content)
        self.assertEqual(validator.call_count, 2)  # Actual text stage and save boundary.
        for call in validator.call_args_list:
            self.assertIs(call.kwargs["strict"], True)
        self.assertEqual(row.summary_data["multi_llm_analysis"]["text_analysis"],
                         self.f.f.good["parsed"])
        self.f.f.create.assert_awaited_once()

    async def test_invalid_typed_text_is_not_saved_or_certified(self):
        bad = copy.deepcopy(self.f.f.good)
        bad["parsed"] = {"analysis": 7}
        self.f.client.analyze.side_effect = None
        self.f.client.analyze.return_value = bad
        self.assertIsNone(await self.f.analyze([coverage_fixtures.post("invalid")]))
        self.assertEqual(self.f.analyzer.reported_errors, 1)
        self.f.f.create.assert_not_awaited()
        self.f.f.update.assert_not_awaited()
        self.assertEqual(self.f.dedup.analysed_hashes(self.f.f.rows), set())
        self.assertEqual(bad["parsed"], {"analysis": 7})

    async def test_invalid_typed_video_keeps_text_but_no_parent_receipt(self):
        async def result(*args, **kwargs):
            value = copy.deepcopy(self.f.f.good)
            if kwargs.get("media_urls"):
                value["parsed"] = {"analysis": 7}
            return value
        self.f.client.analyze.side_effect = result
        content = [coverage_fixtures.post("mixed", media="video")]
        row = await self.f.analyze(content)
        self.f.assert_coverage(content, row, [], expected_errors=1)
        self.assertIsNone(row.content_hash)
        self.assertEqual(row.summary_data["multi_llm_analysis"]["text_analysis"],
                         self.f.f.good["parsed"])
        self.assertEqual(row.summary_data["multi_llm_analysis"]["video_analysis"], {})
        self.assertEqual(self.f.client.analyze.await_count, 2)
        self.f.f.create.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
