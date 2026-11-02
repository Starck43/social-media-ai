"""Isolated read-only folder policy tests; no provider or database operations."""

import pytest

from app.integrations.file_workspace.policy import FileOperation, FolderRestriction, ReadOnlyFolderPolicy

__source_only__ = True


def restriction(path, *denied):
    return FolderRestriction(path, frozenset(denied))


def test_root_and_component_scoping():
    policy = ReadOnlyFolderPolicy("disk:/work")
    assert policy.permits("/work", FileOperation.LIST)
    assert policy.permits("disk:/work/report.txt", FileOperation.READ)
    assert not policy.permits("/workspace/report.txt", FileOperation.READ)
    assert not policy.permits("/other", FileOperation.LIST)
    assert not policy.permits("/", FileOperation.LIST)


@pytest.mark.parametrize("operation", [FileOperation.CREATE, FileOperation.MODIFY, FileOperation.DELETE])
def test_mutations_are_never_granted(operation):
    policy = ReadOnlyFolderPolicy("/work", (restriction("/work/reports"),))
    assert not policy.permits("/work/reports/file", operation)
    assert not policy.permits("/work", operation)


@pytest.mark.parametrize("operation", ["read", "delete", None, 1, True])
def test_untyped_or_unknown_operations_fail_closed(operation):
    assert not ReadOnlyFolderPolicy("/work").permits("/work/file", operation)


def test_child_denials_are_inherited_and_cannot_be_overridden():
    policy = ReadOnlyFolderPolicy(
        "/work",
        (
            restriction("/work/private", FileOperation.LIST, FileOperation.READ),
            restriction("/work/private/child"),
            restriction("/work/reports", FileOperation.READ),
        ),
    )
    assert policy.permits("/work/open", FileOperation.READ)
    assert not policy.permits("/work/private", FileOperation.LIST)
    assert not policy.permits("/work/private/child/file", FileOperation.READ)
    assert policy.permits("/work/private-other", FileOperation.READ)
    assert policy.permits("/work/reports", FileOperation.LIST)
    assert not policy.permits("/work/reports/file", FileOperation.READ)


@pytest.mark.parametrize(
    "path",
    [
        "relative/path",
        "disk:work/file",
        "/work/../secret",
        "/work/./file",
        "/work//file",
        "/work/file/",
        "/work/%2e%2e/secret",
        "/work/%252fsecret",
        "/work/%2Fsecret",
        "/work/100%real",
        "/work\\secret",
        "/work/\x00hidden",
        "/work/\nfile",
        "//work/file",
        "trash:/work/file",
        "https://example.com/work/file",
        "",
        None,
        42,
    ],
)
def test_ambiguous_or_untrusted_paths_are_denied(path):
    assert not ReadOnlyFolderPolicy("/work").permits(path, FileOperation.READ)


@pytest.mark.parametrize("root", ["/work/..", "work", "/work/%2f", "/work//x", "trash:/work", None])
def test_invalid_root_fails_closed_on_configuration(root):
    with pytest.raises(ValueError):
        ReadOnlyFolderPolicy(root)


def test_whole_disk_root_is_supported_without_escaping():
    policy = ReadOnlyFolderPolicy("disk:/", (restriction("/private", FileOperation.READ),))
    assert policy.permits("/work/report", FileOperation.READ)
    assert not policy.permits("/private/report", FileOperation.READ)
    assert not policy.permits("/work/../private/report", FileOperation.READ)


def test_non_ascii_names_are_not_case_folded_or_normalized():
    policy = ReadOnlyFolderPolicy("/Работа")
    assert policy.permits("/Работа/Отчёт.txt", FileOperation.READ)
    assert not policy.permits("/работа/Отчёт.txt", FileOperation.READ)


def test_invalid_restrictions_fail_the_whole_policy():
    with pytest.raises(ValueError):
        ReadOnlyFolderPolicy("/work", (restriction("/workspace/private", FileOperation.READ),))
    with pytest.raises(ValueError):
        ReadOnlyFolderPolicy("/work", (restriction("/work/../private", FileOperation.READ),))
    with pytest.raises(ValueError):
        ReadOnlyFolderPolicy("/work", (FolderRestriction("/work/private", frozenset({"read"})),))
    with pytest.raises(ValueError):
        ReadOnlyFolderPolicy("/work", (FolderRestriction("/work/private", {FileOperation.READ}),))
    with pytest.raises(ValueError):
        ReadOnlyFolderPolicy("/work", (None,))
