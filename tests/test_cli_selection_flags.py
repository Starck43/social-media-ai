"""CLI: `--tracked` / `--keywords` вместо `--monitored`, payload — через один writer.

Owner command:
`.venv/bin/python -m pytest tests/test_cli_selection_flags.py -q --noconftest -o addopts=`

Контракт из `docs/design/task-selection.md`: автор выбирается ключом задачи
`tracked_users` (`platform:external_id`), текстовый скоуп — `keywords_list`, а
payload пишет единственный писатель `AgentTaskManager.build_selection_payload`.
`--monitored` удаляется без совместимого флага.

Проверяется исходник (AST/текст), без запуска typer и без БД: так тест не
зависит от того, какие команды требуют подключения и как называются локальные
переменные внутри тела команды.
"""

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "cli" / "main.py"
COLLECT = ROOT / "cli" / "commands" / "collect.py"
WRITER_MODULE = ROOT / "app" / "models" / "managers" / "agent_task_manager.py"


def _tree(path):
    return ast.parse(path.read_text(encoding="utf-8"))


def _functions(tree):
    """Все функции модуля, включая вложенные (команды-фабрики)."""
    return {node.name: node for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}


def _option_flags(function):
    """Флаги `typer.Option(..., "--flag", ...)` у параметров функции."""
    flags = set()
    for default in function.args.defaults + [d for d in function.args.kw_defaults if d is not None]:
        if not isinstance(default, ast.Call):
            continue
        callee = ast.unparse(default.func)
        if not callee.endswith("typer.Option"):
            continue
        for arg in default.args:
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str) and arg.value.startswith("-"):
                flags.add(arg.value)
    return flags


@pytest.fixture(scope="module")
def main_tree():
    return _tree(MAIN)


@pytest.fixture(scope="module")
def functions(main_tree):
    return _functions(main_tree)


class TestTaskCommandsAcceptTracked:
    def test_task_add_has_tracked_and_keywords(self, functions):
        flags = _option_flags(functions["task_add"])
        assert "--tracked" in flags, f"task add: {sorted(flags)}"
        assert "--keywords" in flags

    def test_task_update_has_tracked_and_keywords(self, functions):
        flags = _option_flags(functions["task_update"])
        assert "--tracked" in flags, f"task update: {sorted(flags)}"
        assert "--keywords" in flags

    def test_one_off_run_command_has_tracked_and_keywords(self, functions):
        """`_make_task_run_command` строит `task collect/analyze/...` (одноразовый запуск)."""
        flags = _option_flags(functions["run"])
        assert "--tracked" in flags, f"one-off run: {sorted(flags)}"
        assert "--keywords" in flags

    def test_manual_collect_has_tracked_and_keywords(self):
        tree = _tree(COLLECT)
        flags = _option_flags(_functions(tree)["collect_cmd"])
        assert "--tracked" in flags, f"collect: {sorted(flags)}"
        assert "--keywords" in flags


class TestMonitoredFlagIsGone:
    @pytest.mark.parametrize("path", [MAIN, COLLECT], ids=["main", "collect"])
    def test_no_monitored_option_or_key_anywhere(self, path):
        """Ни флага `--monitored`, ни ключа payload `monitored_users`.

        Сообщение называет файл и строку: так уборка остаётся точечной, без
        чтения всего исходника в отчёте теста.
        """
        locations = [
            f"{number}: {line.strip()[:80]}"
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1)
            if "--monitored" in line or "monitored_users" in line
        ]
        assert not locations, f"{path.name} ещё содержит настройку:\n" + "\n".join(locations)


class TestSelectionGoesThroughTheSingleWriter:
    def test_task_add_uses_build_selection_payload(self, functions):
        body = ast.unparse(functions["task_add"])
        assert "build_selection_payload" in body
        assert "tracked_users" in body or "tracked" in body

    def test_task_update_uses_build_selection_payload(self, functions):
        body = ast.unparse(functions["task_update"])
        assert "build_selection_payload" in body

    def test_one_off_run_uses_build_selection_payload(self, functions):
        body = ast.unparse(functions["_run_task"])
        assert "build_selection_payload" in body, "одноразовый запуск пишет отбор сам, мимо writer"

    def test_manual_collect_uses_build_selection_payload(self):
        body = ast.unparse(_functions(_tree(COLLECT))["collect_cmd"])
        assert "build_selection_payload" in body

    def test_writer_lives_in_the_task_manager(self):
        """Единственный писатель — `AgentTaskManager.build_selection_payload`."""
        assert "def build_selection_payload" in WRITER_MODULE.read_text(encoding="utf-8")

    def test_cli_does_not_write_tracked_users_by_hand(self, main_tree):
        """Никаких `payload["tracked_users"] = ...` в обход нормализации."""
        for node in ast.walk(main_tree):
            if not isinstance(node, ast.Assign):
                continue
            for target in node.targets:
                rendered = ast.unparse(target)
                assert "tracked_users" not in rendered, f"ручная запись: {rendered}"

    def test_tracked_option_is_declared_as_a_string_list_input(self):
        """`--tracked` принимает строку-список, как `--keywords`, не CSV-файл."""
        for path in (MAIN, COLLECT):
            tree = _tree(path)
            for function in _functions(tree).values():
                for arg, default in zip(
                    function.args.args + function.args.kwonlyargs,
                    ([None] * (len(function.args.args) - len(function.args.defaults)))
                    + list(function.args.defaults)
                    + list(function.args.kw_defaults),
                ):
                    if arg.arg != "tracked" or not isinstance(default, ast.Call):
                        continue
                    flags = [a.value for a in default.args if isinstance(a, ast.Constant)]
                    assert "--tracked" in flags, f"{path.name}:{function.name} — опция не называет флаг"


class TestDryRunFlagPresent:
    def test_manual_collect_has_dry_on(self):
        tree = _tree(COLLECT)
        flags = _option_flags(_functions(tree)["collect_cmd"])
        assert "--dry-on" in flags, f"collect: {sorted(flags)}"

    def test_collect_writes_dry_run_into_the_payload(self):
        """`--dry-on` обязан дойти до хендлера как `payload["dry_run"] = True`."""
        tree = _tree(COLLECT)
        guards = [
            node
            for node in ast.walk(_functions(tree)["collect_cmd"])
            if isinstance(node, ast.If) and "dry_on" in ast.unparse(node.test)
        ]
        assert any("payload['dry_run'] = True" in ast.unparse(guard.body[0]) for guard in guards)

    def test_analyze_command_has_dry_on_and_writes_dry_run(self):
        from pathlib import Path

        direct = _tree(Path(__file__).resolve().parents[1] / "cli" / "commands" / "direct.py")
        cmd = _functions(direct)["cmd_analyze"]
        flags = _option_flags(cmd)
        assert "--dry-on" in flags, f"analyze: {sorted(flags)}"
        guards = [
            node
            for node in ast.walk(cmd)
            if isinstance(node, ast.If) and "dry_on" in ast.unparse(node.test)
        ]
        assert any("payload['dry_run'] = True" in ast.unparse(guard.body[0]) for guard in guards)


class TestExcludedFlagsAreGone:
    @pytest.mark.parametrize("path", [MAIN, COLLECT], ids=["main", "collect"])
    def test_no_excluded_option_or_key_anywhere(self, path):
        locations = [
            f"{number}: {line.strip()[:80]}"
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1)
            if "--excluded" in line or "excluded_users" in line
        ]
        assert not locations, f"{path.name} ещё содержит настройку:\n" + "\n".join(locations)
