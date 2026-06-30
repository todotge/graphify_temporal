# graphify-temporal: Architecture & Specification

**Version:** 1.0.0
**Status:** implemented
**Date:** 2026-06-11

Post-processing extension that enriches a graphify knowledge graph
(`graphify-out/graph.json`) with temporal metadata from filesystem timestamps.
Zero runtime dependencies, stdlib only, Python ≥ 3.10.

## Purpose

Adds `file_mtime`, `file_birthtime`, `dir_mtime` attributes to graph nodes and
deterministic `preceded_by` edges ordered by timestamp and line number.

Also auto-detects AI coding assistants and injects instructions via
`graphify-temporal install` so agents know how to run temporal enrichment
without manual setup.

## Documentation index

| Document | Contents |
|----------|----------|
| [cli-reference.md](cli-reference.md) | Full CLI: `enrich`, `install`, `uninstall`, every flag, examples, error codes |
| [timestamps.md](timestamps.md) | Timestamp semantics, birthtime support, switching modes, node/edge schema, deduplication |
| [team-setup.md](team-setup.md) | `install`/`uninstall`, client detection, OpenCode plugin, team workflow |
| **This file** | Architecture, data flow, test plan, dependencies, non-goals |

## File tree

```
graphify_temporal/
├── __init__.py          # __version__ string
├── __main__.py          # CLI: argparse + dispatch + stats printing
├── enricher.py          # core: load graph.json, stamp nodes, build edges, save
├── fs.py                # pure helpers: resolve_mtime, resolve_birthtime,
│                          resolve_dir_mtime, matches_glob, is_excluded, parse_date,
│                          _birthtime_via_statx (Linux ctypes statx)
├── install.py           # auto-detect AI clients, inject/remove instruction blocks,
│                          register OpenCode plugin
└── query.py             # query nodes by label/time, build timeline, temporal stats
tests/
├── __init__.py
├── test_enricher.py     # TestFs (11) + TestEnricher (15), all via tmp_path
├── test_install.py      # block manipulation (6) + filesystem injection (15)
└── test_query.py        # query_nodes (12) + timeline (6) + stats (5) + CLI (3)
docs/
├── spec.md              # This file — architecture overview
├── cli-reference.md     # Full CLI reference
├── timestamps.md        # Timestamp semantics & switching modes
└── team-setup.md        # Agent instruction injection
```

## Components

| Module | Responsibility | Public API |
|--------|---------------|-----------|
| `__main__.py` | CLI parsing, dispatch to `enrich()` / `install()` / `uninstall()`, stats printing | `main()` |
| `enricher.py` | Load graph.json, stamp nodes, build edges, save, regenerate outputs | `enrich()`, `regenerate_outputs()` |
| `fs.py` | Stat for mtime/ctime/birthtime, dir mtime, glob filtering, date parsing | `resolve_mtime()`, `resolve_birthtime()`, `resolve_dir_mtime()`, `matches_glob()`, `is_excluded()`, `parse_date()` |
| `install.py` | Detect clients (11 platforms), inject/remove instruction blocks, register OpenCode plugin | `detect()`, `install()`, `uninstall()` |
| `query.py` | Query nodes by label/time, build timeline from preceded_by edges, temporal stats | `query_nodes()`, `build_timeline()`, `temporal_stats()` |

## Data flow (`enrich`)

1. Load `graphify-out/graph.json` from project root (NetworkX node-link format, `links` key for edges)
2. Collect unique `source_file` values from all nodes
3. Filter by `--since`, `--include`, `--exclude` if provided
4. For each source file, stat the filesystem (UTC timestamps via `time.gmtime`):
   - Default: `st_mtime` → `file_mtime` (ISO 8601, UTC)
   - `--use-ctime`: `st_ctime` → `file_mtime` (UTC)
   - `--use-birthtime`: `st_birthtime` → `file_mtime` (UTC, Linux via ctypes `statx(2)`)
   - `--include-dir-mtime`: also writes `dir_mtime` (UTC)
5. Intra-file: sort nodes by `(_extract_line, node_id)` → `preceded_by` edges
6. Cross-file (opt-in): sort files by resolved timestamp, chain first nodes
7. If `--dry-run`: return stats dict without writing
8. Deduplicate by `(source, target, relation)` triple against existing `links`
9. Write `graph.json` with `json.dumps(data, ensure_ascii=False)`
10. Default: regenerate `graph.html` and `wiki/` via `graphify export` subprocess

## Data flow (`install`)

1. Scan project root for client markers (e.g. `CLAUDE.md`, `.opencode/`, `GEMINI.md`)
2. For each detected client, write or update a `## graphify-temporal` block in the instruction file
3. For OpenCode: also write `.opencode/plugins/graphify-temporal.js` and register in `opencode.json`

## graph.json conventions

- Edge array keyed `links`, **not** `edges` — NetworkX node-link format
- Top-level: `{"directed": bool, "multigraph": bool, "graph": {}, "nodes": [...], "links": [...], "hyperedges": [...]}`
- `json.dumps(data, ensure_ascii=False)` — non-ASCII content must survive round-trips
- Edge dedup: `(source, target, relation)` triple, not just source/target

## Error handling

| Scenario | Behavior |
|----------|----------|
| `graph.json` missing | `FileNotFoundError`: "Run `graphify .` first." |
| graph.json corrupted | `ValueError` with file path and parse details |
| Source file deleted | `file_mtime = null`, counted in `files_not_found` |
| `--use-ctime` and `--use-birthtime` together | CLI rejects with error, exit 1 |
| Birthtime unavailable | `file_mtime = null`, never a crash |
| `--since` with invalid date | `ValueError` at parse time, friendly message |
| Empty graph | 0 enrichments, 0 edges — no crash |
| Re-run (idempotency) | Edges deduplicated, `edges_deduped` reported |
| No `source_file` on node | Skipped silently |
| `regenerate_outputs` when `graphify` not on PATH | Subprocess fails silently, returns `False` |

## Test Plan

### TestFs — unit (11 tests)

| Test | Coverage |
|------|----------|
| `resolve_mtime` returns ISO 8601 | Existing file → valid string |
| `resolve_mtime` missing file | Non-existent → None |
| `resolve_mtime` ctime flag | `use_ctime=True` → valid ISO 8601 |
| `resolve_birthtime` returns ISO 8601 | Existing file on ext4 → via statx |
| `resolve_birthtime` missing file | Non-existent → None |
| `resolve_dir_mtime` returns ISO 8601 | Parent dir of existing file → valid |
| `resolve_dir_mtime` missing parent | Non-existent parent → None |
| `matches_glob` inclusion | Inside/outside/no-patterns |
| `is_excluded` exclusion | Inside/outside/no-patterns |
| `parse_date` valid | `2026-05-15` → float |
| `parse_date` invalid | `15-05-2026` → ValueError |

### TestEnricher — integration (15 tests)

| Test | Coverage |
|------|----------|
| Basic enrichment | Nodes stamped, intra-file edges created |
| `--use-ctime` | st_ctime used as primary |
| `--use-birthtime` | st_birthtime used as primary |
| `--include-dir-mtime` | dir_mtime written to nodes |
| Both birthtime + dir_mtime | Combine correctly |
| `--cross-file` | Inter-file edges created |
| `--dry-run` | Stats returned, graph.json unchanged |
| `--since` filter | Old files skipped |
| `--include` / `--exclude` | Glob filtering works |
| Missing graph | FileNotFoundError |
| Missing source file | Null mtime, not a crash |
| Empty graph | Zero total, zero edges |
| No source_files | nodes_enriched == 0 |
| Idempotency | Re-run: edges deduplicated |
| Preserves existing edges | Non-temporal edges survive |

### TestInstall — integration (21 tests)

See [team-setup.md](team-setup.md) for the install flow.  Tests cover:
block injection, replacement, removal, idempotency, client detection
(11 platforms), Cursor `.mdc` path, Windsurf `.md` path, OpenCode plugin
install/uninstall, CLI help, and edge cases (no markers, missing files,
empty files).

### TestQuery — integration (26 tests)

| Group | Tests | Coverage |
|-------|-------|----------|
| Unit | 6 | `_parse_date_ts`, `_ts_from_node` edge cases |
| query_nodes | 9 | Search, since/before filters, ordering, empty timestamps, dir_mtime |
| build_timeline | 6 | Basic chain, start_id, since filter, non-preceded_by edges, cycles |
| temporal_stats | 5 | Basic stats, empty graph, dir_mtime, gaps, JSON output |
| CLI | 3 | `--help` output for query, timeline, stats |

## Dependencies

| Scope | Dependency |
|-------|-----------|
| Runtime | Python ≥ 3.10, stdlib only (`json`, `re`, `pathlib`, `os`, `fnmatch`, `argparse`, `ctypes`, `subprocess`) |
| Optional regeneration | `graphify` CLI on PATH |
| Dev | `pytest` |

## Non-Goals (v1)

- No bidirectional temporal edges (always source → target in chronological order)
- No git history integration (blame/commit dates)
- No CI/CD pipeline
- No PyPI publication (pip install via git URL)
