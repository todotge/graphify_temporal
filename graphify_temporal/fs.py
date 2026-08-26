"""Filesystem helpers: stat timestamps, glob pattern matching, date parsing.

All of these are pure, side-effect-free wrappers around stdlib calls — no I/O
beyond a single stat(2) per file.
"""

import calendar
import datetime
import fnmatch
import os
import sys
import time
from pathlib import Path


def resolve_mtime(source_file: str, root: Path, use_ctime: bool = False) -> str | None:
    """Stat a file under root and return its mtime (or ctime) as ISO 8601.

    Returns None when the path doesn't exist or can't be stat'd so callers
    can safely assign file_mtime = None instead of crashing the whole pipeline
    on a single missing file.
    """
    if not source_file:
        return None
    fp = root / source_file
    try:
        st = fp.stat()
        ts = st.st_ctime if use_ctime else st.st_mtime
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))
    except (OSError, FileNotFoundError):
        return None


def matches_glob(path: str, patterns: list[str] | None) -> bool:
    """Return True when *path* matches at least one pattern.

    An empty or None pattern list means „include everything“ — this is the
    default when the user doesn't pass --include.
    """
    if not patterns:
        return True
    return any(fnmatch.fnmatch(path, p) for p in patterns)


def is_excluded(path: str, patterns: list[str] | None) -> bool:
    """Return True when *path* matches an exclusion pattern.

    Mirror of matches_glob with inverted default: if no patterns are given,
    nothing is excluded.
    """
    if not patterns:
        return False
    return any(fnmatch.fnmatch(path, p) for p in patterns)


def parse_date(date_str: str) -> float:
    """Parse YYYY-MM-DD into a Unix timestamp (float).

    Uses UTC — the date is interpreted as midnight UTC and converted to
    a UTC epoch via ``calendar.timegm``.  Rejects impossible dates like
    Feb 29 in non-leap years (``datetime.strptime`` raises ValueError).

    Used both for CLI validation (``__main__.py`` catches ValueError) and
    for the ``--since`` filter inside ``enrich()``.
    """
    try:
        dt = datetime.datetime.strptime(date_str, "%Y-%m-%d")
        return float(calendar.timegm(dt.timetuple()))
    except ValueError:
        raise ValueError(
            f"Invalid date format: '{date_str}'. Expected YYYY-MM-DD."
        )


def iso_to_epoch(iso: str) -> float | None:
    """Parse the ``...Z`` ISO 8601 shape (as written by resolve_mtime,
    resolve_birthtime, git_source.py) back to a UTC epoch float.

    Returns None on any malformed input — never raises. Shared by
    enricher.py's --since comparison and query.py's node timestamp reads,
    which both need the exact inverse of the gmtime-based format this
    module writes.
    """
    try:
        return float(calendar.timegm(time.strptime(iso, "%Y-%m-%dT%H:%M:%SZ")))
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# birth time — true creation time, distinct from st_ctime on Unix
# ---------------------------------------------------------------------------


def _birthtime_via_statx(fp: Path) -> float | None:
    """Retrieve st_birthtime on Linux via the statx(2) syscall.

    Uses ctypes to call statx(2) because CPython os.stat() on Linux ≤ 3.12
    uses the traditional stat(2) syscall which does not return birthtime.
    Returns epoch float or None when the kernel/filesystem doesn't support it
    (STATX_BTIME bit absent from stx_mask).
    """
    import ctypes

    AT_FDCWD = -100
    AT_SYMLINK_NOFOLLOW = 0x100
    STATX_BTIME = 0x800

    class _StatxTimestamp(ctypes.Structure):
        _fields_ = [("tv_sec", ctypes.c_longlong), ("tv_nsec", ctypes.c_uint)]

    class _Statx(ctypes.Structure):
        _fields_ = [
            ("stx_mask", ctypes.c_uint),
            ("stx_blksize", ctypes.c_uint),
            ("stx_attributes", ctypes.c_ulonglong),
            ("stx_nlink", ctypes.c_uint),
            ("stx_uid", ctypes.c_uint),
            ("stx_gid", ctypes.c_uint),
            ("stx_mode", ctypes.c_ushort),
            ("__pad0", ctypes.c_ushort),
            ("stx_ino", ctypes.c_ulonglong),
            ("stx_size", ctypes.c_ulonglong),
            ("stx_blocks", ctypes.c_ulonglong),
            ("stx_attributes_mask", ctypes.c_ulonglong),
            ("stx_atime", _StatxTimestamp),
            ("stx_btime", _StatxTimestamp),
            ("stx_ctime", _StatxTimestamp),
            ("stx_mtime", _StatxTimestamp),
            ("stx_rdev_major", ctypes.c_uint),
            ("stx_rdev_minor", ctypes.c_uint),
            ("stx_dev_major", ctypes.c_uint),
            ("stx_dev_minor", ctypes.c_uint),
            ("stx_mnt_id", ctypes.c_ulonglong),
            ("stx_dio_mem_align", ctypes.c_uint),
            ("stx_dio_offset_align", ctypes.c_uint),
            ("__spare2", ctypes.c_ulonglong * 12),
        ]

    try:
        libc = None
        for lib_name in ("libc.so.6", "libc.musl-x86_64.so.1", "libc.so"):
            try:
                libc = ctypes.CDLL(lib_name, use_errno=True)
                break
            except OSError:
                continue
        if libc is None:
            return None
        stx = _Statx()
        path_bytes = os.fsencode(fp)
        rc = libc.statx(
            AT_FDCWD, path_bytes, AT_SYMLINK_NOFOLLOW, STATX_BTIME,
            ctypes.byref(stx),
        )
        if rc == 0 and (stx.stx_mask & STATX_BTIME):
            return float(stx.stx_btime.tv_sec) + stx.stx_btime.tv_nsec / 1e9
    except (OSError, AttributeError, TypeError, ValueError):
        pass
    return None


def resolve_birthtime(source_file: str, root: Path) -> str | None:
    """Return the birth (creation) time of *source_file* as ISO 8601.

    Birth time is the moment the file was created on the filesystem — distinct
    from st_mtime (last content modification) and st_ctime (on Unix this is
    metadata-change time, not creation time).

    Resolution tries, in order:
    1. ``os.stat().st_birthtime`` — natively available on macOS, Windows,
       and CPython ≥ 3.13 on Linux.
    2. Linux only: ``statx(2)`` via ctypes — works on kernels ≥ 4.11 with
       filesystems that store birthtime (ext4, btrfs, xfs).
    3. Falls back to None when birthtime is unavailable.

    Returns None when the file is missing or the platform doesn't expose
    a birthtime at all.
    """
    if not source_file:
        return None
    fp = root / source_file
    try:
        st = fp.stat()
        bt = getattr(st, "st_birthtime", None)
        if bt is not None and bt > 0:
            return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(bt))
    except (OSError, FileNotFoundError):
        return None

    if sys.platform == "linux":
        bt = _birthtime_via_statx(fp)
        if bt is not None:
            return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(bt))

    return None


# ---------------------------------------------------------------------------
# directory mtime — proxy for "when did this file arrive here"
# ---------------------------------------------------------------------------


def resolve_dir_mtime(source_file: str, root: Path) -> str | None:
    """Return the mtime of the *parent directory* of source_file as ISO 8601.

    Directories update their mtime whenever an entry is added or removed.
    Comparing dir_mtime against file_mtime or file_birthtime gives the best
    proxy the filesystem can offer for "when did this file arrive in its
    current location".

    Returns None when the parent directory is inaccessible.
    """
    if not source_file:
        return None
    fp = root / source_file
    try:
        dir_st = fp.parent.stat()
        return time.strftime(
            "%Y-%m-%dT%H:%M:%SZ", time.gmtime(dir_st.st_mtime)
        )
    except (OSError, FileNotFoundError):
        return None
