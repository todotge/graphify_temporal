"""Git-derived timestamps: author-dates from git log/blame instead of stat(2).

On a `git clone`d repo, stat(2) mtime/birthtime reflect the checkout moment,
not the file's real history — every file lands with nearly the same
timestamp. When a source_file is tracked by git, its real history lives in
git log/blame instead.

Mirrors fs.py's contract: every function returns None on any failure
(missing git binary, not a repo, untracked file, corrupted .git, timeout)
so a single bad file never crashes the enrichment pipeline. All subprocess
calls use argument lists — never shell=True, never string interpolation.
"""

import re
import subprocess
import time
from functools import lru_cache
from pathlib import Path
from shutil import which


@lru_cache(maxsize=1)
def git_available() -> bool:
    """Return True when a ``git`` binary is on PATH."""
    return which("git") is not None


def find_repo_root(start: Path) -> Path | None:
    """Return the git working-tree root containing *start*, or None.

    Uses ``git -C <start> rev-parse --show-toplevel`` so a monorepo
    subdirectory correctly resolves to the enclosing repo root rather than
    the caller's own path. Returns None when *start* isn't inside a git
    working tree, git is missing, or the call fails/times out.
    """
    if not git_available():
        return None
    try:
        result = subprocess.run(
            ["git", "-C", str(start), "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    top = result.stdout.strip()
    if not top:
        return None
    try:
        return Path(top).resolve()
    except OSError:
        return None


def is_shallow_repo(repo_root: Path) -> bool:
    """Return True when *repo_root* is a shallow clone (e.g. ``--depth 1``).

    Single stat, no subprocess: checks for ``.git/shallow``.
    """
    try:
        return (repo_root / ".git" / "shallow").exists()
    except OSError:
        return False


def _iso_from_epoch(ts: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


def resolve_file_date(
    repo_root: Path, relpath: str, mode: str = "last",
) -> str | None:
    """Return the git author-date of *relpath* as ISO 8601, or None.

    mode="last"  — date of the most recent commit touching the file
                   (``git log -1``).  Valid even in a shallow clone: the
                   newest commit's author-date is real regardless of how
                   much history was fetched.
    mode="first" — date of the oldest known commit (creation date).
                   Refused (returns None) on a shallow clone, since the
                   shallow boundary would masquerade as a fake creation
                   date rather than the file's true origin.

    Returns None when git log prints nothing (untracked/new file), the
    call fails, or times out.
    """
    if not git_available():
        return None
    if mode == "first" and is_shallow_repo(repo_root):
        return None
    args = ["git", "-C", str(repo_root), "log", "--follow", "--format=%aI"]
    if mode == "last":
        args.insert(4, "-1")
    args += ["--", relpath]
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    lines = [l for l in result.stdout.splitlines() if l.strip()]
    if not lines:
        return None
    raw = lines[0] if mode == "last" else lines[-1]
    return _normalize_aI(raw)


def _normalize_aI(raw: str) -> str | None:
    """Convert git's %aI (RFC3339 with offset) to the fs.py UTC ...Z shape."""
    try:
        from datetime import datetime
        dt = datetime.fromisoformat(raw.strip())
        return _iso_from_epoch(dt.timestamp())
    except (ValueError, TypeError):
        return None


_BLAME_AUTHOR_TIME_RE = re.compile(r"^author-time (\d+)$")
_BLAME_AUTHOR_RE = re.compile(r"^author (.+)$")


def blame_file(repo_root: Path, relpath: str) -> dict[int, str] | None:
    """Return {line_number: iso_author_date} for every line in *relpath*.

    One `git blame --porcelain` subprocess call parses the whole file's
    attribution in one shot — callers must NOT call this per-line/per-node.
    Returns None when the file is untracked, binary, deleted in HEAD, or
    the call fails/times out.
    """
    if not git_available():
        return None
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_root), "blame", "--porcelain", "--", relpath],
            capture_output=True, text=True, timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None

    # Porcelain repeats a commit's metadata (author-time etc.) only the
    # first time that SHA appears; later line-groups blamed to the same
    # commit show just the line-header. Cache author-time per SHA so those
    # later groups still resolve correctly.
    commit_times: dict[str, float] = {}
    dates: dict[int, str] = {}
    current_sha = ""
    current_line = 0
    for line in result.stdout.splitlines():
        m = _BLAME_AUTHOR_TIME_RE.match(line)
        if m:
            if current_sha:
                commit_times[current_sha] = float(m.group(1))
            continue
        # A porcelain line-header looks like: <sha> <orig-line> <final-line> [group-size]
        parts = line.split(" ")
        if len(parts) >= 3 and len(parts[0]) == 40 and all(c in "0123456789abcdef" for c in parts[0]):
            current_sha = parts[0]
            try:
                current_line = int(parts[2])
            except ValueError:
                continue
            ts = commit_times.get(current_sha)
            if ts is not None:
                dates[current_line] = _iso_from_epoch(ts)
            continue
        # Not a line-header: could be a metadata field (author, summary,
        # filename, ...) or the actual source line (prefixed by a tab).
        # Either way, if we already know this commit's time, backfill now
        # in case the header appeared before author-time was cached.
        if current_sha in commit_times and current_line not in dates:
            dates[current_line] = _iso_from_epoch(commit_times[current_sha])

    return dates or None


def resolve_file_author(repo_root: Path, relpath: str) -> str | None:
    """Return the author name of the most recent commit touching *relpath*.

    One extra `git log -1` call per unique file (same O(unique_files) bound
    as resolve_file_date) — not derived from blame_file's cache since a
    file's most-recent-commit author and its per-line blame authors can
    differ; this answers "who last touched the whole file".
    """
    if not git_available():
        return None
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_root), "log", "-1", "--format=%an", "--", relpath],
            capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    name = result.stdout.strip()
    return name or None


def resolve_line_date(blame_map: dict[int, str] | None, line: int) -> str | None:
    """Pure dict lookup — no subprocess. line=0 (no :L marker) always misses."""
    if not blame_map or line <= 0:
        return None
    return blame_map.get(line)
