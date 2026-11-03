"""Pure strict Jinja/HTML contracts; no application/DB/provider imports."""

import runpy
from html.parser import HTMLParser
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
fixtures = runpy.run_path(str(ROOT / "tests/shared/connector_account_page_fixtures.py"))


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
            assert self.form is None, "forms must not nest"
            self.form = (attrs, [])
            self.forms.append(self.form)
        elif self.form is not None:
            self.form[1].append((tag, attrs))

    def handle_endtag(self, tag):
        if tag == "form":
            self.form = None


@pytest.mark.parametrize("mode", fixtures["MODES"])
def test_states_are_strict_accessible_and_do_not_imply_provider_health(mode):
    html = fixtures["render"](mode)
    doc = Document(html)
    ids = [a["id"] for _, a in doc.tags if "id" in a]
    assert len(ids) == len(set(ids))
    assert sum(tag == "h1" for tag, _ in doc.tags) == 1
    assert "Наличие записи не подтверждает" in html
    assert "не завершает сессию на стороне Telegram" in html
    assert any(tag == "a" and a.get("href") == "/app/settings/telegram-accounts" for tag, a in doc.tags)
    assert not any(tag == "img" for tag, _ in doc.tags)
    forms = [
        (a, fields) for a, fields in doc.forms if a.get("action", "").startswith("/app/settings/telegram-accounts/")
    ]
    assert len(forms) == (1 if mode in {"active", "reauth", "unsafe"} else 0)
    for form, fields in forms:
        assert form["method"] == "post" and form["action"].endswith("/12/revoke")
        inputs = {a["name"]: a for tag, a in fields if tag == "input"}
        assert set(inputs) == {"_csrf", "expected_revision", "confirm"}
        assert inputs["_csrf"]["value"] == "synthetic-account-token"
        assert inputs["expected_revision"]["value"] == "2"
        assert inputs["confirm"]["type"] == "checkbox" and "required" in inputs["confirm"]
        assert inputs["confirm"]["value"] == "revoke-local"
        assert any(tag == "label" and a.get("for") == inputs["confirm"]["id"] for tag, a in fields)
    assert ("data-telegram-empty" in html) == (mode == "empty")
    assert any(a.get("role") == "alert" for _, a in doc.tags) == (mode == "error")
