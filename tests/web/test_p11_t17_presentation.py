"""Source-isolated presentation-boundary checks; no app runtime or DB imports."""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

from jinja2 import Environment

from app.core.presentation import MessageId, PresentationMessage, plural_category, render_message

__source_only__ = True
ROOT = Path(__file__).resolve().parents[2]


def _load_function(path: Path, name: str, namespace: dict[str, object]):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    selected = next(
        (
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == name
        ),
        None,
    )
    if selected is None:
        raise RuntimeError(f"Required function {name} is absent from {path}")
    module = ast.Module(
        body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), selected],
        type_ignores=[],
    )
    exec(compile(ast.fix_missing_locations(module), str(path), "exec"), namespace)
    return namespace[name]


def _source_note():
    return _load_function(
        ROOT / "app/web/task_presenters.py",
        "_source_note",
        {
            "Any": object,
            "MessageId": MessageId,
            "PresentationMessage": PresentationMessage,
            "render_message": render_message,
        },
    )


def _llm_error_reply():
    return _load_function(
        ROOT / "app/agent/runtime.py",
        "_llm_error_reply",
        {
            "BaseException": BaseException,
            "MessageId": MessageId,
            "PresentationMessage": PresentationMessage,
            "render_message": render_message,
        },
    )


class PresentationBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.source_note = _source_note()

    def test_russian_plural_interpolation_preserves_presenter_copy(self):
        self.assertEqual(
            [plural_category(count) for count in (1, 2, 5, 11, 21, 22)],
            ["one", "few", "many", "many", "one", "few"],
        )
        self.assertEqual(
            [self.source_note({"outcome": "collected", "items": count, "new_items": count}) for count in (1, 2, 5)],
            ["1 новая", "2 новых", "5 новых"],
        )
        self.assertEqual(
            [self.source_note({"outcome": "collected", "items": count}) for count in (1, 2, 5)],
            ["1 запись", "2 записи", "5 записей"],
        )
        self.assertEqual(
            [self.source_note({"outcome": "collected", "analyzed": count}) for count in (1, 2, 5)],
            ["1 результат анализа", "2 результата анализа", "5 результатов анализа"],
        )

    def test_error_hint_stays_plain_text_for_template_autoescaping(self):
        hint = "<script>alert(1)</script>"
        note = self.source_note({"outcome": "error", "error_hint": hint})
        html = Environment(autoescape=True).from_string("{{ note }}").render(note=note)
        self.assertIn("&lt;script&gt;", html)
        self.assertNotIn("<script>", html)

    def test_unknown_locale_falls_back_to_russian_copy(self):
        message = PresentationMessage(
            MessageId.LLM_REQUEST_REJECTED,
            {"status": 400},
        )
        expected = "Языковая модель отклонила мой запрос (400). Подробности в логах."
        self.assertEqual(render_message(message), expected)
        self.assertEqual(render_message(message, locale="en-US"), expected)
        self.assertEqual(render_message(message, locale="unknown"), expected)

    def test_llm_error_boundary_keeps_existing_status_copy(self):
        class Error(Exception):
            def __init__(self, status):
                self.response = type("Response", (), {"status_code": status})()

        present_error = _llm_error_reply()
        self.assertEqual(
            present_error(Error(401)),
            "Языковая модель отклонила запрос: неверный или истёкший API-ключ. Подробности в логах.",
        )
        self.assertEqual(
            present_error(Error(429)),
            "Языковая модель временно недоступна из-за лимита запросов. Подробности в логах.",
        )
        self.assertEqual(
            present_error(Error(400)),
            "Языковая модель отклонила мой запрос (400). Подробности в логах.",
        )
        self.assertEqual(
            present_error(Error(503)),
            "Языковая модель сейчас недоступна (503). Подробности в логах.",
        )
        self.assertEqual(
            present_error(RuntimeError("private")),
            "Не удалось обратиться к языковой модели. Подробности в логах.",
        )


if __name__ == "__main__":
    unittest.main()