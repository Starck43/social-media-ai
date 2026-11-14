"""Packaging-only tests with synthetic keys and mocked native build boundaries."""

import hashlib
import importlib.util
import json
import sys
import time
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat


def load(name):
    path = Path(__file__).parents[1] / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"p18_tool_{name}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_windows_normal_launch_denied(monkeypatch, capsys):
    bootstrap = load("bootstrap")
    monkeypatch.setattr(sys, "argv", ["connector"])
    monkeypatch.setattr(sys, "platform", "win32")
    assert bootstrap.main() == 2
    assert "native Windows" in capsys.readouterr().err


def test_bundled_verifier_and_tampered_artifact(tmp_path, monkeypatch):
    bootstrap = load("bootstrap")
    key = Ed25519PrivateKey.generate()
    trust = tmp_path / "connector-release"
    trust.mkdir()
    (trust / "update-key.bin").write_bytes(key.public_key().public_bytes_raw())
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(bootstrap.platform, "machine", lambda: "x86_64")
    artifact = tmp_path / "artifact"
    artifact.write_bytes(b"synthetic build; never executed")
    now = int(time.time())
    raw = json.dumps(
        {
            "schema": 1,
            "product": "ai-assistant-connector",
            "version": "999.0.0",
            "platform": "linux-x86_64",
            "size": artifact.stat().st_size,
            "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
            "issued_at": now - 1,
            "expires_at": now + 60,
        }
    ).encode()
    manifest, signature = tmp_path / "manifest", tmp_path / "signature"
    manifest.write_bytes(raw)
    signature.write_bytes(key.sign(raw))
    paths = [str(manifest), str(signature), str(artifact)]
    assert bootstrap.verify_update(paths) == 0
    artifact.write_bytes(b"tampered")
    assert bootstrap.verify_update(paths) == 2
    (trust / "update-key.bin").write_bytes(b"")
    assert bootstrap.verify_update(paths) == 2


def test_no_source_trust_fallback(monkeypatch):
    bootstrap = load("bootstrap")
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    assert bootstrap.verify_update(["not read", "not read", "not read"]) == 2


def test_linux_package_layout_mocked(tmp_path, monkeypatch):
    formats = load("linux_formats")
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / formats.NAME).write_text("synthetic frozen executable")
    (bundle / "INSTALL.md").write_text("no activation")
    tool, runtime = tmp_path / "tool", tmp_path / "runtime"
    tool.write_bytes(b"synthetic appimagetool")
    runtime.write_bytes(b"synthetic AppImage runtime")
    monkeypatch.setattr(formats, "APPIMAGETOOL_SHA256", hashlib.sha256(tool.read_bytes()).hexdigest())
    monkeypatch.setattr(formats, "RUNTIME_SHA256", hashlib.sha256(runtime.read_bytes()).hexdigest())
    calls = []
    monkeypatch.setattr(formats.subprocess, "run", lambda args, **kwargs: calls.append(args))
    output = tmp_path / "out"
    output.mkdir()
    formats.build(bundle, output, "0.2.0", tool, runtime)
    assert len(calls) == 4
    assert "--runtime-file" in calls[2]
    assert (output / "deb-root" / "opt" / formats.NAME / "INSTALL.md").is_file()
    assert (output / "deb-root" / "DEBIAN" / "control").read_text().startswith("Package:")
    assert sorted(p.name for p in (output / "deb-root" / "DEBIAN").iterdir()) == ["control"]
    assert not (output / "deb-root" / "etc").exists()
    assert (output / f"{formats.NAME}.AppDir" / "AppRun").stat().st_mode & 0o100


def test_linux_build_tool_tampering_denied(tmp_path):
    formats = load("linux_formats")
    tool = tmp_path / "untrusted"
    tool.write_bytes(b"not the pinned tool")
    with pytest.raises(ValueError, match="untrusted appimagetool"):
        formats.build(tmp_path, tmp_path, "0.2.0", tool, tool)


def test_offline_signing_synthetic_key(tmp_path, monkeypatch):
    # Script imports its adjacent verifier, not server/runtime dependencies.
    monkeypatch.syspath_prepend(str(Path(__file__).parents[1]))
    signer = load("sign_manifest")
    key = Ed25519PrivateKey.generate()
    key_path = tmp_path / "synthetic-private.pem"
    key_path.write_bytes(key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()))
    artifact = tmp_path / "artifact"
    artifact.write_bytes(b"synthetic bundle")
    manifest = tmp_path / "manifest.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "sign",
            "--artifact",
            str(artifact),
            "--version",
            "0.3.0",
            "--platform",
            "linux-x86_64",
            "--key",
            str(key_path),
            "--output",
            str(manifest),
        ],
    )
    signer.main()
    raw = manifest.read_bytes()
    key.public_key().verify(manifest.with_suffix(".json.sig").read_bytes(), raw)
    assert json.loads(raw)["sha256"] == hashlib.sha256(artifact.read_bytes()).hexdigest()
    with pytest.raises(FileExistsError):
        signer.main()
