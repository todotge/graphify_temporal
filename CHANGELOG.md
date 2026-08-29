# Changelog

All notable changes to this project will be documented in this file.

____

## [1.0.1] - 2026-08-29

### Added

- Discoverable **skill** install for OpenCode and Claude Code:
  `graphify-temporal install` now also writes
  `.opencode/skills/graphify-temporal/SKILL.md` and
  `.claude/skills/graphify-temporal/SKILL.md` (one template, both formats).
  The frontmatter description is the trigger surface — the agent reaches
  for `enrich`/`query`/`timeline`/`impact` on temporal questions without
  reading the full instruction block.
- Version-tracked skill refresh: a `.graphify-temporal_version` marker
  (same pattern as graphify's `.graphify_version`) makes re-running
  `install` after an upgrade rewrite the skill only when the version
  changed.
- `uninstall` removes the skill files and directory, preserving any user
  files added there.

### Changed

- Refactor cleanup (−72 lines): removed dead `mode="first"` path and
  `is_shallow_repo` from `git_source.py`, merged the duplicate
  install/uninstall result printers, hoisted the duplicated `--platform`
  choices list, dropped redundant `FileNotFoundError` handlers
  (subclass of `OSError`), removed dead guards in cross-file edge chaining,
  and deleted the unused `_BLAME_AUTHOR_RE` regex.
- Tests parametrized (open-code/claude skill cases) instead of duplicated.

## [1.0.0] - 2026-07-06

### Added

- `graphify-temporal enrich`: stamps nodes with `file_mtime` (or
  `st_ctime` via `--use-ctime`, true creation time via
  `--use-birthtime` — statx(2) fallback on Linux), optional
  `dir_mtime` arrival proxy (`--include-dir-mtime`), deterministic
  `preceded_by` edges intra-file (by `source_location` line order) and
  cross-file (`--cross-file`), `--dry-run`, `--since`,
  `--include`/`--exclude` globs. Idempotent: dedup by
  (source, target, relation) triple.
- `--git` timestamp sourcing: file-level author-dates via one `git log`
  per unique file, line-level `git_commit_date` via one
  `git blame --porcelain` per unique file, plus `git_author`.
  Falls back to stat automatically; never overwrites stat-sourced
  `file_mtime` semantics.
- `graphify-temporal query` (time-filtered node search),
  `timeline` (`preceded_by` chain walk), `stats` (temporal coverage),
  `impact` (bounded multi-relation BFS root-cause tracing between two
  areas of code).
- `graphify-temporal install`/`uninstall`: auto-detects 11 AI coding
  clients, injects a `## graphify-temporal` instruction block, and for
  OpenCode registers a plugin that reminds the agent to enrich when
  `graph.json` lacks temporal stamps.

### Fixed

- `UnicodeDecodeError` in `blame_file` on non-UTF-8 content now returns
  None instead of crashing the pipeline.

[1.0.1]: https://github.com/todotge/graphify-temporal/compare/v1.0.0...v1.0.1
[1.0.0]: https://github.com/todotge/graphify-temporal/releases/tag/v1.0.0

____

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
