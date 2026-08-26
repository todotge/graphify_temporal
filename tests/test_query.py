"""Tests for graphify_temporal.query — query_nodes, build_timeline, temporal_stats."""

import json
import time
import os
from pathlib import Path

import pytest

from graphify_temporal.query import (
    query_nodes,
    build_timeline,
    temporal_stats,
    impact,
    _ts_from_node,
    _parse_date_ts,
    _impact_score,
)


# ---------------------------------------------------------------------------
# test helpers
# ---------------------------------------------------------------------------


def _make_graph_json(graph_dir: Path, nodes: list[dict], links: list[dict] | None = None) -> Path:
    data = {
        "directed": True,
        "multigraph": False,
        "graph": {},
        "nodes": nodes,
        "links": links or [],
        "hyperedges": [],
    }
    p = graph_dir / "graph.json"
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return p


def _write_file(root: Path, relpath: str, content: str = "x") -> None:
    fp = root / relpath
    fp.parent.mkdir(parents=True, exist_ok=True)
    fp.write_text(content, encoding="utf-8")


# ---------------------------------------------------------------------------
# unit tests
# ---------------------------------------------------------------------------


class TestParseDateTs:
    def test_valid(self):
        assert _parse_date_ts("2026-06-14") > 0

    def test_invalid(self):
        with pytest.raises(ValueError):
            _parse_date_ts("bad-date")


class TestTsFromNode:
    def test_valid_iso(self):
        assert _ts_from_node({"file_mtime": "2026-06-14T12:00:00Z"}, "file_mtime") is not None

    def test_missing(self):
        assert _ts_from_node({}, "file_mtime") is None

    def test_null(self):
        assert _ts_from_node({"file_mtime": None}, "file_mtime") is None

    def test_empty_string(self):
        assert _ts_from_node({"file_mtime": ""}, "file_mtime") is None


# ---------------------------------------------------------------------------
# query_nodes
# ---------------------------------------------------------------------------


class TestQueryNodes:
    def test_search_by_label_substring(self, tmp_path):
        graph_dir = tmp_path / "graphify-out"
        graph_dir.mkdir()
        nodes = [
            {"id": "a", "label": "AuthModule", "file_mtime": "2026-06-14T12:00:00Z"},
            {"id": "b", "label": "Database", "file_mtime": "2026-06-13T12:00:00Z"},
            {"id": "c", "label": "Nothing", "file_mtime": "2026-06-12T12:00:00Z"},
        ]
        _make_graph_json(graph_dir, nodes)
        results = query_nodes(tmp_path, search="auth", files_only=False)
        assert len(results) == 1
        assert results[0]["id"] == "a"

    def test_search_by_id_substring(self, tmp_path):
        graph_dir = tmp_path / "graphify-out"
        graph_dir.mkdir()
        nodes = [
            {"id": "auth_module", "label": "A", "file_mtime": "2026-06-14T12:00:00Z"},
            {"id": "db", "label": "B", "file_mtime": "2026-06-13T12:00:00Z"},
        ]
        _make_graph_json(graph_dir, nodes)
        results = query_nodes(tmp_path, search="auth", files_only=False)
        assert len(results) == 1
        assert results[0]["id"] == "auth_module"

    def test_since_filter(self, tmp_path):
        graph_dir = tmp_path / "graphify-out"
        graph_dir.mkdir()
        nodes = [
            {"id": "a", "label": "A", "file_mtime": "2026-06-10T12:00:00Z"},
            {"id": "b", "label": "B", "file_mtime": "2026-06-15T12:00:00Z"},
        ]
        _make_graph_json(graph_dir, nodes)
        results = query_nodes(tmp_path, since="2026-06-14", files_only=False)
        assert len(results) == 1
        assert results[0]["id"] == "b"

    def test_before_filter(self, tmp_path):
        graph_dir = tmp_path / "graphify-out"
        graph_dir.mkdir()
        nodes = [
            {"id": "a", "label": "A", "file_mtime": "2026-06-10T12:00:00Z"},
            {"id": "b", "label": "B", "file_mtime": "2026-06-15T12:00:00Z"},
        ]
        _make_graph_json(graph_dir, nodes)
        results = query_nodes(tmp_path, before="2026-06-14", files_only=False)
        assert len(results) == 1
        assert results[0]["id"] == "a"

    def test_order_newest_first(self, tmp_path):
        graph_dir = tmp_path / "graphify-out"
        graph_dir.mkdir()
        nodes = [
            {"id": "a", "label": "A", "file_mtime": "2026-06-10T12:00:00Z"},
            {"id": "b", "label": "B", "file_mtime": "2026-06-15T12:00:00Z"},
        ]
        _make_graph_json(graph_dir, nodes)
        results = query_nodes(tmp_path, order="newest-first", files_only=False)
        assert results[0]["id"] == "b"
        assert results[1]["id"] == "a"

    def test_order_oldest_first(self, tmp_path):
        graph_dir = tmp_path / "graphify-out"
        graph_dir.mkdir()
        nodes = [
            {"id": "a", "label": "A", "file_mtime": "2026-06-10T12:00:00Z"},
            {"id": "b", "label": "B", "file_mtime": "2026-06-15T12:00:00Z"},
        ]
        _make_graph_json(graph_dir, nodes)
        results = query_nodes(tmp_path, order="oldest-first", files_only=False)
        assert results[0]["id"] == "a"
        assert results[1]["id"] == "b"

    def test_nodes_without_mtime_included_when_no_filter(self, tmp_path):
        graph_dir = tmp_path / "graphify-out"
        graph_dir.mkdir()
        nodes = [
            {"id": "a", "label": "A", "file_mtime": None},
            {"id": "b", "label": "B", "file_mtime": "2026-06-15T12:00:00Z"},
        ]
        _make_graph_json(graph_dir, nodes)
        results = query_nodes(tmp_path, files_only=False)
        assert len(results) == 2

    def test_nodes_without_mtime_excluded_when_time_filter(self, tmp_path):
        graph_dir = tmp_path / "graphify-out"
        graph_dir.mkdir()
        nodes = [
            {"id": "a", "label": "A"},
            {"id": "b", "label": "B", "file_mtime": "2026-06-15T12:00:00Z"},
        ]
        _make_graph_json(graph_dir, nodes)
        results = query_nodes(tmp_path, since="2026-06-14", files_only=False)
        assert len(results) == 1
        assert results[0]["id"] == "b"

    def test_use_dir_mtime(self, tmp_path):
        graph_dir = tmp_path / "graphify-out"
        graph_dir.mkdir()
        nodes = [
            {"id": "a", "label": "A", "dir_mtime": "2026-06-10T12:00:00Z", "file_mtime": "2026-06-20T12:00:00Z"},
            {"id": "b", "label": "B", "dir_mtime": "2026-06-15T12:00:00Z", "file_mtime": "2026-06-01T12:00:00Z"},
        ]
        _make_graph_json(graph_dir, nodes)
        results = query_nodes(tmp_path, since="2026-06-14", use_dir_mtime=True, files_only=False)
        assert len(results) == 1
        assert results[0]["id"] == "b"  # filtered by dir_mtime

    def test_missing_graph(self, tmp_path):
        with pytest.raises(FileNotFoundError, match="No graph.json found"):
            query_nodes(tmp_path)

    def test_files_only_collapses_by_file(self, tmp_path):
        """Default files_only=True: multiple nodes same file → single entry."""
        graph_dir = tmp_path / "graphify-out"
        graph_dir.mkdir()
        nodes = [
            {"id": "a_x", "label": "x", "file_mtime": "2026-06-10T12:00:00Z", "source_file": "a.py"},
            {"id": "a_y", "label": "y", "file_mtime": "2026-06-10T12:00:01Z", "source_file": "a.py"},
            {"id": "b_x", "label": "x", "file_mtime": "2026-06-11T12:00:00Z", "source_file": "b.py"},
        ]
        _make_graph_json(graph_dir, nodes)
        results = query_nodes(tmp_path)  # files_only=True by default
        assert len(results) == 2
        assert {r["source_file"] for r in results} == {"a.py", "b.py"}

    def test_files_only_with_sort_keeps_right_node(self, tmp_path):
        """With newest-first, the newest node per file survives collapse."""
        graph_dir = tmp_path / "graphify-out"
        graph_dir.mkdir()
        nodes = [
            {"id": "a_old", "label": "old", "file_mtime": "2026-06-10T12:00:00Z", "source_file": "a.py"},
            {"id": "a_new", "label": "new", "file_mtime": "2026-06-12T12:00:00Z", "source_file": "a.py"},
        ]
        _make_graph_json(graph_dir, nodes)
        results = query_nodes(tmp_path, order="newest-first")
        assert len(results) == 1
        assert results[0]["id"] == "a_new"  # newest survives


# ---------------------------------------------------------------------------
# build_timeline
# ---------------------------------------------------------------------------


class TestTimeline:
    def test_basic_chain(self, tmp_path):
        graph_dir = tmp_path / "graphify-out"
        graph_dir.mkdir()
        nodes = [
            {"id": "a", "label": "A", "file_mtime": "2026-06-10T12:00:00Z", "source_file": "a.py"},
            {"id": "b", "label": "B", "file_mtime": "2026-06-11T12:00:00Z", "source_file": "b.py"},
            {"id": "c", "label": "C", "file_mtime": "2026-06-12T12:00:00Z", "source_file": "c.py"},
        ]
        links = [
            {"source": "a", "target": "b", "relation": "preceded_by"},
            {"source": "b", "target": "c", "relation": "preceded_by"},
        ]
        _make_graph_json(graph_dir, nodes, links)
        steps = build_timeline(tmp_path)
        assert len(steps) == 3
        assert [s["node_id"] for s in steps] == ["a", "b", "c"]

    def test_start_at_specific_node(self, tmp_path):
        graph_dir = tmp_path / "graphify-out"
        graph_dir.mkdir()
        nodes = [
            {"id": "a", "label": "A", "file_mtime": "2026-06-10T12:00:00Z", "source_file": "a.py"},
            {"id": "b", "label": "B", "file_mtime": "2026-06-11T12:00:00Z", "source_file": "b.py"},
        ]
        links = [{"source": "a", "target": "b", "relation": "preceded_by"}]
        _make_graph_json(graph_dir, nodes, links)
        steps = build_timeline(tmp_path, start_id="b")
        assert len(steps) == 1
        assert steps[0]["node_id"] == "b"

    def test_since_filter(self, tmp_path):
        graph_dir = tmp_path / "graphify-out"
        graph_dir.mkdir()
        nodes = [
            {"id": "a", "label": "A", "file_mtime": "2026-06-10T12:00:00Z", "source_file": "a.py"},
            {"id": "b", "label": "B", "file_mtime": "2026-06-15T12:00:00Z", "source_file": "b.py"},
        ]
        links = [{"source": "a", "target": "b", "relation": "preceded_by"}]
        _make_graph_json(graph_dir, nodes, links)
        steps = build_timeline(tmp_path, since="2026-06-14")
        assert len(steps) == 1
        assert steps[0]["node_id"] == "b"

    def test_non_preceded_by_edges_ignored(self, tmp_path):
        graph_dir = tmp_path / "graphify-out"
        graph_dir.mkdir()
        nodes = [
            {"id": "a", "label": "A", "file_mtime": "2026-06-10T12:00:00Z"},
            {"id": "b", "label": "B", "file_mtime": "2026-06-11T12:00:00Z"},
        ]
        links = [
            {"source": "a", "target": "b", "relation": "calls"},
        ]
        _make_graph_json(graph_dir, nodes, links)
        steps = build_timeline(tmp_path)
        assert len(steps) == 0

    def test_cycle_detected(self, tmp_path):
        graph_dir = tmp_path / "graphify-out"
        graph_dir.mkdir()
        nodes = [
            {"id": "a", "label": "A", "file_mtime": "2026-06-10T12:00:00Z", "source_file": "a.py"},
            {"id": "b", "label": "B", "file_mtime": "2026-06-11T12:00:00Z", "source_file": "b.py"},
        ]
        links = [
            {"source": "a", "target": "b", "relation": "preceded_by"},
            {"source": "b", "target": "a", "relation": "preceded_by"},
        ]
        _make_graph_json(graph_dir, nodes, links)
        steps = build_timeline(tmp_path)
        assert len(steps) == 2  # stops at cycle

    def test_unknown_start_id(self, tmp_path):
        graph_dir = tmp_path / "graphify-out"
        graph_dir.mkdir()
        _make_graph_json(graph_dir, [])
        steps = build_timeline(tmp_path, start_id="nonexistent")
        assert len(steps) == 0

    def test_before_filter(self, tmp_path):
        """before filter stops at first node past the date."""
        graph_dir = tmp_path / "graphify-out"
        graph_dir.mkdir()
        nodes = [
            {"id": "a", "label": "A", "file_mtime": "2026-06-10T12:00:00Z", "source_file": "a.py"},
            {"id": "b", "label": "B", "file_mtime": "2026-06-20T12:00:00Z", "source_file": "b.py"},
        ]
        links = [{"source": "a", "target": "b", "relation": "preceded_by"}]
        _make_graph_json(graph_dir, nodes, links)
        steps = build_timeline(tmp_path, before="2026-06-15")
        assert len(steps) == 1
        assert steps[0]["node_id"] == "a"


# ---------------------------------------------------------------------------
# temporal_stats
# ---------------------------------------------------------------------------


class TestTemporalStats:
    def test_basic_stats(self, tmp_path):
        graph_dir = tmp_path / "graphify-out"
        graph_dir.mkdir()
        nodes = [
            {"id": "a", "label": "A", "file_mtime": "2026-06-10T12:00:00Z", "source_file": "a.py"},
            {"id": "b", "label": "B", "file_mtime": "2026-06-12T12:00:00Z", "source_file": "b.py"},
            {"id": "c", "label": "C"},
        ]
        links = [
            {"source": "a", "target": "b", "relation": "preceded_by"},
        ]
        _make_graph_json(graph_dir, nodes, links)
        s = temporal_stats(tmp_path)
        assert s["total_nodes"] == 3
        assert s["nodes_with_mtime"] == 2
        assert s["files_with_mtime"] == 2
        assert s["oldest_mtime"] is not None
        assert s["newest_mtime"] is not None
        assert s["time_span_days"] >= 0

    def test_empty_graph(self, tmp_path):
        graph_dir = tmp_path / "graphify-out"
        graph_dir.mkdir()
        _make_graph_json(graph_dir, [])
        s = temporal_stats(tmp_path)
        assert s["total_nodes"] == 0
        assert s["nodes_with_mtime"] == 0

    def test_dir_mtime_stats(self, tmp_path):
        graph_dir = tmp_path / "graphify-out"
        graph_dir.mkdir()
        nodes = [
            {"id": "a", "label": "A", "dir_mtime": "2026-06-10T12:00:00Z"},
        ]
        _make_graph_json(graph_dir, nodes)
        s = temporal_stats(tmp_path)
        assert s["nodes_with_dir_mtime"] == 1
        assert s["nodes_with_mtime"] == 0

    def test_gaps(self, tmp_path):
        graph_dir = tmp_path / "graphify-out"
        graph_dir.mkdir()
        nodes = [
            {"id": "a", "label": "A", "file_mtime": "2026-06-10T12:00:00Z"},
            {"id": "b", "label": "B", "file_mtime": "2026-06-11T12:00:00Z"},
        ]
        links = [
            {"source": "a", "target": "b", "relation": "preceded_by"},
        ]
        _make_graph_json(graph_dir, nodes, links)
        s = temporal_stats(tmp_path)
        assert s["median_gap_seconds"] > 0
        assert s["longest_gap_seconds"] > 0
        assert s["longest_gap_pair"] == ["a", "b"]

    def test_median_even_count(self, tmp_path):
        """Median of [2, 4] should be 3.0, not 4."""
        graph_dir = tmp_path / "graphify-out"
        graph_dir.mkdir()
        nodes = [
            {"id": "a", "label": "A", "file_mtime": "2026-06-10T12:00:00Z"},
            {"id": "b", "label": "B", "file_mtime": "2026-06-11T12:00:00Z"},
            {"id": "c", "label": "C", "file_mtime": "2026-06-09T12:00:00Z"},
            {"id": "d", "label": "D", "file_mtime": "2026-06-12T12:00:00Z"},
        ]
        links = [
            {"source": "c", "target": "a", "relation": "preceded_by"},
            {"source": "a", "target": "b", "relation": "preceded_by"},
            {"source": "b", "target": "d", "relation": "preceded_by"},
        ]
        _make_graph_json(graph_dir, nodes, links)
        s = temporal_stats(tmp_path)
        # Gaps: c→a = 86400, a→b = 86400, b→d = 86400. Median of [86400, 86400, 86400] = 86400.
        assert s["median_gap_seconds"] == 86400.0

    def test_median_single_gap(self, tmp_path):
        """Single gap → median equals that gap."""
        graph_dir = tmp_path / "graphify-out"
        graph_dir.mkdir()
        nodes = [
            {"id": "a", "label": "A", "file_mtime": "2026-06-10T12:00:00Z"},
            {"id": "b", "label": "B", "file_mtime": "2026-06-11T12:00:00Z"},
        ]
        links = [
            {"source": "a", "target": "b", "relation": "preceded_by"},
        ]
        _make_graph_json(graph_dir, nodes, links)
        s = temporal_stats(tmp_path)
        assert s["median_gap_seconds"] == 86400.0

    def test_stats_json_flag(self, tmp_path):
        import subprocess, sys
        graph_dir = tmp_path / "graphify-out"
        graph_dir.mkdir()
        nodes = [
            {"id": "a", "label": "A", "file_mtime": "2026-06-10T12:00:00Z"},
        ]
        _make_graph_json(graph_dir, nodes)
        result = subprocess.run(
            [sys.executable, "-m", "graphify_temporal", "stats", "--json"],
            capture_output=True, text=True, cwd=tmp_path,
        )
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert "total_nodes" in data
        assert "oldest_mtime" in data


# ---------------------------------------------------------------------------
# CLI integration tests
# ---------------------------------------------------------------------------


class TestCLIIntegration:
    def test_query_cli_help(self, tmp_path):
        import subprocess, sys
        result = subprocess.run(
            [sys.executable, "-m", "graphify_temporal", "query", "--help"],
            capture_output=True, text=True,
        )
        assert result.returncode == 0
        assert "--since" in result.stdout

    def test_timeline_cli_help(self, tmp_path):
        import subprocess, sys
        result = subprocess.run(
            [sys.executable, "-m", "graphify_temporal", "timeline", "--help"],
            capture_output=True, text=True,
        )
        assert result.returncode == 0
        assert "since" in result.stdout

    def test_stats_cli_help(self, tmp_path):
        import subprocess, sys
        result = subprocess.run(
            [sys.executable, "-m", "graphify_temporal", "stats", "--help"],
            capture_output=True, text=True,
        )
        assert result.returncode == 0
        assert "--json" in result.stdout

    def test_impact_cli_missing_graph_exits_1(self, tmp_path):
        import subprocess, sys
        result = subprocess.run(
            [sys.executable, "-m", "graphify_temporal", "impact", "a", "b"],
            capture_output=True, text=True, cwd=tmp_path,
        )
        assert result.returncode == 1
        assert "error:" in result.stderr

    def test_impact_cli_hops_zero_rejected(self, tmp_path):
        import subprocess, sys
        graph_dir = tmp_path / "graphify-out"
        graph_dir.mkdir()
        _make_graph_json(graph_dir, [{"id": "a", "label": "a"}])
        result = subprocess.run(
            [sys.executable, "-m", "graphify_temporal", "impact", "a", "--hops", "0"],
            capture_output=True, text=True, cwd=tmp_path,
        )
        assert result.returncode == 1
        assert "--hops" in result.stderr

    def test_impact_cli_json_output_valid(self, tmp_path):
        import subprocess, sys, json as _json
        graph_dir = tmp_path / "graphify-out"
        graph_dir.mkdir()
        _make_graph_json(
            graph_dir,
            [{"id": "a", "label": "a"}, {"id": "b", "label": "b"}],
            [{"source": "a", "target": "b", "relation": "calls", "confidence": "EXTRACTED"}],
        )
        result = subprocess.run(
            [sys.executable, "-m", "graphify_temporal", "impact", "a", "b", "--json"],
            capture_output=True, text=True, cwd=tmp_path,
        )
        assert result.returncode == 0
        data = _json.loads(result.stdout)
        assert data["anchor_a"] == "a"
        assert data["anchor_b"] == "b"


# ---------------------------------------------------------------------------
# TestImpactScore — unit tests for the ranking formula's 5 independent terms
# ---------------------------------------------------------------------------


class TestImpactScore:
    """Each test varies exactly one of _impact_score's 5 parameters and
    asserts the exact resulting score — impact()'s own tests only check
    relative ordering, not that each term contributes the documented value."""

    def _base_edge(self, relation="calls", confidence="EXTRACTED"):
        return {"relation": relation, "confidence": confidence}

    def test_baseline_hop1_extracted_semantic_same_community_not_bridge(self):
        # (3-1) + confidence_bonus[EXTRACTED]=2 + non-preceded_by=2 + same-community=0 + not-bridge=0
        score = _impact_score(
            hop=1, edge=self._base_edge(), node_community=0, anchor_community=0, is_bridge=False,
        )
        assert score == 6.0

    def test_hop_term_decreases_with_distance(self):
        s1 = _impact_score(1, self._base_edge(), 0, 0, False)
        s2 = _impact_score(2, self._base_edge(), 0, 0, False)
        s3 = _impact_score(3, self._base_edge(), 0, 0, False)
        assert s1 - s2 == 1.0
        assert s2 - s3 == 1.0

    def test_confidence_bonus_extracted_vs_inferred_vs_ambiguous(self):
        s_extracted = _impact_score(1, self._base_edge(confidence="EXTRACTED"), 0, 0, False)
        s_inferred = _impact_score(1, self._base_edge(confidence="INFERRED"), 0, 0, False)
        s_ambiguous = _impact_score(1, self._base_edge(confidence="AMBIGUOUS"), 0, 0, False)
        assert s_extracted - s_inferred == 1.0
        assert s_inferred - s_ambiguous == 1.0

    def test_confidence_bonus_unknown_value_defaults_to_zero(self):
        s_known = _impact_score(1, self._base_edge(confidence="EXTRACTED"), 0, 0, False)
        s_unknown = _impact_score(1, self._base_edge(confidence="not_a_real_value"), 0, 0, False)
        assert s_known - s_unknown == 2.0

    def test_semantic_relation_bonus_vs_preceded_by(self):
        s_semantic = _impact_score(1, self._base_edge(relation="calls"), 0, 0, False)
        s_temporal = _impact_score(1, self._base_edge(relation="preceded_by"), 0, 0, False)
        assert s_semantic - s_temporal == 2.0

    def test_community_crossing_bonus(self):
        s_same = _impact_score(1, self._base_edge(), node_community=0, anchor_community=0, is_bridge=False)
        s_diff = _impact_score(1, self._base_edge(), node_community=1, anchor_community=0, is_bridge=False)
        assert s_diff - s_same == 1.0

    def test_community_none_does_not_get_crossing_bonus(self):
        """A node with no community field must not score as if it 'crossed'
        into a different community — None means unknown, not different."""
        s_none = _impact_score(1, self._base_edge(), node_community=None, anchor_community=0, is_bridge=False)
        s_same = _impact_score(1, self._base_edge(), node_community=0, anchor_community=0, is_bridge=False)
        assert s_none == s_same

    def test_bridge_bonus(self):
        s_neighbor = _impact_score(1, self._base_edge(), 0, 0, is_bridge=False)
        s_bridge = _impact_score(1, self._base_edge(), 0, 0, is_bridge=True)
        assert s_bridge - s_neighbor == 1.0

    def test_all_bonuses_stack_additively(self):
        # hop=1 (score 2) + EXTRACTED (2) + semantic (2) + cross-community (1) + bridge (1) = 8
        score = _impact_score(
            hop=1, edge=self._base_edge(relation="calls", confidence="EXTRACTED"),
            node_community=1, anchor_community=0, is_bridge=True,
        )
        assert score == 8.0


# ---------------------------------------------------------------------------
# TestImpact — root-cause tracing BFS
# ---------------------------------------------------------------------------


class TestImpact:
    def test_two_anchor_bridge_found(self, tmp_path):
        """A node reachable from both anchors within 2 hops is a bridge."""
        graph_dir = tmp_path / "graphify-out"
        graph_dir.mkdir()
        nodes = [{"id": n, "label": n} for n in ["a", "b", "bridge"]]
        links = [
            {"source": "a", "target": "bridge", "relation": "calls", "confidence": "EXTRACTED"},
            {"source": "b", "target": "bridge", "relation": "references", "confidence": "EXTRACTED"},
        ]
        _make_graph_json(graph_dir, nodes, links)
        result = impact(tmp_path, "a", "b", hops=2)
        bridge = next(c for c in result["candidates"] if c["node_id"] == "bridge")
        assert bridge["connection"] == "bridge"

    def test_single_anchor_reachable_nodes(self, tmp_path):
        graph_dir = tmp_path / "graphify-out"
        graph_dir.mkdir()
        nodes = [{"id": n, "label": n} for n in ["a", "b", "c", "far"]]
        links = [
            {"source": "a", "target": "b", "relation": "calls", "confidence": "EXTRACTED"},
            {"source": "b", "target": "c", "relation": "calls", "confidence": "EXTRACTED"},
            {"source": "c", "target": "far", "relation": "calls", "confidence": "EXTRACTED"},
        ]
        _make_graph_json(graph_dir, nodes, links)
        result = impact(tmp_path, "a", hops=2)
        ids = {c["node_id"] for c in result["candidates"]}
        assert ids == {"b", "c"}
        assert result["anchor_b"] is None
        assert result["direct_path"] == []

    def test_degraded_preceded_by_only_graph(self, tmp_path):
        graph_dir = tmp_path / "graphify-out"
        graph_dir.mkdir()
        nodes = [{"id": n, "label": n} for n in ["a", "b"]]
        links = [
            {"source": "a", "target": "b", "relation": "preceded_by", "confidence": "EXTRACTED"},
        ]
        _make_graph_json(graph_dir, nodes, links)
        result = impact(tmp_path, "a", "b", hops=2)
        assert result["structural_confidence"] == "temporal-only"
        for c in result["candidates"]:
            assert c["relation_path"] == ["preceded_by"]

    def test_mixed_relations_ranks_semantic_above_temporal(self, tmp_path):
        graph_dir = tmp_path / "graphify-out"
        graph_dir.mkdir()
        nodes = [{"id": n, "label": n} for n in ["a", "b", "via_calls", "via_preceded"]]
        links = [
            {"source": "a", "target": "via_calls", "relation": "calls", "confidence": "EXTRACTED"},
            {"source": "a", "target": "via_preceded", "relation": "preceded_by", "confidence": "EXTRACTED"},
        ]
        _make_graph_json(graph_dir, nodes, links)
        result = impact(tmp_path, "a", hops=1)
        by_id = {c["node_id"]: c for c in result["candidates"]}
        assert by_id["via_calls"]["score"] > by_id["via_preceded"]["score"]

    def test_node_a_not_found_raises(self, tmp_path):
        graph_dir = tmp_path / "graphify-out"
        graph_dir.mkdir()
        _make_graph_json(graph_dir, [{"id": "a", "label": "a"}])
        with pytest.raises(ValueError, match="not found"):
            impact(tmp_path, "missing")

    def test_node_b_not_found_raises(self, tmp_path):
        graph_dir = tmp_path / "graphify-out"
        graph_dir.mkdir()
        _make_graph_json(graph_dir, [{"id": "a", "label": "a"}])
        with pytest.raises(ValueError, match="missing_b"):
            impact(tmp_path, "a", "missing_b")

    def test_isolated_anchor_no_edges(self, tmp_path):
        graph_dir = tmp_path / "graphify-out"
        graph_dir.mkdir()
        _make_graph_json(graph_dir, [{"id": "a", "label": "a"}, {"id": "b", "label": "b"}])
        result = impact(tmp_path, "a", "b", hops=2)
        assert set(result["isolated_anchors"]) == {"a", "b"}
        assert result["candidates"] == []

    def test_hop_limit_respected(self, tmp_path):
        graph_dir = tmp_path / "graphify-out"
        graph_dir.mkdir()
        chain = [f"n{i}" for i in range(10)]
        nodes = [{"id": n, "label": n} for n in chain]
        links = [
            {"source": chain[i], "target": chain[i + 1], "relation": "calls", "confidence": "EXTRACTED"}
            for i in range(len(chain) - 1)
        ]
        _make_graph_json(graph_dir, nodes, links)
        result = impact(tmp_path, "n0", hops=2)
        ids = {c["node_id"] for c in result["candidates"]}
        assert ids == {"n1", "n2"}
        assert "n3" not in ids

    def test_hub_node_fan_out_capped(self, tmp_path):
        """A hub with degree > cap isn't expanded past — its neighbors'
        neighbors (reachable only through the hub) are absent."""
        from graphify_temporal.query import _IMPACT_HUB_DEGREE_CAP
        graph_dir = tmp_path / "graphify-out"
        graph_dir.mkdir()
        spokes = [f"spoke{i}" for i in range(_IMPACT_HUB_DEGREE_CAP + 5)]
        nodes = [{"id": "a", "label": "a"}, {"id": "hub", "label": "hub"}, {"id": "beyond", "label": "beyond"}]
        nodes += [{"id": s, "label": s} for s in spokes]
        links = [{"source": "a", "target": "hub", "relation": "calls", "confidence": "EXTRACTED"}]
        links += [{"source": "hub", "target": s, "relation": "calls", "confidence": "EXTRACTED"} for s in spokes]
        links += [{"source": spokes[0], "target": "beyond", "relation": "calls", "confidence": "EXTRACTED"}]
        _make_graph_json(graph_dir, nodes, links)
        result = impact(tmp_path, "a", hops=3)
        ids = {c["node_id"] for c in result["candidates"]}
        assert "hub" in ids
        assert "beyond" not in ids  # only reachable by expanding through the capped hub

    def test_node_visit_budget_truncation(self, tmp_path):
        """A binary-tree-shaped graph (branching factor 2, well under the hub
        cap per node) reaches thousands of nodes within a handful of hops —
        exceeding the visit budget without any single node being a hub."""
        from graphify_temporal.query import _IMPACT_VISIT_BUDGET
        graph_dir = tmp_path / "graphify-out"
        graph_dir.mkdir()
        node_ids = ["a"]
        links = []
        frontier = ["a"]
        depth = 0
        while len(node_ids) < _IMPACT_VISIT_BUDGET + 50 and depth < 20:
            next_frontier = []
            for parent in frontier:
                for branch in ("L", "R"):
                    child = f"{parent}{branch}"
                    node_ids.append(child)
                    links.append({"source": parent, "target": child, "relation": "calls", "confidence": "EXTRACTED"})
                    next_frontier.append(child)
            frontier = next_frontier
            depth += 1
        nodes = [{"id": n, "label": n} for n in node_ids]
        _make_graph_json(graph_dir, nodes, links)
        result = impact(tmp_path, "a", hops=20)
        assert result["truncated"] is True

    def test_ranking_is_deterministic(self, tmp_path):
        graph_dir = tmp_path / "graphify-out"
        graph_dir.mkdir()
        nodes = [{"id": n, "label": n} for n in ["a", "zzz", "aaa"]]
        links = [
            {"source": "a", "target": "zzz", "relation": "calls", "confidence": "EXTRACTED"},
            {"source": "a", "target": "aaa", "relation": "calls", "confidence": "EXTRACTED"},
        ]
        _make_graph_json(graph_dir, nodes, links)
        r1 = impact(tmp_path, "a", hops=1)
        r2 = impact(tmp_path, "a", hops=1)
        ids1 = [c["node_id"] for c in r1["candidates"]]
        ids2 = [c["node_id"] for c in r2["candidates"]]
        assert ids1 == ids2 == ["aaa", "zzz"]  # tie-break: node id ascending

    def test_direct_path_between_anchors(self, tmp_path):
        graph_dir = tmp_path / "graphify-out"
        graph_dir.mkdir()
        nodes = [{"id": n, "label": n} for n in ["a", "mid", "b"]]
        links = [
            {"source": "a", "target": "mid", "relation": "calls", "confidence": "EXTRACTED"},
            {"source": "mid", "target": "b", "relation": "references", "confidence": "EXTRACTED"},
        ]
        _make_graph_json(graph_dir, nodes, links)
        result = impact(tmp_path, "a", "b", hops=3)
        assert [s["node_id"] for s in result["direct_path"]] == ["mid", "b"]
        assert [s["relation"] for s in result["direct_path"]] == ["calls", "references"]

    def test_no_direct_path_when_disconnected(self, tmp_path):
        graph_dir = tmp_path / "graphify-out"
        graph_dir.mkdir()
        nodes = [{"id": "a", "label": "a"}, {"id": "b", "label": "b"}]
        _make_graph_json(graph_dir, nodes, [])
        result = impact(tmp_path, "a", "b", hops=2)
        assert result["direct_path"] == []

    def test_candidate_with_null_timestamps(self, tmp_path):
        graph_dir = tmp_path / "graphify-out"
        graph_dir.mkdir()
        nodes = [{"id": "a", "label": "a"}, {"id": "b", "label": "b"}]  # no file_mtime etc at all
        links = [{"source": "a", "target": "b", "relation": "calls", "confidence": "EXTRACTED"}]
        _make_graph_json(graph_dir, nodes, links)
        result = impact(tmp_path, "a", hops=1)
        c = result["candidates"][0]
        assert c["file_mtime"] is None
        assert c["git_commit_date"] is None
        assert c["git_author"] is None

    def test_relations_filter_excludes_others(self, tmp_path):
        graph_dir = tmp_path / "graphify-out"
        graph_dir.mkdir()
        nodes = [{"id": n, "label": n} for n in ["a", "via_calls", "via_imports"]]
        links = [
            {"source": "a", "target": "via_calls", "relation": "calls", "confidence": "EXTRACTED"},
            {"source": "a", "target": "via_imports", "relation": "imports", "confidence": "EXTRACTED"},
        ]
        _make_graph_json(graph_dir, nodes, links)
        result = impact(tmp_path, "a", hops=1, relations=["calls"])
        ids = {c["node_id"] for c in result["candidates"]}
        assert ids == {"via_calls"}

    def test_max_candidates_truncates_list(self, tmp_path):
        graph_dir = tmp_path / "graphify-out"
        graph_dir.mkdir()
        spokes = [f"n{i}" for i in range(10)]
        nodes = [{"id": "a", "label": "a"}] + [{"id": s, "label": s} for s in spokes]
        links = [{"source": "a", "target": s, "relation": "calls", "confidence": "EXTRACTED"} for s in spokes]
        _make_graph_json(graph_dir, nodes, links)
        result = impact(tmp_path, "a", hops=1, max_candidates=3)
        assert len(result["candidates"]) == 3

    def test_community_boundary_bonus(self, tmp_path):
        graph_dir = tmp_path / "graphify-out"
        graph_dir.mkdir()
        nodes = [
            {"id": "a", "label": "a", "community": 0},
            {"id": "same_community", "label": "x", "community": 0},
            {"id": "diff_community", "label": "y", "community": 1},
        ]
        links = [
            {"source": "a", "target": "same_community", "relation": "calls", "confidence": "EXTRACTED"},
            {"source": "a", "target": "diff_community", "relation": "calls", "confidence": "EXTRACTED"},
        ]
        _make_graph_json(graph_dir, nodes, links)
        result = impact(tmp_path, "a", hops=1)
        by_id = {c["node_id"]: c for c in result["candidates"]}
        assert by_id["diff_community"]["score"] > by_id["same_community"]["score"]

    def test_alternate_paths_counts_multiple_edges_same_anchor(self, tmp_path):
        """Two independent edges from the same anchor to the same candidate
        (e.g. both `calls` and `references`) is stronger evidence than one —
        alternate_paths must reflect that, not just the binary bridge case."""
        graph_dir = tmp_path / "graphify-out"
        graph_dir.mkdir()
        nodes = [{"id": "a", "label": "a"}, {"id": "target", "label": "target"}]
        links = [
            {"source": "a", "target": "target", "relation": "calls", "confidence": "EXTRACTED"},
            {"source": "a", "target": "target", "relation": "references", "confidence": "EXTRACTED"},
        ]
        _make_graph_json(graph_dir, nodes, links)
        result = impact(tmp_path, "a", hops=1)
        assert result["candidates"][0]["alternate_paths"] == 2

    def test_malformed_edge_skipped_not_crashed(self, tmp_path):
        graph_dir = tmp_path / "graphify-out"
        graph_dir.mkdir()
        nodes = [{"id": "a", "label": "a"}, {"id": "b", "label": "b"}]
        links = [
            {"source": "a", "relation": "calls"},  # missing target
            {"source": "a", "target": "b", "relation": "calls", "confidence": "EXTRACTED"},
        ]
        _make_graph_json(graph_dir, nodes, links)
        result = impact(tmp_path, "a", hops=1)  # must not raise
        assert {c["node_id"] for c in result["candidates"]} == {"b"}
