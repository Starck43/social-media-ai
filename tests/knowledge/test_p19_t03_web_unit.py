"""Template-level escaping and CSRF evidence without HTTP or database."""

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from jinja2 import ChoiceLoader, DictLoader, Environment, FileSystemLoader, StrictUndefined, select_autoescape


def test_history_escapes_untrusted_text_and_binds_restore_to_current_state():
    root = Path(__file__).resolve().parents[2] / "app/web/templates"
    env = Environment(
        loader=ChoiceLoader(
            [
                DictLoader(
                    {
                        "web/base.html": "{% block page_heading %}{% endblock %}{% block page_actions %}{% endblock %}{% block content %}{% endblock %}"
                    }
                ),
                FileSystemLoader(root),
            ]
        ),
        autoescape=select_autoescape(default=True),
        undefined=StrictUndefined,
    )
    now = datetime(2026, 11, 8, tzinfo=timezone.utc)
    item = SimpleNamespace(
        id=1,
        title="t",
        version=2,
        status="draft",
        approval_invalidated=True,
        contact_id=None,
        case_evidence_id=None,
        owner_user_id=None,
        content_hash="hash",
        current_revision_id=7,
        updated_at=now,
        document_type="memo",
        counterparty="",
        tags=[],
        data_boundary="local_only",
        body_text="body",
    )
    rev = SimpleNamespace(
        id=6,
        version=1,
        author_user_id=None,
        author="<b>evil</b>",
        created_at=now,
        change_description="<script>bad</script>",
    )
    part = SimpleNamespace(operation="replace", before="<img src=x>", after="<script>x</script>")
    html = env.get_template("web/document_management.html").render(
        boundary="local_only",
        editing=item,
        items=[],
        templates=[],
        next_before_id=None,
        can_create=False,
        can_update=True,
        can_delete=False,
        # The real render() always injects perms (None hides optional controls).
        perms=None,
        csrf="token",
        pending={"items": []},
        approval_key="test-key",
        history={"items": [rev], "next_before_id": None},
        diff=SimpleNamespace(parts=[part]),
    )
    assert "<script>bad</script>" not in html and "&lt;script&gt;bad&lt;/script&gt;" in html
    assert "<img src=x>" not in html and "&lt;img src=x&gt;" in html
    assert 'name="_csrf" value="token"' in html and 'name="expected_revision_id" value="7"' in html
    assert "/restore/6" in html
