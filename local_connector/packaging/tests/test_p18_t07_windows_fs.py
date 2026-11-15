"""Contract and denial tests for the native Windows handle/ACL adapter.

Two clearly separated layers:

* Contract tests install a plain-Python fake in place of the Win32 table (via the
  module's ``install_api`` seam) and drive an in-memory object model. They prove
  the *logic* the adapter applies — pinned ancestors, delete-sharing denied,
  reparse/device rejection, identity and race checks, path rules, owner-only
  DACL policy — and that nothing is reachable on a non-Windows host. A fake
  cannot prove real Windows support; only the native tests below can.
* Native tests are marked to skip unless ``windows_available()`` and use the
  real ``kernel32``/``advapi32`` on ``tmp_path``: symlinks, junctions, root
  replacement, read races and real ACLs. They do **not** run on this macOS
  host, so they are reported as unrun, not as passing evidence.

No application import, database, network access or process launch in the
contract layer (the native junction/icacls tests invoke only OS utilities).
"""

from __future__ import annotations

import ctypes
import os
import sys
from ctypes import wintypes
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from local_connector.packaging import windows_fs as wf  # noqa: E402

NATIVE = pytest.mark.skipif(
    not wf.windows_available(),
    reason="native Windows handles and ACLs are required; this host is not Windows",
)

ERROR_FILE_NOT_FOUND = 2
ERROR_PATH_NOT_FOUND = 3
ERROR_ACCESS_DENIED = 5
ERROR_SHARING_VIOLATION = 32
ERROR_FILE_EXISTS = 80
ERROR_ALREADY_EXISTS = 183
ERROR_NO_MORE_FILES = 18
ERROR_INVALID_HANDLE = 6
ERROR_INSUFFICIENT_BUFFER = 122
ERROR_NOT_SUPPORTED = 50

C = wf  # short alias for constants in the fake

# One SID the fake token and every private object report as their owner.
OWNER_SID = b"\x01\x05\x00\x00\x00\x00\x00\x05\x15\x00\x00\x00\x11\x22\x33\x44\x00\x00\x00\x00\x05\x12\x00\x00\x00"


class Failure(Exception):
    """Raised inside the fake to imitate a Win32 call that returns FALSE."""

    def __init__(self, code: int) -> None:
        self.code = code


class Node:
    """One in-memory filesystem object."""

    __slots__ = ("name", "directory", "reparse", "device", "data", "identity", "serial", "mtime", "owner_only")

    def __init__(
        self,
        name,
        *,
        directory=False,
        reparse=False,
        device=False,
        data=b"",
        serial=0x1234,
        identity=1,
        owner_only=False,
    ):
        self.name = name
        self.directory = directory
        self.reparse = reparse
        self.device = device
        self.data = data
        self.serial = serial
        self.identity = identity
        self.mtime = 0x01D0000000000000 + identity
        # Whether this object carries the protected owner-only DACL the adapter
        # creates; profile ancestors (C:\Users\...) are False by construction.
        self.owner_only = owner_only

    def clone(self, **changes):
        other = Node(self.name, serial=self.serial, identity=self.identity)
        other.directory, other.reparse = self.directory, self.reparse
        other.device, other.data, other.mtime = self.device, self.data, self.mtime
        other.owner_only = self.owner_only
        for key, value in changes.items():
            setattr(other, key, value)
        return other


class _Opened:
    __slots__ = ("node", "path", "access", "share", "read_offset")

    def __init__(self, node, path, access, share):
        self.node, self.path, self.access, self.share = node, path, access, share
        self.read_offset = 0


class FakeWin32:
    """Plain-Python Win32 table; method signatures mirror the real prototypes."""

    def __init__(self, tree, *, drive_type=3, owner_sid=OWNER_SID, acl_ok=True):
        self.tree = {key.upper(): node for key, node in tree.items()}
        self.drive_type = drive_type
        self.owner_sid = owner_sid
        self.acl_ok = acl_ok
        self.handles = {}
        self.closed = []
        self.counter = 0x1000
        self.create_calls = []
        self.find_state = {}
        self.find_entries = {}
        self.find_index = {}
        self.find_counter = 0x5000
        self.drives_checked = []
        self.directory_created = []
        self.sddls = []
        self.written = []
        self.error = 0
        self.on_resolve = None
        self.on_read = None
        self.on_acl = None
        self.on_get_security = None
        self.keep_alive = []
        self.read_only_access = None  # emulate an ACL that grants fewer rights
        self._security_node = None  # node behind the last GetSecurityInfo call

    # -- helpers ---------------------------------------------------------
    def _handle(self, record):
        self.counter += 1
        self.handles[self.counter] = record
        return self.counter

    def _resolve(self, path, *, creation):
        key = str(path).upper()
        if self.on_resolve is not None:
            self.on_resolve(key, creation)
        return key, self.tree.get(key)

    def _new_node(self, key, *, directory, owner_only):
        identity = max((node.identity for node in self.tree.values()), default=0) + 1
        node = Node(key.rsplit("\\", 1)[-1], directory=directory, identity=identity, owner_only=owner_only)
        return node

    # -- kernel32 --------------------------------------------------------
    def CreateFileW(self, path, access, share, security, creation, flags, template):
        self.create_calls.append((str(path).upper(), access, share, creation, flags))
        key, existing = self._resolve(path, creation=creation)
        if self.read_only_access is not None and existing is not None and access != 0x02000000:
            if access & ~self.read_only_access:
                # Real Win32 reports the denial through the return value and the
                # last error, so the adapter can retry with MAXIMUM_ALLOWED.
                self.error = ERROR_ACCESS_DENIED
                return C.INVALID_HANDLE_VALUE
        if existing is not None:
            if creation == C.CREATE_NEW:
                self.error = ERROR_ALREADY_EXISTS
                return C.INVALID_HANDLE_VALUE
            # A share mode asks for what its holder denies: ERROR_SHARING_VIOLATION
            # whenever requested and granted rights do not overlap.
            for other in self.handles.values():
                if other.path != key:
                    continue
                if (~other.share & share & 0x7) or (~share & other.share & 0x7):
                    raise Failure(ERROR_SHARING_VIOLATION)
            return self._handle(_Opened(existing, key, access, share))
        parent = self.tree.get(key.rsplit("\\", 1)[0])
        if creation != C.CREATE_NEW or parent is None or not parent.directory:
            self.error = ERROR_FILE_NOT_FOUND if parent is not None else ERROR_PATH_NOT_FOUND
            return C.INVALID_HANDLE_VALUE
        node = self._new_node(key, directory=False, owner_only=True)
        self.tree[key] = node
        return self._handle(_Opened(node, key, access, share))

    def ReadFile(self, handle, buffer, size, count, overlapped):
        record = self.handles.get(handle)
        if record is None:
            raise Failure(ERROR_INVALID_HANDLE)
        # MAXIMUM_ALLOWED grants whatever the ACL allows, so it includes reading.
        if not (record.access & C.FILE_READ_DATA or record.access == 0x02000000):
            raise Failure(ERROR_ACCESS_DENIED)
        data = record.node.data
        offset = record.read_offset
        chunk = data[offset : offset + int(size)]
        ctypes.memmove(buffer, chunk, len(chunk))
        count._obj.value = len(chunk)
        record.read_offset = offset + len(chunk)
        if self.on_read is not None:
            self.on_read(record.path)
        return 1

    def WriteFile(self, handle, buffer, size, count, overlapped):
        record = self.handles.get(handle)
        if record is None:
            raise Failure(ERROR_INVALID_HANDLE)
        if not record.access & C.FILE_WRITE_DATA:
            raise Failure(ERROR_ACCESS_DENIED)
        raw = ctypes.string_at(buffer, int(size))
        self.written.append(raw)
        self.tree[record.path] = record.node.clone(data=record.node.data + raw)
        record.node = self.tree[record.path]
        count._obj.value = int(size)
        return 1

    def CloseHandle(self, handle):
        # Real Win32 takes a plain handle value, not a ctypes box.
        key = getattr(handle, "value", handle)
        try:
            key = int(key)
        except (TypeError, ValueError):
            pass
        record = self.handles.pop(key, None)
        if record is not None:
            self.closed.append(record)
        return 1

    def GetFileInformationByHandle(self, handle, info):
        record = self.handles.get(handle)
        if record is None:
            raise Failure(ERROR_INVALID_HANDLE)
        node = record.node
        out = info._obj
        out.dwFileAttributes = (C.FILE_ATTRIBUTE_DIRECTORY if node.directory else 0) | (
            C.FILE_ATTRIBUTE_REPARSE_POINT if node.reparse else 0
        )
        out.ftLastWriteTime.dwLowDateTime = node.mtime & 0xFFFFFFFF
        out.ftLastWriteTime.dwHighDateTime = node.mtime >> 32
        out.ftCreationTime.dwLowDateTime = node.identity & 0xFFFFFFFF
        out.ftCreationTime.dwHighDateTime = node.identity >> 32
        out.dwVolumeSerialNumber = node.serial
        out.nFileSizeLow = len(node.data) & 0xFFFFFFFF
        out.nFileSizeHigh = 0
        out.nNumberOfLinks = 1
        out.nFileIndexLow = node.identity & 0xFFFFFFFF
        out.nFileIndexHigh = node.identity >> 32
        return 1

    def GetFileInformationByHandleEx(self, handle, info_class, out, size):
        record = self.handles.get(handle)
        if record is None:
            raise Failure(ERROR_INVALID_HANDLE)
        node = record.node
        if info_class == C.FILE_ATTRIBUTE_INFO_CLASS:
            target = ctypes.cast(out, ctypes.POINTER(C.FILE_ATTRIBUTE_TAG_INFO)).contents
            target.FileAttributes = (C.FILE_ATTRIBUTE_DIRECTORY if node.directory else 0) | (
                C.FILE_ATTRIBUTE_REPARSE_POINT if node.reparse else 0
            )
            target.ReparseTag = 0xA0000003 if node.reparse else 0
            return 1
        if info_class == C.FILE_STANDARD_INFO_CLASS:
            target = ctypes.cast(out, ctypes.POINTER(C.FILE_STANDARD_INFO)).contents
            target.EndOfFile = len(node.data)
            target.NumberOfLinks = 1
            target.DeletePending = 0
            target.Directory = 1 if node.directory else 0
            return 1
        raise Failure(ERROR_INVALID_HANDLE)

    def GetFileType(self, handle):
        record = self.handles.get(handle)
        if record is None:
            raise Failure(ERROR_INVALID_HANDLE)
        return 0 if record.node.device else C.FILE_TYPE_DISK

    def GetFileAttributesW(self, path):
        node = self.tree.get(str(path).upper())
        if node is None:
            self.error = ERROR_FILE_NOT_FOUND
            return C.INVALID_FILE_ATTRIBUTES
        return (C.FILE_ATTRIBUTE_DIRECTORY if node.directory else 0) | (
            C.FILE_ATTRIBUTE_REPARSE_POINT if node.reparse else 0
        )

    def FindFirstFileW(self, query, data):
        base = str(query).upper().rsplit("\\*", 1)[0]
        node = self.tree.get(base)
        if node is None or not node.directory:
            self.error = ERROR_FILE_NOT_FOUND
            return C.INVALID_HANDLE_VALUE
        prefix = base + "\\"
        entries = [
            self.tree[key] for key in sorted(self.tree) if key.startswith(prefix) and "\\" not in key[len(prefix) :]
        ]
        self.find_counter += 1
        self.find_entries[self.find_counter] = [Node(".", directory=True), Node("..", directory=True), *entries]
        self.find_index[self.find_counter] = 3
        self._fill(entries[0] if entries else Node(".", directory=True), data)
        return self.find_counter

    def FindNextFileW(self, handle, data):
        entries = self.find_entries.get(handle)
        index = self.find_index.get(handle)
        if entries is None or index is None:
            raise Failure(ERROR_INVALID_HANDLE)
        if index >= len(entries):
            self.error = ERROR_NO_MORE_FILES
            return 0
        self._fill(entries[index], data)
        self.find_index[handle] = index + 1
        return 1

    def FindClose(self, handle):
        self.find_state.pop(handle, None)
        self.find_entries.pop(handle, None)
        self.find_index.pop(handle, None)
        return 1

    def _fill(self, node, data):
        out = data._obj
        out.cFileName = node.name
        out.dwFileAttributes = (C.FILE_ATTRIBUTE_DIRECTORY if node.directory else 0) | (
            C.FILE_ATTRIBUTE_REPARSE_POINT if node.reparse else 0
        )

    def GetDriveTypeW(self, drive):
        self.drives_checked.append(str(drive))
        return self.drive_type

    def CreateDirectoryW(self, path, security):
        key = str(path).upper()
        self.directory_created.append(key)
        if key in self.tree:
            self.error = ERROR_ALREADY_EXISTS
            return 0
        parent = self.tree.get(key.rsplit("\\", 1)[0])
        if parent is None or not parent.directory:
            self.error = ERROR_PATH_NOT_FOUND
            return 0
        node = self._new_node(key, directory=True, owner_only=True)
        self.tree[key] = node
        self.directory_created.append(key)
        return 1

    def SetFileInformationByHandle(self, handle, info_class, payload, size):
        record = self.handles.get(handle)
        if record is None:
            raise Failure(ERROR_INVALID_HANDLE)
        if info_class == C.FILE_DISPOSITION_INFO_CLASS:
            self.tree.pop(record.path, None)
            return 1
        if info_class == C.FILE_DISPOSITION_INFO_EX_CLASS:
            self.tree.pop(record.path, None)
            return 1
        raise Failure(ERROR_INVALID_HANDLE)

    def GetCurrentProcess(self):
        return 0xFFFFFFFFFFFFFFFF

    def OpenProcessToken(self, process, access, token):
        token._obj.value = 0x7777
        return 1

    def GetTokenInformation(self, token, info_class, out, length, return_length):
        # ``TOKEN_USER`` is one caller-owned buffer: a pointer-sized SID pointer
        # followed by the SID itself, both inside ``out``.
        total = ctypes.sizeof(wintypes.LPVOID) + len(self.owner_sid)
        if not out:
            return_length._obj.value = total
            self.error = ERROR_INSUFFICIENT_BUFFER
            return 0
        if int(length) < total:
            return_length._obj.value = total
            self.error = ERROR_INSUFFICIENT_BUFFER
            return 0
        buffer = ctypes.cast(out, ctypes.POINTER(ctypes.c_char * total)).contents
        sid_offset = ctypes.sizeof(wintypes.LPVOID)
        ctypes.memmove(ctypes.addressof(buffer) + sid_offset, self.owner_sid, len(self.owner_sid))
        pointer_slot = ctypes.cast(buffer, ctypes.POINTER(wintypes.LPVOID))
        pointer_slot.contents.value = ctypes.addressof(buffer) + sid_offset
        return_length._obj.value = total
        return 1

    def GetLengthSid(self, sid):
        return len(self.owner_sid)

    def IsValidSid(self, sid):
        return bool(sid)

    def EqualSid(self, first, second):
        if self.on_acl is not None:
            self.on_acl()
        return bool(first) and bool(second)

    def ConvertSidToStringSidW(self, sid, out):
        buffer = ctypes.create_unicode_buffer("S-1-5-21-1-2-3-1001")
        self.keep_alive.append(buffer)
        out._obj.value = ctypes.addressof(buffer)
        return 1

    def LocalFree(self, value):
        return 0

    def ConvertStringSecurityDescriptorToSecurityDescriptorW(self, sddl, revision, out, size):
        self.sddls.append(str(sddl))
        buffer = ctypes.create_string_buffer(64)
        self.keep_alive.append(buffer)
        out._obj.value = ctypes.addressof(buffer)
        size._obj.value = 64
        return 1

    def IsValidSecurityDescriptor(self, descriptor):
        return bool(descriptor)

    def GetSecurityInfo(self, handle, obj_type, info, owner, group, dacl, sacl, descriptor):
        if not self.acl_ok:
            self.error = ERROR_ACCESS_DENIED
            return ERROR_ACCESS_DENIED
        record = self.handles.get(handle)
        if record is None:
            raise Failure(ERROR_INVALID_HANDLE)
        if self.on_get_security is not None:
            self.on_get_security(record.path)
        self._security_node = record.node
        descriptor._obj.value = 0x1000
        owner._obj.value = 0x2000
        return 0

    def GetSecurityDescriptorControl(self, descriptor, control, revision):
        # A node that is not owner-only carries an inherited, unprotected DACL:
        # it must never be accepted as a private cache root.
        node = self._security_node
        control._obj.value = (
            C.SE_DACL_PROTECTED | C.SE_DACL_PRESENT if node is not None and node.owner_only else C.SE_DACL_PRESENT
        )
        revision._obj.value = 1
        return 1

    def GetSecurityDescriptorDacl(self, descriptor, present, dacl, defaulted):
        present._obj.value = 1
        dacl._obj.value = 0x3000
        defaulted._obj.value = 0
        return 1

    def GetAclInformation(self, dacl, info, size, info_class):
        target = ctypes.cast(info, ctypes.POINTER(C.ACL_SIZE_INFORMATION)).contents
        target.AceCount = 1
        target.AclBytesInUse = 32
        target.AclBytesFree = 0
        return 1

    def GetAce(self, dacl, index, ace):
        buffer = ctypes.create_string_buffer(ctypes.sizeof(C.ACCESS_ALLOWED_ACE) + 16)
        self.keep_alive.append(buffer)
        view = ctypes.cast(buffer, ctypes.POINTER(C.ACCESS_ALLOWED_ACE)).contents
        view.Header.AceType = C.ACCESS_ALLOWED_ACE_TYPE
        view.Header.AceFlags = C.PRIVATE_DIRECTORY_INHERITANCE
        view.Header.AceSize = ctypes.sizeof(C.ACCESS_ALLOWED_ACE) + 16
        view.Mask = C.PRIVATE_DIRECTORY_ACCESS
        ace._obj.value = ctypes.addressof(buffer)
        return 1


def default_tree():
    """Profile-shaped tree: ancestors exist but are NOT owner-private.

    The selected root is ``C:\\Users\\A\\Docs`` so the root's own listing has
    no profile noise; the ancestors above it exercise the private-chain rules.
    """

    return {
        # The drive root is the first pinned component of every walk.
        "C:\\": Node("C:\\", directory=True, identity=0),
        "C:\\USERS": Node("Users", directory=True, identity=1),
        "C:\\USERS\\A": Node("A", directory=True, identity=2),
        "C:\\USERS\\A\\APPDATA": Node("AppData", directory=True, identity=3),
        "C:\\USERS\\A\\APPDATA\\LOCAL": Node("Local", directory=True, identity=4),
        "C:\\USERS\\A\\DOCS": Node("Docs", directory=True, identity=5),
        "C:\\USERS\\A\\DOCS\\REPORT.TXT": Node("report.txt", data=b"hello world", identity=6),
    }


ROOT = r"C:\Users\A\Docs"


@pytest.fixture()
def windows(monkeypatch):
    """Pretend to be Windows and install a fake Win32 table for one test."""

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(os, "name", "nt")
    monkeypatch.setattr(wf, "windows_available", lambda: True)
    monkeypatch.setattr(wf, "_require_windows", lambda: None)
    tables = []

    def install(tree, **kwargs):
        table = FakeWin32(tree, **kwargs)
        tables.append(table)
        wf.install_api(table)
        monkeypatch.setattr(wf, "_last_error", lambda: table.error)
        return table

    yield install
    wf.install_api(None)


def _call(fn, *args, **kwargs):
    """Invoke an adapter entry point, converting raw fake failures to denials."""

    try:
        return fn(*args, **kwargs)
    except Failure as failure:
        raise wf._denial_for_code(failure.code) from None


# --------------------------------------------------------------------------- host gate


def test_module_imports_on_posix_and_exposes_prototypes():
    prototypes = wf.windows_api_signatures()
    assert "CreateFileW" in prototypes and "ReadFile" in prototypes
    assert wf.INVALID_HANDLE_VALUE == -1
    assert wf.FILE_FLAG_OPEN_REPARSE_POINT == 0x00200000
    assert not wf.PINNED_SHARE & 0x00000004, "delete sharing must never be requested"


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX fail-closed check")
def test_every_entry_point_fails_closed_on_posix():
    calls = [
        lambda: wf.open_pinned_root(r"C:\Users\A"),
        lambda: wf.pinned_root_identity(r"C:\Users\A"),
        lambda: wf.verify_pinned_root(r"C:\Users\A", wf.RootIdentity(1, 2)),
        lambda: wf.read_relative_file(r"C:\Users\A", "x.txt", max_bytes=10),
        lambda: wf.list_root(r"C:\Users\A"),
        lambda: next(iter(wf.iter_root_files(r"C:\Users\A"))),
        lambda: wf.ensure_private_directory(r"C:\Cache"),
        lambda: wf.create_private_directory(r"C:\Cache", "sub"),
        lambda: wf.open_private_file(r"C:\Cache", "state.bin"),
        lambda: wf.create_private_file(r"C:\Cache", "state.bin", b"x"),
        lambda: wf.delete_private_file(r"C:\Cache", "state.bin"),
        lambda: wf.PrivateCacheRoot(r"C:\Cache"),
    ]
    for call in calls:
        with pytest.raises(wf.WindowsFsError):
            call()
    assert wf.windows_available() is False


def test_spoofed_platform_without_kernel32_denies(monkeypatch):
    """``win32`` must not be believed unless the Win32 libraries really load."""

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(os, "name", "nt")
    monkeypatch.setattr(wf, "_KERNEL32", None)
    monkeypatch.setattr(wf, "_ADVAPI32", None)

    def refuse(*_args, **_kwargs):
        raise OSError("kernel32 not loadable")

    # ``ctypes.WinDLL`` does not exist on POSIX, so the loader is patched on
    # the module's own binding seam instead of on the ctypes attribute.
    real_windll = getattr(ctypes, "WinDLL", None)
    monkeypatch.setattr(ctypes, "WinDLL", refuse, raising=False)
    try:
        assert wf.windows_available() is False
        with pytest.raises(wf.WindowsUnavailableError):
            wf.open_pinned_root(r"C:\Users\A")
    finally:
        if real_windll is None:
            monkeypatch.delattr(ctypes, "WinDLL", raising=False)
        else:
            monkeypatch.setattr(ctypes, "WinDLL", real_windll)


# --------------------------------------------------------------------------- identity


def test_root_identity_is_bijective_over_the_64_bit_index():
    for raw in (0, 1, 2**32, 2**63 - 1, 2**63, 2**64 - 1):
        original = wf.RootIdentity(7, raw)
        signed = original.stat_inode
        # The stored value must always be a plain SQLite INTEGER and must
        # rebuild the *same* identity, never a neighbouring one.
        assert -(2**63) <= signed < 2**63
        restored = wf.RootIdentity.from_approved(7, signed)
        assert restored == original
        assert restored.file_index == raw
    assert wf.RootIdentity(7, 2**63 + 5).stat_inode == -(2**63) + 5


@pytest.mark.parametrize("volume,index", [(2**32, 1), (-1, 1), (1, 2**64), (1.5, 1), (1, "2"), (1, None)])
def test_root_identity_rejects_out_of_range_values(volume, index):
    with pytest.raises(wf.WindowsFsError):
        wf.RootIdentity(volume, index)


# --------------------------------------------------------------------------- path rules


@pytest.mark.parametrize(
    "root",
    [
        r"\\server\share",
        r"\\?\C:\Users",
        r"\\.\C:\Users",
        r"C:relative",
        "relative",
        "",
        r"C:\..\Windows",
        r"C:\Users\..\Windows",
        r"C:\Users\a.",
        r"C:\Users\a ",
        r"C:\Users\bad<name>",
        r"C:\Users\a:b",
        r"C:\Users\CON",
        r"C:\Users\nul.txt",
        r"C:\Users\com1",
        "C:",
        "C:\\",
        "C:\\" + "x" * 300,
    ],
)
def test_network_device_relative_and_alias_roots_deny(root, windows):
    windows(default_tree())
    with pytest.raises(wf.WindowsFsError):
        _call(wf.pinned_root_identity, root)


def test_mapped_network_drive_denies(windows):
    windows(default_tree(), drive_type=wf.DRIVE_REMOTE)
    with pytest.raises(wf.WindowsFsError) as denied:
        _call(wf.pinned_root_identity, r"C:\Users\A")
    assert denied.value.reason == wf.deny_network()


@pytest.mark.parametrize(
    "relative",
    [
        r"..\secret.txt",
        "../secret.txt",
        r"/etc/passwd",
        r"\windows\win.ini",
        "C:/x",
        r"a\..\b",
        "a/../b",
        "a:b",
        "a\\b/c",
        "",
        ".",
        "..",
        "a.",
        "a ",
        "a\x00b",
        "x|y",
        "x" * 300,
    ],
)
def test_unsafe_relative_paths_deny(relative, windows):
    windows(default_tree())
    with pytest.raises(wf.WindowsFsError):
        _call(wf.read_relative_file, r"C:\Users\A", relative, max_bytes=64)


def test_root_normalisation_accepts_case_and_forward_slashes(windows):
    table = windows(default_tree())
    handle, identity = _call(wf.open_pinned_root, "c:/users/a/docs")
    handle.close()
    assert identity.as_tuple() == (0x1234, 5)
    assert table.drives_checked[-1] == "C:\\"


def test_walker_relative_paths_are_accepted_unchanged(windows):
    tree = default_tree()
    tree["C:\\USERS\\A\\DOCS\\REPORT.V2.TXT"] = Node("report.v2.txt", data=b"v2", identity=9)
    windows(tree)
    assert _call(wf.read_relative_file, r"C:\Users\A", "Docs\\report.v2.txt", max_bytes=64) == b"v2"


# --------------------------------------------------------------------------- pinning


def test_opens_never_share_delete_and_always_open_the_reparse_point(windows):
    table = windows(default_tree())
    handle, _ = _call(wf.open_pinned_root, r"C:\Users\A\Docs")
    handle.close()
    assert table.create_calls
    for path, _access, share, _creation, flags in table.create_calls:
        assert not share & 0x00000004, f"delete was shared for {path}"
        assert flags & wf.FILE_FLAG_OPEN_REPARSE_POINT


def test_root_identity_mismatch_denies(windows):
    windows(default_tree())
    with pytest.raises(wf.WindowsFsError) as denied:
        _call(wf.verify_pinned_root, r"C:\Users\A", wf.RootIdentity(0x1234, 999))
    assert denied.value.reason == wf.deny_identity()


def test_root_identity_match_passes(windows):
    windows(default_tree())
    _call(wf.verify_pinned_root, r"C:\Users\A", wf.RootIdentity(0x1234, 2))


def test_root_replacement_is_detected(windows):
    table = windows(default_tree())
    table.on_resolve = lambda key, creation: (
        table.tree.__setitem__("C:\\USERS\\A", table.tree["C:\\USERS\\A"].clone(identity=777))
        if key == "C:\\USERS\\A" and creation == wf.OPEN_EXISTING
        else None
    )
    with pytest.raises(wf.WindowsFsError) as denied:
        _call(wf.verify_pinned_root, r"C:\Users\A", wf.RootIdentity(0x1234, 2))
    assert denied.value.reason == wf.deny_identity()


def test_ancestor_retarget_during_read_denies(windows):
    """A directory replaced while the file is read must deny the read.

    Windows has no ``openat``, so the adapter cannot prevent the replacement;
    it detects it by resolving the ancestor path again after the read and
    comparing the directory identity.
    """

    table = windows(default_tree())
    switched = {"done": False}

    def retarget(path):
        if not switched["done"]:
            switched["done"] = True
            table.tree["C:\\USERS\\A\\DOCS"] = table.tree["C:\\USERS\\A\\DOCS"].clone(identity=4242)

    table.on_read = retarget
    with pytest.raises(wf.WindowsFsError):
        _call(wf.read_relative_file, r"C:\Users\A", r"Docs\report.txt", max_bytes=64)


# --------------------------------------------------------------------------- reparse/device


def test_reparse_root_denies(windows):
    tree = default_tree()
    tree["C:\\USERS\\A"] = tree["C:\\USERS\\A"].clone(reparse=True)
    windows(tree)
    with pytest.raises(wf.WindowsFsError):
        _call(wf.open_pinned_root, r"C:\Users\A")


def test_junction_component_denies(windows):
    tree = default_tree()
    tree["C:\\USERS\\A\\LINK"] = Node("link", reparse=True)
    windows(tree)
    with pytest.raises(wf.WindowsFsError):
        _call(wf.read_relative_file, r"C:\Users\A", r"link\secret.txt", max_bytes=64)


def test_symlinked_file_denies(windows):
    tree = default_tree()
    tree["C:\\USERS\\A\\LINK.TXT"] = Node("link.txt", reparse=True, data=b"secret")
    windows(tree)
    with pytest.raises(wf.WindowsFsError):
        _call(wf.read_relative_file, r"C:\Users\A", "link.txt", max_bytes=64)


def test_device_object_denies(windows):
    tree = default_tree()
    tree["C:\\USERS\\A\\NUL"] = Node("NUL", device=True)
    windows(tree)
    with pytest.raises(wf.WindowsFsError):
        _call(wf.read_relative_file, r"C:\Users\A", "NUL", max_bytes=64)


def test_reparse_entry_denies_listing(windows):
    tree = default_tree()
    tree["C:\\USERS\\A\\DOCS\\JUNCTION"] = Node("junction", reparse=True)
    windows(tree)
    with pytest.raises(wf.WindowsFsError) as denied:
        _call(wf.list_root, r"C:\Users\A\Docs")
    assert denied.value.reason == wf.deny_unsafe()


# --------------------------------------------------------------------------- reads


def test_bounded_read_returns_bytes(windows):
    windows(default_tree())
    assert _call(wf.read_relative_file, r"C:\Users\A", r"Docs\report.txt", max_bytes=64) == b"hello world"


def test_oversize_file_denies_before_reading(windows):
    table = windows(default_tree())
    with pytest.raises(wf.WindowsFsError):
        _call(wf.read_relative_file, r"C:\Users\A", r"Docs\report.txt", max_bytes=4)
    assert not any(record.access & wf.FILE_READ_DATA for record in table.closed)


@pytest.mark.parametrize("bound", [-1, True, 2.5, "16", None])
def test_invalid_bounds_deny(bound, windows):
    windows(default_tree())
    with pytest.raises(wf.WindowsFsError):
        _call(wf.read_relative_file, r"C:\Users\A", r"Docs\report.txt", max_bytes=bound)


def test_missing_file_maps_to_not_found(windows):
    windows(default_tree())
    with pytest.raises(wf.WindowsFsError) as denied:
        _call(wf.read_relative_file, r"C:\Users\A", "absent.txt", max_bytes=64)
    assert denied.value.reason == wf.deny_not_found()


def test_sharing_violation_maps_to_permission(windows):
    table = windows(default_tree())

    def blocked(key, creation):
        if key.endswith("REPORT.TXT"):
            raise Failure(ERROR_SHARING_VIOLATION)

    table.on_resolve = blocked
    with pytest.raises(wf.WindowsFsError) as denied:
        _call(wf.read_relative_file, r"C:\Users\A", r"Docs\report.txt", max_bytes=64)
    assert denied.value.reason == wf.deny_permission()


def test_identity_change_during_read_denies(windows):
    table = windows(default_tree())

    def tamper(_path):
        original = table.tree["C:\\USERS\\A\\DOCS\\REPORT.TXT"]
        table.tree["C:\\USERS\\A\\DOCS\\REPORT.TXT"] = original.clone(data=b"evil payload")

    table.on_read = tamper
    with pytest.raises(wf.WindowsFsError):
        _call(wf.read_relative_file, r"C:\Users\A", r"Docs\report.txt", max_bytes=64)


def test_swap_after_read_denies(windows):
    table = windows(default_tree())
    state = {"read": False}

    def swap(_path):
        if not state["read"]:
            state["read"] = True
            original = table.tree["C:\\USERS\\A\\DOCS\\REPORT.TXT"]
            table.tree["C:\\USERS\\A\\DOCS\\REPORT.TXT"] = original.clone(identity=5150)

    table.on_read = swap
    with pytest.raises(wf.WindowsFsError):
        _call(wf.read_relative_file, r"C:\Users\A", r"Docs\report.txt", max_bytes=64)


def test_denials_do_not_leak_handles(windows):
    table = windows(default_tree())
    with pytest.raises(wf.WindowsFsError):
        _call(wf.read_relative_file, r"C:\Users\A", r"Docs\report.txt", max_bytes=1)
    assert not table.handles


# --------------------------------------------------------------------------- listing


def test_listing_is_sorted_and_typed(windows):
    tree = default_tree()
    tree["C:\\USERS\\A\\DOCS\\B.TXT"] = Node("b.txt", data=b"b", identity=7)
    tree["C:\\USERS\\A\\DOCS\\A.TXT"] = Node("a.txt", data=b"a", identity=8)
    tree["C:\\USERS\\A\\DOCS\\SUB"] = Node("Sub", directory=True, identity=9)
    windows(tree)
    assert _call(wf.list_root, ROOT) == [
        ("a.txt", False),
        ("b.txt", False),
        ("report.txt", False),
        ("Sub", True),
    ]


def test_iteration_scope_and_budget(windows):
    tree = default_tree()
    tree["C:\\USERS\\A\\DOCS\\B.TXT"] = Node("b.txt", data=b"b", identity=7)
    tree["C:\\USERS\\A\\DOCS\\SUB"] = Node("Sub", directory=True, identity=9)
    tree["C:\\USERS\\A\\DOCS\\SUB\\INNER.TXT"] = Node("inner.txt", data=b"i", identity=10)
    windows(tree)
    # Each level is walked in case-folded name order, exactly like the POSIX
    # scaffold: files and directories appear in the order of their names.
    assert list(_call(wf.iter_root_files, ROOT)) == ["b.txt", "report.txt", "Sub\\inner.txt"]
    assert list(_call(wf.iter_root_files, ROOT, recursive=False)) == ["b.txt", "report.txt"]
    with pytest.raises(wf.WindowsFsError):
        list(_call(wf.iter_root_files, ROOT, max_entries=2))


def test_iteration_denies_on_reparse(windows):
    tree = default_tree()
    tree["C:\\USERS\\A\\DOCS\\JUNCTION"] = Node("junction", reparse=True)
    windows(tree)
    with pytest.raises(wf.WindowsFsError):
        list(_call(wf.iter_root_files, ROOT))


def test_iteration_never_traverses_an_excluded_subtree(windows):
    """An excluded/reparse-heavy directory must not be opened at all."""

    tree = default_tree()
    tree["C:\\USERS\\A\\DOCS\\.SSH"] = Node(".ssh", directory=True, identity=21)
    tree["C:\\USERS\\A\\DOCS\\.SSH\\ID_RSA"] = Node("id_rsa", data=b"secret", identity=22)
    tree["C:\\USERS\\A\\DOCS\\NODE_MODULES"] = Node("node_modules", directory=True, identity=23)
    tree["C:\\USERS\\A\\DOCS\\NODE_MODULES\\PKG.JS"] = Node("pkg.js", data=b"x", identity=24)
    tree["C:\\USERS\\A\\DOCS\\CACHE"] = Node("Cache", directory=True, identity=25)
    tree["C:\\USERS\\A\\DOCS\\CACHE\\.A"] = Node(".a", directory=True, identity=26)
    tree["C:\\USERS\\A\\DOCS\\CACHE\\.A\\INDEX.SQLITE"] = Node("index.sqlite", data=b"db", identity=27)
    tree["C:\\USERS\\A\\DOCS\\CACHE\\SAFE.TXT"] = Node("safe.txt", data=b"ok", identity=28)
    table = windows(tree)
    found = list(
        _call(
            wf.iter_root_files,
            ROOT,
            excluded_names=[".ssh", "node_modules"],
            excluded_paths=[r"cache\.a"],
        )
    )
    assert found == ["Cache\\safe.txt", "report.txt"]
    opened = {path for path, _access, _share, _creation, _flags in table.create_calls}
    assert "C:\\USERS\\A\\DOCS\\.SSH\\ID_RSA" not in opened
    assert "C:\\USERS\\A\\DOCS\\NODE_MODULES\\PKG.JS" not in opened
    assert "C:\\USERS\\A\\DOCS\\CACHE\\.A\\INDEX.SQLITE" not in opened
    assert "C:\\USERS\\A\\DOCS\\CACHE" in opened


def test_iteration_still_denies_on_a_visible_reparse_pointer(windows):
    tree = default_tree()
    tree["C:\\USERS\\A\\DOCS\\JUNCTION"] = Node("junction", reparse=True)
    windows(tree)
    with pytest.raises(wf.WindowsFsError):
        list(_call(wf.iter_root_files, r"C:\Users\A", excluded_names=[".ssh"]))


# --------------------------------------------------------------------------- private cache


def test_profile_ancestors_are_pinned_but_never_demanded_private(windows):
    """C:\\Users\\A\\AppData is not owner-private and must not deny the cache."""

    table = windows(default_tree())
    root = r"C:\Users\A\AppData\Local\AI Assistant Connector"
    assert _call(wf.ensure_private_directory, root) == root
    assert "C:\\USERS\\A\\APPDATA\\LOCAL\\AI ASSISTANT CONNECTOR" in table.directory_created
    # The ancestors were opened (nofollow, no delete share) but no ACL refusal
    # was raised for them even though only the leaf is owner-only.
    assert all(not share & wf.FILE_SHARE_DELETE for _path, _a, share, _c, _f in table.create_calls)


def test_existing_private_chain_is_reused_not_recreated(windows):
    tree = default_tree()
    tree["C:\\USERS\\A\\APPDATA\\LOCAL\\AI ASSISTANT CONNECTOR"] = Node(
        "AI Assistant Connector", directory=True, identity=31, owner_only=True
    )
    table = windows(tree)
    root = r"C:\Users\A\AppData\Local\AI Assistant Connector"
    assert _call(wf.ensure_private_directory, root) == root
    assert not [key for key in table.directory_created if "AI ASSISTANT CONNECTOR" in key]


def test_private_directories_use_owner_only_sddl(windows):
    table = windows(default_tree())
    root = r"C:\Users\A\AppData\Local\Connector"
    assert _call(wf.ensure_private_directory, root) == root
    descriptors = [item for item in table.sddls if item.startswith("D:P")]
    assert descriptors, "creating a cache directory must apply a descriptor"
    for sddl in descriptors:
        assert sddl.startswith("D:P(A;OICI;FA;;;")
        assert ";;;WD)" not in sddl and ";;;BU)" not in sddl and ";;;AN)" not in sddl


def test_world_writable_existing_private_root_denies(windows):
    """An existing cache leaf that is not owner-only denies, like POSIX 0o755."""

    tree = default_tree()
    tree["C:\\USERS\\A\\APPDATA\\CONNECTOR"] = Node("Connector", directory=True, identity=41, owner_only=False)
    windows(tree)
    with pytest.raises(wf.WindowsFsError) as denied:
        _call(wf.ensure_private_directory, r"C:\Users\A\AppData\Connector")
    assert denied.value.reason == wf.deny_insecure_cache()


def test_acl_lookup_failure_denies(windows):
    windows(default_tree(), acl_ok=False)
    with pytest.raises(wf.WindowsFsError):
        _call(wf.ensure_private_directory, r"C:\Users\A\AppData\Connector")


@pytest.mark.parametrize("root", [r"\\server\share\cache", r"relative\cache", r"\\.\C:\cache", r"C:\..\cache"])
def test_private_root_rejects_unsafe_forms(root, windows):
    windows(default_tree())
    with pytest.raises(wf.WindowsFsError):
        _call(wf.ensure_private_directory, root)


def test_create_private_directory_returns_nested_path(windows):
    windows(default_tree())
    created = _call(wf.create_private_directory, r"C:\Users\A\AppData\Local\Connector", "installer-state")
    assert created == r"C:\Users\A\AppData\Local\Connector\installer-state"
    with pytest.raises(wf.WindowsFsError):
        _call(wf.create_private_directory, r"C:\Users\A\AppData\Local\Connector", r"a\b")
    with pytest.raises(wf.WindowsFsError):
        _call(wf.create_private_directory, r"C:\Users\A\AppData\Local\Connector", "installer-state")


def test_private_file_roundtrip(windows):
    table = windows(default_tree())
    root = r"C:\Users\A\AppData\Local\Connector"
    _call(wf.create_private_file, root, "state.bin", b"payload")
    assert b"payload" in table.written
    handle = _call(wf.open_private_file, root, "state.bin")
    handle.close()
    with pytest.raises(wf.WindowsFsError):
        _call(wf.open_private_file, root, "absent.bin")


def test_private_file_never_overwrites(windows):
    windows(default_tree())
    root = r"C:\Users\A\AppData\Local\Connector"
    _call(wf.create_private_file, root, "state.bin", b"one")
    with pytest.raises(wf.WindowsFsError):
        _call(wf.create_private_file, root, "state.bin", b"two")


def test_private_file_rejects_network_root(windows):
    windows(default_tree())
    with pytest.raises(wf.WindowsFsError):
        _call(wf.open_private_file, r"\\server\share", "state.bin", create=True)


def test_private_file_never_shares_write_by_default(windows):
    table = windows(default_tree())
    handle = _call(wf.open_private_file, r"C:\Users\A\AppData\Local\Connector", "state.bin", create=True)
    handle.close()
    file_opens = [call for call in table.create_calls if call[0].endswith("STATE.BIN")]
    assert file_opens
    for _path, _access, share, _creation, _flags in file_opens:
        assert not share & wf.FILE_SHARE_WRITE


def test_sqlite_style_share_mode_is_explicit_and_bounded(windows):
    """SQLite needs read+write+delete sharing on its own database file."""

    table = windows(default_tree())
    root = r"C:\Users\A\AppData\Local\Connector"
    handle = _call(
        wf.open_private_file,
        root,
        "index.sqlite",
        create=True,
        share=wf.FILE_SHARE_READ | wf.FILE_SHARE_WRITE | wf.FILE_SHARE_DELETE,
    )
    handle.close()
    opens = [call for call in table.create_calls if call[0].endswith("INDEX.SQLITE")]
    assert opens and all(share == 0x7 for _p, _a, share, _c, _f in opens)
    for bad in (0, -1, 0x8, wf.FILE_SHARE_WRITE):
        with pytest.raises(wf.WindowsFsError):
            _call(wf.open_private_file, root, "index.sqlite", share=bad)


def test_delete_private_file_uses_handle_disposition(windows):
    table = windows(default_tree())
    root = r"C:\Users\A\AppData\Local\Connector"
    _call(wf.create_private_file, root, "state.bin", b"payload")
    _call(wf.delete_private_file, root, "state.bin")
    assert not any(key.endswith("STATE.BIN") for key in table.tree)


def test_read_only_file_is_still_removed(windows):
    """A read-only cache file rejects class 4; the EX class must remove it."""

    table = windows(default_tree())
    root = r"C:\Users\A\AppData\Local\Connector"
    _call(wf.create_private_file, root, "state.bin", b"payload")

    original = table.SetFileInformationByHandle

    def refuse_plain(handle, info_class, payload, size):
        if info_class == wf.FILE_DISPOSITION_INFO_CLASS:
            table.error = ERROR_ACCESS_DENIED
            return 0
        return original(handle, info_class, payload, size)

    table.SetFileInformationByHandle = refuse_plain
    _call(wf.delete_private_file, root, "state.bin")
    assert not any(key.endswith("STATE.BIN") for key in table.tree)


def test_private_cache_root_pins_the_parent_for_the_session(windows):
    table = windows(default_tree())
    root = r"C:\Users\A\AppData\Local\Connector"
    cache = _call(wf.PrivateCacheRoot, root)
    try:
        cache.verify()
        handle = cache.open_file("index.sqlite", create=True)
        handle.close()
        assert cache.child_path("index.sqlite").upper().endswith("CONNECTOR\\INDEX.SQLITE")
        with pytest.raises(wf.WindowsFsError):
            cache.child_path(r"a\b")
    finally:
        cache.close()
    pins = [call for call in table.create_calls if call[0] == root.upper()]
    assert pins and all(not share & wf.FILE_SHARE_DELETE for _p, _a, share, _c, _f in pins)


def test_private_cache_root_denies_a_missing_acl_chain(windows):
    tree = default_tree()
    tree["C:\\USERS\\A\\APPDATA\\CONNECTOR"] = Node("Connector", directory=True, identity=51, owner_only=False)
    windows(tree)
    with pytest.raises(wf.WindowsFsError) as denied:
        _call(wf.PrivateCacheRoot, r"C:\Users\A\AppData\Connector")
    assert denied.value.reason == wf.deny_insecure_cache()


def test_rights_only_open_falls_back_to_maximum_allowed(windows):
    """A data folder granting READ without READ_CONTROL must still be readable."""

    table = windows(default_tree())
    table.read_only_access = wf.FILE_READ_DATA
    assert _call(wf.read_relative_file, r"C:\Users\A", r"Docs\report.txt", max_bytes=64) == b"hello world"
    retries = [call for call in table.create_calls if call[1] == 0x02000000]
    assert retries, "the retry must request MAXIMUM_ALLOWED, not a broader fixed mask"
    for _path, _access, share, _creation, _flags in retries:
        assert not share & wf.FILE_SHARE_DELETE


# --------------------------------------------------------------------------- native Windows
# These use the real Win32 APIs and are skipped on POSIX; they are NOT run here.


@NATIVE
def test_native_pinned_root_and_safe_read(tmp_path):
    root = tmp_path / "root"
    (root / "sub").mkdir(parents=True)
    (root / "sub" / "report.txt").write_bytes(b"native bytes")
    identity = wf.pinned_root_identity(str(root))
    wf.verify_pinned_root(str(root), identity)
    assert wf.read_relative_file(str(root), r"sub\report.txt", max_bytes=64) == b"native bytes"
    assert "sub\\report.txt" in list(wf.iter_root_files(str(root)))


@NATIVE
def test_native_symlinked_file_denied(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    secret = tmp_path / "outside.txt"
    secret.write_text("outside", encoding="utf-8")
    try:
        os.symlink(secret, root / "link.txt")
    except OSError:
        pytest.skip("symlink creation needs Developer Mode or elevation")
    with pytest.raises(wf.WindowsFsError):
        wf.read_relative_file(str(root), "link.txt", max_bytes=64)


@NATIVE
def test_native_junction_denied(tmp_path):
    import subprocess

    root = tmp_path / "root"
    root.mkdir()
    target = tmp_path / "target"
    target.mkdir()
    (target / "secret.txt").write_text("secret", encoding="utf-8")
    junction = root / "junction"
    subprocess.run(["cmd", "/c", "mklink", "/J", str(junction), str(target)], check=True, capture_output=True)
    try:
        with pytest.raises(wf.WindowsFsError):
            wf.read_relative_file(str(root), r"junction\secret.txt", max_bytes=64)
        with pytest.raises(wf.WindowsFsError):
            wf.list_root(str(root))
    finally:
        os.rmdir(junction)


@NATIVE
def test_native_root_replacement_denied(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    identity = wf.pinned_root_identity(str(root))
    root.rename(tmp_path / "moved")
    root.mkdir()
    with pytest.raises(wf.WindowsFsError):
        wf.verify_pinned_root(str(root), identity)


@NATIVE
def test_native_read_race_denied(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    target = root / "report.txt"
    target.write_bytes(b"hello")
    wf.pinned_root_identity(str(root))
    target.write_bytes(b"tampered payload")
    with pytest.raises(wf.WindowsFsError):
        wf.read_relative_file(str(root), "report.txt", max_bytes=1024)


@NATIVE
def test_native_profile_shaped_cache_root_is_accepted(tmp_path):
    """A cache below a non-private ancestor (like %LOCALAPPDATA%) must work."""

    root = tmp_path / "appdata" / "connector-cache"
    created = wf.ensure_private_directory(str(root))
    assert Path(created).is_dir()
    wf.create_private_file(str(root), "state.bin", b"state")
    handle = wf.open_private_file(str(root), "state.bin")
    handle.close()
    wf.delete_private_file(str(root), "state.bin")
    assert not (root / "state.bin").exists()


@NATIVE
def test_native_world_readable_directory_denied(tmp_path):
    import subprocess

    root = tmp_path / "shared"
    root.mkdir()
    subprocess.run(["icacls", str(root), "/grant", "Everyone:(OI)(CI)F"], check=True, capture_output=True)
    with pytest.raises(wf.WindowsFsError) as denied:
        wf.ensure_private_directory(str(root))
    assert denied.value.reason == wf.deny_insecure_cache()
