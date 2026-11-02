"""DB-exempt rendering contracts for the P12 evidence workspace UI."""

from pathlib import Path
from types import SimpleNamespace

import pytest
from bs4 import BeautifulSoup
from jinja2 import Environment, FileSystemLoader, select_autoescape

ROOT = Path(__file__).resolve().parents[2]
PAGES = ("knowledge", "documents", "communications", "followups", "cases")
BOUNDARIES = ("cloud", "company", "local_only")


def render(page, boundary="cloud", rows=(), q=""):
    env = Environment(
        loader=FileSystemLoader(ROOT / "app/web/templates"),
        autoescape=select_autoescape(),
        extensions=["jinja2.ext.do"],
    )
    env.filters["ui_label"] = str
    env.globals["static_v"] = lambda path: f"/static/{path}"
    env.globals["url_for"] = lambda endpoint, **kw: f"/static/{kw.get('path', '')}"
    return env.get_template(f"web/{page}.html").render(
        request=SimpleNamespace(query_params={}),
        user=None,
        tenant=None,
        section=page,
        boundary=boundary,
        evidence_rows=rows,
        **({"q": q} if page == "knowledge" else {}),
    )


@pytest.mark.parametrize("page", PAGES)
@pytest.mark.parametrize("boundary", BOUNDARIES)
def test_filter_values_and_selection(page, boundary):
    html = BeautifulSoup(render(page, boundary), "html.parser")
    picker = html.select_one("[data-popover-select]")
    assert picker is not None
    assert picker.select_one('input[name="boundary"]')["value"] == boundary
    options = picker.select('[role="option"]')
    assert [o["data-popover-option"] for o in options] == list(BOUNDARIES)
    assert [o["data-popover-option"] for o in options if o["aria-selected"] == "true"] == [boundary]
    assert html.select_one('label[for="evidence-boundary-trigger"]')
    assert not html.select('select[name="boundary"]')


def test_search_query_preserved_and_escaped():
    html = BeautifulSoup(render("knowledge", "company", q='<script>test & "x"</script>'), "html.parser")
    assert html.select_one('input[name="q"][type="hidden"]')["value"] == '<script>test & "x"</script>'
    assert not html.select("#page-content script")
    assert html.select_one('input[name="boundary"][type="hidden"]')["value"] == "company"


@pytest.mark.parametrize(
    "page,target",
    [
        ("knowledge", "/app/chat"),
        ("documents", "/app/knowledge"),
        ("communications", "/app/chat"),
        ("followups", "/app/communications"),
        ("cases", "/app/documents"),
    ],
)
def test_return_navigation(page, target):
    html = BeautifulSoup(render(page), "html.parser")
    assert html.select_one(f'#page-actions a[href="{target}"]')
    assert html.select_one("[data-evidence-empty]")


def test_evidence_handoff_attributes_and_escape():
    row = dict(
        context_type="document",
        context_id=27,
        title="<Example>",
        source_ref="source",
        data_boundary="cloud",
        acl_scope="member",
        version=2,
        status="draft",
        approved_by=None,
        counterparty="Acme",
        excerpt="<unsafe>",
        citation_anchor="page 1",
        revision_hash="hash",
        extraction_limit_note="",
        unsupported_terms_note="",
        chat_url="/app/chat?context_type=document&context_id=27",
    )
    html = BeautifulSoup(render("documents", rows=[row]), "html.parser")
    trigger = html.select_one("[data-evidence-trigger]")
    assert trigger["data-context-type"] == "document"
    assert trigger["data-context-id"] == "27"
    assert trigger["data-evidence-boundary"] == "cloud"
    assert html.select_one("[data-evidence-chat-link]")["href"] == row["chat_url"]
    assert html.select_one("blockquote").get_text() == "<unsafe>"
    assert not html.select("blockquote unsafe")
