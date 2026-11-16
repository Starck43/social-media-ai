r"""Native Windows handle/ACL access for the selected-root and private-cache boundaries.

The POSIX scaffold in :mod:`local_connector.paths` refuses to run on ``nt``
because ``O_NOFOLLOW``/``dir_fd`` are unavailable there. This module is the
Windows counterpart and talks to ``kernel32``/``advapi32`` through ``ctypes``,
proving the same invariants with Win32 primitives instead of POSIX flags:

* every path is opened one component at a time from the drive root, and each
  component is checked as it is opened. Windows has no ``openat``, so a walk
  alone cannot stop an ancestor that is swapped *after* it was passed; that gap
  is closed at the instant of use: a read is served from the final handle only,
  its identity/size/times are re-read after the read, and the path is resolved
  again and must still reach the same ``(volume, index)``;
* reparse points are denied by type, not by a follow flag: every open uses
  ``FILE_FLAG_OPEN_REPARSE_POINT`` (the opened object is then the reparse point
  itself), any ``FILE_ATTRIBUTE_REPARSE_POINT`` denies, and device objects are
  rejected by ``GetFileType``;
* UNC, device, ``\\?\``, mapped-network-drive and relative roots are denied,
  as are ``..``, ``.``, colon, separator smuggling, trailing dot/space aliases
  and reserved device stems in relative components;
* each open requests the minimum rights it needs (``LIST`` for directories,
  ``READ`` for files, ``READ_CONTROL`` only when a DACL is checked). An
  ``ACCESS_DENIED`` on a rights-only request is retried once with
  ``MAXIMUM_ALLOWED``, so a normal data root that grants ``READ`` but not
  ``READ_CONTROL`` is not refused for asking the wrong thing;
* identity is ``(volume serial, file index)``. The index is exposed as a signed
  64-bit inode because SQLite turns integers above ``2**63 - 1`` into REAL; the
  mapping is a bijection, so an approved identity survives a restart exactly;
* the local cache never relies on ``chmod``: new private directories and files
  receive an explicit ``D:P(A;OICI;FA;;;<owner>)`` descriptor that drops
  inherited ACEs. Existing objects are validated only from the first private
  component downwards: ``C:\\Users\\<user>\\AppData`` and similar OS profile
  directories are pinned ancestors, never demanded to be owner-private;
* a pinned ancestor handle denies delete sharing (POSIX has no equivalent need
  because ``openat`` is atomic), which is what keeps the private cache parent
  from being moved while the database, ``-wal``/``-shm``/``-journal`` sidecars
  or the exclusive lock are live. The database file itself must keep
  ``FILE_SHARE_READ|WRITE|DELETE`` or SQLite could not operate on it.

Import is safe on POSIX: Win32 constants and structures are defined
unconditionally, while every API entry point fails closed with
:class:`WindowsFsError` unless the host is actually Windows (``WinDLL`` must
load). Nothing here writes outside ``root`` or the private cache root, starts a
process, touches a database or imports application code. ``ctypes`` doubles in
the test module prove API contract and denial logic only; they cannot prove
native Windows support.

Integration (coordinated separately, not part of this unit)
-----------------------------------------------------------
* ``local_connector.paths`` uses :func:`pinned_root_identity`,
  :func:`verify_pinned_root` and :func:`read_into_stream` on ``nt``, storing
  ``RootIdentity.stat_inode`` in place of ``st_ino``.
* ``local_connector.index`` uses :class:`PrivateCacheRoot` for the cache
  directory, the SQLite database and its sidecars, and a separate lock file.
* ``bootstrap``'s fail-closed ``win32`` guard stays until that hook-up lands.
"""

from __future__ import annotations

import ctypes
import os
import re
import stat
import sys
from contextlib import contextmanager
from ctypes import wintypes
from dataclasses import dataclass
from typing import Callable, Iterator, Sequence

__all__ = [
    "PrivateCacheRoot",
    "RootIdentity",
    "WindowsFsError",
    "WindowsUnavailableError",
    "windows_available",
    "deny_permission",
    "deny_not_found",
    "deny_unsafe",
    "deny_network",
    "deny_relative",
    "deny_identity",
    "deny_insecure_cache",
    "api",
    "pinned_root_identity",
    "verify_pinned_root",
    "read_relative_file",
    "iter_root_files",
    "ensure_private_directory",
    "create_private_directory",
    "open_private_file",
    "create_private_file",
    "delete_private_file",
    "windows_api_signatures",
]


# --------------------------------------------------------------------------- errors


class WindowsFsError(PermissionError):
    """Fixed, sanitized denial; never contains attacker-controlled content."""

    def __init__(self, reason: str, winerror: int = 0) -> None:
        self.reason = reason
        self.winerror = int(winerror)
        super().__init__(reason)


class WindowsUnavailableError(WindowsFsError):
    """Native Windows APIs cannot be used on this host (e.g. POSIX)."""


def deny_permission() -> str:
    return "owner-private native Windows access was denied"


def deny_not_found() -> str:
    return "native Windows path is unavailable"


def deny_unsafe() -> str:
    return "links, reparse points, device or network paths are not accepted"


def deny_network() -> str:
    return "network, UNC and device paths are not accepted"


def deny_relative() -> str:
    return "a safe relative path is required"


def deny_identity() -> str:
    return "native Windows filesystem identity changed; select the folder again"


def deny_insecure_cache() -> str:
    return "cache directory must be owned by the current user and private"


def windows_available() -> bool:
    """True only on a real Windows host where ``kernel32`` actually loads."""

    if sys.platform != "win32" or os.name != "nt":
        return False
    try:
        _kernel32()
        _advapi32()
    except (OSError, WindowsFsError):
        return False
    return True


def _require_windows() -> None:
    if not windows_available():
        raise WindowsUnavailableError("native Windows handles are unavailable on this host")


def _denial_for_code(winerror: int) -> WindowsFsError:
    """Map a Win32 error code onto one of the fixed reasons."""

    winerror = int(winerror or 0)
    code = winerror & 0xFFFF
    if code in (2, 3):
        return WindowsFsError(deny_not_found(), winerror)
    if code in (5, 32, 33):
        return WindowsFsError(deny_permission(), winerror)
    # Name smuggling, races and every other unexpected status collapse into one
    # stable refusal for the caller.
    return WindowsFsError(deny_unsafe(), winerror)


# --------------------------------------------------------------------------- constants

FILE_LIST_DIRECTORY = 0x0001
FILE_READ_DATA = 0x0001
FILE_WRITE_DATA = 0x0002
FILE_READ_ATTRIBUTES = 0x0080
FILE_READ_EA = 0x0008
FILE_TRAVERSE = 0x0020
SYNCHRONIZE = 0x00100000
DELETE = 0x00010000

READ_CONTROL = 0x00020000

OPEN_EXISTING = 3
CREATE_NEW = 1
FILE_ATTRIBUTE_DIRECTORY = 0x00000010
FILE_ATTRIBUTE_REPARSE_POINT = 0x00000400
FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
FILE_SHARE_READ = 0x00000001
FILE_SHARE_WRITE = 0x00000002
FILE_SHARE_DELETE = 0x00000004
# Windows lacks openat: a pinned ancestor must be undeletable, so only read+write
# sharing is granted while a descendant is being resolved or read.
PINNED_SHARE = FILE_SHARE_READ | FILE_SHARE_WRITE
# Rights requested for a directory walk; LIST covers TRAVERSE/READ_ATTRIBUTES/
# SYNCHRONIZE in real Win32 and is granted by every data-folder ACL.
DIRECTORY_SHARE_ACCESS = FILE_LIST_DIRECTORY | FILE_TRAVERSE | FILE_READ_ATTRIBUTES | FILE_READ_EA | SYNCHRONIZE
FILE_READ_ACCESS = FILE_READ_DATA | FILE_READ_ATTRIBUTES | FILE_READ_EA | SYNCHRONIZE
# Ancestor directories are only traversed and inspected while a descendant is
# resolved; they never grant a listing right, so a pinned ancestor cannot be
# read as a file.
ANCESTOR_ACCESS = FILE_TRAVERSE | FILE_READ_ATTRIBUTES | SYNCHRONIZE

INVALID_HANDLE_VALUE = -1
# MAXIMUM_ALLOWED asks the kernel for the most rights the ACL grants. It is
# retried only after a fixed mask was denied, and it never widens the share
# mode, so the pinning guarantee is unchanged.
MAXIMUM_ALLOWED = 0x02000000
FILE_TYPE_DISK = 0x0001
FILE_ATTRIBUTE_INFO_CLASS = 9
FILE_STANDARD_INFO_CLASS = 1
FILE_DISPOSITION_INFO_CLASS = 4
FILE_DISPOSITION_INFO_EX_CLASS = 21
FILE_DISPOSITION_FLAG_DELETE = 0x1
FILE_DISPOSITION_FLAG_POSIX_SEMANTICS = 0x2

SE_FILE_OBJECT = 1
OWNER_SECURITY_INFORMATION = 0x00000001
DACL_SECURITY_INFORMATION = 0x00000004

SE_DACL_PROTECTED = 0x1000
SE_DACL_PRESENT = 0x0004
PRIVATE_DIRECTORY_ACCESS = 0x1F01FF  # FILE_ALL_ACCESS
PRIVATE_DIRECTORY_INHERITANCE = 0x03  # CONTAINER_INHERIT_ACE | OBJECT_INHERIT_ACE
ACCESS_ALLOWED_ACE_TYPE = 0x00
MAX_ACE_COUNT = 256

SDDL_REVISION_1 = 1
# Static, owner-only DACL templates; only the SID placeholder is substituted.
OWNER_ONLY_DIRECTORY_SDDL = "D:P(A;OICI;FA;;;{sid})"
OWNER_ONLY_FILE_SDDL = "D:P(A;;FA;;;{sid})"

TOKEN_QUERY = 0x0008
# A SID is 8 header bytes plus up to 15 sub-authorities of 4 bytes = 68 bytes.
TOKEN_USER_CLASS = 1
MAX_SID_BYTES = 68

DRIVE_REMOTE = 4
INVALID_FILE_ATTRIBUTES = 0xFFFFFFFF
ERROR_ALREADY_EXISTS = 183
ERROR_ACCESS_DENIED = 5
ERROR_NO_MORE_FILES = 18
MAX_NAME_CHARS = 255
MAX_ROOT_CHARS = 240
CHUNK = 1024 * 1024

_FORBIDDEN_NAME = re.compile(r'[\x00-\x1f<>:"|?*\\/]')
_DRIVE_COMPONENT = re.compile(r"^[A-Za-z]:$")

S_IFMT = 0o170000
S_IFDIR = 0o040000
S_IFREG = 0o100000


# --------------------------------------------------------------------------- structures


class BY_HANDLE_FILE_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("dwFileAttributes", wintypes.DWORD),
        ("ftCreationTime", wintypes.FILETIME),
        ("ftLastAccessTime", wintypes.FILETIME),
        ("ftLastWriteTime", wintypes.FILETIME),
        ("dwVolumeSerialNumber", wintypes.DWORD),
        ("nFileSizeHigh", wintypes.DWORD),
        ("nFileSizeLow", wintypes.DWORD),
        ("nNumberOfLinks", wintypes.DWORD),
        ("nFileIndexHigh", wintypes.DWORD),
        ("nFileIndexLow", wintypes.DWORD),
    ]


class FILE_ATTRIBUTE_TAG_INFO(ctypes.Structure):
    _fields_ = [("FileAttributes", wintypes.DWORD), ("ReparseTag", wintypes.DWORD)]


class FILE_STANDARD_INFO(ctypes.Structure):
    _fields_ = [
        ("AllocationSize", ctypes.c_int64),
        ("EndOfFile", ctypes.c_int64),
        ("NumberOfLinks", wintypes.DWORD),
        ("DeletePending", wintypes.BOOLEAN),
        ("Directory", wintypes.BOOLEAN),
    ]


class FILE_DISPOSITION_INFO(ctypes.Structure):
    _fields_ = [("DeleteFile", wintypes.BOOLEAN)]


class FILE_DISPOSITION_INFO_EX(ctypes.Structure):
    _fields_ = [("Flags", wintypes.DWORD)]


class ACL_SIZE_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("AceCount", wintypes.DWORD),
        ("AclBytesInUse", wintypes.DWORD),
        ("AclBytesFree", wintypes.DWORD),
    ]


class ACE_HEADER(ctypes.Structure):
    _fields_ = [
        ("AceType", ctypes.c_ubyte),
        ("AceFlags", ctypes.c_ubyte),
        ("AceSize", wintypes.WORD),
    ]


class ACCESS_ALLOWED_ACE(ctypes.Structure):
    _fields_ = [
        ("Header", ACE_HEADER),
        ("Mask", wintypes.DWORD),
        ("SidStart", wintypes.DWORD),
    ]


class SECURITY_ATTRIBUTES(ctypes.Structure):
    _fields_ = [
        ("nLength", wintypes.DWORD),
        ("lpSecurityDescriptor", wintypes.LPVOID),
        ("bInheritHandle", wintypes.BOOL),
    ]


class WIN32_FIND_DATAW(ctypes.Structure):
    _fields_ = [
        ("dwFileAttributes", wintypes.DWORD),
        ("ftCreationTime", wintypes.FILETIME),
        ("ftLastAccessTime", wintypes.FILETIME),
        ("ftLastWriteTime", wintypes.FILETIME),
        ("nFileSizeHigh", wintypes.DWORD),
        ("nFileSizeLow", wintypes.DWORD),
        ("dwReserved0", wintypes.DWORD),
        ("dwReserved1", wintypes.DWORD),
        ("cFileName", wintypes.WCHAR * 260),
        ("cAlternateFileName", wintypes.WCHAR * 14),
    ]


# Exact prototypes shared by the real bindings and by contract test doubles.
_API_ARGTYPES: dict[str, tuple] = {
    "CreateFileW": (
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(SECURITY_ATTRIBUTES),
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ),
    "ReadFile": (wintypes.HANDLE, wintypes.LPVOID, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID),
    "WriteFile": (wintypes.HANDLE, wintypes.LPCVOID, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID),
    "CloseHandle": (wintypes.HANDLE,),
    "GetFileInformationByHandle": (wintypes.HANDLE, ctypes.POINTER(BY_HANDLE_FILE_INFORMATION)),
    "GetFileInformationByHandleEx": (wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD),
    "GetFileType": (wintypes.HANDLE,),
    "GetFileAttributesW": (wintypes.LPCWSTR,),
    "FindFirstFileW": (wintypes.LPCWSTR, ctypes.POINTER(WIN32_FIND_DATAW)),
    "FindNextFileW": (wintypes.HANDLE, ctypes.POINTER(WIN32_FIND_DATAW)),
    "FindClose": (wintypes.HANDLE,),
    "GetDriveTypeW": (wintypes.LPCWSTR,),
    "CreateDirectoryW": (wintypes.LPCWSTR, ctypes.POINTER(SECURITY_ATTRIBUTES)),
    "SetFileInformationByHandle": (wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD),
    "LocalFree": (wintypes.HANDLE,),
    "GetCurrentProcess": (),
    "OpenProcessToken": (wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)),
    "GetTokenInformation": (
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ),
    "GetLengthSid": (wintypes.LPVOID,),
    "IsValidSid": (wintypes.LPVOID,),
    "EqualSid": (wintypes.LPVOID, wintypes.LPVOID),
    "ConvertSidToStringSidW": (wintypes.LPVOID, ctypes.POINTER(wintypes.LPWSTR)),
    "IsValidSecurityDescriptor": (wintypes.LPVOID,),
    "GetSecurityDescriptorControl": (wintypes.LPVOID, ctypes.POINTER(wintypes.WORD), ctypes.POINTER(wintypes.DWORD)),
    "GetSecurityDescriptorDacl": (
        wintypes.LPVOID,
        ctypes.POINTER(wintypes.BOOL),
        ctypes.POINTER(wintypes.LPVOID),
        ctypes.POINTER(wintypes.BOOL),
    ),
    "GetAclInformation": (wintypes.LPVOID, wintypes.LPVOID, wintypes.DWORD, ctypes.c_int),
    "GetAce": (wintypes.LPVOID, wintypes.DWORD, ctypes.POINTER(wintypes.LPVOID)),
    "ConvertStringSecurityDescriptorToSecurityDescriptorW": (
        wintypes.LPCWSTR,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.LPVOID),
        ctypes.POINTER(wintypes.DWORD),
    ),
    "GetSecurityInfo": (
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.LPVOID),
        ctypes.POINTER(wintypes.LPVOID),
        ctypes.POINTER(wintypes.LPVOID),
        ctypes.POINTER(wintypes.LPVOID),
        ctypes.POINTER(wintypes.LPVOID),
    ),
}
_API_RESTYPES: dict[str, object] = {
    "CreateFileW": wintypes.HANDLE,
    "ReadFile": wintypes.BOOL,
    "WriteFile": wintypes.BOOL,
    "CloseHandle": wintypes.BOOL,
    "GetFileInformationByHandle": wintypes.BOOL,
    "GetFileInformationByHandleEx": wintypes.BOOL,
    "GetFileType": wintypes.DWORD,
    "GetFileAttributesW": wintypes.DWORD,
    "FindFirstFileW": wintypes.HANDLE,
    "FindNextFileW": wintypes.BOOL,
    "FindClose": wintypes.BOOL,
    "GetDriveTypeW": wintypes.UINT,
    "CreateDirectoryW": wintypes.BOOL,
    "SetFileInformationByHandle": wintypes.BOOL,
    "LocalFree": wintypes.HANDLE,
    "GetCurrentProcess": wintypes.HANDLE,
    "OpenProcessToken": wintypes.BOOL,
    "GetTokenInformation": wintypes.BOOL,
    "GetLengthSid": wintypes.DWORD,
    "IsValidSid": wintypes.BOOL,
    "EqualSid": wintypes.BOOL,
    "ConvertSidToStringSidW": wintypes.BOOL,
    "IsValidSecurityDescriptor": wintypes.BOOL,
    "GetSecurityDescriptorControl": wintypes.BOOL,
    "GetSecurityDescriptorDacl": wintypes.BOOL,
    "GetAclInformation": wintypes.BOOL,
    "GetAce": wintypes.BOOL,
    "ConvertStringSecurityDescriptorToSecurityDescriptorW": wintypes.BOOL,
    "GetSecurityInfo": wintypes.DWORD,
}


def windows_api_signatures() -> dict[str, tuple]:
    """Public copy of the prototypes a contract test double must reproduce."""

    return dict(_API_ARGTYPES)


# --------------------------------------------------------------------------- bindings

_KERNEL32: object | None = None
_ADVAPI32: object | None = None


class _Api:
    """Late-bound Win32 table; each attribute is a ``ctypes`` function pointer."""

    def __init__(self) -> None:
        self._cache: dict[str, Callable] = {}

    def __getattr__(self, name: str) -> Callable:
        function = self._cache.get(name)
        if function is None:
            if name not in _API_ARGTYPES:
                raise AttributeError(name)
            function = _bind(name)
            self._cache[name] = function
        return function

    def snapshot(self) -> dict[str, Callable]:
        """Preload every prototype so a missing export fails closed here."""

        for name in _API_ARGTYPES:
            getattr(self, name)
        return dict(self._cache)

    def reset(self) -> None:
        self._cache.clear()

    def __dir__(self) -> list[str]:
        return sorted(_API_ARGTYPES)


api = _Api()
_current: object = api


def current_api():
    """The active Win32 table; production is ``api``, tests may substitute one."""

    return _current


def install_api(table) -> None:
    """Swap the Win32 table (test seam); ``None`` restores the real bindings."""

    global _current
    _current = api if table is None else table


def _kernel32():
    global _KERNEL32
    if _KERNEL32 is None:
        _KERNEL32 = ctypes.WinDLL("kernel32", use_last_error=True)
    return _KERNEL32


def _advapi32():
    global _ADVAPI32
    if _ADVAPI32 is None:
        _ADVAPI32 = ctypes.WinDLL("advapi32", use_last_error=True)
    return _ADVAPI32


def _bind(name: str) -> Callable:
    libraries = (("kernel32", _kernel32), ("advapi32", _advapi32))
    for _, factory in libraries:
        library = factory()
        try:
            function = getattr(library, name)
        except AttributeError:
            continue
        function.argtypes = list(_API_ARGTYPES[name])
        function.restype = _API_RESTYPES[name]
        return function
    raise WindowsUnavailableError(deny_unsafe(), 127)


def _last_error() -> int:
    return int(ctypes.get_last_error())


def _fail(reason: str) -> WindowsFsError:
    return WindowsFsError(reason, _last_error())


def _call(result, reason: str | None = None):
    """Return a successful Win32 result or raise a sanitized denial."""

    if result:
        return result
    if reason is not None:
        raise _fail(reason)
    raise _denial_for_code(_last_error())


def _soft(result) -> bool:
    """Boolean outcome for a call whose failure is inspected by the caller."""

    return bool(result)


# --------------------------------------------------------------------------- identity


def _signed_file_index(file_index: int) -> int:
    """Map the 64-bit file index onto a signed 64-bit integer.

    SQLite stores Python integers above ``2**63 - 1`` as REAL, which would make
    an identity comparison lossy. The mapping is a bijection on
    ``range(2**64)``, so the approved inode survives a restart unchanged.
    """

    value = int(file_index) & 0xFFFFFFFFFFFFFFFF
    return value - (1 << 64) if value >= (1 << 63) else value


def _unsigned_file_index(stored: int) -> int:
    """Inverse of :func:`_signed_file_index`; a stored non-negative value is its own.

    A non-negative value cannot come from a raw Windows index: such an index
    would already be below ``2**63`` and therefore stored unchanged, so both
    readings agree and no identity can be forged by encoding choice.
    """

    if type(stored) is not int:
        raise WindowsFsError(deny_identity())
    return stored & 0xFFFFFFFFFFFFFFFF


class RootIdentity:
    """Volume serial number + file index of the directory approved by selection."""

    __slots__ = ("volume_serial", "file_index")

    def __init__(self, volume_serial: int, file_index: int, *, signed: bool = False) -> None:
        if type(volume_serial) is not int or type(file_index) is not int:
            raise WindowsFsError(deny_identity())
        if not 0 <= volume_serial <= 0xFFFFFFFF:
            raise WindowsFsError(deny_identity())
        if signed:
            file_index = _unsigned_file_index(file_index)
        if not 0 <= file_index <= 0xFFFFFFFFFFFFFFFF:
            raise WindowsFsError(deny_identity())
        self.volume_serial = int(volume_serial)
        self.file_index = int(file_index)

    @classmethod
    def from_approved(cls, volume_serial: int, file_index: int) -> "RootIdentity":
        """Rebuild an identity read back from storage (signed 64-bit inode)."""

        return cls(volume_serial, file_index, signed=True)

    @property
    def stat_device(self) -> int:
        """``st_dev`` value: the 32-bit volume serial, as on POSIX."""

        return self.volume_serial

    @property
    def stat_inode(self) -> int:
        """``st_ino`` value: the file index, signed so SQLite stores it exactly."""

        return _signed_file_index(self.file_index)

    def as_tuple(self) -> tuple[int, int]:
        return (self.volume_serial, self.file_index)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, RootIdentity):
            return self.as_tuple() == other.as_tuple()
        return NotImplemented

    def __hash__(self) -> int:
        return hash(self.as_tuple())

    def __repr__(self) -> str:
        return f"RootIdentity({self.volume_serial:#010x}, {self.file_index:#018x})"


def _stat_from(info: "_Info") -> os.stat_result:
    """``os.stat_result``-shaped view; parents can map fields onto ``SelectedRoot``.

    ``st_mtime_ns`` is preserved exactly; ``st_dev``/``st_ino`` reuse the volume
    serial / file index so a single ``(device, inode)`` comparison keeps working
    where the parent has no native ``RootIdentity``.
    """

    mode = stat.S_IFDIR if info.is_directory else stat.S_IFREG
    result = os.stat_result((mode | 0o700, 0, 0, info.links, 0, 0, info.size, info.mtime / 10_000_000, 0, 0))
    result.st_dev = info.volume_serial
    result.st_ino = _signed_file_index(info.file_index)
    result.st_mtime_ns = info.mtime * 100
    result.st_nlink = info.links
    return result


@dataclass(frozen=True)
class _Info:
    attributes: int
    volume_serial: int
    file_index: int
    size: int
    mtime: int
    ctime: int
    links: int

    @property
    def identity(self) -> tuple[int, int]:
        return (self.volume_serial, self.file_index)

    @property
    def is_directory(self) -> bool:
        return bool(self.attributes & FILE_ATTRIBUTE_DIRECTORY)

    @property
    def is_reparse(self) -> bool:
        return bool(self.attributes & FILE_ATTRIBUTE_REPARSE_POINT)

    def signature(self) -> tuple[int, int, int, int, int]:
        return (self.volume_serial, self.file_index, self.size, self.mtime, self.ctime)


# --------------------------------------------------------------------------- handles


class _Handle:
    """Guarded Win32 handle; close is idempotent and always safe to repeat."""

    __slots__ = ("value",)

    def __init__(self, value: int) -> None:
        self.value = int(value)

    @property
    def closed(self) -> bool:
        return not self.value or self.value == INVALID_HANDLE_VALUE

    def close(self) -> None:
        value, self.value = self.value, 0
        if value and value != INVALID_HANDLE_VALUE:
            current_api().CloseHandle(value)

    def __enter__(self) -> "_Handle":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    def __repr__(self) -> str:
        return f"_Handle(0x{self.value:x})"


def _handle_value(result) -> int:
    """Normalise a ``CreateFileW``/``FindFirstFileW`` result to a signed ``int``.

    ``wintypes.HANDLE`` is an unsigned 64-bit pointer, so a failed open can
    arrive as ``2**64 - 1``; every handle compared by this module therefore uses
    the signed form, and a version-mismatched signing pointer (larger than any
    real handle) still cannot alias ``-1``.
    """

    if result is None:
        return INVALID_HANDLE_VALUE
    try:
        value = int(result)
    except (TypeError, ValueError):
        return INVALID_HANDLE_VALUE
    if not 0 < value < 1 << 63:
        return INVALID_HANDLE_VALUE
    return value


def _open_handle(path: str, *, directory: bool, access: int, share: int, creation: int, security=None) -> int:
    """Open one object; every open is ``FILE_FLAG_OPEN_REPARSE_POINT``.

    A fixed access mask can be denied by an ACL that still grants a usable
    subset (``READ_CONTROL`` on a data folder, for example). The only retry is
    ``MAXIMUM_ALLOWED`` with the same share mode and flags, so the kernel grants
    exactly what the owner already allowed and nothing more.
    """

    flags = FILE_FLAG_OPEN_REPARSE_POINT
    if directory:
        flags |= FILE_FLAG_BACKUP_SEMANTICS
    value = _handle_value(current_api().CreateFileW(path, access, share, security, creation, flags, None))
    if value in (0, INVALID_HANDLE_VALUE):
        code = _last_error() & 0xFFFF
        if code == ERROR_ACCESS_DENIED and access != MAXIMUM_ALLOWED:
            value = _handle_value(
                current_api().CreateFileW(path, MAXIMUM_ALLOWED, share, security, creation, flags, None)
            )
        if value in (0, INVALID_HANDLE_VALUE):
            raise _denial_for_code(_last_error())
    return value


def _info(value: int) -> _Info:
    """Prove the opened object is a real on-disk, non-reparse file/directory."""

    data = BY_HANDLE_FILE_INFORMATION()
    _call(current_api().GetFileInformationByHandle(value, ctypes.byref(data)))
    tag = FILE_ATTRIBUTE_TAG_INFO()
    _call(
        current_api().GetFileInformationByHandleEx(
            value, FILE_ATTRIBUTE_INFO_CLASS, ctypes.byref(tag), ctypes.sizeof(FILE_ATTRIBUTE_TAG_INFO)
        )
    )
    attributes = int(tag.FileAttributes)
    if attributes & FILE_ATTRIBUTE_REPARSE_POINT:
        raise _fail(deny_unsafe())
    if int(current_api().GetFileType(value)) != FILE_TYPE_DISK:
        raise _fail(deny_unsafe())
    standard = FILE_STANDARD_INFO()
    _call(
        current_api().GetFileInformationByHandleEx(
            value, FILE_STANDARD_INFO_CLASS, ctypes.byref(standard), ctypes.sizeof(standard)
        )
    )
    if standard.DeletePending or int(standard.NumberOfLinks) != int(data.nNumberOfLinks):
        raise _fail(deny_unsafe())
    if bool(standard.Directory) != bool(attributes & FILE_ATTRIBUTE_DIRECTORY):
        raise _fail(deny_unsafe())
    return _Info(
        attributes=attributes,
        volume_serial=int(data.dwVolumeSerialNumber),
        file_index=(int(data.nFileIndexHigh) << 32) | int(data.nFileIndexLow),
        size=(int(data.nFileSizeHigh) << 32) | int(data.nFileSizeLow),
        mtime=int(data.ftLastWriteTime.dwHighDateTime) << 32 | int(data.ftLastWriteTime.dwLowDateTime),
        ctime=int(data.ftCreationTime.dwHighDateTime) << 32 | int(data.ftCreationTime.dwLowDateTime),
        links=int(data.nNumberOfLinks),
    )


@contextmanager
def _opened(
    path: str, *, directory: bool, access: int, share: int, creation: int = OPEN_EXISTING, security=None
) -> Iterator[tuple[_Handle, _Info]]:
    """Open one object, prove its type, and close it on every exit path."""

    handle = _Handle(
        _open_handle(path, directory=directory, access=access, share=share, creation=creation, security=security)
    )
    try:
        info = _info(handle.value)
        if info.is_directory != directory:
            raise WindowsFsError(deny_identity(), _last_error())
    except BaseException:
        handle.close()
        raise
    try:
        yield handle, info
    finally:
        handle.close()


def _resolve(path: str, *, directory: bool, access: int, share: int) -> _Info:
    """Open ``path`` once, prove it, record identity and close it again."""

    with _opened(path, directory=directory, access=access, share=share) as (_handle, info):
        return info


def _qualified(root: str, parts: Sequence[str]) -> str:
    """Join a drive root (``C:\\``) with components, never doubling a separator."""

    rest = "\\".join(parts)
    if not rest:
        return root
    return f"{root}{rest}" if root.endswith("\\") else f"{root}\\{rest}"


# --------------------------------------------------------------------------- path rules


def _check_component(part: str) -> None:
    """Reject names Windows folds onto another object or onto a device."""

    if not part or part in (".", "..") or part != part.rstrip(" ."):
        raise WindowsFsError(deny_relative())
    if len(part) > MAX_NAME_CHARS or _FORBIDDEN_NAME.search(part):
        raise WindowsFsError(deny_relative())
    stem = part.split(".", 1)[0].rstrip(" ").casefold()
    if stem in {"con", "prn", "aux", "nul", "conin$", "conout$"}:
        raise WindowsFsError(deny_relative())
    if len(stem) == 4 and stem[:3] in {"com", "lpt"} and stem[3] in "0123456789":
        raise WindowsFsError(deny_relative())


def _split_absolute(root) -> tuple[str, tuple[str, ...]]:
    """Split ``X:\\a\\b`` into (``X:\\``, ``("a", "b")``); unsafe forms deny."""

    try:
        text = os.fspath(root)
    except TypeError:
        raise WindowsFsError(deny_network()) from None
    if not isinstance(text, str) or not text:
        raise WindowsFsError(deny_network())
    if len(text) < 3 or not _DRIVE_COMPONENT.match(text[:2]) or text[2] not in ("\\", "/"):
        # UNC (\\server\share), device (\\.\), verbatim (\\?\), relative and
        # extended-length roots all land here: none starts with "X:\".
        raise WindowsFsError(deny_network())
    parts = tuple(part for part in re.split(r"[\\/]+", text[3:]) if part)
    if not parts:
        raise WindowsFsError(deny_network())  # a bare drive root is not selectable
    for part in parts:
        _check_component(part)
    drive = text[:2].upper() + "\\"
    if len(_qualified(drive, parts)) > MAX_ROOT_CHARS:
        raise WindowsFsError(deny_unsafe())
    if int(current_api().GetDriveTypeW(drive)) == DRIVE_REMOTE:
        raise WindowsFsError(deny_network())
    return drive, parts


def _normalise_root(root) -> str:
    """Return a canonical absolute ``X:\\...`` root or deny it outright."""

    drive, parts = _split_absolute(root)
    return _qualified(drive, parts)


def _relative_parts(relative) -> tuple[str, ...]:
    """Validate a caller-supplied relative path; no aliases, escapes or devices.

    This is used for *both* an indexed POSIX style path (``a/b.txt``) and a
    path produced by :func:`iter_root_files` (``a\\b.txt``). A component that
    the filesystem actually holds may legitimately contain dots
    (``report.v2.txt`` -> ``a.txt.txt`` when ``a.txt`` is also an alias), so
    trailing dots/spaces are rejected but a folded alias is not: the caller
    never passes such a name, and the walker's names are opened by identity
    check anyway.
    """

    try:
        value = os.fspath(relative)
    except TypeError:
        raise WindowsFsError(deny_relative()) from None
    if not isinstance(value, str) or not value or "\x00" in value:
        raise WindowsFsError(deny_relative())
    if value.startswith(("\\", "/")) or ":" in value:
        raise WindowsFsError(deny_relative())
    # Accept both separators for input that ultimately reaches this OS, but
    # never a mix: a mixed path is never produced by the walker.
    if "\\" in value and "/" in value:
        raise WindowsFsError(deny_relative())
    parts = tuple(part for part in value.replace("/", "\\").split("\\") if part not in ("", "."))
    if not parts or any(part == ".." for part in parts):
        raise WindowsFsError(deny_relative())
    for part in parts:
        if part != part.rstrip(" ."):
            raise WindowsFsError(deny_relative())
        if len(part) > MAX_NAME_CHARS or _FORBIDDEN_NAME.search(part):
            raise WindowsFsError(deny_relative())
    return parts


# --------------------------------------------------------------------------- selected root


def _walk(root, *, leaf_directory: bool) -> tuple[_Handle, _Info]:
    """Open ``root`` from its drive one component at a time, keeping ancestors.

    ``openat`` does not exist on Windows, so ancestor handles cannot substitute
    for it; they are still retained for the duration of the walk so the names
    actually resolved are the approved ones. The undetectable remainder - a
    rename between the parent close and the child open - is covered by the
    identity check after the walk (:func:`pinned_root_identity`,
    :func:`read_relative_file`).
    """

    drive, parts = _split_absolute(root)
    handle = _Handle(
        _open_handle(drive, directory=True, access=DIRECTORY_SHARE_ACCESS, share=PINNED_SHARE, creation=OPEN_EXISTING)
    )
    try:
        info = _info(handle.value)
        for index in range(len(parts)):
            last = index == len(parts) - 1
            is_directory = leaf_directory if last else True
            opened, child_info = _opened(
                _qualified(drive, parts[: index + 1]),
                directory=is_directory,
                access=DIRECTORY_SHARE_ACCESS if is_directory else FILE_READ_ACCESS,
                share=PINNED_SHARE,
            ).__enter__()
            handle.close()
            handle, info = opened, child_info
    except BaseException:
        handle.close()
        raise
    return handle, info


def open_pinned_root(root) -> tuple[_Handle, RootIdentity]:
    """Open the selected root for the caller to pin; the caller closes it.

    Keeping this handle alive for the whole traversal both pins the identity
    and blocks a delete/rename of the root for the duration.
    """

    _require_windows()
    handle, info = _walk(_normalise_root(root), leaf_directory=True)
    if not info.is_directory:
        handle.close()
        raise _fail(deny_unsafe())
    return handle, RootIdentity(info.volume_serial, info.file_index)


def pinned_root_identity(root) -> RootIdentity:
    """Identity of ``root`` now; raises if it is not a plain directory."""

    handle, identity = open_pinned_root(root)
    handle.close()
    return identity


def verify_pinned_root(root, identity: RootIdentity) -> None:
    """Fail unless ``root`` currently resolves to exactly ``identity``."""

    _require_windows()
    if not isinstance(identity, RootIdentity):
        raise WindowsFsError(deny_identity())
    current = pinned_root_identity(root)
    if current != identity:
        raise WindowsFsError(deny_identity())


# --------------------------------------------------------------------------- reads


def _read_handle(value: int, *, max_bytes: int) -> tuple[bytes, int]:
    """Read a handle to EOF, refusing once more than ``max_bytes`` was produced."""

    buffer = ctypes.create_string_buffer(CHUNK)
    read_count = wintypes.DWORD(0)
    chunks: list[bytes] = []
    total = 0
    while True:
        _call(current_api().ReadFile(value, buffer, CHUNK, ctypes.byref(read_count), None))
        if read_count.value == 0:
            break
        total += int(read_count.value)
        if total > max_bytes:
            raise _fail(deny_unsafe())
        chunks.append(buffer.raw[: int(read_count.value)])
    return b"".join(chunks), total


@contextmanager
def _verified_read(root, relative, *, max_bytes: int) -> Iterator[tuple[_Handle, _Info, bytes]]:
    """Open, bound, read and prove one file; yields ``(handle, info, bytes)``.

    The leaf is opened first, so the bytes come from one verified object. Every
    ancestor directory is then opened and pinned for the rest of the operation,
    and the leaf path is resolved once more at the end: if an ancestor was
    replaced in the meantime, the path now leads to a different object and the
    read is refused instead of silently serving another file.
    """

    if type(max_bytes) is not int or max_bytes < 0:
        raise WindowsFsError(deny_unsafe())
    canonical = _normalise_root(root)
    parts = _relative_parts(relative)
    ancestors = parts[:-1]
    leaf = _qualified(canonical, parts)
    pinned: list[tuple[str, _Handle, _Info]] = []
    try:
        # The ancestors are opened first and kept open without delete sharing, so
        # a directory that is replaced while the file is read is detected when
        # the paths are resolved again afterwards.
        for index in range(len(ancestors)):
            path = _qualified(canonical, ancestors[: index + 1])
            ancestor = _Handle(
                _open_handle(path, directory=True, access=ANCESTOR_ACCESS, share=PINNED_SHARE, creation=OPEN_EXISTING)
            )
            try:
                info = _info(ancestor.value)
                if not info.is_directory:
                    raise WindowsFsError(deny_identity())
            except BaseException:
                ancestor.close()
                raise
            pinned.append((path, ancestor, info))
        # The size is checked through an attributes-only handle, so an oversized
        # file is never opened for reading.
        with _opened(leaf, directory=False, access=FILE_READ_ATTRIBUTES, share=PINNED_SHARE) as (_sized, size_info):
            if size_info.is_directory or size_info.is_reparse:
                raise _fail(deny_unsafe())
            if size_info.size > max_bytes:
                raise _fail(deny_unsafe())
        with _opened(leaf, directory=False, access=FILE_READ_ACCESS, share=PINNED_SHARE) as (handle, before):
            if before.is_directory or before.is_reparse:
                raise _fail(deny_unsafe())
            if before.size > max_bytes:
                raise _fail(deny_unsafe())
            data, total = _read_handle(handle.value, max_bytes=max_bytes)
            after = _info(handle.value)
            if after.signature() != before.signature() or total != before.size:
                raise _fail(deny_identity())
            current = _resolve(leaf, directory=False, access=FILE_READ_ACCESS, share=PINNED_SHARE)
            if current.signature() != before.signature():
                raise _fail(deny_identity())
            for path, _ancestor_handle, ancestor_info in pinned:
                resolved = _resolve(path, directory=True, access=ANCESTOR_ACCESS, share=PINNED_SHARE)
                if resolved.identity != ancestor_info.identity:
                    raise _fail(deny_identity())
            yield handle, before, data
    finally:
        for _path, ancestor_handle, _info_before in pinned:
            ancestor_handle.close()


def read_relative_file(root, relative, *, max_bytes: int) -> bytes:
    """Read one regular file beneath ``root`` through a single verified handle.

    Bounded to ``max_bytes`` (a larger file denies instead of truncating),
    identity and timestamps are captured on the same handle before and after
    the read, and the path is re-resolved to prove the name still reaches the
    same file.
    """

    _require_windows()
    with _verified_read(root, relative, max_bytes=max_bytes) as (_handle, _info_before, data):
        return data


def iter_root_files(
    root,
    *,
    recursive: bool = True,
    max_entries: int = 100_000,
    excluded_names: Sequence[str] = (),
    excluded_paths: Sequence[str] = (),
) -> Iterator[str]:
    """Yield backslash relative file paths; directories are never yielded.

    Every directory is re-opened by component walk and must still be a
    directory before descending, so a swap during the walk denies instead of
    crossing into another tree. Reparse points anywhere in the walk deny.

    Exclusion is applied *before* a directory is opened, which is what keeps a
    sensitive tree (``.ssh``, a browser profile, the cache itself) from being
    traversed at all. Only explicit exclusions (``excluded_names`` case-folded,
    ``excluded_paths`` relative to the root) skip entries silently; nothing is
    hidden implicitly, so the caller decides the policy. Reparse points outside
    an excluded tree still deny.
    """

    _require_windows()
    if type(max_entries) is not int or max_entries <= 0:
        raise WindowsFsError(deny_unsafe())
    canonical = _normalise_root(root)
    names = {name.casefold() for name in excluded_names}
    paths = {tuple(part.casefold() for part in _relative_parts(item)) for item in excluded_paths}
    remaining = [int(max_entries)]

    def excluded(parts: tuple[str, ...], name: str) -> bool:
        lowered = name.casefold()
        if lowered in names:
            return True
        child = tuple(part.casefold() for part in parts + (name,))
        return any(child[: len(item)] == item for item in paths)

    def walk(prefix: tuple[str, ...]) -> Iterator[str]:
        base = _qualified(canonical, prefix)
        for name, is_directory in _list_directory(base, max_entries=remaining[0]):
            if excluded(prefix, name):
                continue
            remaining[0] -= 1
            if remaining[0] < 0:
                raise WindowsFsError(deny_unsafe())
            child = prefix + (name,)
            if not is_directory:
                with _opened(
                    _qualified(canonical, child),
                    directory=False,
                    access=FILE_READ_ACCESS,
                    share=PINNED_SHARE,
                ) as (_opened_handle, _info):
                    pass
                yield "\\".join(child)
                continue
            if recursive:
                yield from walk(child)

    yield from walk(())


def _list_directory(canonical: str, *, max_entries: int) -> list[tuple[str, bool]]:
    """Enumerate one directory re-opened to the same identity.

    Entry metadata comes from the same enumeration as the names; any reparse
    point denies, and the entry budget is enforced while enumerating. The
    directory is opened before and after enumeration so a swap between the two
    is visible.
    """

    with _opened(canonical, directory=True, access=DIRECTORY_SHARE_ACCESS, share=PINNED_SHARE) as (
        _directory_handle,
        before,
    ):
        entries = _enumerate(canonical, max_entries=max_entries)
        if _info(_directory_handle.value).identity != before.identity:
            raise WindowsFsError(deny_identity())
        return entries


def _enumerate(canonical: str, *, max_entries: int) -> list[tuple[str, bool]]:
    """One ``FindFirstFileW``/``FindNextFileW`` pass; every reparse point denies."""

    data = WIN32_FIND_DATAW()
    find = _handle_value(current_api().FindFirstFileW(canonical + "\\*", ctypes.byref(data)))
    if find == INVALID_HANDLE_VALUE:
        error = _last_error()
        if error & 0xFFFF in (2, 3):
            return []
        raise _denial_for_code(error)
    entries: list[tuple[str, bool]] = []
    try:
        while True:
            name = str(data.cFileName)
            attributes = int(data.dwFileAttributes)
            if name not in (".", ".."):
                if attributes & FILE_ATTRIBUTE_REPARSE_POINT or attributes == INVALID_FILE_ATTRIBUTES:
                    raise _fail(deny_unsafe())
                entries.append((name, bool(attributes & FILE_ATTRIBUTE_DIRECTORY)))
                if len(entries) > max_entries:
                    raise _fail(deny_unsafe())
            if not current_api().FindNextFileW(find, ctypes.byref(data)):
                error = _last_error() & 0xFFFF
                if error not in (0, ERROR_NO_MORE_FILES):
                    raise _denial_for_code(error)
                break
    finally:
        current_api().FindClose(find)
    entries.sort(key=lambda item: item[0].casefold())
    return entries


def list_root(root, *, max_entries: int = 100_000) -> list[tuple[str, bool]]:
    """List the selected root's own level as ``(name, is_directory)``.

    The directory is opened by component walk under pinned ancestors; any
    reparse point raises. Excluded-name filtering stays with the caller.
    """

    _require_windows()
    if type(max_entries) is not int or max_entries <= 0:
        raise WindowsFsError(deny_unsafe())
    return _list_directory(_normalise_root(root), max_entries=int(max_entries))


# --------------------------------------------------------------------------- private cache


class _SidBuffer:
    """Owner SID bytes copied out of a token; the copy is validated and stable.

    ``ConvertSidToStringSidW``/``EqualSid`` are called with a pointer into this
    buffer, which stays alive for as long as the object does.
    """

    __slots__ = ("raw", "_buffer")

    def __init__(self, raw: bytes) -> None:
        if not 8 <= len(raw) <= MAX_SID_BYTES:
            raise WindowsFsError(deny_permission())
        self.raw = raw
        self._buffer = ctypes.create_string_buffer(raw, len(raw))
        if not current_api().IsValidSid(self.pointer):
            raise WindowsFsError(deny_permission())

    @property
    def pointer(self):
        return ctypes.cast(self._buffer, wintypes.LPVOID)

    @property
    def text(self) -> str:
        text = wintypes.LPWSTR()
        _call(current_api().ConvertSidToStringSidW(self.pointer, ctypes.byref(text)), deny_permission())
        try:
            if not text.value:
                raise WindowsFsError(deny_permission())
            return str(text.value)
        finally:
            if text:
                current_api().LocalFree(text)


def _token_owner_sid() -> _SidBuffer:
    """Return the current process token's user SID (real token, never a constant)."""

    token = wintypes.HANDLE()
    if not current_api().OpenProcessToken(current_api().GetCurrentProcess(), TOKEN_QUERY, ctypes.byref(token)):
        raise _fail(deny_permission())
    try:
        size = wintypes.DWORD(0)
        # Expected failure: this call only reports the required size.
        _soft(current_api().GetTokenInformation(token, TOKEN_USER_CLASS, None, 0, ctypes.byref(size)))
        if not 8 <= size.value <= MAX_SID_BYTES + 64:
            raise _fail(deny_permission())
        raw = ctypes.create_string_buffer(size.value)
        _call(
            current_api().GetTokenInformation(token, TOKEN_USER_CLASS, raw, size.value, ctypes.byref(size)),
            deny_permission(),
        )
        # TOKEN_USER begins with SID_AND_ATTRIBUTES: a pointer-sized pointer to
        # the SID (which lives inside the same buffer) plus a DWORD of flags.
        base = ctypes.addressof(raw)
        sid_address = int(ctypes.cast(raw, ctypes.POINTER(wintypes.LPVOID)).contents.value or 0)
        if not base <= sid_address < base + size.value:
            raise WindowsFsError(deny_permission())
        sid_pointer = ctypes.cast(ctypes.c_void_p(sid_address), wintypes.LPVOID)
        length = int(current_api().GetLengthSid(sid_pointer))
        if not 8 <= length <= min(MAX_SID_BYTES, base + size.value - sid_address):
            raise WindowsFsError(deny_permission())
        return _SidBuffer(ctypes.string_at(sid_pointer, length))
    finally:
        current_api().CloseHandle(token)


def _private_descriptor(owner: _SidBuffer, *, directory: bool) -> SECURITY_ATTRIBUTES:
    """Build a SECURITY_ATTRIBUTES from the static owner-only SDDL template."""

    template = OWNER_ONLY_DIRECTORY_SDDL if directory else OWNER_ONLY_FILE_SDDL
    sddl = template.format(sid=owner.text)
    descriptor = wintypes.LPVOID()
    size = wintypes.DWORD(0)
    _call(
        current_api().ConvertStringSecurityDescriptorToSecurityDescriptorW(
            sddl, SDDL_REVISION_1, ctypes.byref(descriptor), ctypes.byref(size)
        ),
        deny_permission(),
    )
    if not descriptor:
        raise WindowsFsError(deny_permission())
    return SECURITY_ATTRIBUTES(ctypes.sizeof(SECURITY_ATTRIBUTES), descriptor, False)


def _free_descriptor(attributes: SECURITY_ATTRIBUTES) -> None:
    if attributes.lpSecurityDescriptor:
        current_api().LocalFree(attributes.lpSecurityDescriptor)
        attributes.lpSecurityDescriptor = None


def _acl_is_owner_only(value: int, owner: _SidBuffer) -> bool:
    """Prove the object DACL is protected and grants *only* the current owner.

    Any ACE for another SID (including explicit denies and inherit-only grants)
    denies, and ``Everyone``/group grants can therefore never hide behind an
    inherited flag. Every ACE must be an allowed ACE for the owner SID.
    """

    descriptor = wintypes.LPVOID()
    owner_out = wintypes.LPVOID()
    # GetSecurityInfo returns an error code; 0 is success, anything else denies.
    status = current_api().GetSecurityInfo(
        value,
        SE_FILE_OBJECT,
        OWNER_SECURITY_INFORMATION | DACL_SECURITY_INFORMATION,
        ctypes.byref(owner_out),
        None,
        None,
        None,
        ctypes.byref(descriptor),
    )
    if status != 0:
        raise WindowsFsError(deny_insecure_cache())
    try:
        if not descriptor or not owner_out:
            return False
        if not current_api().IsValidSecurityDescriptor(descriptor):
            return False
        if not current_api().EqualSid(owner_out, owner.pointer):
            return False
        control = wintypes.WORD(0)
        revision = wintypes.DWORD(0)
        _call(
            current_api().GetSecurityDescriptorControl(descriptor, ctypes.byref(control), ctypes.byref(revision)),
            deny_insecure_cache(),
        )
        flags = int(control.value)
        if not flags & SE_DACL_PROTECTED or not flags & SE_DACL_PRESENT:
            return False
        present = wintypes.BOOL(0)
        defaulted = wintypes.BOOL(0)
        dacl = wintypes.LPVOID()
        _call(
            current_api().GetSecurityDescriptorDacl(
                descriptor, ctypes.byref(present), ctypes.byref(dacl), ctypes.byref(defaulted)
            ),
            deny_insecure_cache(),
        )
        if not present.value or not dacl:
            return False
        size = ACL_SIZE_INFORMATION()
        _call(current_api().GetAclInformation(dacl, ctypes.byref(size), ctypes.sizeof(size), 2), deny_insecure_cache())
        if not 1 <= int(size.AceCount) <= MAX_ACE_COUNT:
            return False
        for index in range(int(size.AceCount)):
            ace_pointer = wintypes.LPVOID()
            _call(current_api().GetAce(dacl, index, ctypes.byref(ace_pointer)), deny_insecure_cache())
            if not ace_pointer:
                return False
            header = ctypes.cast(ace_pointer, ctypes.POINTER(ACE_HEADER)).contents
            if int(header.AceType) != ACCESS_ALLOWED_ACE_TYPE:
                return False
            if int(header.AceSize) < ctypes.sizeof(ACCESS_ALLOWED_ACE):
                return False
            ace = ctypes.cast(ace_pointer, ctypes.POINTER(ACCESS_ALLOWED_ACE)).contents
            if int(ace.Mask) & PRIVATE_DIRECTORY_ACCESS != PRIVATE_DIRECTORY_ACCESS:
                return False
            sid_pointer = ctypes.cast(
                ctypes.c_void_p(ctypes.addressof(ace) + ACCESS_ALLOWED_ACE.SidStart.offset), wintypes.LPVOID
            )
            if not current_api().EqualSid(sid_pointer, owner.pointer):
                return False
        return True
    finally:
        if descriptor:
            current_api().LocalFree(descriptor)


def _exists(path: str) -> bool:
    """Presence probe that does not follow a reparse point.

    ``GetFileAttributesW`` reports a reparse point's own attributes, so a
    junction counts as present (and then denies when opened, as it must). An
    ``ERROR_ACCESS_DENIED`` counts as present because the following open
    reports the precise denial; anything else is absent.
    """

    attributes = int(current_api().GetFileAttributesW(path))
    if attributes != INVALID_FILE_ATTRIBUTES:
        return True
    return _last_error() & 0xFFFF == 5


def _create_directory(path: str, owner: _SidBuffer) -> None:
    """Create one directory with the owner-only descriptor; races are tolerated."""

    security = _private_descriptor(owner, directory=True)
    try:
        created = current_api().CreateDirectoryW(path, ctypes.byref(security))
    finally:
        _free_descriptor(security)
    if created:
        return
    error = _last_error() & 0xFFFF
    if error != ERROR_ALREADY_EXISTS:  # a racing creator; the opener validates it
        raise _denial_for_code(error)


def _private_chain_start(drive: str, parts: Sequence[str], owner: _SidBuffer) -> int:
    """Index of the first component that must prove an owner-only DACL.

    Everything above it is inherited profile structure (``C:\\Users\\<user>``,
    ``AppData``, ``Local``) and is only opened without following links:
    demanding the OS profile directory be owner-only would reject every normal
    data root. The chain starts at the first missing component, or at the first
    existing component that already carries the owner-only protected DACL.
    """

    for index in range(1, len(parts) + 1):
        path = _qualified(drive, parts[:index])
        if not _exists(path):
            return index
        with _opened(path, directory=True, access=READ_CONTROL | DIRECTORY_SHARE_ACCESS, share=PINNED_SHARE) as (
            handle,
            _info_before,
        ):
            if _acl_is_owner_only(handle.value, owner):
                return index
    # Every component exists and none is ours yet: the leaf is the one that has
    # to be proven private, so a shared existing cache root is rejected below.
    return len(parts)


def ensure_private_directory(root) -> str:
    """Create/validate a cache root: pinned ancestors, owner-only private chain.

    Missing components are created with an explicit
    ``D:P(A;OICI;FA;;;<owner>)`` descriptor; every existing component from the
    first private one downward must carry that protected owner-only DACL or the
    whole call denies. Returns the canonical drive-absolute path.
    """

    _require_windows()
    drive, parts = _split_absolute(root)
    owner = _token_owner_sid()
    start = _private_chain_start(drive, parts, owner)
    for index in range(1, len(parts) + 1):
        path = _qualified(drive, parts[:index])
        private = index >= start
        if private and not _exists(path):
            _create_directory(path, owner)
        with _opened(
            path,
            directory=True,
            access=(READ_CONTROL | DIRECTORY_SHARE_ACCESS) if private else DIRECTORY_SHARE_ACCESS,
            share=PINNED_SHARE,
        ) as (handle, _info_before):
            if private and not _acl_is_owner_only(handle.value, owner):
                raise WindowsFsError(deny_insecure_cache())
    return _qualified(drive, parts)


def create_private_directory(root, relative) -> str:
    """Create one new owner-only subdirectory beneath a validated cache root."""

    _require_windows()
    drive, parts = _split_absolute(root)
    leaf = _relative_parts(relative)
    if len(leaf) != 1:
        raise WindowsFsError(deny_relative())
    owner = _token_owner_sid()
    base = ensure_private_directory(_qualified(drive, parts))
    path = _qualified(base, leaf)
    if _exists(path):
        raise WindowsFsError(deny_insecure_cache(), ERROR_ALREADY_EXISTS)
    _create_directory(path, owner)
    with _opened(path, directory=True, access=READ_CONTROL | DIRECTORY_SHARE_ACCESS, share=PINNED_SHARE) as (
        handle,
        _info_before,
    ):
        if not _acl_is_owner_only(handle.value, owner):
            raise WindowsFsError(deny_insecure_cache())
    return path


def _open_private(
    root,
    relative,
    *,
    access: int,
    creation: int,
    directory: bool = False,
    share: int = FILE_SHARE_READ,
) -> tuple[_Handle, _Info]:
    """Open one cache object and prove its own DACL.

    The cache root is validated first; then the object itself is opened with
    the requested rights and must carry the same owner-only DACL, so a file
    dropped in from outside by another account is refused even inside a
    correct directory.
    """

    canonical = ensure_private_directory(root)
    owner = _token_owner_sid()
    leaf = _relative_parts(relative)
    path = _qualified(canonical, leaf)
    security = _private_descriptor(owner, directory=directory) if creation == CREATE_NEW else None
    try:
        handle = _Handle(
            _open_handle(
                path,
                directory=directory,
                access=access,
                share=share,
                creation=creation,
                security=ctypes.byref(security) if security is not None else None,
            )
        )
    finally:
        if security is not None:
            _free_descriptor(security)
    try:
        info = _info(handle.value)
        if info.is_directory != directory:
            raise WindowsFsError(deny_identity())
        if not _acl_is_owner_only(handle.value, owner):
            raise WindowsFsError(deny_insecure_cache())
    except BaseException:
        handle.close()
        raise
    return handle, info


def open_private_file(root, relative, *, create: bool = False, share: int = FILE_SHARE_READ) -> _Handle:
    """Open a cache/installer-state file, creating it owner-only when asked.

    Without ``create`` the file must exist. With ``create`` an absent file is
    created with the owner-only file DACL; either way the returned handle must
    prove the owner-only DACL first.

    ``share`` defaults to read-only sharing, i.e. an exclusive writer.
    ``share=FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE`` is what a
    live SQLite database needs (it reopens its own file); the owner-only DACL
    and the separate lock file keep the exclusion.
    """

    _require_windows()
    # A private file is always readable by its owner; a share mode without
    # FILE_SHARE_READ would make the file unreadable for everyone else.
    if type(share) is not int or not 0 < share <= 0x7 or not share & FILE_SHARE_READ:
        raise WindowsFsError(deny_unsafe())
    access = FILE_READ_DATA | FILE_WRITE_DATA | FILE_READ_ATTRIBUTES | SYNCHRONIZE
    if create:
        try:
            handle, _info_before = _open_private(root, relative, access=access, creation=CREATE_NEW, share=share)
            return handle
        except WindowsFsError as exc:
            if exc.winerror & 0xFFFF != ERROR_ALREADY_EXISTS:  # exists -> open it
                raise
    handle, _info_after = _open_private(root, relative, access=access, creation=OPEN_EXISTING, share=share)
    return handle


def create_private_file(root, relative, data: bytes) -> None:
    """Create a new owner-only cache file with exact bytes; never overwrites."""

    _require_windows()
    if not isinstance(data, bytes):
        raise WindowsFsError(deny_unsafe())
    handle, _info_before = _open_private(
        root,
        relative,
        access=FILE_READ_DATA | FILE_WRITE_DATA | FILE_READ_ATTRIBUTES | SYNCHRONIZE,
        creation=CREATE_NEW,
        share=FILE_SHARE_READ,
    )
    try:
        _write(handle.value, data)
    finally:
        handle.close()


def _write(value: int, data: bytes) -> None:
    written = wintypes.DWORD(0)
    offset = 0
    while offset < len(data):
        buffer = ctypes.create_string_buffer(data[offset:], len(data) - offset)
        _call(
            current_api().WriteFile(value, buffer, len(data) - offset, ctypes.byref(written), None),
            deny_permission(),
        )
        if written.value == 0:
            raise WindowsFsError(deny_permission())
        offset += int(written.value)


@contextmanager
def _private_temp(root, name: str, *, access: int):
    """Create one owner-only temporary sibling and always clean it up."""

    temporary = f".{name}.{os.urandom(8).hex()}.tmp"
    handle, _info_before = _open_private(root, temporary, access=access, creation=CREATE_NEW, share=FILE_SHARE_READ)
    published = False
    try:
        yield temporary, handle
        published = True
    finally:
        handle.close()
        if not published:
            try:
                delete_private_file(root, temporary)
            except WindowsFsError:
                pass


def replace_private_file(root, relative, data: bytes, *, attempts: int = 4) -> None:
    """Publish ``data`` at ``relative`` inside one private cache root, atomically.

    The bytes go to a fresh owner-only sibling (``CREATE_NEW``), are flushed by
    closing, and the closed temp file is renamed onto the target. A reader
    therefore sees either the old or the new state file, never a mix; no mode
    bits are involved, so nothing depends on ``chmod`` on Windows.
    """

    _require_windows()
    if not isinstance(data, bytes):
        raise WindowsFsError(deny_unsafe())
    canonical = ensure_private_directory(root)
    target = _relative_parts(relative)
    if len(target) != 1:
        raise WindowsFsError(deny_relative())
    access = FILE_READ_DATA | FILE_WRITE_DATA | FILE_READ_ATTRIBUTES | DELETE | SYNCHRONIZE
    last: WindowsFsError | None = None
    for _ in range(max(1, int(attempts))):
        with _private_temp(canonical, target[0], access=access) as (temporary, handle):
            _write(handle.value, data)
        temporary_path = _qualified(canonical, (temporary,))
        try:
            os.replace(temporary_path, _qualified(canonical, target))
            return
        except OSError as exc:
            last = _denial_for_code(getattr(exc, "winerror", None) or 32)
        finally:
            if _exists(temporary_path):
                delete_private_file(canonical, temporary)
    raise WindowsFsError(
        last.reason if last is not None else deny_insecure_cache(), last.winerror if last else 0
    ) from None


def delete_private_file(root, relative) -> None:
    """Remove one owner-only cache file after proving its DACL; missing denies.

    ``FILE_DISPOSITION_INFO`` on the already-validated handle is used so the
    deleted object is exactly the one whose ACL was verified; an immutable file
    (which rejects that class) is retried through ``FILE_DISPOSITION_INFO_EX``
    with the POSIX-semantics delete flag, matching POSIX unlink semantics.
    """

    _require_windows()
    handle, _info_before = _open_private(
        root,
        relative,
        access=DELETE | FILE_READ_ATTRIBUTES | SYNCHRONIZE,
        creation=OPEN_EXISTING,
        share=FILE_SHARE_READ,
    )
    try:
        info = FILE_DISPOSITION_INFO(1)
        if not _soft(
            current_api().SetFileInformationByHandle(
                handle.value, FILE_DISPOSITION_INFO_CLASS, ctypes.byref(info), ctypes.sizeof(info)
            )
        ):
            extended = FILE_DISPOSITION_INFO_EX(0x1 | 0x2)  # DELETE | POSIX_SEMANTICS
            _call(
                current_api().SetFileInformationByHandle(
                    handle.value,
                    FILE_DISPOSITION_INFO_EX_CLASS,
                    ctypes.byref(extended),
                    ctypes.sizeof(extended),
                ),
                deny_permission(),
            )
    finally:
        handle.close()


class PrivateCacheRoot:
    """Session pin for one owner-private cache directory.

    ``ensure_private_directory`` validates/creates the chain; the directory is
    then opened once as a pinned ancestor handle (no delete sharing) that stays
    alive for the session. Every later sidecar or lock open re-checks the
    directory identity, so a swap after the pin denies instead of landing in a
    replacement directory, and the pinned handle keeps the directory itself
    from being renamed or removed underneath the SQLite files.
    """

    __slots__ = ("_handle", "_identity", "_path")

    def __init__(self, root) -> None:
        _require_windows()
        self._path = ensure_private_directory(root)
        self._handle = _Handle(
            _open_handle(
                self._path,
                directory=True,
                access=READ_CONTROL | DIRECTORY_SHARE_ACCESS,
                share=PINNED_SHARE,
                creation=OPEN_EXISTING,
            )
        )
        try:
            info = _info(self._handle.value)
            if not info.is_directory or not _acl_is_owner_only(self._handle.value, _token_owner_sid()):
                raise WindowsFsError(deny_insecure_cache())
        except BaseException:
            self._handle.close()
            raise
        self._identity = RootIdentity(info.volume_serial, info.file_index)

    @property
    def path(self) -> str:
        """Canonical cache directory; pinned for the session's lifetime."""

        return self._path

    @property
    def identity(self) -> RootIdentity:
        return self._identity

    @property
    def closed(self) -> bool:
        return self._handle.closed

    def verify(self) -> None:
        """Fail unless the pinned directory still is the approved one.

        The pin denies delete sharing for its whole lifetime, so a successful
        rename/remove of this path cannot have happened: the identity is read
        back from the *pinned handle*, which no second open needs to touch.
        """

        if self._handle.closed:
            raise WindowsFsError(deny_identity())
        info = _info(self._handle.value)
        if not info.is_directory or info.identity != self._identity.as_tuple():
            raise WindowsFsError(deny_identity())

    def open_file(self, name: str, *, create: bool = False, share: int = FILE_SHARE_READ) -> _Handle:
        """Open a direct child file (database, sidecar, lock) with its DACL proven."""

        if self._handle.closed:
            raise WindowsFsError(deny_identity())
        parts = _relative_parts(name)
        if len(parts) != 1:
            raise WindowsFsError(deny_relative())
        self.verify()
        return open_private_file(self._path, parts[0], create=create, share=share)

    def child_path(self, name: str) -> str:
        """Drive-absolute path of one direct child; SQLite needs a real path."""

        parts = _relative_parts(name)
        if len(parts) != 1:
            raise WindowsFsError(deny_relative())
        return _qualified(self._path, parts)

    def prepare_child_directory(self, name: str) -> str:
        """Validate (or create) one owner-only direct child directory."""

        parts = _relative_parts(name)
        if len(parts) != 1:
            raise WindowsFsError(deny_relative())
        self.verify()
        path = _qualified(self._path, parts)
        if not _exists(path):
            create_private_directory(self._path, parts[0])
        with _opened(path, directory=True, access=READ_CONTROL | DIRECTORY_SHARE_ACCESS, share=PINNED_SHARE) as (
            handle,
            _info_before,
        ):
            if not _acl_is_owner_only(handle.value, _token_owner_sid()):
                raise WindowsFsError(deny_insecure_cache())
        return path

    def close(self) -> None:
        self._handle.close()

    def __enter__(self) -> "PrivateCacheRoot":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()
