"""No-DB checks for the selection keys of the analysis fingerprint.

Owner command: `.venv/bin/python -m pytest tests/test_analysis_fingerprint_selection.py -q --noconftest -o addopts=`
"""

import importlib.util
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]

_spec = importlib.util.spec_from_file_location(
    "_analysis_context", _ROOT / "app" / "services" / "ai" / "analysis_context.py"
)
_analysis_context = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _analysis_context
_spec.loader.exec_module(_analysis_context)

analysis_fingerprint = _analysis_context.analysis_fingerprint


def fingerprint(payload):
    return analysis_fingerprint(None, task_payload=payload)


def test_tracked_users_change_the_context():
    """Different people are a different analysis, not a repeat of it."""
    assert fingerprint({"tracked_users": ["vk:1"]}) != fingerprint({"tracked_users": ["vk:2"]})
    assert fingerprint({}) != fingerprint({"tracked_users": ["vk:1"]})


def test_text_scope_changes_the_context():
    assert fingerprint({"keywords_list": ["Италия"]}) != fingerprint({"keywords_list": ["Киров"]})
    assert fingerprint({"keywords_list": ["a", "b"], "keywords_match": "all"}) != fingerprint(
        {"keywords_list": ["a", "b"]}
    )


def test_same_selection_is_stable():
    first = fingerprint({"tracked_users": ["vk:1", "vk:2"], "keywords_list": ["x"]})
    second = fingerprint({"tracked_users": ["vk:1", "vk:2"], "keywords_list": ["x"]})
    assert first == second


def test_monitored_users_is_no_longer_an_operational_exclusion():
    """A leftover key would otherwise be a semantic input nobody reads."""
    assert fingerprint({"monitored_users": ["a"]}) != fingerprint({})
