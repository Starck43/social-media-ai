from __future__ import annotations

from pathlib import Path

import pytest

from local_connector.paths import PathSafetyError, iter_files, resolve_relative


def test_iter_files_excludes_hidden_sensitive_and_user_paths(tmp_path: Path) -> None:
    (tmp_path / "visible.txt").write_text("ok", encoding="utf-8")
    (tmp_path / ".hidden.txt").write_text("secret", encoding="utf-8")
    (tmp_path / ".ssh").mkdir()
    (tmp_path / ".ssh" / "id_rsa").write_text("secret", encoding="utf-8")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "pkg.js").write_text("secret", encoding="utf-8")
    (tmp_path / "private").mkdir()
    (tmp_path / "private" / "x.txt").write_text("private", encoding="utf-8")

    found = list(iter_files(tmp_path, excluded_paths=["private"]))

    assert [relative for _, relative in found] == ["visible.txt"]


def test_traversal_and_absolute_substitution_are_rejected(tmp_path: Path) -> None:
    (tmp_path / "safe.txt").write_text("safe", encoding="utf-8")
    outside = tmp_path.parent / "outside-p18-t03.txt"
    outside.write_text("outside", encoding="utf-8")
    try:
        with pytest.raises(PathSafetyError):
            resolve_relative(tmp_path, "../outside-p18-t03.txt")
        with pytest.raises(PathSafetyError):
            resolve_relative(tmp_path, str(outside))
    finally:
        outside.unlink()


def test_symlink_is_denied_even_when_it_points_inside_root(tmp_path: Path) -> None:
    target = tmp_path / "safe.txt"
    target.write_text("safe", encoding="utf-8")
    link = tmp_path / "link.txt"
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")

    with pytest.raises(PathSafetyError):
        list(iter_files(tmp_path))
    with pytest.raises(PathSafetyError):
        resolve_relative(tmp_path, "link.txt")
