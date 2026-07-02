# graphify-temporal

![version](https://img.shields.io/badge/version-1.0.0-blue)
![python](https://img.shields.io/badge/python-%3E%3D3.10-blue)
![license](https://img.shields.io/badge/license-MIT-green)
[![Ko-fi](https://img.shields.io/badge/support-Ko--fi-ff5e5b)](https://ko-fi.com/gianlucagernone)

Enrich a [graphify](https://github.com/safishamsi/graphify) knowledge graph with temporal metadata from filesystem timestamps.

Adds `file_mtime`, `file_birthtime`, `dir_mtime` to nodes and deterministic `preceded_by` edges — making your knowledge graph time-aware without any LLM cost.

## What timestamps can you see?

graphify-temporal can resolve three distinct timestamps from the filesystem:

| Attribute       | Flag                  | Meaning                                                                 |
|-----------------|-----------------------|-------------------------------------------------------------------------|
| `file_mtime`    | _(default)_           | Last content modification                                               |
| `file_mtime`    | `--use-ctime`         | Inode metadata change (Unix) / creation (Windows)                       |
| `file_mtime`    | `--use-birthtime`     | True creation time — when the file was born on disk                     |
| `dir_mtime`     | `--include-dir-mtime` | Parent directory mtime — best proxy for "when did this file arrive here"|

The three timestamps (`mtime`, `birthtime`, `dir_mtime`) are often different, giving
you a timeline: **created** → **arrived in this directory** → **last modified**.

Birth time (`st_birthtime`) is supported on:
- **Linux** kernel ≥ 4.11 on ext4 / btrfs / xfs (via `statx` syscall)
- **macOS** ≥ 10.4 (natively via `st_birthtime`)
- **Windows** (natively via `st_birthtime`)

When birthtime is unavailable the fallback is `file_mtime = None` — never a crash.

## Install

```bash
pip install git+https://github.com/todotge/graphify-temporal.git
```

Or clone and install locally:
```bash
git clone https://github.com/todotge/graphify-temporal.git
cd graphify-temporal
pip install .
```

If `graphify-temporal` is not found after install, use `python -m graphify_temporal` instead — the binary lives in your venv's `bin/` directory.

## Usage

`graphify-temporal` is a separate CLI from `graphify`. The enrichment
subcommand is `graphify-temporal enrich`, not `graphify enrich`.
**After every `/graphify` build, run `graphify-temporal enrich`** — the
graph is not complete without temporal stamps.

```bash
# Basic: add file_mtime to all nodes + intra-file preceded_by edges
graphify-temporal enrich

# Cross-file temporal edges + filter by date
graphify-temporal enrich --cross-file --since 2026-05-01

# Use true creation time (birthtime) instead of modification time
graphify-temporal enrich --use-birthtime

# See when files arrived in their directories
graphify-temporal enrich --include-dir-mtime

# Full timeline: birthtime as primary + directory arrival time
graphify-temporal enrich --use-birthtime --include-dir-mtime

# Use creation time instead of modification time
graphify-temporal enrich --use-ctime

# Preview on a subdirectory without modifying
graphify-temporal enrich --include "your/archive/**" --dry-run

# Exclude archive directories
graphify-temporal enrich --exclude "**/archive/**" --exclude "**/old/**"
```

### Options

| Flag | Description |
|------|-------------|
| `PATH` | Project root (default `.`) |
| `--use-ctime` | Use `st_ctime` instead of `st_mtime` (metadata-change on Unix, creation on Windows) |
| `--use-birthtime` | Use `st_birthtime` instead of `st_mtime` (true creation time). Mutually exclusive with `--use-ctime` |
| `--include-dir-mtime` | Also add `dir_mtime` (parent directory mtime) to nodes — arrival proxy |
| `--cross-file` | Create `preceded_by` edges across different files |
| `--dry-run` | Show stats without modifying `graph.json` |
| `--since DATE` | Only process files modified after `DATE` (YYYY-MM-DD) |
| `--include GLOB` | Only process files matching glob (repeatable) |
| `--exclude GLOB` | Exclude files matching glob (repeatable) |
| `--no-regenerate` | Skip regenerating HTML/wiki |
| `--quiet`, `-q` | Minimal output |

## What it does

1. Reads `graphify-out/graph.json` from your project
2. For each node with a `source_file`, stats the filesystem:
   - `file_mtime` (ISO 8601) — modification time (or ctime / birthtime depending on flag)
   - `dir_mtime` (ISO 8601) — parent directory mtime, when `--include-dir-mtime` is set
3. Creates `preceded_by` edges within each file (ordered by line number)
4. Optionally creates cross-file chronological edges
5. Regenerates `graph.html` and `wiki/`

### Example output

```
graphify-temporal v1.0.0
  Files analyzed:     1,220
  Nodes enriched:    14,173 (85%)
  Files not found:       12
  Edges added:       12,946 (intra-file: 12,911, cross-file: 35)
  html:               ✓
  wiki:               ✓
```

## Switching timestamp modes

Enrichment is **idempotent** — you can re-run `enrich` with different flags anytime.
It updates `file_mtime` in-place and deduplicates edges, so you never get double
edges or corrupted data.

```bash
# First pass: default modification time
graphify-temporal enrich

# Now you want to know when files arrived in their directories
graphify-temporal enrich --include-dir-mtime

# Or switch primary timestamp to true creation time
graphify-temporal enrich --use-birthtime

# Full timeline: birthtime as primary + directory arrival
graphify-temporal enrich --use-birthtime --include-dir-mtime
```

No need to rebuild the graph — just run the enrichment again with the flags you need.

## Querying the graph

Once the graph is enriched with temporal metadata, you can filter and explore it
by time.  **All time filters require explicit dates** (YYYY-MM-DD) — the tool
does not understand "last week" or "yesterday".  You (or your AI agent) must
compute the date range before calling the command.

`query` and `timeline` default to **one entry per file** (not per node).  Use
`--full` to see every node.  Files inside `graphify-out/` are automatically
excluded from enrichment.

```bash
# Find nodes by name (one per file)
graphify-temporal query "auth"

# All nodes, no file-level collapse
graphify-temporal query "auth" --full

# Filter by date range
graphify-temporal query "auth" --since 2026-05-01 --before 2026-06-01

# Sort chronologically
graphify-temporal query "auth" --order newest-first

# Walk the preceded_by chain (one per file)
graphify-temporal timeline

# Every node in the chain
graphify-temporal timeline --full

# Start the timeline from a specific node
graphify-temporal timeline "enricher_enrich"

# See temporal coverage stats
graphify-temporal stats
```

**AI agent note:** When a user asks "what happened last week", compute the date
range first with `date -I` or `datetime`, then pass it to `--since`/`--before`.
See [docs/cli-reference.md](docs/cli-reference.md#prompt-examples--what-to-ask-your-ai-agent) for a full table of realistic prompts.

## Team setup

graphify-temporal auto-detects which AI coding assistant you're using and injects
instructions so the agent knows how to run temporal enrichment.

```bash
# Auto-detect all clients and install instructions
graphify-temporal install

# Install for a specific client
graphify-temporal install --platform opencode
graphify-temporal install --platform claude

# Remove instructions from all clients
graphify-temporal uninstall
```

### Supported clients

| Client | Instruction file | Plugin |
|--------|-----------------|--------|
| Claude Code | `CLAUDE.md` | — |
| OpenCode | `AGENTS.md` | `.opencode/plugins/graphify-temporal.js` |
| Codex | `AGENTS.md` | — |
| Gemini CLI | `GEMINI.md` | — |
| Cursor | `.cursor/rules/graphify-temporal.mdc` | — |
| CodeBuddy | `CODEBUDDY.md` | — |
| Copilot | `.github/copilot-instructions.md` | — |
| Windsurf | `.windsurf/rules/graphify-temporal.md` | — |
| Aider | `AGENTS.md` | — |
| Kilo Code | `AGENTS.md` | — |
| Trae | `AGENTS.md` | — |

The OpenCode plugin checks whether `graph.json` exists but lacks `file_mtime`
and reminds the agent to run enrichment before it reaches for raw file reads.

## Requirements

- Python >= 3.10
- An existing `graphify-out/graph.json` (run `graphify .` first)
- [graphify](https://github.com/safishamsi/graphify) CLI (for output regeneration)

## Documentation

| Document | Contents |
|----------|----------|
| [docs/cli-reference.md](docs/cli-reference.md) | Complete CLI: every flag, subcommand, example, error code |
| [docs/timestamps.md](docs/timestamps.md) | Timestamp semantics, birthtime support, switching modes, schema |
| [docs/team-setup.md](docs/team-setup.md) | `install`/`uninstall`, client detection, OpenCode plugin, team workflow |
| [docs/spec.md](docs/spec.md) | Architecture overview, data flow, test plan, dependencies, non-goals |

## Contributing

Issues and PRs welcome — bug reports, edge cases on unusual filesystems, and
platform quirks around birthtime resolution are especially useful.

```bash
git clone https://github.com/todotge/graphify-temporal.git
cd graphify-temporal
pip install -e ".[dev]"
pytest
```

Open an issue before a large PR so we can align on approach first. Small
fixes and test additions can go straight to a PR.

## Support

If graphify-temporal saves you time, consider [buying me a coffee](https://ko-fi.com/gianlucagernone) — it goes straight back into building and maintaining tools like this one.

## License

MIT
