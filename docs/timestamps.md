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

All timestamp fields are ISO 8601 strings (`2026-06-11T19:45:25`) or `null`
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
  birthtime: 2026-06-11T19:45:25  ← file was created
  dir_mtime: 2026-06-11T19:47:14  ← arrived in its current directory
  mtime:     2026-06-11T20:12:29  ← last content change
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
  "file_mtime": "2026-06-11T19:45:25",
  "dir_mtime": "2026-06-11T19:47:14"
}
```

| Field | Type | When present |
|-------|------|-------------|
| `file_mtime` | `string \| null` | Always (mtime by default, ctime via `--use-ctime`, birthtime via `--use-birthtime`) |
| `dir_mtime` | `string \| null` | Only when `--include-dir-mtime` is set |

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
