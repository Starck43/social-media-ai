"""Event controls and worker notification behavior with explicit no-DB fakes."""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import event
from sqlalchemy.engine.interfaces import Dialect

__source_only__ = True


@pytest.fixture(autouse=True)
def no_database():
    def forbid(*args, **kwargs):
        raise AssertionError("real database forbidden")

    event.listen(Dialect, "do_connect", forbid)
    yield
    event.remove(Dialect, "do_connect", forbid)


class Session:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def begin(self):
        return self


@pytest.mark.asyncio
async def test_web_opt_in_and_draft_require_csrf_and_invoke_separate_actions(monkeypatch):
    from app.web import planner as web

    monkeypatch.setattr(web, "async_session_maker", Session)
    monkeypatch.setattr(web, "ensure_csrf", lambda request, value: value == "ok")
    configure = AsyncMock(return_value=True)
    draft = AsyncMock(return_value={"subject": "Hello", "draft_text": "Text", "send_enabled": False})
    monkeypatch.setattr(web.event_planning_service, "set_enabled", configure)
    monkeypatch.setattr(web.event_planning_service, "draft_for_task", draft)
    monkeypatch.setattr(
        web, "render", lambda request, template, **context: {"template": template, "draft": context["draft"]}
    )
    app = FastAPI()
    app.include_router(web.router, prefix="/app")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://unit.invalid") as client:
        assert (
            await client.post("/app/planner/events/settings", data={"_csrf": "bad", "enabled": "on"})
        ).status_code == 403
        assert (
            await client.post("/app/planner/events/settings", data={"_csrf": "ok", "enabled": "on"})
        ).status_code == 303
        response = await client.post("/app/planner/events/5/draft", data={"_csrf": "ok"})
        assert response.status_code == 200 and response.json()["draft"]["send_enabled"] is False
        assert (await client.post("/app/planner/events/5/draft", data={"_csrf": "bad"})).status_code == 403
    configure.assert_awaited_once()
    assert configure.await_args.args == (True,)
    draft.assert_awaited_once()
    assert draft.await_args.args == (5,)


@pytest.mark.asyncio
async def test_worker_skipped_outcome_produces_no_notification(monkeypatch):
    from app.jobs.claim_outcomes import JobClaim, JobOutcomeReceipt, OutcomeAck
    from app.jobs.dispatcher import _post_ordinary_outcome

    notify = AsyncMock()
    monkeypatch.setattr("app.jobs.dispatcher._notify_job_result", notify)
    claim = JobClaim(1, 7, "planner.events.daily", 4, 1, datetime.now(timezone.utc))
    receipt = JobOutcomeReceipt(
        claim, OutcomeAck.DONE, result={"status": "skipped", "reason": "disabled", "created": 0}
    )
    assert await _post_ordinary_outcome(receipt) == receipt
    notify.assert_not_awaited()


def test_draft_template_escapes_contact_content():
    from app.web.deps import templates

    html = templates.env.get_template("web/planner_event_draft.html").render(
        request=SimpleNamespace(query_params={}),
        user=None,
        tenant=None,
        perms=None,
        csrf="csrf",
        section="planner",
        draft={"subject": "<script>alert(1)</script>", "draft_text": "<b>unsafe</b>"},
        url_for=lambda name, **kwargs: "/static/" + kwargs.get("path", ""),
        static_v=lambda path: "/static/" + path,
    )
    assert "<script>alert(1)</script>" not in html and "&lt;script&gt;" in html
    assert "&lt;b&gt;unsafe&lt;/b&gt;" in html
