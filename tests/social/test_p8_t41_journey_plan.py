"""Standalone source-only journey validation, not DB or five-surface acceptance."""

import unittest
from copy import deepcopy

from tests.shared.vk_research_journey import journey_plan, load_plan_modules


class JourneyPlanTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.policy, cls.plans, cls.preparation = load_plan_modules()

    def test_selected_wall_roundtrip_create_reopen_edit(self):
        raw = journey_plan()
        created = self.preparation.prepare_task_plan_create({}, raw, job_type="analyze")
        self.assertEqual(created.source_ids, (11,))
        self.assertEqual(created.scenario_id, 42)
        self.assertEqual(created.cron_expr, "@once")
        canonical = self.plans.task_plan_block(self.plans.parse_task_plan(raw))
        self.assertEqual(created.payload["task_plan"], canonical)
        update = self.plans.prepare_task_plan_update(created.payload, {"name": "Renamed"}, job_type="analyze")
        self.assertEqual(update.changes["payload"]["task_plan"], canonical)
        self.assertEqual(canonical["vk_access"], {"mode": "user", "credential_id": 7})
        self.assertNotEqual(canonical["window"], canonical["digest"]["window"])
        self.assertTrue(created.payload["collect_before_analyze"])
        self.assertEqual(raw, journey_plan(), "Preparation must not mutate caller input")

    def test_invalid_scope_window_category_and_delivery_are_rejected(self):
        mutations = {
            "empty_selected": lambda p: p.update(source_ids=[]),
            "foreign_category": lambda p: p["collection"].update(content_types=["market"]),
            "reversed_window": lambda p: p["window"].update(end="2026-10-01"),
            "reversed_report": lambda p: p["digest"]["window"].update(end="2026-10-01"),
            "unconfirmed_send": lambda p: p["digest"].update(delivery={"send": True}),
            "automatic_fallback": lambda p: p.update(vk_access={"mode": "auto"}),
            "collect_without_analysis": lambda p: p["pipeline"].update(stages=["collect", "digest"]),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                plan = deepcopy(journey_plan())
                mutate(plan)
                with self.assertRaises(self.plans.TaskPlanError):
                    self.plans.parse_task_plan(plan)

    def test_conflicting_create_fields_do_not_expand_scope(self):
        for fields in ({"requested_scenario_id": 999}, {"requested_source_ids": [88], "enforce_source_match": True}):
            with self.subTest(fields=fields), self.assertRaises(self.preparation.TaskPlanCreateConflict):
                self.preparation.prepare_task_plan_create({}, journey_plan(), job_type="analyze", **fields)


if __name__ == "__main__":
    unittest.main()
