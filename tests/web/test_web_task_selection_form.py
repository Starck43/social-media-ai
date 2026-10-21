"""Веб-форма задачи: поля отбора вместо «отслеживаемых пользователей» источника.

Owner command:
`.venv/bin/python -m pytest tests/test_web_task_selection_form.py -q --noconftest -o addopts=`

Контракт из `docs/design/task-selection.md`: люди и фразы живут в задаче
(`tracked_users`, `keywords_list` + `keywords_match`), а не в `Source.params`
и не в `monitored_users`. Форма и шаблоны проверяются по исходнику (AST/текст):
запуск FastAPI и БД не нужны, поэтому тест не зависит от шаблонизатора и роутинга.
"""

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
TEMPLATES = ROOT / "app" / "web" / "templates" / "web"
WEB = ROOT / "app" / "web"


def _tree(path):
    return ast.parse(path.read_text(encoding="utf-8"))


def _form_fields(path):
    """Имена Form-полей функции, включая `name: str = Form(...)` по позиции."""
    fields = set()
    for function in (n for n in ast.walk(_tree(path)) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))):
        args = function.args.args + function.args.kwonlyargs
        defaults = [None] * (len(function.args.args) - len(function.args.defaults)) + list(function.args.defaults)
        defaults += list(function.args.kw_defaults)
        for arg, default in zip(args, defaults):
            if isinstance(default, ast.Call) and ast.unparse(default.func).endswith("Form"):
                fields.add(arg.arg)
    return fields


def _text(path):
    return path.read_text(encoding="utf-8")


class TestTaskFormUsesSelection:
    def test_no_monitored_users_form_field(self):
        fields = _form_fields(WEB / "tasks.py")
        assert "monitored_users" not in fields
        assert "tracked_users" in fields, f"форма задачи не принимает tracked_users: {sorted(fields)}"

    def test_keywords_and_match_mode_are_present(self):
        fields = _form_fields(WEB / "tasks.py")
        assert {"keywords_list", "keywords_match"} <= fields, sorted(fields)

    def test_payload_is_written_through_the_selection_writer(self):
        source = _text(WEB / "tasks.py")
        assert "build_selection_payload" in source, "форма собирает payload в обход единственного writer"

    def test_edit_context_exposes_tracked_users_not_monitored(self):
        """Страница редактирования показывает сохранённый отбор задачи."""
        source = _text(WEB / "tasks.py")
        assert "monitored_users" not in source
        assert '"tracked_users"' in source or "'tracked_users'" in source


class TestTaskTemplates:
    @pytest.mark.parametrize("name", ["task_edit.html", "task_detail.html"])
    def test_template_has_no_monitored_users(self, name):
        assert "monitored_users" not in _text(TEMPLATES / name)

    @pytest.mark.parametrize("name", ["task_edit.html", "task_detail.html"])
    def test_template_shows_tracked_users(self, name):
        assert "tracked_users" in _text(TEMPLATES / name)


class TestSourcePagesLoseTheSetting:
    def test_source_endpoint_has_no_monitored_users_field(self):
        fields = _form_fields(WEB / "sources.py")
        assert "monitored_users" not in fields

    def test_source_module_does_not_mention_monitored_users(self):
        assert "monitored_users" not in _text(WEB / "sources.py")

    @pytest.mark.parametrize("name", ["sources.html", "source_detail.html", "_macros.html"])
    def test_source_templates_do_not_mention_monitored_users(self, name):
        assert "monitored_users" not in _text(TEMPLATES / name)
