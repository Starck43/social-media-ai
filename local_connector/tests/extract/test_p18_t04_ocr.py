"""P18-T04: bounded Tesseract subprocess (double-driven)."""

import os
import stat as stat_module
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402
from helpers import stub_tesseract  # noqa: E402

from local_connector.extract.limits import OcrBudget  # noqa: E402
from local_connector.extract.ocr import OcrOutcome, OcrRunner  # noqa: E402


def make_runner(tmp_path, binary, *, wall=5.0, cpu=10):
    budget = OcrBudget(
        languages=("rus", "eng"), cpu_seconds=cpu, wall_seconds=wall, memory_bytes=1 << 30, binary=str(binary)
    )
    return OcrRunner(budget, workdir=tmp_path / "ocr-work")


def test_ok_stdout_captured(tmp_path, monkeypatch):
    monkeypatch.delenv("P18T04_OCR", raising=False)
    binary = stub_tesseract(tmp_path)
    runner = make_runner(tmp_path, binary)
    outcome = runner.ocr_image(b"\x89PNG fake")
    assert isinstance(outcome, OcrOutcome)
    assert outcome.ok
    assert outcome.text.startswith("OCR-TEXT-MARKER")
    assert list((tmp_path / "ocr-work").iterdir()) == []


def test_unavailable_binary(tmp_path):
    runner = make_runner(tmp_path, tmp_path / "missing-tesseract.py")
    assert runner.ocr_image(b"\x89PNG fake").note == "ocr_unavailable"


def test_unavailable_via_path(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path))
    budget = OcrBudget(languages=("rus", "eng"), cpu_seconds=5, wall_seconds=5, memory_bytes=1 << 30, binary=None)
    runner = OcrRunner(budget, workdir=tmp_path / "ocr-work")
    assert runner.ocr_image(b"\x89PNG fake").note == "ocr_unavailable"


def test_languages_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("P18T04_OCR_LANGS", "eng")
    binary = stub_tesseract(tmp_path)
    runner = make_runner(tmp_path, binary)
    assert runner.ocr_image(b"\x89PNG fake").note == "ocr_languages_missing"
    monkeypatch.delenv("P18T04_OCR_LANGS", raising=False)


def test_empty_and_oversize_images(tmp_path):
    binary = stub_tesseract(tmp_path)
    small = make_runner(tmp_path, binary)
    assert small.ocr_image(b"").note == "ocr_empty_image"
    tiny = make_runner(tmp_path, binary)
    assert tiny.ocr_image(b"\x00" * (tiny.max_image_bytes + 1)).note == "ocr_image_too_large"


def test_wall_limit_kills_slow_child(tmp_path, monkeypatch):
    monkeypatch.setenv("P18T04_OCR", "slow")
    binary = stub_tesseract(tmp_path)
    runner = make_runner(tmp_path, binary, wall=1.0)
    started = time.monotonic()
    outcome = runner.ocr_image(b"\x89PNG fake")
    elapsed = time.monotonic() - started
    assert outcome.note == "ocr_wall_limit"
    assert elapsed < 10
    monkeypatch.delenv("P18T04_OCR", raising=False)


def test_wall_limit_reaps_whole_process_group(tmp_path):
    if os.name != "posix":
        pytest.skip("process groups are a POSIX concern")
    pidfile = tmp_path / "orphan-sleeper.pid"
    script = tmp_path / "group-sleeper.sh"
    script.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = "--list-langs" ]; then\n'
        "  echo 'List of available languages in ...:'\n"
        '  echo "rus"\n'
        '  echo "eng"\n'
        "  exit 0\n"
        "fi\n"
        f"( sleep 60 & echo $! > {pidfile} )\n"
        "sleep 60\n",
        encoding="utf-8",
    )
    script.chmod(stat_module.S_IRWXU)
    runner = make_runner(tmp_path, script, wall=1.0)
    assert runner.ocr_image(b"\x89PNG fake").note == "ocr_wall_limit"
    deadline = time.monotonic() + 5.0
    orphan_pid = None
    while time.monotonic() < deadline:
        if pidfile.exists():
            orphan_pid = int(pidfile.read_text(encoding="utf-8").strip())
            break
        time.sleep(0.05)
    if orphan_pid is None:
        return  # the child was killed before it could register its orphan
    while time.monotonic() < deadline:
        try:
            os.kill(orphan_pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.05)
    pytest.fail("process-group member outlived the wall timeout")


def test_cpu_limit_rlimit(tmp_path, monkeypatch):
    import resource

    if not hasattr(resource, "RLIMIT_CPU"):
        pytest.skip("CPU rlimit unavailable")
    monkeypatch.setenv("P18T04_OCR", "spin")
    binary = stub_tesseract(tmp_path)
    runner = make_runner(tmp_path, binary, cpu=1, wall=30.0)
    outcome = runner.ocr_image(b"\x89PNG fake")
    assert outcome.note == "ocr_cpu_limit"
    monkeypatch.delenv("P18T04_OCR", raising=False)


def test_child_failure_exit_code(tmp_path, monkeypatch):
    monkeypatch.setenv("P18T04_OCR", "fail")
    binary = stub_tesseract(tmp_path)
    runner = make_runner(tmp_path, binary)
    assert runner.ocr_image(b"\x89PNG fake").note == "ocr_error"
    monkeypatch.delenv("P18T04_OCR", raising=False)


def test_memory_rlimit_setting_failure_is_best_effort(tmp_path, monkeypatch):
    import resource

    real_setrlimit = resource.setrlimit

    def deny_memory(*args):
        if args and args[0] == resource.RLIMIT_AS:
            raise OSError("denied in test")
        real_setrlimit(*args)

    monkeypatch.setattr(resource, "setrlimit", deny_memory)
    binary = stub_tesseract(tmp_path)
    runner = make_runner(tmp_path, binary)
    assert runner.ocr_image(b"\x89PNG fake").ok
    monkeypatch.setattr(resource, "setrlimit", real_setrlimit)


def test_budget_validation():
    with pytest.raises(ValueError):
        OcrBudget(languages=(), cpu_seconds=1, wall_seconds=1, memory_bytes=1 << 20)
