"""Strict template regressions only; never import the application or its DB."""

import runpy
from html.parser import HTMLParser
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
fixtures = runpy.run_path(str(ROOT / "tests/settings_layout_fixtures.py"))


class Document(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.tags = []
        self.forms = []
        self.form = None
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        self.tags.append((tag, attrs))
        if tag == "form":
            assert self.form is None, "Forms must never be nested"
            self.form = [attrs, []]
            self.forms.append(self.form)
        elif self.form is not None:
            self.form[1].append((tag, attrs))

    def handle_endtag(self, tag):
        if tag == "form":
            self.form = None


def render(mode, **overrides):
    c = fixtures["context"](mode)
    c.update(overrides)
    return fixtures["environment"]().get_template("web/settings.html").render(**c)


@pytest.mark.parametrize("mode", fixtures["MODES"])
def test_every_settings_state_has_one_navigation_and_safe_independent_forms(mode):
    doc = Document(render(mode))
    assert sum(tag == "nav" and a.get("aria-label") == "Разделы настроек" for tag, a in doc.tags) == 1
    assert not any(a.get("class", "").startswith("ui-segments") for _, a in doc.tags)
    selected = [
        a
        for tag, a in doc.tags
        if tag == "a" and a.get("href", "").startswith("/app/settings?tab=") and a.get("aria-current") == "page"
    ]
    assert len(selected) == 1
    ids = [a["id"] for _, a in doc.tags if "id" in a]
    assert len(ids) == len(set(ids))
    for form, fields in doc.forms:
        if form.get("method", "").lower() == "post":
            assert sum(a.get("name") == "_csrf" for _, a in fields) == 1


def test_team_compact_controls_preserve_actions_roles_and_owner_protection():
    doc = Document(render("owner-team"))
    assert not any(tag == "table" for tag, _ in doc.tags)
    forms = {form["action"]: fields for form, fields in doc.forms}
    for member in (2, 3):
        assert f"/app/settings/members/{member}/role" in forms
        assert f"/app/settings/members/{member}/ai" in forms
        assert f"/app/settings/members/{member}/disable" in forms
    assert "/app/settings/members/1/role" not in forms
    assert "/app/settings/members/1/disable" not in forms
    assert "/app/settings/members/1/ai" not in forms
    assert next(a["value"] for _, a in forms["/app/settings/members/2/ai"] if a.get("name") == "enabled") == "0"
    assert next(a["value"] for _, a in forms["/app/settings/members/3/ai"] if a.get("name") == "enabled") == "1"
    assert any(
        tag == "form" and a.get("data-confirm") for tag, a in doc.tags if a.get("action", "").endswith("/disable")
    )


@pytest.mark.parametrize("mode", ["member-team", "member-workspace", "operator", "no-workspace"])
def test_non_managers_do_not_render_privileged_controls(mode):
    html = render(mode)
    doc = Document(html)
    assert "data-team-members" not in html
    assert 'name="daily_cost_limit"' not in html
    assert 'name="agent_system_prompt"' not in html
    assert not any(form["action"].startswith("/app/settings/members/") for form, _ in doc.forms)
    assert not any(tag == "a" and a.get("href") == "/app/settings?tab=agent" for tag, a in doc.tags)


def test_model_probe_is_a_distinct_submitter_with_results_and_help():
    doc = Document(render("agent"))
    form, fields = next((a, f) for a, f in doc.forms if a.get("id") == "agent-settings")
    probe = next(a for tag, a in fields if a.get("formaction") == "/app/settings/agent/model-probe")
    assert probe["formmethod"] == "post"
    assert probe["aria-describedby"] == "ag-model-help"
    assert any("data-agent-save" in a for _, a in fields)
    assert form["action"] == "/app/settings/agent"
    assert any(a.get("role") == "status" for _, a in doc.tags)
    assert 'role="status"' in render("agent-error")


def test_names_and_confirmation_text_remain_escaped():
    payload = '"><img src=x onerror="bad()">'
    html = render("owner-team", member_names={101: "Owner", 102: payload, 103: "Viewer"})
    assert "&lt;img" in html
    doc = Document(html)
    assert not any(tag == "img" for tag, _ in doc.tags)
    assert any(payload in a.get("data-confirm", "") for _, a in doc.tags)


@pytest.mark.parametrize("state", ["open", "empty", "issued"])
def test_compact_invitation_states_keep_issue_revoke_and_one_time_result(state):
    html = fixtures["environment"]().get_template("web/_invite_dialog.html").render(**fixtures["invite_context"](state))
    doc = Document(html)
    assert not any(tag == "table" for tag, _ in doc.tags)
    assert any(form["action"] == "/app/settings/invites" for form, _ in doc.forms)
    assert ("data-issued-invite" in html) == (state == "issued")
    assert any(form["action"].endswith("/revoke") and form.get("data-confirm") for form, _ in doc.forms) == (
        state != "empty"
    )
    for _, fields in doc.forms:
        assert sum(a.get("name") == "_csrf" for _, a in fields) == 1
