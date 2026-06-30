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
├── __init__.py   → __version__ string
├── __main__.py   → CLI only: argparse + enrich() dispatch + stats printing
├── enricher.py   → core: load graph.json, stamp nodes, build edges, save, regenerate
└── fs.py         → pure helpers: resolve_mtime, matches_glob, is_excluded, parse_date
tests/
├── __init__.py
└── test_enricher.py   → TestFs (unit) + TestEnricher (integration), all via tmp_path
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
  filesystem graph is ever touched.

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
graphify-temporal enrich --use-birthtime     # true creation time (st_birthtime)
graphify-temporal enrich --include-dir-mtime # directory arrival proxy
graphify-temporal enrich --use-birthtime --include-dir-mtime  # full timeline
graphify-temporal enrich --cross-file        # cross-file chronological edges
graphify-temporal enrich --dry-run           # preview, no write
graphify-temporal enrich --since DATE        # filter by modification date
graphify-temporal enrich --include GLOB      # filter files by glob (repeatable)
graphify-temporal enrich --exclude GLOB      # exclude files by glob (repeatable)
```

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
- Zero runtime dependencies — stdlib only, Python >= 3.10
- Idempotent — safe to re-run with different flags (updates in-place)
- Works on Linux, macOS, and Windows
- `st_birthtime` supported on Linux (kernel >= 4.11), macOS, Windows

