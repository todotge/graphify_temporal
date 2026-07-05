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
Optionally (`--git`), derives dates from git history (`git log`/`git blame`)
instead of filesystem stat — necessary on cloned repos, where stat timestamps
reflect checkout time rather than real file history — adding `git_commit_date`
(line-accurate) and `git_author` node attributes.

Also provides `impact` — a read-only root-cause tracing query that walks
every edge relation in the graph (not just the temporal chain) between one
or two nodes, bounded by hop depth, ranked by structural + temporal
relevance. Built for "I changed X, then Y broke — what did I touch that
could have caused it?"

Also auto-detects AI coding assistants and injects instructions via
`graphify-temporal install` so agents know how to run temporal enrichment
(and reach for `impact` during debugging) without manual setup.

## Documentation index

| Document | Contents |
|----------|----------|
| [cli-reference.md](cli-reference.md) | Full CLI: `enrich`, `install`, `uninstall`, `query`, `timeline`, `stats`, `impact`, every flag, examples, error codes |
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
├── git_source.py        # pure helpers: git_available, find_repo_root, is_shallow_repo,
│                          resolve_file_date, blame_file, resolve_line_date,
│                          resolve_file_author (--git mode)
├── install.py           # auto-detect AI clients, inject/remove instruction blocks,
│                          register OpenCode plugin
└── query.py             # query nodes by label/time, build timeline, temporal stats,
│                          impact (root-cause tracing BFS)
tests/
├── __init__.py
├── test_enricher.py     # TestFs + TestGitSource (unit) + TestEnricher + TestEnrichGit
│                          (integration), all via tmp_path; git tests skip if no git binary
├── test_install.py      # block manipulation (6) + filesystem injection (18) + impact/git
│                          drift regression guards (2)
└── test_query.py        # TestParseDateTs (2) + TestTsFromNode (4) + query_nodes (12)
│                          + timeline (7) + stats (7) + TestImpactScore (9) + TestImpact (18)
│                          + CLI (6)
docs/
├── spec.md              # This file — architecture overview
├── cli-reference.md     # Full CLI reference
├── timestamps.md        # Timestamp semantics & switching modes
└── team-setup.md        # Agent instruction injection
```

## Components

| Module | Responsibility | Public API |
|--------|---------------|-----------|
| `__main__.py` | CLI parsing, dispatch to `enrich()` / `install()` / `uninstall()` / `query_nodes()` / `build_timeline()` / `temporal_stats()`, stats printing | `main()` |
| `enricher.py` | Load graph.json, stamp nodes, build edges, save, regenerate outputs | `enrich()`, `regenerate_outputs()` |
| `fs.py` | Stat for mtime/ctime/birthtime, dir mtime, glob filtering, date parsing, Linux statx via ctypes | `resolve_mtime()`, `resolve_birthtime()`, `resolve_dir_mtime()`, `matches_glob()`, `is_excluded()`, `parse_date()` |
| `git_source.py` | Git-derived timestamps: repo-root/shallow detection, file-level date (`git log`), line-level date (`git blame`, one call per unique file), author name. Every function returns `None` on any failure — never raises | `git_available()`, `find_repo_root()`, `is_shallow_repo()`, `resolve_file_date()`, `blame_file()`, `resolve_line_date()`, `resolve_file_author()` |
| `install.py` | Detect clients (11 platforms), inject/remove instruction blocks, register OpenCode plugin | `detect()`, `install()`, `uninstall()` |
| `query.py` | Query nodes by label/time (with file-level collapse), build timeline from preceded_by edges, temporal stats, root-cause tracing (bounded multi-relation BFS + ranking, read-only) | `query_nodes()`, `build_timeline()`, `temporal_stats()`, `impact()` |

## Data flow (`enrich`)

1. Load `graphify-out/graph.json` from project root (NetworkX node-link format, `links` key for edges)
2. Collect unique `source_file` values from all nodes
3. Filter by `--since`, `--include`, `--exclude` if provided
4. If `--git`: resolve `git_root` once via `git_source.find_repo_root()` (None if
   git is missing or the path isn't a working tree — falls through to step 5 for
   every file, one notice printed for the whole run, not per-file)
5. For each source file:
   - `--git` and git-resolvable: `git_source.resolve_file_date(mode="last")` →
     `file_mtime` (one `git log` call per unique file). Untracked/unresolvable
     files fall through to filesystem stat below.
   - Default: `st_mtime` → `file_mtime` (ISO 8601 with Z suffix, UTC)
   - `--use-ctime`: `st_ctime` → `file_mtime` (UTC)
   - `--use-birthtime`: `st_birthtime` → `file_mtime` (UTC, Linux via ctypes `statx(2)`)
   - `--include-dir-mtime`: also writes `dir_mtime` (UTC)
6. Per node, if `--git` resolved a date for its file: `git_source.blame_file()`
   (one `git blame --porcelain` call per unique file, cached — never per node)
   parsed into `{line: date}`; node's `git_commit_date` = line-level date if the
   node's `source_location` line resolves, else the file-level date. Also stamps
   `git_author` (one `git log -1 --format=%an` call per unique file).
7. Intra-file: sort nodes by `(_extract_line, node_id)` → `preceded_by` edges (deterministic)
8. Cross-file (opt-in): sort by resolved timestamp (git-derived when available), chain first nodes (line-ordered)
9. If `--dry-run`: return stats dict without writing
10. Deduplicate by `(source, target, relation)` triple against existing `links`
11. Write `graph.json` with `json.dumps(data, ensure_ascii=False)`
12. Default: regenerate `graph.html` and `wiki/` via `graphify export` subprocess
    (`--no-regenerate` skips this step)

Nodes from `graphify-out/` itself are **automatically excluded** from enrichment
— no need to pass `--exclude "graphify-out/**"`.

Subprocess bound for `--git`: **O(unique_files)** `git log`/`git blame` calls,
never O(nodes) — a file with 500 AST nodes still costs at most 3 subprocess
calls (file date + blame + author), not 1500.

## Data flow (`install`)

1. Scan project root for client markers (e.g. `CLAUDE.md`, `.opencode/`, `GEMINI.md`)
2. For each detected client, write or update a `## graphify-temporal` block in the instruction file
3. For OpenCode: also write `.opencode/plugins/graphify-temporal.js` and register in `opencode.json`

## Data flow (`impact`)

1. Load `graphify-out/graph.json` (same `_load_graph` as every other query function — no caching, fresh read every call)
2. Validate both anchor node ids exist; raise `ValueError` if not (same except clause as every other CLI query command)
3. Build an **undirected** adjacency index from `links` once — edge direction encodes provenance, not causality direction, so BFS reachability ignores it. Malformed links (missing `source`/`target`) are skipped silently.
4. Compute `has_semantic_edges` once (`any(relation != "preceded_by")`) → `structural_confidence` field
5. Bounded BFS from each anchor independently: `hops` deep (default 3), node-visit budget 500 total (mirrors `build_timeline`'s own `max_steps=500`), hub cap — a node with combined degree > 50 is recorded as reached but not expanded further (mirrors core graphify's `analyze.py::god_nodes` exclusion instinct)
6. A third BFS (target-seeking, stops on hit) finds the shortest direct path between the two anchors, if any
7. Merge: nodes reached from both anchors are `"bridge"` (strongest signal); re-discovery of an already-visited node via a *different* edge increments `alternate_paths` for it (no extra BFS cost — just a counter bump on an edge already being iterated)
8. Score each candidate: `(3 - hop) + confidence_bonus + (2 if relation != preceded_by) + (1 if cross-community) + (1 if bridge)` — see [cli-reference.md](cli-reference.md#impact--root-cause-tracing) for the full formula
9. Sort by score desc, then node id asc (deterministic); truncate to `max_candidates`
10. Return a dict — never writes to `graph.json`

Subprocess/complexity bound: **O(visited nodes)** per BFS call, capped by the 500-node budget and the 50-degree hub cap — never unbounded graph traversal from a single hub node.

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
| `--use-ctime`, `--use-birthtime`, `--git` — more than one together | CLI rejects with error, exit 1 |
| Birthtime unavailable | `file_mtime = null`, never a crash |
| `--since` with invalid date | `ValueError` at parse time, friendly message |
| Empty graph | 0 enrichments, 0 edges — no crash |
| Re-run (idempotency) | Edges deduplicated, `edges_deduped` reported |
| No `source_file` on node | Skipped silently |
| `regenerate_outputs` when `graphify` not on PATH | Subprocess fails silently, returns `False` |
| `--git` but `git` not on PATH | One notice printed, falls back to stat for every file — not an error |
| `--git` but path isn't a git working tree | One notice printed, falls back to stat for every file — not an error |
| `--git` and file is untracked/new | That file falls back to stat silently; other git-resolved files unaffected |
| `--git` on a shallow clone (`--depth 1`) | `mode="first"` (creation-date query) refused, since the shallow boundary would masquerade as a fake creation date; `mode="last"` (used by `enrich()`) is unaffected — the newest commit's date is real regardless of clone depth |
| `--git` and `source_file` resolves outside the git root (e.g. `../` traversal) | `_safe_relative()` rejects it (`Path.relative_to()` raises `ValueError`), falls back to stat — never reaches subprocess |
| `impact`: anchor node id not found | `ValueError` — CLI rejects with error, exit 1 |
| `impact`: anchor exists but has zero edges | Not an error — `isolated_anchors` lists it, candidates empty for it |
| `impact`: graph has zero semantic edges | Not an error — `structural_confidence: "temporal-only"`, degrades gracefully |
| `impact`: pathological hop/hub expansion | Bounded by 500-node visit budget + 50-degree hub cap → `"truncated": true`, never hangs |
| `impact`: node with null timestamps/source_location | Every candidate field defaults to `None` via `.get()` — pure-structural tracing still works |
| `impact`: `--hops 0` or negative | CLI rejects before calling the core function, exit 1 |
| `impact`: malformed edge (missing `source`/`target`) | Skipped silently while building adjacency |

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

### TestGitSource — unit (12 tests, `@requires_git` — skipped if `git` isn't on PATH)

| Test | Coverage |
|------|----------|
| `git_available` true when installed | `shutil.which("git")` found |
| `find_repo_root` in a git repo | Returns the resolved repo root |
| `find_repo_root` not a repo | Returns `None` |
| `find_repo_root` monorepo subdir | Subdirectory still resolves to the real root |
| `resolve_file_date` last touch | Two commits → returns the second (most recent) commit's date |
| `resolve_file_date` first creation | Two commits → `mode="first"` returns the first commit's date |
| `resolve_file_date` untracked file | Returns `None` |
| `is_shallow_repo` false | Normal clone → `False` |
| `is_shallow_repo` true | `.git/shallow` present → `True` |
| `blame_file` line dates | Modified line shows the newer commit's date; untouched lines keep the original — the key correctness test proving line-level (not just file-level) attribution works |
| `blame_file` untracked | Returns `None` |
| `resolve_line_date` lookup | Dict lookup, hit/miss/`line=0`/`blame_map=None` all handled |

### TestEnrichGit — integration (5 tests, `@requires_git`)

| Test | Coverage |
|------|----------|
| `--git` basic | `git_commit_date` reflects the real commit date, proven by bumping the file's stat mtime to "now" via `os.utime` after commit (simulating a fresh clone) and asserting `git_commit_date` still shows the original commit date, not the bumped mtime |
| Falls back when not a repo | No `git init` at all → behaves identically to `use_git=False`, no crash, no `git_commit_date` field |
| Untracked file falls back to stat | Repo exists, one file uncommitted → that file gets stat `file_mtime` and no `git_commit_date`, its committed sibling gets both |
| `--cross-file` uses git dates | Both files' mtimes bumped to "now" (simulating post-clone checkout) — edge ordering still follows git commit dates, not the now-identical stat mtimes |
| Git binary missing | `git_source.which` monkeypatched to `None` → graceful fallback, no exception |

### TestEnricher — integration (20 tests)

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
| `links: null` handled | graph.json with null links doesn't crash |
| `links` key missing | graph.json without links key handled |
| `source_file: ""` | Empty source_file → None, no crash |
| Birthtime + since filter | --use-birthtime + --since doesn't TypeError |
| ISO Z suffix | file_mtime ends with Z |
| Include + exclude together | Both glob filters work simultaneously |

### TestInstall — integration (24 tests)

See [team-setup.md](team-setup.md) for the install flow.  Tests cover:
block injection, replacement, removal, idempotency, client detection
(11 platforms), Cursor `.mdc` path, Windsurf `.md` path, OpenCode plugin
install/uninstall, CLI help, and edge cases (no markers, missing files,
empty files).

### TestQuery — integration (56 tests)

| Group | Tests | Coverage |
|-------|-------|----------|
| Unit | 6 | `_parse_date_ts`, `_ts_from_node` edge cases |
| query_nodes | 12 | Search, since/before filters, ordering, empty timestamps, dir_mtime, file collapse |
| build_timeline | 7 | Basic chain, start_id, since/before filters, non-preceded_by edges, cycles |
| temporal_stats | 7 | Basic stats, empty graph, dir_mtime, gaps, median, JSON output |
| CLI | 6 | `--help` output for query, timeline, stats; `impact` missing-graph exit 1, `--hops 0` rejection, `--json` output validity |

### TestImpactScore — unit (9 tests)

Each test varies exactly one of `_impact_score`'s 5 parameters and asserts
the *exact* resulting score — not just relative ordering.

| Test | Coverage |
|------|----------|
| Baseline | hop=1, EXTRACTED, semantic relation, same community, not bridge → exact score |
| Hop term | Score decreases by exactly 1.0 per additional hop |
| Confidence bonus | EXTRACTED − INFERRED = 1.0, INFERRED − AMBIGUOUS = 1.0 |
| Confidence unknown value | Unrecognized confidence string defaults to bonus 0 |
| Semantic vs temporal | Non-`preceded_by` relation scores exactly 2.0 higher |
| Community crossing | Cross-community candidate scores exactly 1.0 higher |
| Community `None` | A node with no community field does NOT get the crossing bonus (None ≠ "different") |
| Bridge bonus | Bridge candidate scores exactly 1.0 higher than non-bridge |
| All bonuses stack | Full additive formula verified against a hand-computed total |

### TestImpact — integration (18 tests)

| Test | Coverage |
|------|----------|
| Two-anchor bridge found | Node reachable from both anchors → `connection: "bridge"` |
| Single-anchor reachable nodes | `node_b=None` → every node within `hops` of `node_a` |
| Degraded preceded_by-only graph | `structural_confidence: "temporal-only"`, every candidate's `relation_path == ["preceded_by"]` |
| Mixed relations rank semantic above temporal | `calls`-connected candidate outscores `preceded_by`-connected at same hop |
| Anchor not found (a / b) | `ValueError` naming the specific missing node |
| Isolated anchor | Anchor exists but has zero edges → no exception, `isolated_anchors` populated |
| Hop limit respected | Nodes beyond the hop limit are absent from candidates |
| Hub node fan-out capped | A degree>50 hub is reached but not expanded through — nodes only reachable past it are absent |
| Node visit budget truncation | A wide binary-tree-shaped graph exceeds the 500-node budget → `truncated: true` |
| Ranking deterministic | Identical scores tie-break by node id ascending, stable across repeated calls |
| Direct path between anchors | Shortest path found and returned with correct relation labels |
| No direct path when disconnected | `direct_path == []`, not an error |
| Candidate with null timestamps | Missing `file_mtime`/`git_commit_date`/`source_file` → fields are `None`, no crash |
| Relations filter excludes others | `relations=["calls"]` on a mixed-relation graph only returns `calls`-reached nodes |
| Max candidates truncates | More qualifying candidates than `max_candidates` → list capped, best-scoring kept |
| Community boundary bonus | Cross-community candidate scores higher (integration-level, complements TestImpactScore's unit-level check) |
| Malformed edge skipped | A link missing `target` → no `KeyError`, edge simply ignored |
| Alternate paths (multi-edge) | Two independent edges (`calls` + `references`) from the same anchor to the same candidate → `alternate_paths == 2` |

## Dependencies

| Scope | Dependency |
|-------|-----------|
| Runtime | Python ≥ 3.10, stdlib only (`json`, `re`, `pathlib`, `os`, `fnmatch`, `argparse`, `ctypes`, `subprocess`, `shutil`, `functools`) |
| Optional regeneration | `graphify` CLI on PATH |
| Optional, `--git` only | `git` binary on PATH — external, not a pip dependency; absent → automatic fallback to stat |
| Dev | `pytest` |

## Non-Goals

- No bidirectional temporal edges (always source → target in chronological order)
- No non-git VCS integration (Mercurial, SVN, Perforce) — only git is supported
- No pluggable "timestamp provider" abstraction — exactly two sources (git, stat),
  handled by a direct if/else, not an interface — added only if a third source is
  ever actually requested
- No `query`/`timeline`/`stats` support for filtering/sorting by `git_commit_date`
  (only `file_mtime` is queryable today) — additive follow-up if requested, same
  pattern as `--use-dir-mtime`
- No persistent cross-invocation cache for git-resolved dates — each CLI call is
  a fresh process, and within a single run resolution is already deduplicated by
  unique file
- No pluggable ranking-strategy interface for `impact` — one fixed additive
  formula (5 terms), no config file for hop limit/hub cap/visit budget — those
  are fixed constants (raise only if a real large graph proves them wrong)
- No non-git-VCS-aware "blame" analog for `impact`'s edge walk — it walks
  whatever `relation` values already exist in `graph.json` (calls, imports,
  references, ...), regardless of what tool produced them
- No CI/CD pipeline
- No PyPI publication (pip install via git URL)

## Changelog

- **v1.0.0 → `--git` mode**: added `graphify_temporal/git_source.py` (git
  log/blame timestamp resolution), `enrich(use_git=...)`, CLI `--git` flag.
  Additive only — `file_mtime` unchanged in meaning, `git_commit_date`/
  `git_author` are new optional node fields. See
  [timestamps.md](timestamps.md#git-derived-timestamps---git) for the full
  design rationale and fallback rules.
- **v1.0.0 → `impact` (root-cause tracing)**: added `impact()` to `query.py`
  (bounded multi-relation BFS + ranking, read-only), CLI `impact` subcommand,
  and a "Root-cause tracing" section injected into agent instruction files
  via `install.py` so AI coding assistants reach for it proactively during
  debugging. Also fixed a pre-existing drift where `install.py`'s
  `_INSTRUCTION_BLOCK` template had fallen behind the real `AGENTS.md`
  (missing `--git` docs) — a fresh `install` would have silently overwritten
  the richer content. See
  [cli-reference.md](cli-reference.md#impact--root-cause-tracing) for the
  full flag reference and ranking formula.
