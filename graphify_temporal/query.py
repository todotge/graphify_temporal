"""Query a graphify-temporal enriched graph: search, filter by time, timeline.

Zero runtime dependencies — reads graphify-out/graph.json and applies
temporal filters to nodes.  Four entry points:

  query_nodes     — search nodes by label/id, filter by file_mtime or dir_mtime
  build_timeline  — walk preceded_by chains and return ordered steps
  temporal_stats  — summary: coverage, gaps, oldest/newest
  impact          — bounded BFS between two nodes over ALL edge relations
                     (not just preceded_by), ranked by structural + temporal
                     relevance — root-cause tracing for debugging
"""

from __future__ import annotations

import json
from collections import deque
from pathlib import Path
from typing import Any

from .fs import parse_date as _parse_date_ts, iso_to_epoch


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

    Returns None when the field is missing or null. Delegates the actual
    parsing to fs.iso_to_epoch (UTC, matches the gmtime-based timestamps
    written by the enricher).
    """
    val = node.get(key)
    if not val:
        return None
    return iso_to_epoch(val)


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
    files_only: bool = True,
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

    if files_only:
        results = _collapse_by_file(results)

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
    files_only: bool = True,
) -> list[dict[str, Any]]:
    """Walk ``preceded_by`` edges and return an ordered list of steps.

    Each step is a dict: ``{node_id, label, file_mtime, dir_mtime,
    source_file, source_location}``.

    When *files_only* is True (default), consecutive steps from the same
    file are collapsed — only the first node of each file appears in the
    timeline.

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

    nodes_by_id: dict[str, dict[str, Any]] = {n["id"]: n for n in nodes}

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
        return _collapse_by_file(chain) if files_only else chain

    # No start — pick the oldest node that starts a chain
    start_nodes = [
        nid for nid in nodes_by_id
        if nid in out_edges and nid not in in_edges
    ]
    if not start_nodes:
        # Fall back to any node with outgoing edges
        start_nodes = list(out_edges.keys())

    best_id = min(
        start_nodes,
        key=lambda nid: _ts_from_node(nodes_by_id[nid], "file_mtime") or float("inf"),
        default=None,
    )

    if best_id:
        chain = _walk_forward(
            best_id, nodes_by_id, out_edges, max_steps,
            since_ts, before_ts,
        )
        return _collapse_by_file(chain) if files_only else chain
    return []


def _collapse_by_file(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep only the first step per source_file.  Dict insertion order
    preserves the original sort (Python 3.7+)."""
    index: dict[str, dict[str, Any]] = {}
    for s in steps:
        index.setdefault(s.get("source_file") or "", s)
    return list(index.values())


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


# ---------------------------------------------------------------------------
# impact — root-cause tracing via bounded multi-relation BFS
# ---------------------------------------------------------------------------

# Node-visit budget per BFS call — same order of magnitude as build_timeline's
# own max_steps=500 default, not a new number invented from nothing.
_IMPACT_VISIT_BUDGET = 500

# Hub cap: a node with this much combined degree stops being expanded further
# (its neighbors are still recorded as reached, just not fanned out through).
# 500/50 are deliberately fixed, not configurable — no config file was asked
# for. Mirrors core graphify's own god-node exclusion instinct in analyze.py.
# ponytail: fixed constants, well above any observed real-world node degree
# in this repo's own graphs (max 36) — raise only if a real large graph
# proves 50 too low.
_IMPACT_HUB_DEGREE_CAP = 50

_IMPACT_CONFIDENCE_BONUS = {"EXTRACTED": 2, "INFERRED": 1, "AMBIGUOUS": 0}


def _build_adjacency(links: list[dict[str, Any]]) -> dict[str, list[tuple[str, dict[str, Any]]]]:
    """Undirected adjacency: node id -> [(neighbor_id, edge_dict), ...].

    Undirected because BFS reachability for root-cause tracing cares about
    "is there a recorded relationship", not the direction of the edge — a
    bug in alpha caused by beta can be recorded as either `beta calls alpha`
    or `alpha references beta`. Malformed links (missing source/target) are
    skipped silently, never crash the traversal.
    """
    adjacency: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    for e in links:
        src = e.get("source")
        tgt = e.get("target")
        if not src or not tgt:
            continue
        adjacency.setdefault(src, []).append((tgt, e))
        adjacency.setdefault(tgt, []).append((src, e))
    return adjacency


def _impact_bfs(
    start: str,
    adjacency: dict[str, list[tuple[str, dict[str, Any]]]],
    hops: int,
    relations: set[str] | None,
) -> tuple[dict[str, tuple[int, dict[str, Any], int]], bool]:
    """Bounded BFS from *start*. Returns ({node_id: (hop, best_edge, edge_count)}, truncated).

    best_edge is the edge used to first reach that node (for relation_path
    display) — when a node is reached via multiple edges, the first BFS
    discovery (shortest hop, breadth-first order) wins, which is also the
    edge used for ranking. edge_count is how many distinct edges (from any
    already-visited node) lead to this node — a node linked via both `calls`
    and `references` independently is a stronger signal than one reached a
    single way, even though only the first-discovered edge drives its score.
    Counting re-discoveries costs nothing extra (no re-queueing, no path
    storage) — just one counter bump per edge already being iterated.
    Hub nodes (degree > _IMPACT_HUB_DEGREE_CAP) are recorded as reached but
    not expanded further, so one central node can't turn a 3-hop trace into
    "the whole graph".
    """
    reached: dict[str, tuple[int, dict[str, Any], int]] = {}
    visited: set[str] = {start}
    queue: deque[tuple[str, int]] = deque([(start, 0)])
    truncated = False

    while queue:
        node_id, hop = queue.popleft()
        if hop >= hops:
            continue
        neighbors = adjacency.get(node_id, [])
        if len(neighbors) > _IMPACT_HUB_DEGREE_CAP:
            continue  # hub: don't fan out through it further
        for neighbor_id, edge in neighbors:
            if relations is not None and edge.get("relation") not in relations:
                continue
            if neighbor_id in visited:
                if neighbor_id in reached:
                    h, e, count = reached[neighbor_id]
                    reached[neighbor_id] = (h, e, count + 1)
                continue
            if len(visited) >= _IMPACT_VISIT_BUDGET:
                truncated = True
                break
            visited.add(neighbor_id)
            reached[neighbor_id] = (hop + 1, edge, 1)
            queue.append((neighbor_id, hop + 1))
        if truncated:
            break

    return reached, truncated


def _impact_shortest_path(
    start: str,
    goal: str,
    adjacency: dict[str, list[tuple[str, dict[str, Any]]]],
) -> list[dict[str, Any]]:
    """Shortest path start -> goal via plain BFS, target-seeking (stops on hit).

    Returns [] when no path exists or start == goal (trivial, nothing to
    report) within the visit budget. Each step: {node_id, relation, hop}.
    """
    if start == goal:
        return []
    visited: set[str] = {start}
    queue: deque[tuple[str, list[dict[str, Any]]]] = deque([(start, [])])
    while queue:
        node_id, path = queue.popleft()
        if len(visited) >= _IMPACT_VISIT_BUDGET:
            break
        for neighbor_id, edge in adjacency.get(node_id, []):
            if neighbor_id in visited:
                continue
            new_path = path + [{
                "node_id": neighbor_id,
                "relation": edge.get("relation", ""),
                "hop": len(path) + 1,
            }]
            if neighbor_id == goal:
                return new_path
            visited.add(neighbor_id)
            queue.append((neighbor_id, new_path))
    return []


def _impact_score(
    hop: int,
    edge: dict[str, Any],
    node_community: Any,
    anchor_community: Any,
    is_bridge: bool,
) -> float:
    """Ranking formula — mirrors core graphify's analyze.py::_surprise_score
    additive-bonus shape, built from fields this schema actually has.

    score = (3 - hop)                                closer = more relevant
          + confidence_bonus[edge's confidence]       trust structural certainty
          + 2 if relation != "preceded_by"             semantic edge > temporal chain
          + 1 if node_community != anchor_community    cross-community = more surprising
          + 1 if is_bridge                             reached from both anchors
    """
    score = (3 - hop)
    score += _IMPACT_CONFIDENCE_BONUS.get(edge.get("confidence", ""), 0)
    if edge.get("relation") != "preceded_by":
        score += 2
    if node_community is not None and node_community != anchor_community:
        score += 1
    if is_bridge:
        score += 1
    return float(score)


def impact(
    root: Path,
    node_a: str,
    node_b: str | None = None,
    hops: int = 3,
    relations: list[str] | None = None,
    max_candidates: int = 25,
) -> dict[str, Any]:
    """Trace structural + temporal connections between one or two nodes.

    Root-cause tracing: given two areas of code (node_a, node_b), find nodes
    that are reachable from either (or both — "bridge" nodes) within *hops*
    steps over ANY edge relation (calls, references, imports,
    conceptually_related_to, preceded_by, ...), ranked by how relevant they
    look as a connection between the two. With node_b omitted, explores
    what's reachable/at-risk around node_a alone.

    Read-only — never writes to graph.json. Safe to call repeatedly.

    Parameters
    ----------
    root : Path
        Project root containing graphify-out/graph.json.
    node_a : str
        First anchor node id. Raises ValueError if not found in the graph.
    node_b : str | None
        Second anchor node id (optional). Raises ValueError if given but not
        found. When None, single-anchor mode: every node reached from
        node_a is a candidate.
    hops : int
        Max traversal depth from each anchor (default 3).
    relations : list[str] | None
        If given, only follow edges whose `relation` is in this list.
        Default None follows every relation, including preceded_by — this
        is what makes the temporal-only degraded case visible instead of
        silently invisible.
    max_candidates : int
        Cap on returned candidates (default 25), best-scoring kept.

    Returns
    -------
    dict
        Keys: anchor_a, anchor_b, structural_confidence, direct_path,
        candidates (list, score-desc then node_id-asc), truncated,
        isolated_anchors.
    """
    data = _load_graph(root)
    nodes: list[dict[str, Any]] = data.get("nodes", [])
    links: list[dict[str, Any]] = data.get("links", [])
    nodes_by_id: dict[str, dict[str, Any]] = {n["id"]: n for n in nodes}

    if node_a not in nodes_by_id:
        raise ValueError(f"Node '{node_a}' not found in graph")
    if node_b is not None and node_b not in nodes_by_id:
        raise ValueError(f"Node '{node_b}' not found in graph")

    relation_set = set(relations) if relations else None
    adjacency = _build_adjacency(links)
    has_semantic_edges = any(e.get("relation") != "preceded_by" for e in links)

    isolated_anchors = [
        n for n in (node_a, node_b)
        if n is not None and not adjacency.get(n)
    ]

    reached_a, truncated_a = _impact_bfs(node_a, adjacency, hops, relation_set)
    reached_b, truncated_b = ({}, False)
    if node_b is not None:
        reached_b, truncated_b = _impact_bfs(node_b, adjacency, hops, relation_set)

    direct_path: list[dict[str, Any]] = []
    if node_b is not None:
        direct_path = _impact_shortest_path(node_a, node_b, adjacency)

    community_a = nodes_by_id.get(node_a, {}).get("community")

    # Merge reached sets: bridge nodes win the label; otherwise keep the
    # best (lowest-hop) discovery between the two BFS runs for scoring.
    # alternate_paths sums the distinct-edge counts from each BFS that
    # reached this node — a node linked via two independent edges from the
    # SAME anchor (e.g. both `calls` and `references`) counts as 2, same as
    # a node reached once from each anchor (the bridge case); both are
    # genuinely stronger evidence than a single link and should read that way.
    all_ids = set(reached_a) | set(reached_b)
    candidates: list[dict[str, Any]] = []
    for nid in all_ids:
        in_a = nid in reached_a
        in_b = nid in reached_b
        is_bridge = in_a and in_b
        if in_a and (not in_b or reached_a[nid][0] <= reached_b[nid][0]):
            hop, edge, _ = reached_a[nid]
            connection = "bridge" if is_bridge else "neighbor-of-a"
        else:
            hop, edge, _ = reached_b[nid]
            connection = "bridge" if is_bridge else "neighbor-of-b"

        node = nodes_by_id.get(nid, {})
        node_community = node.get("community")
        score = _impact_score(hop, edge, node_community, community_a, is_bridge)
        alternate_paths = (
            (reached_a[nid][2] if in_a else 0) + (reached_b[nid][2] if in_b else 0)
        )

        candidates.append({
            "node_id": nid,
            "label": node.get("label", nid),
            "hop": hop,
            "connection": connection,
            "relation_path": [edge.get("relation", "")],
            "score": score,
            "alternate_paths": alternate_paths,
            "community": node_community,
            "file_mtime": node.get("file_mtime"),
            "dir_mtime": node.get("dir_mtime"),
            "git_commit_date": node.get("git_commit_date"),
            "git_author": node.get("git_author"),
            "source_file": node.get("source_file"),
        })

    candidates.sort(key=lambda c: (-c["score"], c["node_id"]))
    truncated = truncated_a or truncated_b
    if len(candidates) > max_candidates:
        candidates = candidates[:max_candidates]

    return {
        "anchor_a": node_a,
        "anchor_b": node_b,
        "structural_confidence": "structural+temporal" if has_semantic_edges else "temporal-only",
        "direct_path": direct_path,
        "candidates": candidates,
        "truncated": truncated,
        "isolated_anchors": isolated_anchors,
    }
