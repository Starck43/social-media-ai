"""VK connection selection in actual task-plan source; no app bootstrap or DB."""

import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace

from tests.source_import_isolation import load_isolated_source

ROOT = Path(__file__).resolve().parents[2]


def _plan(**updates):
    plan = {
        "version": 1,
        "platforms": ["vk"],
        "source_scope": "discovery",
        "collection": {"queries": ["renovation"]},
        "trigger": {"type": "once"},
    }
    plan.update(updates)
    return plan


def _load_plans():
    config = ModuleType("app.core.config")
    config.settings = SimpleNamespace(SOCIAL_COLLECT_LIMIT=100, SOCIAL_COLLECT_HARD_LIMIT=1000)
    intent = load_isolated_source(
        "_p8_t38_plan_intent", ROOT / "app/services/collection/intent.py", {"app.core.config": config}
    )
    access = load_isolated_source("_p8_t38_vk_access", ROOT / "app/services/social/vk_access.py")
    return load_isolated_source(
        "_p8_t38_task_plans",
        ROOT / "app/services/task_plans.py",
        {
            "app.services.collection.intent": intent,
            "app.services.social.vk_access": access,
        },
    )


class VKTaskPlanAccessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plans = _load_plans()

    def test_vk_access_is_optional_and_round_trips(self):
        for selection in ({"mode": "api"}, {"mode": "user", "credential_id": 17}):
            with self.subTest(selection=selection):
                parsed = self.plans.parse_task_plan(_plan(vk_access=selection))
                self.assertEqual(parsed.vk_access.to_dict(), selection)
                self.assertEqual(self.plans.task_plan_block(parsed)["vk_access"], selection)
                payload = self.plans.apply_task_plan({}, _plan(vk_access=selection), job_type="collect")
                self.assertEqual(payload["task_plan"]["vk_access"], selection)

    def test_legacy_plan_without_vk_access_stays_valid_and_omits_field(self):
        block = self.plans.task_plan_block(self.plans.parse_task_plan(_plan()))
        self.assertNotIn("vk_access", block)

    def test_malformed_selection_fails_with_fixed_secret_free_error(self):
        malformed = (
            {"mode": "auto"},
            {"mode": "user"},
            {"mode": "user", "credential_id": True},
            {"mode": "api", "credential_id": 17},
            {"mode": "user", "credential_id": 17, "token": "secret-value"},
            "secret-value",
        )
        for selection in malformed:
            with self.subTest(selection=selection):
                with self.assertRaisesRegex(self.plans.TaskPlanError, "task_plan_vk_access_invalid") as caught:
                    self.plans.parse_task_plan(_plan(vk_access=selection))
                self.assertNotIn("secret-value", str(caught.exception))

    def test_vk_access_is_rejected_for_non_vk_task(self):
        with self.assertRaisesRegex(self.plans.TaskPlanError, "task_plan_vk_access_requires_vk"):
            self.plans.parse_task_plan(_plan(platforms=["telegram"], vk_access={"mode": "api"}))

    def test_apply_task_plan_preserves_existing_selection_when_omitted(self):
        pinned = {"mode": "user", "credential_id": 17}
        persisted = self.plans.apply_task_plan({}, _plan(vk_access=pinned), job_type="collect")
        updated = self.plans.apply_task_plan(
            persisted, _plan(collection={"queries": ["new phrase"]}), job_type="collect"
        )
        self.assertEqual(updated["task_plan"]["vk_access"], pinned)

    def test_explicit_null_cannot_clear_persisted_selection(self):
        persisted = self.plans.apply_task_plan(
            {}, _plan(vk_access={"mode": "user", "credential_id": 17}), job_type="collect"
        )
        with self.assertRaisesRegex(self.plans.TaskPlanError, "task_plan_vk_access_clear_requires_explicit_selection"):
            self.plans.apply_task_plan(persisted, _plan(vk_access=None), job_type="collect")

    def test_persisted_update_rejects_silent_selection_loss(self):
        existing = {"task_plan": {"vk_access": {"mode": "api"}}}
        with self.assertRaisesRegex(self.plans.TaskPlanError, "task_plan_vk_access_retention_required"):
            self.plans.validate_vk_access_retained(existing, {"task_plan": {}}, job_type="collect")
        self.plans.validate_vk_access_retained(
            existing, {"task_plan": {"vk_access": {"mode": "user", "credential_id": 23}}}, job_type="analyze"
        )
        self.plans.validate_vk_access_retained(existing, {}, job_type="digest")

    def test_task_plan_update_preserves_existing_selection_when_omitted(self):
        pinned = {"mode": "user", "credential_id": 17}
        persisted = self.plans.apply_task_plan({}, _plan(vk_access=pinned), job_type="collect")
        update = self.plans.prepare_task_plan_update(
            persisted,
            {"task_plan": _plan(collection={"queries": ["new phrase"]})},
            job_type="collect",
        )
        self.assertEqual(update.changes["payload"]["task_plan"]["vk_access"], pinned)

    def test_per_run_override_cannot_change_selected_connection(self):
        persisted = self.plans.apply_task_plan(
            {}, _plan(vk_access={"mode": "user", "credential_id": 17}), job_type="collect"
        )
        with self.assertRaisesRegex(self.plans.TaskPlanError, "task_plan_override_changes_persisted_scope"):
            self.plans.apply_task_plan_override(
                persisted,
                _plan(vk_access={"mode": "user", "credential_id": 18}),
                job_type="collect",
            )

    def test_per_run_override_preserves_connection_when_omitted(self):
        pinned = {"mode": "user", "credential_id": 17}
        persisted = self.plans.apply_task_plan({}, _plan(vk_access=pinned), job_type="collect")
        updated = self.plans.apply_task_plan_override(
            persisted,
            _plan(collection={"queries": ["narrowed phrase"]}),
            job_type="collect",
        )
        self.assertEqual(updated["task_plan"]["vk_access"], pinned)
