"""Query a graphify-temporal enriched graph: search, filter by time, timeline.

Zero runtime dependencies — reads graphify-out/graph.json and applies
temporal filters to nodes.  Three entry points:

  query_nodes     — search nodes by label/id, filter by file_mtime or dir_mtime
  build_timeline  — walk preceded_by chains and return ordered steps
  temporal_stats  — summary: coverage, gaps, oldest/newest
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .fs import parse_date as _parse_date_ts


def _load_graph(root: Path) -> dict[str, Any]:
    """Load and validate graphify-out/graph.json under *root*."""
    graph_path = root / "graphify-out" / "graph.json"
    if not graph_path.exists():
        raise FileNotFoundError(
            f"No graph.json found at {graph_path}. "
            "Run `graphify .` first to build the graph."
        )
    try:
        return json.loads(graph_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON in {graph_path}: {e}")


def _ts_from_node(node: dict, key: str) -> float | None:
    """Extract a timestamp float from a node's ISO 8601 string attribute.

    Returns None when the field is missing or null.  Uses
    ``calendar.timegm`` (UTC) to match the gmtime-based timestamps
    written by the enricher.
    """
    import calendar, time
    val = node.get(key)
    if not val:
        return None
    try:
        return float(calendar.timegm(time.strptime(val, "%Y-%m-%dT%H:%M:%SZ")))
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# query_nodes
# ---------------------------------------------------------------------------


def query_nodes(
    root: Path,
    search: str | None = None,
    since: str | None = None,
    before: str | None = None,
    use_dir_mtime: bool = False,
    order: str = "none",
) -> list[dict[str, Any]]:
    """Return nodes matching *search* (case-insensitive label or id),
    filtered by time range, and optionally sorted.

    Parameters
    ----------
    root : Path
        Project root containing graphify-out/graph.json.
    search : str | None
        Substring to match against node label and id.
    since : str | None
        ISO date (YYYY-MM-DD).  Nodes with timestamp < since are excluded.
    before : str | None
        ISO date (YYYY-MM-DD).  Nodes with timestamp > before are excluded.
    use_dir_mtime : bool
        Filter/sort by dir_mtime instead of file_mtime.
    order : str
        ``"newest-first"``, ``"oldest-first"``, or ``"none"``.

    Returns
    -------
    list[dict]
        Matching nodes (subset of the original node dicts).
    """
    data = _load_graph(root)
    nodes: list[dict[str, Any]] = data.get("nodes", [])
    time_key = "dir_mtime" if use_dir_mtime else "file_mtime"
    since_ts = _parse_date_ts(since) if since else None
    before_ts = _parse_date_ts(before) if before else None

    results: list[dict[str, Any]] = []
    for node in nodes:
        if search:
            label = str(node.get("label", "")).lower()
            nid = str(node.get("id", "")).lower()
            s = search.lower()
            if s not in label and s not in nid:
                continue

        ts = _ts_from_node(node, time_key)
        if ts is None:
            if since_ts is not None or before_ts is not None:
                continue  # time filter requested but node has no timestamp
        else:
            if since_ts is not None and ts < since_ts:
                continue
            if before_ts is not None and ts > before_ts:
                continue

        results.append(node)

    if order == "newest-first":
        results.sort(
            key=lambda n: _ts_from_node(n, time_key) or 0.0,
            reverse=True,
        )
    elif order == "oldest-first":
        results.sort(
            key=lambda n: _ts_from_node(n, time_key) or float("inf"),
        )

    return results


# ---------------------------------------------------------------------------
# build_timeline
# ---------------------------------------------------------------------------


def build_timeline(
    root: Path,
    start_id: str | None = None,
    since: str | None = None,
    before: str | None = None,
    max_steps: int = 500,
) -> list[dict[str, Any]]:
    """Walk ``preceded_by`` edges and return an ordered list of steps.

    Each step is a dict: ``{node_id, label, file_mtime, dir_mtime,
    source_file, source_location}``.

    When *start_id* is given, the walk begins at that node and follows edges
    forward (target direction).  Otherwise it picks the oldest node in the
    graph and walks forward from there.

    Parameters
    ----------
    root : Path
        Project root.
    start_id : str | None
        Begin the timeline at this node id.
    since / before : str | None
        Date filters applied to node timestamps.
    max_steps : int
        Safety limit.
    """
    data = _load_graph(root)
    nodes: list[dict[str, Any]] = data.get("nodes", [])
    links: list[dict[str, Any]] = data.get("links", [])

    # Index nodes by id
    nodes_by_id: dict[str, dict[str, Any]] = {}
    for n in nodes:
        nodes_by_id[n["id"]] = n

    since_ts = _parse_date_ts(since) if since else None
    before_ts = _parse_date_ts(before) if before else None

    # Choose starting nodes: all nodes with at least one outgoing preceded_by
    # edge, sorted oldest-first by file_mtime
    out_edges: dict[str, list[dict]] = {}
    in_edges: dict[str, list[dict]] = {}
    for e in links:
        if e.get("relation") != "preceded_by":
            continue
        src = e["source"]
        tgt = e["target"]
        out_edges.setdefault(src, []).append(e)
        in_edges.setdefault(tgt, []).append(e)

    if start_id:
        if start_id not in nodes_by_id:
            return []
        chain = _walk_forward(
            start_id, nodes_by_id, out_edges, max_steps,
            since_ts, before_ts,
        )
        return chain

    # No start — pick the oldest node that starts a chain
    start_nodes = [
        nid for nid in nodes_by_id
        if nid in out_edges and nid not in in_edges
    ]
    if not start_nodes:
        # Fall back to any node with outgoing edges
        start_nodes = list(out_edges.keys())

    # Pick oldest by file_mtime
    best_id: str | None = None
    best_ts: float = float("inf")
    for nid in start_nodes:
        ts = _ts_from_node(nodes_by_id[nid], "file_mtime")
        if ts is None:
            ts = float("inf")
        if ts < best_ts:
            best_ts = ts
            best_id = nid

    if best_id:
        return _walk_forward(
            best_id, nodes_by_id, out_edges, max_steps,
            since_ts, before_ts,
        )
    return []


def _walk_forward(
    start_id: str,
    nodes_by_id: dict[str, dict[str, Any]],
    out_edges: dict[str, list[dict]],
    max_steps: int,
    since_ts: float | None,
    before_ts: float | None,
) -> list[dict[str, Any]]:
    """Walk forward along preceded_by edges, building a timeline."""
    chain: list[dict[str, Any]] = []
    seen: set[str] = set()
    current = start_id
    for _ in range(max_steps):
        if current in seen:
            break
        seen.add(current)
        node = nodes_by_id.get(current)
        if node is None:
            break

        ts = _ts_from_node(node, "file_mtime")
        if since_ts is not None and (ts is None or ts < since_ts):
            # Skip this node but keep walking forward
            edges = out_edges.get(current, [])
            if not edges:
                break
            current = edges[0]["target"]
            continue
        if before_ts is not None and (ts is None or ts > before_ts):
            break

        chain.append({
            "node_id": node["id"],
            "label": node.get("label", node["id"]),
            "file_mtime": node.get("file_mtime"),
            "dir_mtime": node.get("dir_mtime"),
            "source_file": node.get("source_file"),
            "source_location": node.get("source_location"),
        })

        # Follow the first outgoing preceded_by edge
        edges = out_edges.get(current, [])
        if not edges:
            break
        next_id = edges[0]["target"]
        if next_id == current:
            break
        current = next_id

    return chain


# ---------------------------------------------------------------------------
# temporal_stats
# ---------------------------------------------------------------------------


def temporal_stats(root: Path) -> dict[str, Any]:
    """Return temporal summary of the graph.

    Keys: total_nodes, nodes_with_mtime, nodes_with_dir_mtime,
    oldest_mtime, newest_mtime, time_span_days, median_gap_seconds,
    longest_gap, avg_gap_seconds, files_with_mtime.
    """
    data = _load_graph(root)
    nodes: list[dict[str, Any]] = data.get("nodes", [])
    links: list[dict[str, Any]] = data.get("links", [])

    total = len(nodes)
    nodes_with_mtime = 0
    nodes_with_dir_mtime = 0
    mtimes: list[float] = []
    dir_mtimes: list[float] = []
    files: set[str] = set()

    for n in nodes:
        ts = _ts_from_node(n, "file_mtime")
        if ts is not None:
            nodes_with_mtime += 1
            mtimes.append(ts)
            sf = n.get("source_file")
            if sf:
                files.add(sf)
        dts = _ts_from_node(n, "dir_mtime")
        if dts is not None:
            nodes_with_dir_mtime += 1
            dir_mtimes.append(dts)

    # Edge gaps: time difference between consecutive preceded_by edges
    gaps: list[float] = []
    longest_gap_secs = 0.0
    longest_gap_pair: tuple[str, str] = ("", "")
    _nidx = {n["id"]: n for n in nodes}
    for e in links:
        if e.get("relation") != "preceded_by":
            continue
        src = e["source"]
        tgt = e["target"]
        ts_src = _ts_from_node(_nidx.get(src, {}), "file_mtime")
        ts_tgt = _ts_from_node(_nidx.get(tgt, {}), "file_mtime")
        if ts_src is not None and ts_tgt is not None and ts_src != ts_tgt:
            gap = abs(ts_tgt - ts_src)
            gaps.append(gap)
            if gap > longest_gap_secs:
                longest_gap_secs = gap
                longest_gap_pair = (src, tgt)

    sorted_gaps = sorted(gaps)
    n = len(sorted_gaps)
    if n == 0:
        median_gap = 0.0
    elif n % 2 == 1:
        median_gap = sorted_gaps[n // 2]
    else:
        median_gap = (sorted_gaps[n // 2 - 1] + sorted_gaps[n // 2]) / 2.0
    avg_gap = sum(gaps) / len(gaps) if gaps else 0.0

    sorted_mtimes = sorted(mtimes)
    oldest_mtime = (
        sorted_mtimes[0] if sorted_mtimes else None
    )
    newest_mtime = (
        sorted_mtimes[-1] if sorted_mtimes else None
    )

    import datetime as _dt
    oldest_d = (
        _dt.datetime.fromtimestamp(oldest_mtime, tz=_dt.timezone.utc).date()
        if oldest_mtime else None
    )
    newest_d = (
        _dt.datetime.fromtimestamp(newest_mtime, tz=_dt.timezone.utc).date()
        if newest_mtime else None
    )
    span_days: float = (
        (newest_d - oldest_d).days
        if oldest_d and newest_d
        else 0.0
    )

    return {
        "total_nodes": total,
        "nodes_with_mtime": nodes_with_mtime,
        "nodes_with_dir_mtime": nodes_with_dir_mtime,
        "files_with_mtime": len(files),
        "oldest_mtime": oldest_mtime,
        "newest_mtime": newest_mtime,
        "time_span_days": span_days,
        "median_gap_seconds": median_gap,
        "avg_gap_seconds": avg_gap,
        "longest_gap_seconds": longest_gap_secs,
        "longest_gap_pair": list(longest_gap_pair),
    }
