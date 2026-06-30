"""Auto-detect AI coding clients and inject graphify-temporal instructions.

Scans a project root for known client markers (CLAUDE.md, .opencode/,
GEMINI.md, .cursor/, etc.), then writes a ``## graphify-temporal`` block
into the appropriate instruction file.  For OpenCode it also registers a
``tool.execute.before`` plugin that reminds the agent to run enrichment
when graph.json exists but lacks temporal stamps.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable


# ---------------------------------------------------------------------------
# client registry
# ---------------------------------------------------------------------------

# Each client entry: (instruction_file_relative_to_root, list_of_marker_paths)
# Instruction file may be None for clients where we only inject a plugin.
# Marker paths can be files or directories — any one existing means the client
# is present.
_DEFINITIONS: dict[str, tuple[str | None, list[str]]] = {
    "claude":    ("CLAUDE.md",                                ["CLAUDE.md", ".claude"]),
    "codex":     ("AGENTS.md",                                [".codex"]),
    "opencode":  ("AGENTS.md",                                [".opencode"]),
    "gemini":    ("GEMINI.md",                                ["GEMINI.md", ".gemini"]),
    "cursor":    (".cursor/rules/graphify-temporal.mdc",      [".cursor", ".cursorrules"]),
    "codebuddy": ("CODEBUDDY.md",                             ["CODEBUDDY.md", ".codebuddy"]),
    "copilot":   (".github/copilot-instructions.md",          [".github/copilot-instructions.md"]),
    "windsurf":  (".windsurf/rules/graphify-temporal.md",      [".windsurf"]),
    "aider":     ("AGENTS.md",                                [".aider", ".aider.conf.yml"]),
    "kilo":      ("AGENTS.md",                                [".kilo"]),
    "trae":      ("AGENTS.md",                                [".trae"]),
}

# Instruction block delimiters — everything between (inclusive) is managed.
_BLOCK_START = "## graphify-temporal\n"
_BLOCK_END   = ("# ", "## ")  # headings of any level

# ---- template ----
_INSTRUCTION_BLOCK = """\
## graphify-temporal

Temporal enrichment for graphify knowledge graphs — stamps nodes with filesystem
timestamps and creates deterministic `preceded_by` edges.

> **Note:** `graphify-temporal` is a SEPARATE command from `graphify`.
> The enrichment subcommand is `graphify-temporal enrich`, NOT `graphify enrich`.
> graphify itself has no temporal features — this extension adds them.

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
"""

# OpenCode plugin — JavaScript
_OPLUGIN_JS = """\
// graphify-temporal OpenCode plugin
// Checks whether graphify-out/graph.json exists but nodes lack temporal
// stamps, then nudges the agent to run graphify-temporal enrich.
import { existsSync, readFileSync } from "fs";
import { join } from "path";

const _hasStamps = (dir) => {
  const p = join(dir, "graphify-out", "graph.json");
  if (!existsSync(p)) return false;
  try {
    const nodes = JSON.parse(readFileSync(p, "utf-8")).nodes || [];
    return nodes.some((n) => n.file_mtime || n.dir_mtime);
  } catch { return false; }
};

export const GraphifyTemporalPlugin = async ({ directory }) => {
  let done = false;
  return {
    "tool.execute.before": async (input, output) => {
      if (done) return;
      if (!existsSync(join(directory, "graphify-out", "graph.json"))) return;
      if (_hasStamps(directory)) return;
      if (input.tool !== "bash") return;
      output.args.command =
        '[graphify-temporal] graph.json has no file_mtime. Run `graphify-temporal enrich` to add timestamps. Use --use-birthtime for creation time, --include-dir-mtime for arrival proxy.` && ' +
        output.args.command;
      done = true;
    },
  };
};
"""


# ---------------------------------------------------------------------------
# public API
# ---------------------------------------------------------------------------


def detect(root: Path) -> list[str]:
    """Return alphabetically sorted list of client IDs found under *root*."""
    found: list[str] = []
    for cid, (_, markers) in _DEFINITIONS.items():
        for m in markers:
            if (root / m).exists():
                found.append(cid)
                break
    found.sort()
    return found


def install(
    root: Path,
    clients: Iterable[str] | None = None,
) -> dict[str, bool]:
    """Inject the graphify-temporal block and (for OpenCode) the plugin.

    Returns ``{client_id: True/False}`` per processed client.
    """
    if clients is None:
        clients = detect(root)
    results: dict[str, bool] = {}

    for cid in sorted(set(clients)):
        ok = True
        instr_file, _markers = _DEFINITIONS.get(cid, (None, []))
        if instr_file:
            ok = _inject_block(root / instr_file) and ok
        if cid == "opencode":
            ok = _install_opencode_plugin(root) and ok
            ok = _register_opencode_json(root) and ok
        results[cid] = ok

    return results


def uninstall(
    root: Path,
    clients: Iterable[str] | None = None,
) -> dict[str, bool]:
    """Remove the graphify-temporal block and (for OpenCode) the plugin.

    Returns ``{client_id: True/False}`` per processed client.
    """
    if clients is None:
        clients = detect(root)
    results: dict[str, bool] = {}

    for cid in sorted(set(clients)):
        ok = True
        instr_file, _markers = _DEFINITIONS.get(cid, (None, []))
        if instr_file:
            ok = _remove_block(root / instr_file) and ok
        if cid == "opencode":
            ok = _uninstall_opencode_plugin(root) and ok
            ok = _unregister_opencode_json(root) and ok
        results[cid] = ok

    return results


# ---------------------------------------------------------------------------
# instruction block injection / removal
# ---------------------------------------------------------------------------


def _inject_block(filepath: Path) -> bool:
    """Write or update the ``## graphify-temporal`` block in *filepath*."""
    try:
        if filepath.exists():
            text = filepath.read_text(encoding="utf-8")
        else:
            text = ""
    except OSError:
        return False

    if _BLOCK_START in text:
        new_text = _replace_block(text)
    else:
        new_text = _append_block(text)

    if new_text == text:
        return True  # nothing changed — already up to date

    try:
        filepath.parent.mkdir(parents=True, exist_ok=True)
        filepath.write_text(new_text, encoding="utf-8")
        return True
    except OSError:
        return False


def _remove_block(filepath: Path) -> bool:
    """Remove the ``## graphify-temporal`` block from *filepath*."""
    if not filepath.exists():
        return True  # nothing to remove
    try:
        text = filepath.read_text(encoding="utf-8")
    except OSError:
        return False

    if _BLOCK_START not in text:
        return True

    new_text = _replace_block(text, replacement="")
    if new_text == text:
        return True

    try:
        filepath.write_text(new_text, encoding="utf-8")
        return True
    except OSError:
        return False


def _replace_block(text: str, replacement: str | None = None) -> str:
    """Extract lines between (and including) first ``_BLOCK_START`` and the
    next ``## `` heading.  Replace with *replacement* (if None, use the
    template).  Preserves exactly one blank line before the next heading
    when a block is removed."""
    replacement = _INSTRUCTION_BLOCK if replacement is None else replacement
    lines = text.splitlines(keepends=True)

    start_idx: int | None = None
    end_idx: int | None = None

    for i, line in enumerate(lines):
        if start_idx is None and line.startswith(_BLOCK_START):
            start_idx = i
        elif start_idx is not None and i > start_idx and (
            line.startswith(_BLOCK_END[0]) or line.startswith(_BLOCK_END[1])
        ):
            end_idx = i
            break

    if start_idx is None:
        return text

    before = "".join(lines[:start_idx])

    if end_idx is None:
        # block at end of file — remove to EOF
        return before + replacement

    after = "".join(lines[end_idx:])
    if replacement:
        # replacement ends with a newline already; ensure one blank separator
        return before + replacement.rstrip("\n") + "\n\n" + after.lstrip("\n")
    else:
        # uninstall — keep one blank line before the next heading
        return before.rstrip("\n") + "\n\n" + after.lstrip("\n")


def _append_block(text: str) -> str:
    """Append the instruction block ensuring exactly one blank line before."""
    stripped = text.rstrip()
    if stripped:
        return stripped + "\n\n" + _INSTRUCTION_BLOCK + "\n"
    return _INSTRUCTION_BLOCK + "\n"


# ---------------------------------------------------------------------------
# OpenCode plugin helpers
# ---------------------------------------------------------------------------

_OPLUGIN_PATH = Path(".opencode/plugins/graphify-temporal.js")


def _install_opencode_plugin(root: Path) -> bool:
    """Write the OpenCode plugin JS file."""
    fp = root / _OPLUGIN_PATH
    try:
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(_OPLUGIN_JS, encoding="utf-8")
        return True
    except OSError:
        return False


def _uninstall_opencode_plugin(root: Path) -> bool:
    """Delete the OpenCode plugin JS file."""
    fp = root / _OPLUGIN_PATH
    try:
        if fp.exists():
            fp.unlink()
        return True
    except OSError:
        return False


def _register_opencode_json(root: Path) -> bool:
    """Add the plugin to opencode.json if not already registered."""
    config_path = root / ".opencode" / "opencode.json"
    try:
        if config_path.exists():
            data = json.loads(config_path.read_text(encoding="utf-8"))
        else:
            data = {"$schema": "https://opencode.ai/config.json", "plugin": []}
    except (json.JSONDecodeError, OSError):
        return False

    plugins_raw = data.get("plugin")
    if not isinstance(plugins_raw, list):
        plugins_raw = []
    plugins: list[str] = plugins_raw
    data["plugin"] = plugins
    plugin_ref = ".opencode/plugins/graphify-temporal.js"
    if plugin_ref not in plugins:
        plugins.append(plugin_ref)

    try:
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        return True
    except OSError:
        return False


def _unregister_opencode_json(root: Path) -> bool:
    """Remove the plugin reference from opencode.json."""
    config_path = root / ".opencode" / "opencode.json"
    if not config_path.exists():
        return True
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return False

    plugins_raw = data.get("plugin")
    if not isinstance(plugins_raw, list):
        # Malformed config — restore to a list.
        plugins_raw = []
    plugins: list[str] = plugins_raw
    plugin_ref = ".opencode/plugins/graphify-temporal.js"
    if plugin_ref in plugins:
        plugins.remove(plugin_ref)

    try:
        config_path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        return True
    except OSError:
        return False
