"""Bounded Tesseract OCR in a subprocess.

The runner talks to one regular executable resolved by the owner. A single
invocation is a bounded subprocess: CPU-time rlimit (mandatory on POSIX),
best-effort address-space rlimit, wall-clock timeout, a private 0600 scratch
file for the input image, and a fixed command shape with bounded language
arguments. Failures become sanitized note codes from a fixed set; command
arguments never embed file content.
"""

from __future__ import annotations

import os
import shutil
import signal
import stat as stat_module
import subprocess
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from functools import partial
from pathlib import Path

from .limits import OcrBudget

try:
    import resource
except ImportError:  # pragma: no cover - non-POSIX platforms reject the runner below
    resource = None

RUNNER_NAME = "tesseract"
_LANG_PROBE_TIMEOUT_SECONDS = 5.0
MAX_LANGUAGE_NAME_LENGTH = 32
MAX_LANGUAGE_COUNT = 16
DEFAULT_MAX_IMAGE_BYTES = 20 * 1024 * 1024

NOTES = (
    "ocr_unavailable",
    "ocr_empty_image",
    "ocr_image_too_large",
    "ocr_languages_missing",
    "ocr_wall_limit",
    "ocr_cpu_limit",
    "ocr_killed",
    "ocr_error",
)


def _set_rlimits(budget: OcrBudget) -> None:
    """Apply CPU (mandatory) and address-space (best effort) limits in the child."""

    assert resource is not None
    cpu = max(1, int(budget.cpu_seconds) + 1)
    resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu))
    if budget.memory_bytes > 0:
        try:
            resource.setrlimit(resource.RLIMIT_AS, (budget.memory_bytes, budget.memory_bytes))
        except (ValueError, OSError):
            # Address-space rlimits are unstable on several POSIX systems; the
            # CPU limit plus the wall-clock timeout still bound the call.
            pass


@dataclass(frozen=True)
class OcrOutcome:
    """Bounded OCR result; text is untrusted, note is a fixed code set."""

    text: str | None
    note: str | None = None

    @property
    def ok(self) -> bool:
        return self.text is not None


def _bounded_run(
    command: Sequence[str],
    *,
    capture_output: bool,
    timeout: float | None,
    check: bool,
    start_new_session: bool = True,
    preexec_fn: Callable[[], None] | None = None,
) -> subprocess.CompletedProcess[bytes]:
    """``subprocess.run`` stand-in that reaps the whole group on timeout.

    The child leads its own session (``start_new_session``), so a wall-clock
    timeout SIGKILLs the entire process group instead of only the direct
    child; no group member outlives the bounded call.
    """

    argv = list(command)
    process = subprocess.Popen(
        argv,
        stdout=subprocess.PIPE if capture_output else None,
        stderr=subprocess.PIPE if capture_output else None,
        start_new_session=start_new_session,
        preexec_fn=preexec_fn,
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        if os.name == "posix":
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except (OSError, ProcessLookupError):
                pass
        try:
            process.kill()
        except OSError:
            pass
        process.communicate()
        raise
    if check and process.returncode:
        raise subprocess.CalledProcessError(process.returncode, argv, stdout, stderr)
    return subprocess.CompletedProcess(argv, process.returncode, stdout=stdout, stderr=stderr)


class OcrRunner:
    """Resolve, validate and invoke one bounded Tesseract binary."""

    def __init__(
        self,
        budget: OcrBudget,
        *,
        workdir: Path | str,
        max_image_bytes: int = DEFAULT_MAX_IMAGE_BYTES,
        which: Callable[[str], str | None] = shutil.which,
        spawn: Callable[..., "subprocess.CompletedProcess[str]"] = _bounded_run,
    ) -> None:
        if max_image_bytes <= 0:
            raise ValueError("max_image_bytes must be positive")
        self.budget = budget
        self.workdir = Path(workdir)
        self.max_image_bytes = max_image_bytes
        self._which = which
        self._spawn = spawn
        self._binary: str | None = None
        self._languages: frozenset[str] | None = None

    # -- resolution ---------------------------------------------------------

    def _resolve_binary(self) -> str | None:
        if self._binary is not None:
            return self._binary
        resolved: str | None = None
        candidate = self.budget.binary
        if candidate:
            path = Path(candidate).expanduser()
            if path.is_file():
                info = path.stat()
                if stat_module.S_ISREG(info.st_mode) and os.access(path, os.X_OK):
                    resolved = str(path)
        else:
            resolved = self._which(RUNNER_NAME)
        if resolved is None:
            return None
        try:
            info = os.stat(resolved, follow_symlinks=True)
        except OSError:
            return None
        if not stat_module.S_ISREG(info.st_mode) or not os.access(resolved, os.X_OK):
            return None
        self._binary = resolved
        return resolved

    def _available_languages(self) -> frozenset[str] | None:
        if self._languages is not None:
            return self._languages
        binary = self._resolve_binary()
        if binary is None:
            return None
        try:
            outcome = self._spawn(
                [str(binary), "--list-langs"],
                capture_output=True,
                timeout=_LANG_PROBE_TIMEOUT_SECONDS,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            self._languages = frozenset()
            return self._languages
        if outcome.returncode != 0:
            self._languages = frozenset()
            return self._languages
        languages: set[str] = set()
        for line in outcome.stdout.decode("utf-8", "replace").splitlines():
            name = line.strip()
            if not name or len(name) > MAX_LANGUAGE_NAME_LENGTH or " " in name or "\t" in name:
                continue
            languages.add(name)
            if len(languages) >= MAX_LANGUAGE_COUNT:
                break
        self._languages = frozenset(languages)
        return self._languages

    def _is_ready(self) -> str | None:
        if self._resolve_binary() is None:
            return "ocr_unavailable"
        languages = self._available_languages()
        if languages is None or not frozenset(self.budget.languages) <= languages:
            return "ocr_languages_missing"
        return None

    # -- invocation ---------------------------------------------------------

    def ocr_image(self, image: bytes | bytearray) -> OcrOutcome:
        """Run one bounded OCR call over image bytes; never raise."""

        if not isinstance(image, (bytes, bytearray)) or not image:
            return OcrOutcome(None, "ocr_empty_image")
        if len(image) > self.max_image_bytes:
            return OcrOutcome(None, "ocr_image_too_large")
        problem = self._is_ready()
        if problem is not None:
            return OcrOutcome(None, problem)
        binary = self._resolve_binary()
        assert binary is not None

        if not self.workdir.is_dir():
            try:
                self.workdir.mkdir(mode=0o700)
                self.workdir.chmod(0o700)
            except OSError:
                return OcrOutcome(None, "ocr_unavailable")
        scratch: str | None = None
        try:
            descriptor, scratch = tempfile.mkstemp(prefix="p18ocr-in-", suffix=".png", dir=self.workdir)
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(bytes(image))
            preexec = partial(_set_rlimits, self.budget) if os.name == "posix" and resource is not None else None
            try:
                outcome = self._spawn(
                    [binary, scratch, "stdout", "-l", "+".join(self.budget.languages), "--psm", "3"],
                    capture_output=True,
                    timeout=self.budget.wall_seconds,
                    check=False,
                    start_new_session=True,
                    preexec_fn=preexec,
                )
            except subprocess.TimeoutExpired:
                return OcrOutcome(None, "ocr_wall_limit")
            except (OSError, subprocess.SubprocessError):
                return OcrOutcome(None, "ocr_unavailable")
            return self._interpret(outcome)
        finally:
            if scratch is not None:
                try:
                    os.unlink(scratch)
                except OSError:
                    pass

    @staticmethod
    def _interpret(outcome: "subprocess.CompletedProcess[str]") -> OcrOutcome:
        returncode = int(outcome.returncode)
        if returncode == 0:
            return OcrOutcome(outcome.stdout.decode("utf-8", "replace").strip())
        if returncode < 0:
            if -returncode == getattr(signal, "SIGXCPU", 24):
                return OcrOutcome(None, "ocr_cpu_limit")
            return OcrOutcome(None, "ocr_killed")
        return OcrOutcome(None, "ocr_error")
