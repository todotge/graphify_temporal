# graphify-temporal

![version](https://img.shields.io/badge/version-1.0.0-blue)
![python](https://img.shields.io/badge/python-%3E%3D3.10-blue)
![license](https://img.shields.io/badge/license-MIT-green)
[![Ko-fi](https://img.shields.io/badge/support-Ko--fi-ff5e5b)](https://ko-fi.com/gianlucagernone)

Enrich a [graphify](https://github.com/safishamsi/graphify) knowledge graph with temporal metadata from filesystem timestamps.

Adds `file_mtime`, `file_birthtime`, `dir_mtime`, and (optionally) git-derived
`git_commit_date`/`git_author` to nodes, plus deterministic `preceded_by` edges
— making your knowledge graph time-aware without any LLM cost. Also adds
`impact`, a root-cause tracing query that walks the graph's structural edges
(calls, imports, references, ...) together with its temporal history to find
candidate causes for "I changed X, then Y broke" — instead of grepping files
one at a time.

## What timestamps can you see?

graphify-temporal can resolve timestamps from two sources: the filesystem
(always available) and git history (opt-in, more accurate on cloned repos).

| Attribute        | Flag                  | Meaning                                                                 |
|------------------|-----------------------|---------------------------------------------------------------------------|
| `file_mtime`     | _(default)_           | Last content modification (filesystem)                                    |
| `file_mtime`     | `--use-ctime`         | Inode metadata change (Unix) / creation (Windows)                         |
| `file_mtime`     | `--use-birthtime`     | True creation time — when the file was born on disk                       |
| `dir_mtime`      | `--include-dir-mtime` | Parent directory mtime — best proxy for "when did this file arrive here"  |
| `git_commit_date`| `--git`               | Real author-date from `git log`/`git blame` — per node, line-accurate     |
| `git_author`     | `--git`               | Author of the commit that last touched the node's file                    |

The three filesystem timestamps (`mtime`, `birthtime`, `dir_mtime`) are often
different, giving you a timeline: **created** → **arrived in this directory**
→ **last modified**.

### Why `--git`?

On a repo you cloned (from GitHub or anywhere else), filesystem timestamps
are checkout artifacts, not history: every file gets ~the same `mtime`/
`birthtime` at clone time, regardless of when it was actually written. `--git`
sidesteps this by reading the timestamps out of the repo's own commit
history instead of the filesystem — `git log` for a file-level date, `git
blame` for a line-accurate date per graph node. It requires the `git` binary
and a git working tree; falls back to filesystem timestamps automatically
(per file) when either is missing, never a crash. See
[docs/timestamps.md](docs/timestamps.md#git-derived-timestamps---git) for the
full schema and fallback rules.

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

# Derive dates from git history instead of filesystem stat (cloned repos)
graphify-temporal enrich --git

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
| `--use-birthtime` | Use `st_birthtime` instead of `st_mtime` (true creation time). Mutually exclusive with `--use-ctime`/`--git` |
| `--git` | Derive `file_mtime` from git author-dates (`git log`/`git blame`) instead of stat, for files tracked in a git repo. Falls back to stat automatically per file. Also adds `git_commit_date`/`git_author` node fields. Mutually exclusive with `--use-ctime`/`--use-birthtime` |
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
2. For each node with a `source_file`:
   - Default: stats the filesystem for `file_mtime` (ISO 8601) — modification
     time (or ctime / birthtime depending on flag), plus `dir_mtime` when
     `--include-dir-mtime` is set
   - With `--git`: resolves `file_mtime` from `git log` instead (falls back to
     stat automatically if the file is untracked or not in a git repo), and
     stamps `git_commit_date` (line-accurate via `git blame`, one subprocess
     call per unique file) + `git_author` on each node
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

## Root-cause tracing (`impact`)

`impact` traces structural + temporal connections between one or two nodes —
built for the "I wrote X, then Y modified module alpha, what did I do in
module beta that could have broken alpha?" case. It walks **every** edge
relation in the graph (`calls`, `imports`, `references`,
`conceptually_related_to`, `preceded_by`, ...), not just the temporal chain,
bounded to a configurable hop depth, and ranks candidates by how relevant
they look as a connection between the areas you named.

```bash
# How are these two areas of code connected?
graphify-temporal impact NODE_A NODE_B

# What's reachable from/at-risk around a single node?
graphify-temporal impact NODE_A

# Widen or narrow the search
graphify-temporal impact NODE_A NODE_B --hops 5
graphify-temporal impact NODE_A NODE_B --relations calls,references

# Machine-readable output
graphify-temporal impact NODE_A NODE_B --json
```

Node ids come from `graphify-temporal query "<search term>"` — run that first
if you don't already know the exact id.

**Read-only** — `impact` never writes to `graph.json`, safe to re-run
repeatedly mid-debugging-session.

**Ranking** combines: hop distance (closer = more relevant), edge confidence
(`EXTRACTED` > `INFERRED` > `AMBIGUOUS`), whether the edge is a real
structural relation vs. just `preceded_by` timestamp-chaining, whether the
candidate crosses a community boundary, and whether it's a "bridge" — reached
from *both* named anchors, the strongest signal available.

**Degraded mode:** if the graph currently has no semantic edges (only
`preceded_by`), results carry `"structural_confidence": "temporal-only"` —
a clear, explicit signal (not a silent guess) that the connections shown
reflect timestamp proximity, not confirmed code relationships. This can
happen if `graphify` hasn't run a full semantic pass recently; consider
`/graphify --update deep` if results look thin. See
[docs/cli-reference.md](docs/cli-reference.md#impact--root-cause-tracing)
for the full flag reference, output shape, and error handling.

### Example: tracing a regression

```bash
graphify-temporal query "AuthModule"          # find the node id
graphify-temporal impact auth_module database_pool --hops 3
```
```
graphify-temporal v1.0.0  — impact trace: auth_module <-> database_pool

  Direct path: auth_module -> connection_manager -> database_pool  (2 hops, relation: calls, references)

  Candidates (bridge/neighbor, ranked):
    #1   bridge         hop=1  score=8.0   connection_manager  (calls)  alt=2  2026-06-30T12:08:40
    #2   neighbor-of-a  hop=1  score=6.0   session_store       (calls)        2026-06-28T09:14:02
    ...
```

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
| [docs/cli-reference.md](docs/cli-reference.md) | Complete CLI: every flag, subcommand (including `impact`), example, error code |
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
