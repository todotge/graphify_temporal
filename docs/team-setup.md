# Team setup — install agent instructions

`graphify-temporal install` auto-detects which AI coding assistant you're
using and injects instructions so the agent knows how to run temporal
enrichment without you having to explain it.

## Quick start

```bash
# Auto-detect all clients and install instructions
graphify-temporal install

# Install for a specific client
graphify-temporal install --platform opencode
graphify-temporal install --platform claude

# Remove instructions from all clients
graphify-temporal uninstall
```

## Supported clients

| Client | Instruction file | What it gets |
|--------|-----------------|-------------|
| Claude Code | `CLAUDE.md` | `## graphify-temporal` block |
| OpenCode | `AGENTS.md` | Block + plugin (`.opencode/plugins/graphify-temporal.js`) + `opencode.json` registration |
| Codex | `AGENTS.md` | `## graphify-temporal` block |
| Gemini CLI | `GEMINI.md` | `## graphify-temporal` block |
| Cursor | `.cursor/rules/graphify-temporal.mdc` | `## graphify-temporal` block |
| CodeBuddy | `CODEBUDDY.md` | `## graphify-temporal` block |
| Copilot | `.github/copilot-instructions.md` | `## graphify-temporal` block |
| Windsurf | `.windsurf/rules/graphify-temporal.md` | `## graphify-temporal` block |
| Aider | `AGENTS.md` | `## graphify-temporal` block |
| Kilo Code | `AGENTS.md` | `## graphify-temporal` block |
| Trae | `AGENTS.md` | `## graphify-temporal` block |

## How detection works

Each client has one or more marker files/directories.  If any marker exists
under the project root, the client is considered present.  For example:

| Client | Markers checked |
|--------|----------------|
| OpenCode | `.opencode/` directory |
| Claude Code | `CLAUDE.md` or `.claude/` directory |
| Codex | `.codex/` directory |
| Gemini CLI | `GEMINI.md` or `.gemini/` directory |
| Cursor | `.cursor/` directory or `.cursorrules` file |
| CodeBuddy | `CODEBUDDY.md` or `.codebuddy/` directory |
| Copilot | `.github/copilot-instructions.md` |
| Windsurf | `.windsurf/` directory |
| Aider | `.aider/` directory or `.aider.conf.yml` |
| Kilo Code | `.kilo/` directory |
| Trae | `.trae/` directory |

## What gets injected

The `## graphify-temporal` block contains:

- **Setup** — `pip install git+...`, `git clone ... && pip install .`, and `uv venv && uv pip install -e ".[dev]"`
- **All `enrich`, `query`, `timeline`, `stats` commands** with examples and agent guidance,
  including `--git` for repos where filesystem timestamps are checkout artifacts
  (cloned repos, CI checkouts) rather than real history
- **`install` / `uninstall`** commands
- **Test command** — `.venv/bin/pytest tests/ -v`
- **Key facts** — zero pip deps, idempotent, cross-platform, st_birthtime support,
  `--git` requires the `git` binary but falls back to stat automatically if absent

The block is delimited by `## graphify-temporal` and the next `## ` heading.
On re-install the block is replaced in-place — never duplicated.
On uninstall the block is removed cleanly, preserving one blank line before
the next section.

## OpenCode plugin

For OpenCode, `graphify-temporal install` also writes a JavaScript plugin:

```
.opencode/plugins/graphify-temporal.js
```

And registers it in `.opencode/opencode.json`:

```json
{
  "plugin": [
    ".opencode/plugins/graphify-temporal.js"
  ]
}
```

The plugin hooks into `tool.execute.before` and checks whether
`graphify-out/graph.json` exists but nodes lack `file_mtime` or `dir_mtime`.
If so, it reminds the agent to run `graphify-temporal enrich` before it
reaches for raw file reads.  The reminder fires once per session.

## Idempotency

`install` and `uninstall` are safe to run multiple times:

- **install twice** — the block is replaced with the current version, no duplication
- **uninstall when nothing is installed** — succeeds silently
- **install after uninstall** — writes a fresh block as if it were the first time

## Workflow for a team

1. One person runs `graphify-temporal install` and commits the instruction
   files + `.opencode/plugins/` + `opencode.json`.
2. Everyone pulls — their AI assistant immediately knows how to run
   enrichment.
3. After modifying code, each developer runs `graphify-temporal enrich` to
   keep timestamps current (idempotent, no harm in re-running).

Since every teammate's checkout is itself a git clone, `--use-birthtime`/
default `mtime` will show ~the same value for every file that hasn't been
touched locally since the clone — not useful for tracing who-wrote-what.
For a team repo, prefer:

```bash
graphify-temporal enrich --git
```

This reads dates from the shared commit history instead, so `file_mtime`
and `git_commit_date`/`git_author` reflect the team's actual authorship
timeline regardless of when each person cloned the repo. See
[timestamps.md](timestamps.md#git-derived-timestamps---git) for the
resolution order and fallback rules.
