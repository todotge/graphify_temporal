# CLI Reference

## `enrich` — stamp nodes with temporal metadata

```
graphify-temporal enrich [PATH] [OPTIONS]
```

| Flag | Type | Description |
|------|------|-------------|
| `PATH` | positional | Project root directory (default: `.`) |
| `--use-ctime` | flag | Use `st_ctime` instead of `st_mtime` (Unix: metadata-change time) |
| `--use-birthtime` | flag | Use `st_birthtime` instead of `st_mtime` (true creation time). Mutually exclusive with `--use-ctime` |
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
| `--use-ctime` and `--use-birthtime` together | 1 | `--use-ctime and --use-birthtime are mutually exclusive` |
| Invalid `--since` date | 1 | `invalid --since date '...'. Expected YYYY-MM-DD.` |
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

### Examples

```bash
graphify-temporal query "auth"                          # find nodes by name
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

### Examples

```bash
graphify-temporal timeline                             # full chain from oldest node
graphify-temporal timeline --since 2026-05-01           # from oldest >= May
graphify-temporal timeline "enricher_enrich"             # from a specific node
```

### Output

```
  #1   opencode_package               2026-06-11T19:54:05  package.json
                                                          ↓ preceded_by
  #2   opencode_package_dependencies  2026-06-11T19:54:05  dependencies
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
