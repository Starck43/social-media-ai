"""Standalone route tests for the `/app/billing/exposure` operator view.

No application/DB/provider imports or route-server startup: `app/web/billing.py`
is loaded with module-local import doubles (the same `load_isolated_source`
idiom as the jobs queue signals suite), so the superuser gate, tenant pin and
no-fabricated-zeros failure path are exercised without `app.core.database`.
"""

import logging
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

TESTS_ROOT = Path(__file__).resolve().parents[1]
if str(TESTS_ROOT) not in sys.path:
    sys.path.insert(0, str(TESTS_ROOT))

from source_import_isolation import load_isolated_source  # isort:skip

ROOT = Path(__file__).resolve().parents[2]
NO_TENANT = object()
MISSING_TENANT_MESSAGE = "Выберите пространство для просмотра финансовой экспозиции"


class Router:
    def __init__(self, **kwargs):
        self.prefix = kwargs.get("prefix")

    def get(self, path, **kwargs):
        return lambda function: function

    post = get


class Redirect:
    """Double for `fastapi.responses.RedirectResponse` recording url and code."""

    def __init__(self, url, status_code=302):
        self.url = url
        self.status_code = status_code


class BillingExposureRouteTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        logger = logging.getLogger("app.web._billing_exposure_isolated")
        logger.addHandler(logging.NullHandler())
        logger.propagate = False

    def setUp(self):
        self.spend = {"scope": "workspace_payer_bound_dispatches", "dispatch_count": 2, "settled_usd": "0.000009000"}
        self.unpriced = [{"provider": "prov", "model_id": "model", "attempts": 3, "last_observed_at": None}]
        self.zero_price = {
            "total": 1,
            "models": [{"provider": "prov", "model_id": "free", "name": "Free", "is_default": False}],
        }
        self.workspace_spend_summary = AsyncMock(return_value=self.spend)
        self.unpriced_attempt_models = AsyncMock(return_value=self.unpriced)
        self.zero_price_active_models = AsyncMock(return_value=self.zero_price)
        self.flashes = []
        deps = SimpleNamespace(
            add_flash=lambda request, kind, text: self.flashes.append((kind, text)),
            ensure_csrf=lambda *args, **kwargs: True,
            guard_superuser=Mock(return_value=None),
            render=Mock(return_value="page"),
        )
        self.deps = deps
        self.source = load_isolated_source(
            "app.web._billing_exposure_isolated",
            ROOT / "app/web/billing.py",
            {
                "fastapi": SimpleNamespace(
                    APIRouter=Router, Form=lambda default=None, **kwargs: default, Request=object
                ),
                "fastapi.responses": SimpleNamespace(JSONResponse=object, RedirectResponse=Redirect),
                "app.models.managers.personal_billing_manager": SimpleNamespace(
                    PersonalBillingError=PermissionError, PersonalBillingUnavailable=RuntimeError
                ),
                "app.services.billing": SimpleNamespace(
                    configure_current_personal_account=AsyncMock(),
                    consent_current_user_as_task_payer=AsyncMock(),
                    current_personal_account=AsyncMock(),
                    current_personal_spend_summary=AsyncMock(),
                    unpriced_attempt_models=self.unpriced_attempt_models,
                    workspace_spend_summary=self.workspace_spend_summary,
                    zero_price_active_models=self.zero_price_active_models,
                ),
                "app.web.deps": deps,
            },
        )

    def request(self, tenant_id=NO_TENANT):
        state = {"web_user": SimpleNamespace(id=17, is_active=True), "web_perms": object()}
        if tenant_id is not NO_TENANT:
            state["tenant_id"] = tenant_id
        return SimpleNamespace(state=SimpleNamespace(**state))

    def assert_no_reads(self):
        self.workspace_spend_summary.assert_not_awaited()
        self.unpriced_attempt_models.assert_not_awaited()
        self.zero_price_active_models.assert_not_awaited()
        self.deps.render.assert_not_called()

    async def test_nonsuperuser_denial_returns_guard_response_without_reads(self):
        denied = Redirect("/app/jobs")
        self.deps.guard_superuser.return_value = denied
        request = self.request(31)

        result = await self.source.billing_exposure(request)

        self.assertIs(result, denied)
        self.deps.guard_superuser.assert_called_once_with(request, back="/app/jobs")
        self.assert_no_reads()
        self.assertEqual(self.flashes, [])

    async def test_superuser_passes_tenant_to_scoped_reads_and_renders_all_results(self):
        request = self.request(31)

        result = await self.source.billing_exposure(request)

        self.assertEqual(result, "page")
        self.workspace_spend_summary.assert_awaited_once_with(31)
        self.unpriced_attempt_models.assert_awaited_once_with(31)
        self.zero_price_active_models.assert_awaited_once_with()
        self.deps.render.assert_called_once()
        args, kwargs = self.deps.render.call_args
        self.assertEqual(args, (request, "web/billing_exposure.html"))
        self.assertEqual(kwargs["section"], "jobs")
        self.assertIs(kwargs["spend"], self.spend)
        self.assertIs(kwargs["unpriced"], self.unpriced)
        self.assertIs(kwargs["zero_price"], self.zero_price)
        self.assertIs(kwargs["exposure_unavailable"], False)
        self.assertEqual(kwargs["exposure_days"], 7)
        self.assertEqual(self.flashes, [])

    async def test_read_failure_renders_unavailable_without_fabricated_zeros(self):
        self.workspace_spend_summary.side_effect = RuntimeError("ledger read failed")

        result = await self.source.billing_exposure(self.request(31))

        self.assertEqual(result, "page")
        self.unpriced_attempt_models.assert_not_awaited()
        self.zero_price_active_models.assert_not_awaited()
        kwargs = self.deps.render.call_args.kwargs
        self.assertIsNone(kwargs["spend"])
        self.assertIsNone(kwargs["unpriced"])
        self.assertIsNone(kwargs["zero_price"])
        self.assertIs(kwargs["exposure_unavailable"], True)
        self.assertEqual(kwargs["exposure_days"], 7)
        self.assertEqual(kwargs["section"], "jobs")
        self.assertEqual(self.flashes, [])

    async def test_missing_or_invalid_tenant_flashes_error_and_redirects_to_jobs(self):
        for tenant_id in (NO_TENANT, None, 0, -7, "31", 31.0):
            with self.subTest(tenant_id=tenant_id):
                self.flashes = []
                request = self.request(tenant_id)

                result = await self.source.billing_exposure(request)

                self.assertIsInstance(result, Redirect)
                self.assertEqual(result.url, "/app/jobs")
                self.assertEqual(result.status_code, 302)
                self.deps.guard_superuser.assert_called_with(request, back="/app/jobs")
                self.assertEqual(self.flashes, [("error", MISSING_TENANT_MESSAGE)])
                self.assert_no_reads()


if __name__ == "__main__":
    unittest.main()
