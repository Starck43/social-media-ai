"""Quick-reply choices: parsing, channel rendering and web chat contracts (no DB)."""

from __future__ import annotations

from pathlib import Path

from app.agent.quick_replies import MAX_OPTIONS, render_for_channel, split_quick_replies


def test_trailing_marker_becomes_options_and_is_removed():
    body, options = split_quick_replies("За какой период?\n[[options: За неделю | За месяц | За вчера]]")
    assert body == "За какой период?"
    assert options == ["За неделю", "За месяц", "За вчера"]


def test_marker_followed_by_a_closing_sentence_still_offers_buttons():
    """A question with «или напишите своё» after the choices keeps its buttons.

    The model put the closing sentence after the marker, the marker stopped
    being the last thing in the reply, and the owner got prose instead of
    one-tap answers — the exact shape the live probe produced.
    """
    body, options = split_quick_replies(
        "С какой даты собирать контент?\n[[options: С сегодняшнего дня | За последние 30 дней]]\n"
        "Если нужна другая дата — напишите её."
    )
    assert options == ["С сегодняшнего дня", "За последние 30 дней"]
    assert "С какой даты" in body and "напишите её" in body
    assert "[[" not in body


def test_marker_in_the_middle_is_left_untouched():
    text = "Строка [[options: A | B]] внутри\nи дальше"
    assert split_quick_replies(text) == (text, [])


def test_options_are_deduplicated_trimmed_and_capped():
    labels = " | ".join(["Да", "да", "  ", "x" * 61] + [f"Вариант {i}" for i in range(10)])
    _, options = split_quick_replies(f"Выберите\n[[варианты: {labels}]]")
    assert options[0] == "Да" and "да" not in options
    assert all(0 < len(option) <= 60 for option in options)
    assert len(options) == MAX_OPTIONS


def test_pending_confirmation_offers_action_buttons_without_marker():
    body, options = split_quick_replies(
        "Требуется подтверждение: добавить источник «Кабанов» — ВКонтакте. Ответьте «да» или «нет»."
    )
    # The primary button names the action; «Отмена» is the only way to refuse.
    assert options[0].startswith("Да, подтверждаю: добавить источник")
    assert options[1:] == ["Покажи данные", "Отмена"]
    # The instruction is replaced by the buttons, the question itself stays.
    assert "Ответьте" not in body
    assert body.endswith("ВКонтакте.")


def test_confirmation_primary_label_stays_button_sized():
    _, options = split_quick_replies("Требуется подтверждение: " + "очень длинное действие " * 5)
    assert len(options[0]) <= 60
    assert options[0].endswith("…")


def test_confirmation_with_marker_keeps_the_model_labels():
    body, options = split_quick_replies("Требуется подтверждение: удалить задачу.\n[[options: Удалить | Оставить]]")
    assert options == ["Удалить", "Оставить"]
    assert "[[", "] ]" not in body and "[[" not in body


def test_primary_button_is_accepted_as_consent():
    from app.agent.runtime import is_confirmation_verdict

    label = "Да, подтверждаю: добавить источник «kaban828»…"
    import re

    assert is_confirmation_verdict(re.sub(r"…$", "", label))
    assert is_confirmation_verdict(label)
    assert is_confirmation_verdict("  ДА ")
    assert is_confirmation_verdict("подтверждаю")
    assert not is_confirmation_verdict("нет")
    assert not is_confirmation_verdict("Покажи данные")
    assert not is_confirmation_verdict("")


def test_confirmation_text_is_verbatim_reused_by_the_model():
    from app.agent.runtime import _human_confirmation

    text = _human_confirmation("source_add", {"external_id": "kaban828", "platform": "vk", "source_type": "user"})
    assert text.startswith("Требуется подтверждение:")
    _, options = split_quick_replies(text)
    assert options[-1] == "Отмена" and "Ответьте" not in text


def test_plain_text_and_empty_reply_have_no_options():
    assert split_quick_replies("Готово.") == ("Готово.", [])
    assert split_quick_replies(None) == ("", [])


def test_channel_rendering_strips_marker_and_lists_choices():
    rendered = render_for_channel("Что дальше?\n[[options: Мои задачи | Сводка]]")
    assert "[[" not in rendered
    assert rendered.endswith("Варианты: «Мои задачи» · «Сводка»")
    confirmation = "Требуется подтверждение: x."
    assert render_for_channel(confirmation) == confirmation


def test_system_prompt_forbids_restating_and_word_answers():
    from app.agent.prompts import DEFAULT_SYSTEM_PROMPT, QUICK_REPLIES_SECTION

    assert "Не перечисляй то, что не спрашивали" in DEFAULT_SYSTEM_PROMPT
    assert "сразу к делу" in DEFAULT_SYSTEM_PROMPT
    assert "кнопки действия" in QUICK_REPLIES_SECTION
    assert "Не пиши «напишите да или нет»" in QUICK_REPLIES_SECTION


def test_a_clarifying_question_must_offer_buttons():
    """A question with a handful of answers is asked as buttons, not prose.

    The agent asked «с какой даты собирать посты?» as plain text, so the owner
    had to type a date although «С сегодняшнего дня» was an obvious choice.
    """
    from app.agent.prompts import QUICK_REPLIES_SECTION, TASK_SECTION

    assert "уточняющий вопрос" in QUICK_REPLIES_SECTION
    assert "ОБЯЗАТЕЛЬНО заканчивается этой\n  строкой" in QUICK_REPLIES_SECTION
    assert "С сегодняшнего дня" in TASK_SECTION
    body, options = split_quick_replies(
        "С какой даты собирать контент?\n[[options: С сегодняшнего дня | За последние 30 дней]]"
    )
    assert body.startswith("С какой даты")
    assert options == ["С сегодняшнего дня", "За последние 30 дней"]


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
