"""Only the new desktop/update composition paths; no runtime or OS vault."""

import importlib.util
import json
import sys
from pathlib import Path

import pytest


def bootstrap():
    spec = importlib.util.spec_from_file_location("p18_bootstrap", Path(__file__).parents[1] / "bootstrap.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def fixture_bundle(tmp_path, monkeypatch, key=b"", feed=None):
    folder = tmp_path / "connector-release"
    folder.mkdir()
    (folder / "update-key.bin").write_bytes(key)
    (folder / "release-config.json").write_text(json.dumps({"feed": feed}))
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)


def test_default_frozen_build_never_enables_updates(tmp_path, monkeypatch):
    fixture_bundle(tmp_path, monkeypatch)
    assert bootstrap().bundled_release() == (b"", None)


def test_feed_only_comes_from_bundle(tmp_path, monkeypatch):
    fixture_bundle(tmp_path, monkeypatch, key=b"k" * 32, feed="https://updates.example.test/connector")
    monkeypatch.setenv("CONNECTOR_UPDATE_FEED", "https://evil.example.test")
    assert bootstrap().bundled_release() == (b"k" * 32, "https://updates.example.test/connector")


@pytest.mark.parametrize(
    "key, feed", [(b"x", None), (b"", "https://updates.example.test"), (b"k" * 32, "http://updates.example.test")]
)
def test_bad_bundled_identity_denied(tmp_path, monkeypatch, key, feed):
    fixture_bundle(tmp_path, monkeypatch, key=key, feed=feed)
    with pytest.raises(ValueError):
        bootstrap().bundled_release()


def test_self_test_dispatch_precedes_runtime(monkeypatch):
    module = bootstrap()
    monkeypatch.setattr(sys, "argv", ["connector", "--self-test"])
    monkeypatch.setattr(module, "self_test", lambda: 0)
    assert module.main() == 0


def test_no_source_trust_fallback(monkeypatch):
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    assert bootstrap().bundled_release() == (b"", None)
