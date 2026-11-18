"""Guard the P8-T51 width contract: every default job type fits both columns.

Source-only: no DB rows, external calls or migrations are touched.
"""

import unittest

from app.models import AgentTask, Job
from app.tasks.bootstrap import DEFAULT_TASKS


def _too_long(column) -> dict[str, str]:
    width = column.type.length
    return {name: job_type for name, (_, job_type, _) in DEFAULT_TASKS.items() if len(job_type) > width}


class JobTypeWidthTests(unittest.TestCase):
    def test_the_old_width_really_rejected_default_tasks(self):
        self.assertTrue(
            any(len(job_type) > 20 for _, job_type, _ in DEFAULT_TASKS.values()),
            "the regression guard must cover at least one long default job type",
        )

    def test_agent_task_column_fits_every_default_job_type(self):
        self.assertEqual(_too_long(AgentTask.__table__.c.job_type), {})

    def test_job_column_fits_every_default_job_type(self):
        self.assertEqual(_too_long(Job.__table__.c.job_type), {})

    def test_both_columns_are_wide_enough_for_the_longest_dispatch_key(self):
        longest = max(len(job_type) for _, job_type, _ in DEFAULT_TASKS.values())
        self.assertGreaterEqual(AgentTask.__table__.c.job_type.type.length, longest)
        self.assertGreaterEqual(Job.__table__.c.job_type.type.length, longest)
