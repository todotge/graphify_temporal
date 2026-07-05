# CLI Reference

> `graphify-temporal` is a separate CLI from `graphify`. All subcommands
> start with `graphify-temporal`, not `graphify`.

## `enrich` — stamp nodes with temporal metadata

Nodes from `graphify-out/` itself are automatically excluded — no need to
pass `--exclude "graphify-out/**"`.

```
graphify-temporal enrich [PATH] [OPTIONS]
```

| Flag | Type | Description |
|------|------|-------------|
| `PATH` | positional | Project root directory (default: `.`) |
| `--use-ctime` | flag | Use `st_ctime` instead of `st_mtime` (Unix: metadata-change time) |
| `--use-birthtime` | flag | Use `st_birthtime` instead of `st_mtime` (true creation time). Mutually exclusive with `--use-ctime`/`--git` |
| `--git` | flag | Derive `file_mtime` from git author-dates (`git log --follow`/`git blame --porcelain`) instead of stat, for files tracked in a git repo. Falls back to stat automatically per file when git is missing, the path isn't a repo, or the file is untracked. Also stamps `git_commit_date` (line-accurate) and `git_author` on nodes. Mutually exclusive with `--use-ctime`/`--use-birthtime` |
| `--include-dir-mtime` | flag | Also add `dir_mtime` (parent directory mtime) to nodes — arrival proxy |
| `--cross-file` | flag | Create `preceded_by` edges across different files ordered by mtime |
| `--dry-run` | flag | Compute stats and print them without modifying `graph.json` |
| `--since DATE` | string | Only process files with `st_mtime >= DATE` (YYYY-MM-DD) |
| `--include GLOB` | repeatable | Only process `source_file` paths matching glob |
| `--exclude GLOB` | repeatable | Exclude `source_file` paths matching glob |
| `--no-regenerate` | flag | Skip regenerating HTML/wiki after enrichment |
| `--quiet`, `-q` | flag | Suppress output |

### Examples

```bash
# Basic: mtime + intra-file preceded_by edges
graphify-temporal enrich

# Git-derived dates instead of filesystem stat (cloned repos, CI checkouts)
graphify-temporal enrich --git

# Git dates + cross-file chronological edges
graphify-temporal enrich --git --cross-file

# True creation time instead of modification time
graphify-temporal enrich --use-birthtime

# Directory arrival proxy alongside default mtime
graphify-temporal enrich --include-dir-mtime

# Full timeline: birthtime as primary + arrival proxy
graphify-temporal enrich --use-birthtime --include-dir-mtime

# Cross-file chains filtered by date
graphify-temporal enrich --cross-file --since 2026-05-01

# Preview a subdirectory without modifying
graphify-temporal enrich --include "your/archive/**" --dry-run

# Exclude archive directories
graphify-temporal enrich --exclude "**/archive/**" --exclude "**/old/**"
```

### Output

```
graphify-temporal v1.0.0
  Files analyzed:     1,220
  Nodes enriched:    14,173 (85%)
  Files not found:       12
  Edges added:       12,946 (intra-file: 12,911, cross-file: 35)
  html:               ✓
  wiki:               ✓
```

With `--dry-run` the same stats are printed but `graph.json` is not modified.
When re-running, `Edges deduplicated: N` appears for edges skipped because an
identical `(source, target, relation)` triple already exists.

### Error exit codes

| Scenario | Exit code | Message |
|----------|-----------|---------|
| `graph.json` not found | 1 | `No graph.json found at ... Run \`graphify .\` first.` |
| Invalid JSON in graph.json | 1 | `Invalid JSON in ...` |
| `--use-ctime`, `--use-birthtime`, `--git` — more than one together | 1 | `--git, --use-ctime and --use-birthtime are mutually exclusive. Choose one timestamp source.` |
| Invalid `--since` date | 1 | `invalid --since date '...'. Expected YYYY-MM-DD.` |
| `--git` but git not on PATH | 0 | Notice printed, falls back to stat — not an error |
| `--git` but path isn't a git repo | 0 | Notice printed, falls back to stat — not an error |
| Success (or `--dry-run`) | 0 | Stats printed |

---

## `install` — inject agent instructions

```
graphify-temporal install [--platform CLIENT]
```

Scans the project root for AI coding assistant markers and writes a
`## graphify-temporal` block into the appropriate instruction file.

| Flag | Description |
|------|-------------|
| `--platform CLIENT` | Force a specific client: `claude`, `codex`, `opencode`, `gemini`, `cursor`, `codebuddy`, `copilot`, `windsurf`, `aider`, `kilo`, `trae`. Default: auto-detect all. |

For OpenCode, also registers a `tool.execute.before` plugin in
`.opencode/plugins/graphify-temporal.js` and adds it to `opencode.json`.

### Examples

```bash
graphify-temporal install                    # auto-detect all clients
graphify-temporal install --platform claude   # Claude Code only
graphify-temporal install --platform opencode # OpenCode (includes plugin)
```

---

## `uninstall` — remove agent instructions

```
graphify-temporal uninstall [--platform CLIENT]
```

Removes the `## graphify-temporal` block from instruction files and
de-registers the OpenCode plugin.

### Examples

```bash
graphify-temporal uninstall                    # remove from all detected clients
graphify-temporal uninstall --platform opencode # OpenCode only
```

---

## `query` — search and filter enriched nodes

**`--since` and `--before` require explicit dates (YYYY-MM-DD).**  The tool does
not interpret "last week" or "yesterday" — compute the range first, then pass it.

```
graphify-temporal query [SEARCH] [OPTIONS]
```

| Flag | Type | Description |
|------|------|-------------|
| `SEARCH` | positional | Substring to match against node `id` and `label` (case-insensitive) |
| `--since DATE` | string | Only show nodes with timestamp >= DATE (YYYY-MM-DD) |
| `--before DATE` | string | Only show nodes with timestamp <= DATE (YYYY-MM-DD) |
| `--use-dir-mtime` | flag | Filter/sort by `dir_mtime` instead of `file_mtime` |
| `--order MODE` | choice | `newest-first`, `oldest-first`, or `none` (default) |
| `--full` | flag | Show all nodes (default: one per source_file, like `timeline`) |

### Examples

```bash
graphify-temporal query "auth"                          # find nodes by name (one per file)
graphify-temporal query "auth" --full                   # every matching node
graphify-temporal query "auth" --since 2026-05-01       # + filter by date
graphify-temporal query "auth" --order newest-first     # sorted chronologically
graphify-temporal query --since 2026-06-01 --order oldest-first  # all nodes this month

# Typical agent usage — compute the date first, then query:
# TODAY=$(date -I) && graphify-temporal query --since "$TODAY" --order newest-first
```

### Agent guidance

When a user asks for a time-based report ("last week", "what changed today"),
do NOT guess dates.  Compute them explicitly:

```bash
# Today
TODAY=$(date -I)
graphify-temporal query --since "$TODAY" --order newest-first

# Last 7 days
LAST_WEEK=$(date -I -d "7 days ago")
graphify-temporal query --since "$LAST_WEEK" --order oldest-first
```

Then present the results in a human-readable summary — the raw table is verbose.

---

## `timeline` — walk preceded_by chains

```
graphify-temporal timeline [START_ID] [OPTIONS]
```

| Flag | Type | Description |
|------|------|-------------|
| `START_ID` | positional | Begin the timeline at this node id (default: oldest chain-start) |
| `--since DATE` | string | Only show steps with timestamp >= DATE |
| `--before DATE` | string | Only show steps with timestamp <= DATE |
| `--full` | flag | Show every node (default: collapsed, one entry per file) |

### Examples

```bash
graphify-temporal timeline                             # one entry per file
graphify-temporal timeline --full                      # every node, verbose
graphify-temporal timeline --since 2026-05-01           # from oldest >= May
graphify-temporal timeline "enricher_enrich"             # from a specific node
```

### Output

```
  #1   opencode_package               2026-06-11T19:54:05Z  package.json
                                                          ↓ preceded_by
  #2   opencode_package_dependencies  2026-06-11T19:54:05Z  dependencies
                                                          ↓ preceded_by
  #3   ...
```

---

## `stats` — temporal coverage summary

```
graphify-temporal stats [--json]
```

| Flag | Description |
|------|-------------|
| `--json` | Output machine-readable JSON instead of text |

### Example output

```
graphify-temporal v1.0.0  — temporal stats

  Nodes total:           340
  With file_mtime:       340 (100%)
  With dir_mtime:        0 (0%)
  Files with mtime:      27
  Time span:             2026-06-11 → 2026-06-14 (3.1 days)
```

---

## `impact` — root-cause tracing

Trace structural + temporal connections between one or two nodes: given two
areas of code, find nodes reachable from either (or both — a "bridge") within
a bounded number of hops over **any** edge relation (`calls`, `imports`,
`references`, `conceptually_related_to`, `preceded_by`, ...), ranked by
relevance. Built for "I changed X, then Y broke — what did I touch that
could have caused it?"

```
graphify-temporal impact NODE_A [NODE_B] [OPTIONS]
```

| Flag | Type | Description |
|------|------|-------------|
| `NODE_A` | positional | First node id (e.g. the file/function you changed) |
| `NODE_B` | positional, optional | Second node id (e.g. the file that broke). Omit for single-anchor mode: explore what's reachable/at-risk around `NODE_A` alone |
| `--hops N` | int | Max traversal depth from each anchor (default: 3) |
| `--relations REL,REL` | string | Comma-separated relation types to follow (default: all relations, including `preceded_by` — this is what makes the temporal-only degraded case visible instead of silently invisible) |
| `--max-candidates N` | int | Cap on returned candidates, best-scoring kept (default: 25) |
| `--json` | flag | Output as JSON instead of human-readable text |

Node ids come from `graphify-temporal query "<search term>"` — run that first
if you don't already know the exact id.

**Read-only** — never writes to `graph.json`. Safe to call repeatedly during
a debugging session.

### Examples

```bash
# How are these two areas connected?
graphify-temporal impact auth_module database_pool

# What's reachable/at-risk around this node alone?
graphify-temporal impact auth_module

# Widen the search
graphify-temporal impact auth_module database_pool --hops 5

# Structural-only — exclude preceded_by timestamp-chain edges
graphify-temporal impact auth_module database_pool --relations calls,references

# Machine-readable
graphify-temporal impact auth_module database_pool --json
```

### Ranking formula

Each candidate's score is additive:

```
score = (3 - hop)                                  closer = more relevant
      + confidence_bonus[edge's confidence]         EXTRACTED=2, INFERRED=1, AMBIGUOUS=0
      + 2 if relation != "preceded_by"               real structural edge > temporal chaining
      + 1 if candidate's community != anchor's       cross-community = more surprising
      + 1 if reached from both anchors ("bridge")     strongest signal
```

Ties break by node id, ascending (deterministic output). A node reached via
multiple independent edges (e.g. both `calls` and `references` from the same
anchor, or from both anchors) shows `alt=N` in the human-readable output —
independent confirmation is itself a relevance signal.

### Output (human-readable)

```
graphify-temporal v1.0.0  — impact trace: auth_module <-> database_pool

  Direct path: auth_module -> connection_manager -> database_pool  (2 hops, relation: calls, references)

  Candidates (bridge/neighbor, ranked):
    #1   bridge         hop=1  score=8.0   connection_manager  (calls)  alt=2  2026-06-30T12:08:40
    #2   neighbor-of-a  hop=1  score=6.0   session_store       (calls)        2026-06-28T09:14:02
```

If the graph has no semantic edges (only `preceded_by`), a warning line
prints first:
```
  [temporal-only: no semantic edges in this graph — results reflect
  timestamp proximity only, not confirmed code relationships]
```

### Output (`--json`)

```json
{
  "anchor_a": "auth_module",
  "anchor_b": "database_pool",
  "structural_confidence": "structural+temporal",
  "direct_path": [
    {"node_id": "connection_manager", "relation": "calls", "hop": 1},
    {"node_id": "database_pool", "relation": "references", "hop": 2}
  ],
  "candidates": [
    {
      "node_id": "connection_manager", "label": "ConnectionManager",
      "hop": 1, "connection": "bridge", "relation_path": ["calls"],
      "score": 8.0, "alternate_paths": 2, "community": 3,
      "file_mtime": "2026-06-30T12:08:40Z", "dir_mtime": null,
      "git_commit_date": "2026-06-30T12:08:40Z", "git_author": "kemycrome",
      "source_file": "app/db.py"
    }
  ],
  "truncated": false,
  "isolated_anchors": []
}
```

### Error exit codes

| Scenario | Exit code | Message |
|----------|-----------|---------|
| `graph.json` not found | 1 | `No graph.json found at ... Run \`graphify .\` first.` |
| Invalid JSON in graph.json | 1 | `Invalid JSON in ...` |
| `NODE_A`/`NODE_B` not found in graph | 1 | `Node '...' not found in graph` |
| `--hops 0` or negative | 1 | `--hops must be >= 1` (rejected before calling the core query) |
| Anchor exists but has zero edges | 0 | Not an error — `isolated_anchors` lists it, candidates empty for it |
| Graph has zero semantic edges | 0 | Not an error — `structural_confidence: "temporal-only"` |
| Hop/candidate budget reached | 0 | Not an error — `truncated: true`, results still returned |
| Success | 0 | Trace printed |

### Agent guidance

Use this **proactively during debugging, before manually grepping**. When a
user reports something broke and names (or you can infer) two related areas
of code — "I changed X, then something in Y broke", "what did I touch that
could have caused Z" — run `impact <node_a> <node_b>` first. If the result is
`temporal-only`, treat it as weaker evidence and say so.

---

## `--version`

```
graphify-temporal --version
```

Prints the installed version and exits.

---

## Prompt examples — what to ask your AI agent

Once `graphify-temporal install` has injected the instructions into your
agent's config, these prompts work directly:

### Daily workflow

| Prompt | What the agent runs |
|--------|---------------------|
| `what changed today?` | `graphify-temporal query --since $(date -I) --order newest-first` |
| `recap of last week` | `graphify-temporal query --since $(date -I -d "7 days ago") --order oldest-first` |
| `show me the last 10 changes` | `graphify-temporal query --order newest-first \| head -10` |

### Timeline

| Prompt | What the agent runs |
|--------|---------------------|
| `walk me through the project timeline` | `graphify-temporal timeline` |
| `show the chain starting from enricher.py` | `graphify-temporal timeline "graphify_temporal_enricher_enrich"` |
| `which files were created first?` | `graphify-temporal enrich --use-birthtime && graphify-temporal timeline` |

### Audit

| Prompt | What the agent runs |
|--------|---------------------|
| `how old is this project?` | `graphify-temporal stats` |
| `are there files without timestamps?` | `graphify-temporal enrich --dry-run` (check `files_not_found`) |

### Git-derived dates

| Prompt | What the agent runs |
|--------|---------------------|
| `this is a cloned repo, the timestamps look wrong` | `graphify-temporal enrich --git` |
| `who wrote this function and when?` | `graphify-temporal enrich --git && graphify-temporal query "<name>" --full` (check `git_author`/`git_commit_date`) |
| `use real commit history instead of file timestamps` | `graphify-temporal enrich --git` |
| `order changes by actual commit date, not checkout date` | `graphify-temporal enrich --git --cross-file` |

### Root-cause tracing

| Prompt | What the agent runs |
|--------|---------------------|
| `I changed X, then Y broke — what did I do wrong?` | `graphify-temporal impact <node_x> <node_y>` |
| `what could break if I touch this function?` | `graphify-temporal impact <node_id>` (single-anchor mode) |
| `how are these two modules connected?` | `graphify-temporal impact <node_a> <node_b>` |
| `trace a wider blast radius` | `graphify-temporal impact <node_a> <node_b> --hops 5` |
| `only show real code relationships, not just timing` | `graphify-temporal impact <node_a> <node_b> --relations calls,references` |
| `this used to work, what changed?` | `graphify-temporal impact <node_a> <node_b>` — check `structural_confidence`; if `temporal-only`, suggest `/graphify --update deep` first |

### Creation vs arrival

| Prompt | What the agent runs |
|--------|---------------------|
| `when were files actually created vs when did they arrive here?` | `graphify-temporal enrich --use-birthtime --include-dir-mtime && graphify-temporal query --order oldest-first` |
| `show me files that arrived long after being created` | Compare `file_mtime` and `dir_mtime` in enriched graph |
| `what's the real creation date of __init__.py?` | `graphify-temporal query "__init__" --order oldest-first` (with birthtime) |

### Combined filters

| Prompt | What the agent runs |
|--------|---------------------|
| `find everything about auth modified in June` | `graphify-temporal query "auth" --since 2026-06-01 --before 2026-07-01 --order oldest-first` |
| `files in src/ touched this week` | `graphify-temporal query --since $(date -I -d monday) --order newest-first` |

### Setup

| Prompt | What the agent runs |
|--------|---------------------|
| `set up graphify-temporal for this project` | `graphify-temporal install` |
| `add timestamps to the existing graph` | `graphify-temporal enrich` |
| `I also want to know when files arrived in directories` | `graphify-temporal enrich --include-dir-mtime` |
| `update the graph with real creation dates` | `graphify-temporal enrich --use-birthtime` |

### Code review & modifications

| Prompt | What the agent runs |
|--------|---------------------|
| `review the most recently modified files` | `graphify-temporal query --order newest-first \| head -20` |
| `which files have changed this week? review them` | `graphify-temporal query --since $(date -I -d "7 days ago") --order newest-first` |
| `show me what arrived here vs what was created` | `graphify-temporal enrich --use-birthtime --include-dir-mtime && graphify-temporal query --order oldest-first` |

### Onboarding & audit

| Prompt | What the agent runs |
|--------|---------------------|
| `how active is this project?` | `graphify-temporal stats` |
| `which files haven't been touched in months?` | `graphify-temporal query --before 2026-01-01 --order oldest-first` |
| `were these files copied in bulk or modified individually?` | `graphify-temporal query --order oldest-first` (timestamps identici = bulk copy) |
| `what did the project look like at the end of last month?` | `graphify-temporal query --before 2026-05-31 --order newest-first` |
| `which folders changed the most recently?` | `graphify-temporal query --since $(date -I -d "30 days ago") --order newest-first` |
| `what was the exact order of changes during that incident?` | `graphify-temporal timeline --since DATE --before DATE` |
