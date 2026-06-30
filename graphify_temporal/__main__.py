"""CLI for graphify-temporal — enrich, install, query, timeline, stats.

Parsing lives here so every module stays importable and testable without
argparse noise.  Core functions (enrich, query_nodes, ...) have no knowledge
of the CLI — all argument conversion happens before the call.
"""

import argparse
import sys
from pathlib import Path

from . import __version__
from .enricher import enrich, regenerate_outputs
from .install import detect, install as _install, uninstall as _uninstall
from .query import query_nodes, build_timeline, temporal_stats


def _print_install_results(results: dict[str, bool]) -> None:
    """Print a one-line-per-client install summary."""
    for cid, ok in sorted(results.items()):
        status = "\u2713" if ok else "\u2717"
        print(f"  {cid:12s} {status}")


def _print_uninstall_results(results: dict[str, bool]) -> None:
    """Print a one-line-per-client uninstall summary."""
    for cid, ok in sorted(results.items()):
        status = "\u2713" if ok else "\u2717"
        print(f"  {cid:12s} {status}")


def _print_as_table(rows: list[dict], columns: list[str]) -> None:
    """Print a list of dicts as aligned columns."""
    if not rows:
        print("  (no results)")
        return
    widths = {c: len(c) for c in columns}
    for r in rows:
        for c in columns:
            val = str(r.get(c, ""))
            if c.endswith("_mtime") and val:
                val = val[:19]
            widths[c] = max(widths[c], len(val))
    header = "  " + "  ".join(c.ljust(widths[c]) for c in columns)
    print(header)
    sep = "  " + "  ".join("-" * widths[c] for c in columns)
    print(sep)
    for r in rows:
        vals = []
        for c in columns:
            val = str(r.get(c, ""))
            if c.endswith("_mtime") and val:
                val = val[:19]
            vals.append(val.ljust(widths[c]))
        print("  " + "  ".join(vals))


def _stats_json(s: dict) -> dict:
    """Convert stats dict to JSON-safe types (timestamp → ISO string)."""
    import time
    d = dict(s)
    for key in ("oldest_mtime", "newest_mtime"):
        if d.get(key):
            d[key] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(d[key]))
    d["time_span_days"] = round(d["time_span_days"], 1)
    d["median_gap_seconds"] = round(d["median_gap_seconds"], 1)
    d["avg_gap_seconds"] = round(d["avg_gap_seconds"], 1)
    d["longest_gap_seconds"] = round(d["longest_gap_seconds"], 1)
    return d


def _print_stats(s: dict) -> None:
    """Print human-readable temporal stats."""
    import time
    print(f"graphify-temporal v{__version__}  — temporal stats\n")
    total = s["total_nodes"]
    with_mtime = s["nodes_with_mtime"]
    with_dir = s["nodes_with_dir_mtime"]
    print(f"  Nodes total:           {total:,}")
    print(f"  With file_mtime:       {with_mtime:,} ({_pct(with_mtime, total):.0f}%)")
    print(f"  With dir_mtime:        {with_dir:,} ({_pct(with_dir, total):.0f}%)")
    print(f"  Files with mtime:      {s['files_with_mtime']:,}")

    if s["oldest_mtime"] and s["newest_mtime"]:
        oldest = time.strftime("%Y-%m-%d", time.gmtime(s["oldest_mtime"]))
        newest = time.strftime("%Y-%m-%d", time.gmtime(s["newest_mtime"]))
        print(f"  Time span:             {oldest} → {newest} ({s['time_span_days']:.1f} days)")

    if s["median_gap_seconds"] > 0:
        print(f"  Median gap (edges):    {_fmt_duration(s['median_gap_seconds'])}")
        print(f"  Longest gap:           {_fmt_duration(s['longest_gap_seconds'])}  ({s['longest_gap_pair'][0]} → {s['longest_gap_pair'][1]})")


def _pct(part: int, total: int) -> float:
    return (part / total * 100) if total else 0.0


def _fmt_duration(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.1f}s"
    elif seconds < 3600:
        return f"{seconds / 60:.1f}m"
    elif seconds < 86400:
        return f"{seconds / 3600:.1f}h"
    else:
        return f"{seconds / 86400:.1f}d"


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="graphify-temporal",
        description=(
            "Enrich a graphify knowledge graph with temporal metadata "
            "from filesystem timestamps."
        ),
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"graphify-temporal {__version__}",
    )

    sub = parser.add_subparsers(dest="command")
    enrich_parser = sub.add_parser(
        "enrich", help="Enrich graph.json with temporal metadata"
    )

    # Positional: project root directory.
    enrich_parser.add_argument(
        "path",
        nargs="?",
        default=".",
        help="Project root directory (default: .)",
    )
    # Timestamp source toggles — mutually exclusive.
    enrich_parser.add_argument(
        "--use-ctime",
        action="store_true",
        help="Use st_ctime instead of st_mtime (Unix: metadata-change time)",
    )
    enrich_parser.add_argument(
        "--use-birthtime",
        action="store_true",
        help="Use st_birthtime instead of st_mtime (true creation time)",
    )
    # Cross-file chaining.
    enrich_parser.add_argument(
        "--cross-file",
        action="store_true",
        help="Create preceded_by edges across different files (by mtime order)",
    )
    # Preview mode.
    enrich_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show statistics without modifying graph.json",
    )
    # Date-based filter.
    enrich_parser.add_argument(
        "--since",
        type=str,
        default=None,
        metavar="DATE",
        help="Only process files with mtime >= DATE (YYYY-MM-DD)",
    )
    # Glob include (repeatable).
    enrich_parser.add_argument(
        "--include",
        action="append",
        default=None,
        metavar="GLOB",
        help="Include only source_file paths matching glob (repeatable)",
    )
    # Glob exclude (repeatable).
    enrich_parser.add_argument(
        "--exclude",
        action="append",
        default=None,
        metavar="GLOB",
        help="Exclude source_file paths matching glob (repeatable)",
    )
    # Skip output regeneration.
    enrich_parser.add_argument(
        "--no-regenerate",
        action="store_true",
        help="Skip regenerating HTML/wiki after enrichment",
    )
    # Also stamp directory mtime (arrival proxy).
    enrich_parser.add_argument(
        "--include-dir-mtime",
        action="store_true",
        help="Also add dir_mtime (parent directory mtime) to nodes",
    )
    # Quiet mode.
    enrich_parser.add_argument(
        "--quiet", "-q",
        action="store_true",
        help="Reduce output verbosity",
    )

    # ---- install subcommand ------------------------------------------------
    install_parser = sub.add_parser(
        "install",
        help="Inject graphify-temporal instructions into AI client config files",
    )
    install_parser.add_argument(
        "--platform",
        choices=["claude", "codex", "opencode", "gemini", "cursor", "codebuddy", "copilot", "windsurf", "aider", "kilo", "trae"],
        default=None,
        help="Force a specific client (default: auto-detect all)",
    )

    # ---- uninstall subcommand ----------------------------------------------
    uninstall_parser = sub.add_parser(
        "uninstall",
        help="Remove graphify-temporal instructions from AI client config files",
    )
    uninstall_parser.add_argument(
        "--platform",
        choices=["claude", "codex", "opencode", "gemini", "cursor", "codebuddy", "copilot", "windsurf", "aider", "kilo", "trae"],
        default=None,
        help="Force a specific client (default: auto-detect all)",
    )

    # ---- query subcommand ---------------------------------------------------
    query_parser = sub.add_parser(
        "query",
        help="Search and filter enriched nodes by time",
    )
    query_parser.add_argument(
        "search",
        nargs="?",
        default=None,
        help="Substring to match against node id and label",
    )
    query_parser.add_argument(
        "--since",
        type=str,
        default=None,
        metavar="DATE",
        help="Only show nodes with timestamp >= DATE (YYYY-MM-DD)",
    )
    query_parser.add_argument(
        "--before",
        type=str,
        default=None,
        metavar="DATE",
        help="Only show nodes with timestamp <= DATE (YYYY-MM-DD)",
    )
    query_parser.add_argument(
        "--use-dir-mtime",
        action="store_true",
        help="Filter/sort by dir_mtime instead of file_mtime",
    )
    query_parser.add_argument(
        "--order",
        choices=["newest-first", "oldest-first", "none"],
        default="none",
        help="Sort results chronologically",
    )
    query_parser.add_argument(
        "--full",
        action="store_true",
        help="Show all nodes (default: one per source_file)",
    )

    # ---- timeline subcommand ------------------------------------------------
    timeline_parser = sub.add_parser(
        "timeline",
        help="Walk preceded_by chains and show ordered steps with timestamps",
    )
    timeline_parser.add_argument(
        "start_id",
        nargs="?",
        default=None,
        help="Start the timeline at this node id (default: oldest chain-start)",
    )
    timeline_parser.add_argument(
        "--since",
        type=str,
        default=None,
        metavar="DATE",
        help="Only show steps with timestamp >= DATE",
    )
    timeline_parser.add_argument(
        "--before",
        type=str,
        default=None,
        metavar="DATE",
        help="Only show steps with timestamp <= DATE",
    )
    timeline_parser.add_argument(
        "--full",
        action="store_true",
        help="Show all nodes (default: one per file)",
    )

    # ---- stats subcommand ---------------------------------------------------
    stats_parser = sub.add_parser(
        "stats",
        help="Show temporal coverage statistics of the enriched graph",
    )
    stats_parser.add_argument(
        "--json",
        action="store_true",
        help="Output as JSON instead of human-readable text",
    )

    args = parser.parse_args()

    if args.command == "install":
        root = Path(".").resolve()
        try:
            if args.platform:
                results = _install(root, clients=[args.platform])
            else:
                detected = detect(root)
                if not detected:
                    # No client markers — default to AGENTS.md (most common)
                    results = _install(root, clients=["opencode"])
                else:
                    results = _install(root, clients=detected)
        except OSError as e:
            print(f"error: {e}", file=sys.stderr)
            sys.exit(1)
        _print_install_results(results)
        return

    if args.command == "uninstall":
        root = Path(".").resolve()
        try:
            if args.platform:
                results = _uninstall(root, clients=[args.platform])
            else:
                detected = detect(root)
                if not detected:
                    # Nothing to remove — clean exit
                    return
                results = _uninstall(root, clients=detected)
        except OSError as e:
            print(f"error: {e}", file=sys.stderr)
            sys.exit(1)
        _print_uninstall_results(results)
        return

    if args.command == "query":
        root = Path(".").resolve()
        try:
            results = query_nodes(
                root,
                search=args.search,
                since=args.since,
                before=args.before,
                use_dir_mtime=args.use_dir_mtime,
                order=args.order,
                files_only=not args.full,
            )
        except (FileNotFoundError, ValueError, OSError) as e:
            print(f"error: {e}", file=sys.stderr)
            sys.exit(1)
        if not args.search and not args.since and not args.before:
            print(f"graphify-temporal v{__version__}")
        columns = ["node_id", "label", "file_mtime", "dir_mtime", "source_file"]
        _print_as_table(
            [
                {
                    "node_id": n["id"],
                    "label": n.get("label", ""),
                    "file_mtime": n.get("file_mtime", ""),
                    "dir_mtime": n.get("dir_mtime", ""),
                    "source_file": n.get("source_file", ""),
                }
                for n in results
            ],
            columns,
        )
        return

    if args.command == "timeline":
        root = Path(".").resolve()
        try:
            steps = build_timeline(
                root,
                start_id=args.start_id,
                since=args.since,
                before=args.before,
                files_only=not args.full,
            )
        except (FileNotFoundError, ValueError, OSError) as e:
            print(f"error: {e}", file=sys.stderr)
            sys.exit(1)
        if not steps:
            print("  (no timeline found)")
        else:
            for i, s in enumerate(steps, 1):
                mt = s.get("file_mtime", "") or ""
                label = s.get("label", "")
                print(f"  #{i:<3} {s['node_id']:<45s} {mt[:19]}  {label}")
                if i < len(steps):
                    print(f"       {'↓ preceded_by':>60s}")
        return

    if args.command == "stats":
        root = Path(".").resolve()
        try:
            s = temporal_stats(root)
        except (FileNotFoundError, ValueError, OSError) as e:
            print(f"error: {e}", file=sys.stderr)
            sys.exit(1)
        if args.json:
            import json as _json
            print(_json.dumps(_stats_json(s), indent=2, ensure_ascii=False))
        else:
            _print_stats(s)
        return

    # argparse will set command to the subparser name when matched, or None
    # when no subparser matched at all.
    if args.command != "enrich":
        parser.print_help()
        sys.exit(1)

    # --use-ctime and --use-birthtime are mutually exclusive.
    if args.use_ctime and args.use_birthtime:
        print(
            "error: --use-ctime and --use-birthtime are mutually exclusive. "
            "Choose one timestamp source.",
            file=sys.stderr,
        )
        sys.exit(1)

    root = Path(args.path).resolve()

    # Validate --since early so we fail fast with a clear message instead of
    # raising deep inside enrich() where the user can't tell if the problem
    # was a bad date or a missing graph.
    if args.since:
        from .fs import parse_date
        try:
            parse_date(args.since)
        except ValueError:
            print(
                f"error: invalid --since date '{args.since}'. "
                "Expected YYYY-MM-DD.",
                file=sys.stderr,
            )
            sys.exit(1)

    try:
        stats = enrich(
            root=root,
            use_ctime=args.use_ctime,
            use_birthtime=args.use_birthtime,
            cross_file=args.cross_file,
            dry_run=args.dry_run,
            since=args.since,
            include=args.include,
            exclude=args.exclude,
            include_dir_mtime=args.include_dir_mtime,
            quiet=args.quiet,
        )
    except (FileNotFoundError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)

    # ---- print summary ----------------------------------------------------
    if not args.quiet:
        print(f"graphify-temporal v{__version__}")
        if args.dry_run:
            print("  (dry run — no changes written)")

        pct = (
            (stats["nodes_enriched"] / stats["nodes_total"] * 100)
            if stats["nodes_total"]
            else 0
        )
        print(f"  Files analyzed:     {stats['files_analyzed']:,}")
        print(f"  Nodes enriched:     {stats['nodes_enriched']:,} ({pct:.0f}%)")

        if stats.get("files_not_found"):
            print(f"  Files not found:    {stats['files_not_found']}")

        cross = stats["edges_cross_file"]
        intra = stats["edges_intra_file"]
        print(
            f"  Edges added:        {stats['edges_total']:,} "
            f"(intra-file: {intra:,}, cross-file: {cross:,})"
        )

        if stats.get("edges_deduped"):
            print(f"  Edges deduplicated: {stats['edges_deduped']:,}")

    # No further work for dry-run.
    if args.dry_run:
        sys.exit(0)

    # ---- regenerate outputs -----------------------------------------------
    if not args.no_regenerate:
        if not args.quiet:
            print("  Regenerating outputs...")
        results = regenerate_outputs(root, quiet=args.quiet)
        if not args.quiet:
            for name, ok in results.items():
                status = "\u2713" if ok else "\u2717"
                print(f"  {name}:               {status}")


if __name__ == "__main__":
    main()
