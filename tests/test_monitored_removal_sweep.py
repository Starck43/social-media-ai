"""Сквозная уборка `monitored_users`: ключ не должен остаться ни в app/, ни в cli/.

Owner command:
`.venv/bin/python -m pytest tests/test_monitored_removal_sweep.py -q --noconftest -o addopts=`

Это тест-метла, а не проверка поведения: он падает, пока рефакторинг не
завершён, и зеленеет, когда настройка удалена из кода, форм, шаблонов, CLI и
API. Поиск — по исходникам `*.py` и шаблонам, без импорта приложения.

Допустимое исключение (узкое, с причиной):

* `migrations/` — история миграций неизменна; чистка данных выполняется
  *новой* миграцией, а не правкой применённых. Поэтому каталог не сканируется
  вовсе.

Не-исполняемые артефакты (`__pycache__`, `node_modules`, `.git`) пропускаются.
Если где-то остаётся текст «удалено/removed» рядом с ключом (например, подсказка
в админке), строка вычитается только при наличии слова «removed» в той же
строке — и тест отдельно требует, чтобы такие строки не были кодом.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCAN_ROOTS = (ROOT / "app", ROOT / "cli")
SUFFIXES = {".py", ".html", ".js", ".json", ".toml", ".yaml", ".yml", ".jinja", ".jinja2", ".txt", ".md"}
SKIP_DIRS = {"__pycache__", "node_modules", ".git", ".mypy_cache", ".ruff_cache", ".pytest_cache"}
KEY = "monitored_users"
REMOVED_MARKERS = ("removed", "удален")


def _files():
    for base in SCAN_ROOTS:
        for path in sorted(base.rglob("*")):
            if not path.is_file() or path.suffix not in SUFFIXES:
                continue
            if SKIP_DIRS & set(path.parts):
                continue
            yield path


def _read(path):
    try:
        return path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return ""


def _mentions():
    """{относительный путь: [(номер строки, текст)]} для всех упоминаний ключа."""
    found = {}
    for path in _files():
        hits = [
            (number, line.strip())
            for number, line in enumerate(_read(path).splitlines(), start=1)
            if KEY in line and not any(marker in line.lower() for marker in REMOVED_MARKERS)
        ]
        if hits:
            found[path.relative_to(ROOT).as_posix()] = hits
    return found


def test_no_monitored_users_outside_allowlist():
    remaining = _mentions()
    report = "\n".join(
        f"{path}:{number}: {line}" for path, hits in remaining.items() for number, line in hits
    )
    assert not remaining, f"остались читатели/поля {KEY}:\n{report}"


def test_sweep_actually_scans_sources():
    """Метла не должна быть пустой: она видит хотя бы файлы приложения."""
    scanned = list(_files())
    assert any(path.name == "handlers.py" for path in scanned)
    assert any(path.suffix == ".html" for path in scanned)


def test_migrations_are_out_of_scope():
    """Историческая миграция с ключом не сканируется — она вне app/ и cli/."""
    assert (ROOT / "migrations").is_dir(), "каталог миграций переехал — перепроверьте allowlist"
    for path in SCAN_ROOTS:
        assert "migrations" not in path.parts


def test_removed_markers_only_on_prose_lines():
    """«removed» рядом с ключом допустим только в пояснении, не в присваивании."""
    pattern = re.compile(rf"{KEY}.{{0,40}}(removed|удален)|(removed|удален).{{0,40}}{KEY}", re.IGNORECASE)
    for path in _files():
        for match in pattern.finditer(_read(path)):
            fragment = match.group(0)
            assert "=" not in fragment, f"{path.relative_to(ROOT)}: пояснение размечено как код: {fragment!r}"
