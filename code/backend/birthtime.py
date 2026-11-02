"""
DataVault Dashboard - Birthtime (creation date) of a file on macOS

Finder and Photos show a file's creation date ("Aanmaakdatum"). os.utime can only set
the access and modification times; the birthtime is set here with setattrlist(2) and
ATTR_CMN_CRTIME, read back with getattrlist(2), to the nanosecond (Python 3.12's
os.stat has st_birthtime only as a float). Used by the export, so a copy keeps the
creation date of the original.

On another system, or a filesystem that has no birthtime or refuses it (FAT, some network
shares), the functions return None / False and change nothing; the caller keeps its own
fallback. Only ever called on files the app itself just wrote (export copies), never on
an archive file.
"""
import ctypes
import ctypes.util
import os
import struct
import sys
from typing import Optional

ATTR_BIT_MAP_COUNT = 5
ATTR_CMN_CRTIME = 0x00000200
FSOPT_NOFOLLOW = 0x00000001


class _AttrList(ctypes.Structure):
    _fields_ = [
        ("bitmapcount", ctypes.c_ushort),
        ("reserved", ctypes.c_uint16),
        ("commonattr", ctypes.c_uint32),
        ("volattr", ctypes.c_uint32),
        ("dirattr", ctypes.c_uint32),
        ("fileattr", ctypes.c_uint32),
        ("forkattr", ctypes.c_uint32),
    ]


class _Timespec(ctypes.Structure):
    _fields_ = [("tv_sec", ctypes.c_long), ("tv_nsec", ctypes.c_long)]


_libc = None


def _lib():
    global _libc
    if _libc is None:
        libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
        libc.getattrlist.argtypes = [ctypes.c_char_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_uint]
        libc.getattrlist.restype = ctypes.c_int
        libc.setattrlist.argtypes = [ctypes.c_char_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_uint]
        libc.setattrlist.restype = ctypes.c_int
        _libc = libc
    return _libc


def supported() -> bool:
    return sys.platform == "darwin"


def _crtime_list() -> _AttrList:
    attrs = _AttrList()
    attrs.bitmapcount = ATTR_BIT_MAP_COUNT
    attrs.commonattr = ATTR_CMN_CRTIME
    return attrs


def get_birthtime_ns(path) -> Optional[int]:
    """The file's birthtime in nanoseconds since 1970, or None where it cannot be read."""
    if not supported():
        return None
    attrs = _crtime_list()
    buf = ctypes.create_string_buffer(4 + ctypes.sizeof(_Timespec))
    try:
        rc = _lib().getattrlist(os.fsencode(path), ctypes.byref(attrs), buf, len(buf), FSOPT_NOFOLLOW)
    except (OSError, AttributeError):
        return None
    if rc != 0:
        return None
    length, sec, nsec = struct.unpack("=Iqq", buf.raw[:4 + 16])
    if length < 4 + 16:
        return None
    return sec * 1_000_000_000 + nsec


def set_birthtime_ns(path, ns: int) -> bool:
    """Set the file's birthtime (not following a symlink). True when the system took it."""
    if not supported() or ns is None:
        return False
    attrs = _crtime_list()
    sec, nsec = divmod(int(ns), 1_000_000_000)
    value = _Timespec(sec, nsec)
    try:
        rc = _lib().setattrlist(os.fsencode(path), ctypes.byref(attrs), ctypes.byref(value),
                                ctypes.sizeof(value), FSOPT_NOFOLLOW)
    except (OSError, AttributeError):
        return False
    return rc == 0


def filesystem_type(path) -> Optional[str]:
    """'apfs', 'hfs', 'msdos', 'exfat', 'smbfs', ... for the volume path is on (statfs(2)
    f_fstypename), or None off macOS or when it cannot be read."""
    if not supported():
        return None
    libc = _lib()
    statfs = getattr(libc, "statfs$INODE64", None) or libc.statfs   # Intel names the 64-bit one apart
    buf = ctypes.create_string_buffer(4096)                           # struct statfs is 2168 bytes
    try:
        if statfs(os.fsencode(path), buf) != 0:
            return None
    except (OSError, AttributeError):
        return None
    # f_fstypename (char[16]) follows 8 fields of 4 and 8 bytes: offset 72
    return buf.raw[72:88].split(b"\0", 1)[0].decode("ascii", "replace") or None
