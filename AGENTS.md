# graphify-temporal

## graphify

This project uses graphify for knowledge graph introspection. The graph lives at `graphify-out/`.

When the user invokes `/graphify`:
- If it is a full-build command (`/graphify .`, `--update`, `--mode deep`), follow the pipeline in the graphify skill.
- If it is a question about the codebase, run `graphify query "<question>"` directly — the graph is already built.

Keep graphify current after meaningful changes:
```
/graphify --update deep --directed --wiki
```

Rules:
- Run `graphify query "<question>"` for codebase exploration when `graphify-out/graph.json` exists.
- After modifying code, run `graphify update .` to keep the graph current.

## Development

```bash
# Setup
uv venv && uv pip install -e ".[dev]"

# Run all tests
.venv/bin/pytest tests/ -v

# Run a single test
.venv/bin/pytest tests/test_enricher.py::TestEnricher::test_idempotency -v

# Install from local checkout
pip install .
```

## Architecture

Zero runtime dependencies — stdlib only. Python >= 3.10.

```
graphify_temporal/
├── __init__.py     → __version__ string
├── __main__.py     → CLI only: argparse + enrich() dispatch + stats printing
├── enricher.py     → core: load graph.json, stamp nodes, build edges, save, regenerate
├── fs.py           → pure helpers: resolve_mtime, matches_glob, is_excluded, parse_date
└── git_source.py   → pure helpers: git log/blame timestamp resolution (--git mode)
tests/
├── __init__.py
└── test_enricher.py   → TestFs + TestGitSource (unit) + TestEnricher + TestEnrichGit (integration), all via tmp_path
```

`__main__.py` has no business logic — it converts CLI args and prints. All enrichment
lives in `enricher.enrich()`. Tests import `enrich()` and `regenerate_outputs()`
directly, never through the CLI.

## graph.json conventions

- The edge array is keyed `links`, **not** `edges`. This is NetworkX node-link format.
- Top-level structure: `{"directed": bool, "multigraph": bool, "graph": {}, "nodes": [...], "links": [...], "hyperedges": [...]}`
- `json.dumps(data, ensure_ascii=False)` — non-ASCII content in the graph must survive round-trips.

## Key behaviors an agent might miss

- **Edge dedup triple**: `(source, target, relation)` — not just source/target. Two edges
  with the same triple are considered duplicates. This is what makes enrichment idempotent.
- **`parse_date` is the single source of truth** for YYYY-MM-DD parsing.
  Use `from .fs import parse_date` — never inline `time.strptime(since, ...)`.
- **Intra-file ordering**: nodes within a file are sorted by `_extract_line()`, which
  regex-extracts `L<digits>` from the `source_location` field. Nodes without a line
  marker sort to position 0.
- **`regenerate_outputs()` spawns the `graphify` CLI as a subprocess**. If `graphify`
  isn't on PATH, html/wiki regeneration silently fails (returns `False`).
- **Tests are pure in-memory** — they use `_make_graph_json()` and `_write_file()`
  helpers to create a synthetic `graphify-out/graph.json` inside `tmp_path`. No real
  filesystem graph is ever touched. Git tests use `_make_git_repo()`/`_git_commit()`
  and are skipped (not failed) when `git` isn't on PATH (`@requires_git` marker).
- **`--git` never overwrites `file_mtime`** — it stays stat-sourced always, so
  `query.py`'s `--since`/`--before`/`order`/`temporal_stats()` keep meaning exactly
  what they mean today regardless of which flags produced a given `graph.json`.
  Git-derived data lands in new additive fields (`git_commit_date`, `git_author`)
  that existing consumers simply don't read.
- **All `git_source.py` subprocess calls are argument-lists, never `shell=True`**,
  and every `source_file` is passed through `_safe_relative()` in `enricher.py`
  first — `Path.relative_to()` rejects anything outside the git root (e.g. a
  `../` traversal attempt), falling back to stat instead of reaching subprocess.
- **One `git log` + one `git blame --porcelain` per unique file, never per node**
  — `blame_file()` parses the whole file's line-attribution in one subprocess
  call into an in-memory `{line: date}` map; every node then does an O(1) dict
  lookup via `resolve_line_date()`. Complexity is O(unique_files) subprocess
  spawns, not O(nodes).

## graphify-temporal

Temporal enrichment for graphify knowledge graphs — stamps nodes with filesystem
timestamps and creates deterministic `preceded_by` edges.

> **Note:** `graphify-temporal` is a SEPARATE command from `graphify`.
> The enrichment subcommand is `graphify-temporal enrich`, NOT `graphify enrich`.
> graphify itself has no temporal features — this extension adds them.
>
> **Workflow:** after running `/graphify` to build or update the graph,
> always run `graphify-temporal enrich` immediately.  The graph is not
> complete without temporal stamps.  `enrich` is idempotent — safe to
> re-run anytime.

### Setup
```bash
pip install git+https://github.com/todotge/graphify-temporal.git
```
Or clone and install locally:
```bash
git clone https://github.com/todotge/graphify-temporal.git
cd graphify-temporal
pip install .
```
Editable install (development):
```bash
uv venv && uv pip install -e ".[dev]"
```

> **If `graphify-temporal` is not found** after pip install, the binary lives
> in the venv's `bin/` directory.  Use the full path
> (`.venv/bin/graphify-temporal`) or fall back to
> `python -m graphify_temporal` — both are equivalent.

### Usage
```bash
graphify-temporal enrich                     # default: mtime + intra-file edges
graphify-temporal enrich --git                # git commit/blame dates instead of stat mtime
graphify-temporal enrich --use-birthtime     # true creation time (st_birthtime)
graphify-temporal enrich --include-dir-mtime # directory arrival proxy
graphify-temporal enrich --use-birthtime --include-dir-mtime  # full timeline
graphify-temporal enrich --cross-file        # cross-file chronological edges
graphify-temporal enrich --dry-run           # preview, no write
graphify-temporal enrich --since DATE        # filter by modification date
graphify-temporal enrich --include GLOB      # filter files by glob (repeatable)
graphify-temporal enrich --exclude GLOB      # exclude files by glob (repeatable)
```

> **Why `--git` exists:** on a cloned repo (GitHub, CI checkout, etc.) `stat()`
> mtime/birthtime reflect the moment of `git clone`/`checkout`, not the file's
> real history — every file lands with nearly the same timestamp, making the
> default enrichment nearly useless for tracing real changes. `--git` derives
> dates from `git log`/`git blame` instead: one `git log` call per unique file
> for the file-level date, one `git blame --porcelain` call per unique file
> (parsed once into a `{line: date}` map) for line-level `git_commit_date` per
> node. Falls back to stat automatically per-file when git is missing, the
> file is untracked, or the path isn't inside a git repo — never a crash.
> Mutually exclusive with `--use-ctime`/`--use-birthtime` (reject, don't
> silently override). Adds `git_commit_date`/`git_author` as new node fields;
> `file_mtime` itself is untouched so existing `query`/`timeline`/`stats`
> consumers keep working unchanged.

### Install into AI assistant
```bash
graphify-temporal install                    # auto-detect all clients
graphify-temporal install --platform claude   # force a specific client
graphify-temporal uninstall                  # remove instructions
```

### Querying
```bash
graphify-temporal query "auth"              # search nodes (one per file)
graphify-temporal query --full               # show every node, not collapsed
graphify-temporal query --since DATE         # filter by timestamp
graphify-temporal query --order newest-first # sort chronologically
graphify-temporal timeline                  # walk preceded_by chain (one per file)
graphify-temporal timeline --full            # show every node, not collapsed
graphify-temporal timeline "node_id"         # from a specific node
graphify-temporal stats                      # temporal coverage
```

When a user asks for time-based reports ("last week", "what changed today"),
compute the date range with `date -I` or `datetime` first, then pass it to
`--since` / `--before`.  The tool requires explicit YYYY-MM-DD dates — it
does not understand relative time expressions.

### Testing
```bash
.venv/bin/pytest tests/ -v
```

### Key facts
- Zero runtime dependencies — stdlib only, Python >= 3.10 (git itself is an
  external binary invoked via subprocess, not a pip dependency)
- Idempotent — safe to re-run with different flags (updates in-place)
- Works on Linux, macOS, and Windows
- `st_birthtime` supported on Linux (kernel >= 4.11), macOS, Windows
- `--git` requires the `git` binary on PATH and a git working tree; absent
  either, enrichment falls back to stat automatically (no crash, one notice)

