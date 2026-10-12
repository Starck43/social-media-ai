"""Quick-reply choices: parsing, channel rendering and web chat contracts (no DB)."""

from __future__ import annotations

from pathlib import Path

from app.agent.quick_replies import MAX_OPTIONS, render_for_channel, split_quick_replies


def test_trailing_marker_becomes_options_and_is_removed():
    body, options = split_quick_replies("За какой период?\n[[options: За неделю | За месяц | За вчера]]")
    assert body == "За какой период?"
    assert options == ["За неделю", "За месяц", "За вчера"]


def test_marker_in_the_middle_is_left_untouched():
    text = "Строка [[options: A | B]] внутри\nи дальше"
    assert split_quick_replies(text) == (text, [])


def test_options_are_deduplicated_trimmed_and_capped():
    labels = " | ".join(["Да", "да", "  ", "x" * 61] + [f"Вариант {i}" for i in range(10)])
    _, options = split_quick_replies(f"Выберите\n[[варианты: {labels}]]")
    assert options[0] == "Да" and "да" not in options
    assert all(0 < len(option) <= 60 for option in options)
    assert len(options) == MAX_OPTIONS


def test_pending_confirmation_offers_yes_no_without_marker():
    body, options = split_quick_replies("Требуется подтверждение: добавить источник. Ответьте «да» или «нет».")
    assert options == ["Да", "Нет"]
    assert body.startswith("Требуется подтверждение")


def test_plain_text_and_empty_reply_have_no_options():
    assert split_quick_replies("Готово.") == ("Готово.", [])
    assert split_quick_replies(None) == ("", [])


def test_channel_rendering_strips_marker_and_lists_choices():
    rendered = render_for_channel("Что дальше?\n[[options: Мои задачи | Сводка]]")
    assert "[[" not in rendered
    assert rendered.endswith("Варианты: «Мои задачи» · «Сводка»")
    confirmation = "Требуется подтверждение: x. Ответьте «да» или «нет»."
    assert render_for_channel(confirmation) == confirmation


def test_help_text_offers_quick_replies():
    from app.agent.prompts import AGENT_HELP_TEXT

    body, options = split_quick_replies(AGENT_HELP_TEXT)
    assert "/stop" in body and "[[" not in body
    assert options


def test_web_chat_renders_quick_replies_and_thinking_indicator():
    chat = Path("app/web/templates/web/chat.html").read_text()
    js = Path("app/static/js/chat.js").read_text()
    assert 'class="chat-quick-replies"' in chat and "sendMessage(option)" in chat
    assert 'class="chat-thinking"' in chat and "thinkingLabel()" in chat
    assert 'class="chat-starters"' in chat
    assert "startThinking()" in js and "stopThinking()" in js


def test_web_transcript_splits_options_from_assistant_content():
    from types import SimpleNamespace

    from app.web.chat import _enrich_message

    assistant = SimpleNamespace(id=1, role="assistant", content="Период?\n[[options: Неделя | Месяц]]", created_at=None)
    user = SimpleNamespace(id=2, role="user", content="[[options: A | B]]", created_at=None)
    enriched = _enrich_message(assistant)
    assert enriched["content"] == "Период?" and enriched["options"] == ["Неделя", "Месяц"]
    assert _enrich_message(user) == {
        "id": 2, "role": "user", "content": "[[options: A | B]]", "options": [], "created_at": "",
    }
