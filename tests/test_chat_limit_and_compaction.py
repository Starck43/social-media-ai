"""Chat surfaces refuse a spent daily cap visibly, not with a silent 200.

Owner command:
`.venv/bin/python -m pytest tests/test_chat_limit_and_compaction.py -q --noconftest -o addopts=`

The failure this pins: at the daily cap the runtime answered with a plain
string, the web endpoint returned HTTP 200 carrying it, and the browser threw
the user's message away — the text looked like it had never been typed.
"""

import asyncio
import inspect
from types import SimpleNamespace

import pytest

from app.agent import runtime
from app.web import chat


def test_limit_prefix_is_a_stable_contract():
    assert runtime.LIMIT_REACHED_PREFIX == "Дневной лимит"


def test_runtime_limit_reply_states_spend_and_limit():
    source = inspect.getsource(runtime)
    assert "LIMIT_REACHED_PREFIX" in source
    assert "{spent:.2f}" in source and "{limit:.2f}" in source


def test_web_surface_returns_429_with_a_code_for_the_limit(monkeypatch):
    """The endpoint maps a limit refusal to 429 + a code the client can style."""
    source = inspect.getsource(chat.chat_send)
    assert "daily_limit_reached" in source
    assert "status_code=429" in source


def test_web_surface_uses_the_shared_prefix():
    source = inspect.getsource(chat.chat_send)
    assert "LIMIT_REACHED_PREFIX" in source, "the endpoint must not duplicate the marker text"


class TestCompactSourceList:
    def test_list_rows_carry_only_what_the_model_needs(self, monkeypatch):
        from app.agent.toolset import sources as tools

        row = SimpleNamespace(
            id=7,
            name="Анна",
            platform=SimpleNamespace(platform_type="vk", name="ВКонтакте"),
            source_type=SimpleNamespace(name="USER"),
            external_id="annastrizhovadesign",
            is_active=True,
            params={"noise": "x" * 500},
        )

        class _Query:
            def filter(self, **kwargs):
                return self

            def select_related(self, *relations):
                return self

            async def limit(self, count):
                return [row]

        class _Objects:
            def filter(self, **kwargs):
                return _Query()

            def select_related(self, *relations):
                return _Query()

        monkeypatch.setattr("app.models.Source.objects", _Objects(), raising=False)
        result = asyncio.run(tools.sources_list())

        assert result == [
            {"id": 7, "name": "Анна", "platform": "vk", "type": "user", "external_id": "annastrizhovadesign"}
        ]
        assert len(str(result)) < 200, "a compact row must not grow with display labels or params"

    def test_source_add_reports_an_existing_source_instead_of_failing(self):
        source = inspect.getsource(__import__("app.agent.toolset.sources", fromlist=["x"]).source_add)
        assert 'return {"status": "exists"' in source
        assert "get_source_by_platform" in source


class TestClientKeepsTheRefusedMessage:
    def test_js_marks_the_message_failed_instead_of_dropping_it(self):
        from pathlib import Path

        js = (Path(__file__).resolve().parents[1] / "app" / "static" / "js" / "chat.js").read_text()
        assert "userMsg.failed = true" in js
        assert "m !== userMsg" not in js, "the optimistic message must stay visible"
        assert "daily_limit_reached" in js


@pytest.mark.parametrize("widget", ["chat.html"])
def test_template_renders_a_failed_state(widget):
    from pathlib import Path

    html = (Path(__file__).resolve().parents[1] / "app" / "web" / "templates" / "web" / widget).read_text()
    assert "chat-bubble-failed" in html
    assert "chat-notice-error" in html
