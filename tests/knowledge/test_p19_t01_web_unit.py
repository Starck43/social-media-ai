"""DB-exempt form and template checks with explicit request doubles."""

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from jinja2 import ChoiceLoader, DictLoader, Environment, FileSystemLoader, StrictUndefined, select_autoescape
from sqlalchemy import event
from sqlalchemy.engine.interfaces import Dialect
from starlette.datastructures import FormData

from app.services.knowledge.document_contracts import DocumentOut
from app.web import documents as web


@pytest.fixture(autouse=True)
def no_db():
    def forbidden(*args, **kwargs):
        raise AssertionError("DB access forbidden")

    event.listen(Dialect, "do_connect", forbidden)
    yield
    event.remove(Dialect, "do_connect", forbidden)


@pytest.mark.asyncio
async def test_form_requires_csrf_and_rejects_duplicate_and_invalid_ids(monkeypatch):
    from app.web import deps

    monkeypatch.setattr(deps, "ensure_csrf", lambda request, token: token == "valid")
    req = SimpleNamespace(form=AsyncMock(return_value=FormData([("_csrf", "invalid")])))
    with pytest.raises(HTTPException) as exc:
        await web._business_form(req)
    assert exc.value.status_code == 403
    req.form.return_value = FormData([("_csrf", "valid"), ("title", "one"), ("title", "two")])
    with pytest.raises(HTTPException) as exc:
        await web._business_form(req)
    assert exc.value.status_code == 422
    req.form.return_value = FormData([("_csrf", "valid"), ("contact_id", "01")])
    with pytest.raises(HTTPException) as exc:
        await web._business_form(req)
    assert exc.value.status_code == 422
    req.form.return_value = FormData(
        [("_csrf", "valid"), ("boundary", "company"), ("contact_id", "2"), ("tags", "a, b"), ("change_description", "")]
    )
    boundary, values = await web._business_form(req)
    assert boundary == "company" and values == {"contact_id": 2, "tags": ["a", "b"]}


def test_document_management_template_escapes_content_and_confirms_deletion():
    root = Path(__file__).resolve().parents[2] / "app/web/templates"
    loader = ChoiceLoader(
        [
            DictLoader(
                {
                    "web/base.html": "{% block page_heading %}{% endblock %}{% block page_actions %}{% endblock %}{% block content %}{% endblock %}"
                }
            ),
            FileSystemLoader(root),
        ]
    )
    env = Environment(loader=loader, autoescape=select_autoescape(default=True), undefined=StrictUndefined)
    item = DocumentOut(
        id=1,
        title="<script>bad</script>",
        document_type="memo",
        body_text="<b>unsafe</b>",
        counterparty="",
        tags=[],
        contact_id=None,
        case_evidence_id=None,
        owner_user_id=2,
        data_boundary="cloud",
        change_description=None,
        current_revision_id=3,
        version=1,
        content_hash="sha256:" + "a" * 64,
        status="draft",
        approval_invalidated=False,
        approved_by=None,
        author_user_id=2,
        updated_at=datetime(2026, 11, 7, tzinfo=timezone.utc),
    )
    html = env.get_template("web/document_management.html").render(
        boundary="cloud",
        editing=item,
        items=[item],
        templates=[],
        next_before_id=None,
        can_create=True,
        can_update=True,
        can_delete=True,
        csrf="csrf-test",
    )
    assert "<script>bad</script>" not in html and "&lt;script&gt;bad&lt;/script&gt;" in html
    assert "&lt;b&gt;unsafe&lt;/b&gt;" in html
    assert 'name="_csrf" value="csrf-test"' in html
    assert 'name="confirm" value="delete" required' in html


def test_review_buttons_have_bound_state_and_clear_owner_only_notice():
    root = Path(__file__).resolve().parents[2] / "app/web/templates"
    source = (root / "web/document_management.html").read_text()
    for target in ("approved", "rejected", "revision_requested"):
        assert target in source
    for field in ("expected_revision_id", "expected_hash", "expected_status", "_csrf"):
        assert field in source
    assert "только активный owner" in source
