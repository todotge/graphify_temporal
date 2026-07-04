"""Core enrichment pipeline.

Reads graphify-out/graph.json, stamps every node whose source_file exists on
disk with file_mtime (ISO 8601), inserts deterministic preceded_by edges, and
writes the result back in-place.  Also reinvokes graphify export for html/wiki
so visualizations stay current.

Edge generation happens in two passes:

  intra-file  — nodes inside the same file are chained by source_location
                line number.  Three nodes at L3, L7, L12 produce two edges:
                L3→L7, L7→L12.

  cross-file  — the first node in each file is linked to the first node in
                the next file ordered by mtime (opt-in via --cross-file).
                Only files that resolved to a real timestamp participate.

All edges carry confidence EXTRACTED / 1.0 because they come from a
deterministic stat + sort, not from a model.
"""

import json
import re
from pathlib import Path
from collections import defaultdict
from typing import Any

from .fs import (
    resolve_mtime, resolve_birthtime, resolve_dir_mtime,
    matches_glob, is_excluded, parse_date,
)
from . import git_source


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _extract_line(source_location: Any) -> int:
    """Pull the line number out of a ``source_location`` string.

    graphify source_location looks like ``foo/bar.py:L42``.  The regex grabs
    the first ``L<digits>`` token.  Returns 0 when the field is empty or
    doesn't contain a line marker — this is the safe fallback for nodes that
    weren't produced by AST extraction (e.g. doc concepts).
    """
    if not source_location:
        return 0
    m = re.search(r":L(\d+)$", str(source_location))
    return int(m.group(1)) if m else 0


def _iso_to_epoch(iso: str) -> float | None:
    """Parse the fs.py/git_source.py ``...Z`` ISO 8601 shape back to epoch."""
    import calendar
    import time as _time
    try:
        return float(calendar.timegm(_time.strptime(iso, "%Y-%m-%dT%H:%M:%SZ")))
    except (ValueError, TypeError):
        return None


def _safe_relative(git_root: Path, abs_path: Path) -> str | None:
    """Return *abs_path* relative to *git_root* as a posix string, or None.

    Guards every git subprocess call against a malicious/traversing
    ``source_file`` value (e.g. containing ``../``) reaching git's argv —
    ``Path.relative_to`` raises ValueError for anything outside git_root,
    which we treat as "can't use git for this file", not a crash.
    """
    try:
        return abs_path.resolve().relative_to(git_root).as_posix()
    except (ValueError, OSError):
        return None


# ---------------------------------------------------------------------------
# main entry point
# ---------------------------------------------------------------------------


def enrich(
    root: Path,
    use_ctime: bool = False,
    use_birthtime: bool = False,
    use_git: bool = False,
    cross_file: bool = False,
    dry_run: bool = False,
    since: str | None = None,
    include: list[str] | None = None,
    exclude: list[str] | None = None,
    include_dir_mtime: bool = False,
    quiet: bool = False,
) -> dict[str, Any]:
    """Run the full enrichment pipeline and return a stats dictionary.

    Parameters
    ----------
    root : Path
        Project root — must contain ``graphify-out/graph.json``.
    use_ctime : bool
        Stat st_ctime instead of st_mtime (Unix: metadata-change time).
    use_birthtime : bool
        Stat st_birthtime instead of st_mtime (true creation time).
        Mutually exclusive with use_ctime and use_git.
    use_git : bool
        Derive ``file_mtime`` from git author-dates (git log) instead of
        stat(2), for files tracked inside a git repository — stat mtime on a
        clone reflects checkout time, not history. Falls back to stat for
        untracked files or when git/history is unavailable. Also stamps
        each node with ``git_commit_date`` (line-level via git blame when
        possible, file-level otherwise) and ``git_author``. Mutually
        exclusive with use_ctime and use_birthtime.
    cross_file : bool
        Generate chronological edges between the first node of each file.
    dry_run : bool
        Compute stats and return without touching graph.json.
    since : str | None
        ISO date string (YYYY-MM-DD).  Files older than this are skipped.
    include : list[str] | None
        Glob patterns for source_file paths to process.
    exclude : list[str] | None
        Glob patterns for source_file paths to ignore.
    include_dir_mtime : bool
        Also stamp each node with ``dir_mtime`` — the parent directory's
        mtime, which is the best filesystem proxy for arrival time.
    quiet : bool
        Suppress output regeneration stderr.

    Returns
    -------
    dict
        Keys: files_analyzed, nodes_enriched, nodes_total, files_not_found,
        edges_intra_file, edges_cross_file, edges_total, edges_deduped.
    """

    # ---- load the existing graph ------------------------------------------
    graph_path = root / "graphify-out" / "graph.json"
    if not graph_path.exists():
        raise FileNotFoundError(
            f"No graph.json found at {graph_path}. "
            "Run `graphify .` first to build the graph."
        )

    try:
        data: dict[str, Any] = json.loads(
            graph_path.read_text(encoding="utf-8")
        )
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON in {graph_path}: {e}")

    nodes: list[dict[str, Any]] = data.get("nodes", [])
    raw_links = data.get("links")
    if isinstance(raw_links, list):
        links: list[dict[str, Any]] = raw_links
    else:
        # links is null, missing, or wrong type — create a fresh list in-place
        links = []
        data["links"] = links

    # Convert the since date once so we compare floats, not strings.
    since_ts: float | None = parse_date(since) if since else None

    # ---- resolve git repo root once (use_git only) -------------------------
    # git_root stays None when git is missing, root isn't inside a working
    # tree, or use_git wasn't requested — every file then falls straight
    # through to the existing stat-based resolution, unchanged.
    git_root: Path | None = None
    if use_git:
        if git_source.git_available():
            git_root = git_source.find_repo_root(root)
            if git_root is None and not quiet:
                print(
                    "  --git requested but no git repository found at "
                    f"{root} — falling back to filesystem timestamps",
                )
        elif not quiet:
            print(
                "  --git requested but git not found on PATH — "
                "falling back to filesystem timestamps",
            )

    # ---- collect unique source_file paths ---------------------------------
    unique_files: set[str] = set()
    for node in nodes:
        sf = node.get("source_file")
        if sf and matches_glob(sf, include) and not is_excluded(sf, exclude):
            if sf.startswith("graphify-out/") or sf == "graphify-out":
                continue
            unique_files.add(sf)

    # ---- resolve timestamps for every unique file -------------------------
    # mtime_cache maps source_file → ISO 8601 string or None (missing/old).
    mtime_cache: dict[str, str | None] = {}
    # git_file_date_cache holds the git-derived date per file (use_git only),
    # separate from mtime_cache so the per-node blame step below can tell
    # "this file's timestamp came from git" without re-deriving it.
    git_file_date_cache: dict[str, str | None] = {}
    git_relpaths: dict[str, str] = {}
    not_found = 0

    for sf in sorted(unique_files):
        git_date: str | None = None
        if git_root is not None:
            relpath = _safe_relative(git_root, root / sf)
            if relpath is not None:
                git_relpaths[sf] = relpath
                git_date = git_source.resolve_file_date(git_root, relpath, mode="last")
                git_file_date_cache[sf] = git_date

        if since_ts is not None:
            if git_date is not None:
                # git supplies a trustworthy date directly — skip the stat
                # pre-check entirely (a clone's stat mtime is an artifact of
                # checkout time, not a signal to filter on).
                effective_ts = _iso_to_epoch(git_date)
            else:
                # --since path: stat the file ourselves so we can skip old
                # ones before ever calling resolve_mtime.
                fp = root / sf
                try:
                    st = fp.stat()
                except (OSError, FileNotFoundError):
                    not_found += 1
                    mtime_cache[sf] = None
                    continue
                if use_birthtime:
                    bt = getattr(st, "st_birthtime", None)
                    if bt:
                        effective_ts = bt
                    else:
                        # birthtime not in os.stat() — keep effective_ts = None.
                        # Don't filter: resolve_birthtime() may still succeed via
                        # statx(2) during stamping. Filtering conservatively
                        # avoids excluding files whose birthtime we can't see yet.
                        effective_ts = None
                elif use_ctime:
                    effective_ts = st.st_ctime
                else:
                    effective_ts = st.st_mtime
            if effective_ts is not None and effective_ts < since_ts:
                mtime_cache[sf] = None
                continue
        # No --since (or file passed the since check): resolve timestamp.
        if git_date is not None:
            mtime_cache[sf] = git_date
        elif use_birthtime:
            mtime_cache[sf] = resolve_birthtime(sf, root)
        else:
            mtime_cache[sf] = resolve_mtime(sf, root, use_ctime)
        if mtime_cache[sf] is None:
            # resolve_mtime failed — file went missing between the since
            # check and the stat, or --since wasn't used and the file simply
            # doesn't exist on disk.
            not_found += 1

    # ---- stamp nodes in-place ---------------------------------------------
    # nodes_by_file groups nodes by source_file so the edge builder can
    # iterate per-file.  Values are (source_location, node_id) tuples.
    nodes_by_file: dict[str, list[tuple[str, str]]] = defaultdict(list)
    enriched = 0
    # Lazily populated: one `git blame --porcelain` per unique file that
    # actually has a git-resolved date — never per node. A file resolves to
    # None here once and stays None (blame failed/untracked), so subsequent
    # nodes in the same file skip straight to the file-level git date.
    blame_cache: dict[str, dict[int, str] | None] = {}
    author_cache: dict[str, str | None] = {}

    for node in nodes:
        sf = node.get("source_file")
        if sf and sf in mtime_cache:
            mt = mtime_cache[sf]
            if mt:
                node["file_mtime"] = mt
                if include_dir_mtime:
                    node["dir_mtime"] = resolve_dir_mtime(sf, root)
                enriched += 1
            else:
                node["file_mtime"] = None

            if git_root is not None and git_file_date_cache.get(sf):
                relpath = git_relpaths.get(sf)
                if relpath is not None and sf not in blame_cache:
                    blame_cache[sf] = git_source.blame_file(git_root, relpath)
                line = _extract_line(node.get("source_location"))
                blame_map = blame_cache.get(sf)
                line_date = git_source.resolve_line_date(blame_map, line)
                node["git_commit_date"] = line_date or git_file_date_cache[sf]
                if sf not in author_cache:
                    author_cache[sf] = git_source.resolve_file_author(git_root, relpath) if relpath else None
                if author_cache.get(sf):
                    node["git_author"] = author_cache[sf]

            sl = node.get("source_location")
            if sl is None:
                sl = ""
            nodes_by_file[sf].append((sl, node["id"]))
        elif sf:
            node["file_mtime"] = None
        else:
            # source_file is empty or missing entirely
            node["file_mtime"] = None

    # ---- build preceded_by edges ------------------------------------------
    new_edges: list[dict[str, Any]] = []
    intra_count = 0
    cross_count = 0

    # Intra-file: chain nodes within the same file by line-order.
    # L0 nodes (concepts, docs) share line 0 — the secondary sort on node_id
    # gives deterministic ordering instead of arbitrary JSON order.
    for sf, node_list in nodes_by_file.items():
        if len(node_list) < 2:
            continue  # single-node file → nothing to chain
        sorted_nodes = sorted(
            node_list, key=lambda x: (_extract_line(x[0]), x[1])
        )
        for i in range(len(sorted_nodes) - 1):
            new_edges.append({
                "source": sorted_nodes[i][1],
                "target": sorted_nodes[i + 1][1],
                "relation": "preceded_by",
                "confidence": "EXTRACTED",
                "confidence_score": 1.0,
                "source_file": sf,
                "source_location": (
                    str(sorted_nodes[i][0]) if sorted_nodes[i][0] else "L1"
                ),
                "weight": 1.0,
            })
        intra_count += len(sorted_nodes) - 1

    # Cross-file: chain the first node of each file ordered by mtime.
    if cross_file:
        # Only files whose mtime resolved (truthy) can appear in the timeline.
        file_timeline = sorted(
            [(sf, mtime_cache[sf]) for sf in nodes_by_file
             if mtime_cache.get(sf)],
            key=lambda x: x[1],
        )
        for i in range(len(file_timeline) - 1):
            sf_a, _ = file_timeline[i]
            sf_b, _ = file_timeline[i + 1]
            # Sort by line so the "first" node is truly the first by position,
            # consistent with intra-file edge ordering.
            nodes_a = sorted(
                nodes_by_file[sf_a], key=lambda x: (_extract_line(x[0]), x[1])
            ) if nodes_by_file.get(sf_a) else []
            nodes_b = sorted(
                nodes_by_file[sf_b], key=lambda x: (_extract_line(x[0]), x[1])
            ) if nodes_by_file.get(sf_b) else []
            first_a = nodes_a[0][1] if nodes_a else None
            first_b = nodes_b[0][1] if nodes_b else None
            if first_a and first_b and first_a != first_b:
                new_edges.append({
                    "source": first_a,
                    "target": first_b,
                    "relation": "preceded_by",
                    "confidence": "EXTRACTED",
                    "confidence_score": 1.0,
                    "source_file": sf_a,
                    "source_location": "L1",
                    "weight": 1.0,
                })
                cross_count += 1

    # ---- snapshot stats before any mutation --------------------------------
    stats: dict[str, Any] = {
        "files_analyzed": sum(1 for v in mtime_cache.values() if v),
        "nodes_enriched": enriched,
        "nodes_total": len(nodes),
        "files_not_found": not_found,
        "edges_intra_file": intra_count,
        "edges_cross_file": cross_count,
        "edges_total": intra_count + cross_count,
    }

    if dry_run:
        return stats

    # ---- deduplicate against existing links, then write back ---------------
    # An edge is considered duplicate when (source, target, relation) already
    # exists — this is what makes enrichment idempotent.
    existing_ids = {
        (e.get("source"), e.get("target"), e.get("relation", ""))
        for e in links
    }
    deduped = [
        e for e in new_edges
        if (e["source"], e["target"], e["relation"]) not in existing_ids
    ]
    deduped_count = len(new_edges) - len(deduped)
    links.extend(deduped)

    try:
        graph_path.write_text(
            json.dumps(data, ensure_ascii=False), encoding="utf-8"
        )
    except OSError as e:
        raise OSError(f"Failed to write {graph_path}: {e}")
    stats["edges_deduped"] = deduped_count

    return stats


# ---------------------------------------------------------------------------
# output regeneration
# ---------------------------------------------------------------------------


def regenerate_outputs(root: Path, quiet: bool = False) -> dict[str, bool]:
    """Rebuild graphify html and wiki exports via the graphify CLI.

    Called automatically after enrichment unless --no-regenerate is passed.
    Returns a dict like {"html": True, "wiki": False} so the caller can
    print a ✓/✗ status line per output type.
    """
    import subprocess

    results: dict[str, bool] = {}
    for cmd_name, args in [
        ("html", ["graphify", "export", "html"]),
        ("wiki", ["graphify", "export", "wiki"]),
    ]:
        try:
            subprocess.run(
                args,
                cwd=root,
                capture_output=quiet,
                check=True,
                timeout=120,
            )
            results[cmd_name] = True
        except (
            subprocess.CalledProcessError,
            FileNotFoundError,
            subprocess.TimeoutExpired,
        ):
            results[cmd_name] = False
    return results
