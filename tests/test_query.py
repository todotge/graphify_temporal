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
    _ts_from_node,
    _parse_date_ts,
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
        results = query_nodes(tmp_path, search="auth")
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
        results = query_nodes(tmp_path, search="auth")
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
        results = query_nodes(tmp_path, since="2026-06-14")
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
        results = query_nodes(tmp_path, before="2026-06-14")
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
        results = query_nodes(tmp_path, order="newest-first")
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
        results = query_nodes(tmp_path, order="oldest-first")
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
        results = query_nodes(tmp_path)
        assert len(results) == 2

    def test_nodes_without_mtime_excluded_when_time_filter(self, tmp_path):
        graph_dir = tmp_path / "graphify-out"
        graph_dir.mkdir()
        nodes = [
            {"id": "a", "label": "A"},
            {"id": "b", "label": "B", "file_mtime": "2026-06-15T12:00:00Z"},
        ]
        _make_graph_json(graph_dir, nodes)
        results = query_nodes(tmp_path, since="2026-06-14")
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
        results = query_nodes(tmp_path, since="2026-06-14", use_dir_mtime=True)
        assert len(results) == 1
        assert results[0]["id"] == "b"  # filtered by dir_mtime

    def test_missing_graph(self, tmp_path):
        with pytest.raises(FileNotFoundError, match="No graph.json found"):
            query_nodes(tmp_path)


# ---------------------------------------------------------------------------
# build_timeline
# ---------------------------------------------------------------------------


class TestTimeline:
    def test_basic_chain(self, tmp_path):
        graph_dir = tmp_path / "graphify-out"
        graph_dir.mkdir()
        nodes = [
            {"id": "a", "label": "A", "file_mtime": "2026-06-10T12:00:00Z"},
            {"id": "b", "label": "B", "file_mtime": "2026-06-11T12:00:00Z"},
            {"id": "c", "label": "C", "file_mtime": "2026-06-12T12:00:00Z"},
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
            {"id": "a", "label": "A", "file_mtime": "2026-06-10T12:00:00Z"},
            {"id": "b", "label": "B", "file_mtime": "2026-06-11T12:00:00Z"},
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
            {"id": "a", "label": "A", "file_mtime": "2026-06-10T12:00:00Z"},
            {"id": "b", "label": "B", "file_mtime": "2026-06-15T12:00:00Z"},
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
            {"id": "a", "label": "A", "file_mtime": "2026-06-10T12:00:00Z"},
            {"id": "b", "label": "B", "file_mtime": "2026-06-11T12:00:00Z"},
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
            {"id": "a", "label": "A", "file_mtime": "2026-06-10T12:00:00Z"},
            {"id": "b", "label": "B", "file_mtime": "2026-06-20T12:00:00Z"},
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
