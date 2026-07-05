# Timestamp semantics & switching modes

## Which timestamps can you see?

graphify-temporal resolves three distinct timestamps from the filesystem.
Each gets written to a different node attribute depending on the flag.

| Attribute | Flag | Meaning |
|-----------|------|---------|
| `file_mtime` | _(default)_ | Last content modification |
| `file_mtime` | `--use-ctime` | Inode metadata change (Unix) / creation (Windows) |
| `file_mtime` | `--use-birthtime` | True creation time — when the file was born on disk |
| `dir_mtime` | `--include-dir-mtime` | Parent directory mtime — best proxy for arrival time |

All timestamp fields are ISO 8601 strings (`2026-06-11T19:45:25Z`) or `null`
when the source file is missing, inaccessible, or the requested timestamp is
unavailable on the platform.

Timestamps are stored in **UTC** — `--since` and `--before` filters compare
against UTC epoch values, so date boundaries are consistent regardless of the
machine's local timezone.

## The timeline

The three timestamps are often different, giving you a timeline:

```
created → arrived in this directory → last modified
```

Example from this repo:

```
file: graphify_temporal/__init__.py
  birthtime: 2026-06-11T19:45:25Z  ← file was created
  dir_mtime: 2026-06-11T19:47:14Z  ← arrived in its current directory
  mtime:     2026-06-11T20:12:29Z  ← last content change
```

## Birth time (`st_birthtime`)

Birth time is the true creation time — distinct from `st_ctime` which is
metadata-change time on Unix.  Resolution tries, in order:

1. `os.stat().st_birthtime` — natively available on macOS, Windows, and
   CPython ≥ 3.13 on Linux.
2. Linux only: `statx(2)` syscall via ctypes — works on kernels ≥ 4.11 with
   filesystems that store birthtime (ext4, btrfs, xfs).
3. Falls back to `None` when birthtime is unavailable — never a crash.

### Platform support

| Platform | Method | Requirement |
|----------|--------|-------------|
| Linux | `statx(2)` via ctypes | Kernel ≥ 4.11, ext4/btrfs/xfs |
| macOS | `os.stat().st_birthtime` | macOS ≥ 10.4 |
| Windows | `os.stat().st_birthtime` | native |

## Git-derived timestamps (`--git`)

### Why filesystem timestamps fail on cloned repos

`file_mtime`/`file_birthtime` come from `stat()` — they reflect what happened
to the file **on this disk**. On a repo obtained via `git clone` (GitHub,
GitLab, a CI checkout, anything), every file is written to disk at checkout
time, so `mtime`/`birthtime` cluster around the moment of the clone —
regardless of when the code was actually written. The default enrichment is
close to useless for tracing real history on a freshly cloned repo.

`--git` fixes this by reading dates out of the repo's own commit history
instead of the filesystem:

```bash
graphify-temporal enrich --git
```

### Resolution order

For each unique `source_file`:

1. Is `--git` set, and is `git` on PATH, and is the project root inside a git
   working tree? If any of these is false, skip straight to the normal
   filesystem resolution (`file_mtime` behaves exactly as without `--git`).
2. Is the file tracked by git (`git log --follow -- <file>` returns
   something)? If not (new/untracked/staged-only file), fall back to stat for
   that file only — every other git-resolved file is unaffected.
3. File-level date: `git log -1 --follow --format=%aI -- <file>` → the most
   recent commit's author-date. This is what lands in `file_mtime` when git
   resolution succeeds — **as a drop-in replacement for the stat value**, same
   field, same ISO 8601 UTC shape.
4. Per node, line-level date: `git blame --porcelain -- <file>` (one
   subprocess call per unique file, not per node) parsed into a `{line:
   date}` map. If the node's `source_location` line resolves in that map,
   its `git_commit_date` is the commit that last touched that exact line —
   more precise than the file-level date, since a file with 500 lines can
   have functions written years apart. Falls back to the file-level date if
   the line lookup misses (e.g. `source_location` has no line marker, as with
   doc/concept nodes).
5. `git_author`: one extra `git log -1 --format=%an -- <file>` per unique
   file — the author of the most recent commit touching the whole file (not
   derived from blame, since "who last touched the file" and "who wrote this
   specific line" are different questions).

### Why `git_commit_date` is a new field, not a `file_mtime` override

Only the **file-level** git date replaces `file_mtime` (same meaning: "when
was this file last touched", just from a better source). The **line-level**,
more precise date goes into a separate `git_commit_date` field instead of
also overwriting `file_mtime`, so that:

- `query.py`'s `--since`/`--before`, `--order`, and `temporal_stats()` keep
  reading `file_mtime` with unchanged semantics regardless of whether the
  graph was enriched with `--git` or not.
- Nothing existing needs to change to keep working — `git_commit_date` is
  purely additive, ignored by every consumer that doesn't know about it yet.

### Shallow clones (`--depth 1`)

A shallow clone truncates history — the oldest commit git can see is really
just the shallow boundary, not the file's true first commit. `--git` detects
this (`.git/shallow` present) and only refuses the "first commit / creation
date" query in that case; the "most recent commit" date used by `enrich()`
is unaffected, since the newest commit's author-date is real regardless of
how much older history was fetched.

### Untracked files, non-repos, missing git binary

All three degrade the same way: silently fall back to filesystem timestamps
for the affected file(s), print at most one notice for the whole run (never
per-file), and never raise. `--git` on a plain non-git folder of documents/
PDFs/images behaves identically to not passing `--git` at all.

### Security

Every `source_file` is validated to resolve inside the git repo root
(`Path.relative_to()`) before it's ever used as a subprocess argument — this
rejects a path that tries to escape the repo (e.g. containing `../`) by
falling back to stat for that file, never passing untrusted input to `git`.
All git invocations use argument lists (never `shell=True`) and a `--`
separator before the path.

## Directory mtime (`dir_mtime`)

Directories update their `st_mtime` whenever an entry is added or removed.
Comparing `dir_mtime` against `file_mtime` or `file_birthtime` gives the
best proxy the filesystem can offer for "when did this file arrive in its
current location."

It's not a guarantee — multiple files added in quick succession share the
same directory mtime — but it's the best signal available without git
history or external instrumentation.

## Switching modes

Enrichment is **idempotent** — you can re-run `enrich` with different flags
anytime.  It updates attributes in-place and deduplicates edges.

```bash
# First pass: default modification time
graphify-temporal enrich

# Later: now you want to know when files arrived in their directories
graphify-temporal enrich --include-dir-mtime

# Later still: switch primary timestamp to true creation time
graphify-temporal enrich --use-birthtime

# Full timeline: birthtime as primary + directory arrival
graphify-temporal enrich --use-birthtime --include-dir-mtime
```

No need to rebuild the graph — just run enrichment again with the flags you
need.  Use `--dry-run` first to preview what will change.

## Node/Edge schema

### Node attributes added

```json
{
  "file_mtime": "2026-06-11T19:45:25Z",
  "dir_mtime": "2026-06-11T19:47:14Z",
  "git_commit_date": "2023-03-15T09:12:00Z",
  "git_author": "Mario"
}
```

| Field | Type | When present |
|-------|------|-------------|
| `file_mtime` | `string \| null` | Always (mtime by default, ctime via `--use-ctime`, birthtime via `--use-birthtime`, git author-date via `--git` when resolvable) |
| `dir_mtime` | `string \| null` | Only when `--include-dir-mtime` is set |
| `git_commit_date` | `string` | Only when `--git` is set AND the node's file resolved via git (omitted, not `null`, otherwise) — line-accurate via `git blame` when possible, file-level otherwise |
| `git_author` | `string` | Only when `--git` is set AND the file's author name resolved (omitted otherwise) |

### Edge format (within `links` array)

```json
{
  "source": "node_id",
  "target": "node_id",
  "relation": "preceded_by",
  "confidence": "EXTRACTED",
  "confidence_score": 1.0,
  "source_file": "path/to/file.py",
  "source_location": "path/to/file.py:L42",
  "weight": 1.0
}
```

All edges carry `confidence: EXTRACTED` / `confidence_score: 1.0` because
they come from a deterministic stat + sort, not from a model.

### Edge deduplication

An edge is considered duplicate when `(source, target, relation)` already
exists in the graph — not just source/target.  This is what makes enrichment
idempotent.
