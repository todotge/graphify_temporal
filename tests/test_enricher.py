"""Tests for graphify_temporal — fs utilities and end-to-end enrichment."""

import calendar
import json
import os
import time
import tempfile
from pathlib import Path

import pytest

from graphify_temporal.enricher import enrich
from graphify_temporal.fs import (
    resolve_mtime, resolve_birthtime, resolve_dir_mtime,
    matches_glob, is_excluded, parse_date,
)


# ---------------------------------------------------------------------------
# test helpers
# ---------------------------------------------------------------------------


def _make_graph_json(
    graph_dir: Path,
    nodes: list[dict],
    links: list[dict] | None = None,
) -> Path:
    """Write a minimal graph.json to *graph_dir* and return its path."""
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
    """Create a file under *root*, creating parent dirs as needed."""
    fp = root / relpath
    fp.parent.mkdir(parents=True, exist_ok=True)
    fp.write_text(content, encoding="utf-8")


# ---------------------------------------------------------------------------
# TestFs — unit tests for the filesystem module
# ---------------------------------------------------------------------------


class TestFs:
    """Unit tests for graphify_temporal.fs — no graph.json needed."""

    def test_resolve_mtime_returns_iso8601(self, tmp_path):
        """Existing file → ISO 8601 string with 'T' separator, length 19."""
        _write_file(tmp_path, "test.py")
        mt = resolve_mtime("test.py", tmp_path)
        assert mt is not None
        assert "T" in mt
        assert len(mt) == 20
        assert mt.endswith("Z")

    def test_resolve_mtime_missing_file(self, tmp_path):
        """Non-existent file → None."""
        assert resolve_mtime("nonexistent.py", tmp_path) is None

    def test_resolve_mtime_ctime_flag(self, tmp_path):
        """use_ctime=True still returns valid ISO 8601."""
        _write_file(tmp_path, "test.py")
        mt = resolve_mtime("test.py", tmp_path, use_ctime=True)
        assert mt is not None
        assert "T" in mt

    def test_matches_glob(self):
        """Glob match: True for inside pattern, False for outside, True for no
        patterns at all (include-everything default)."""
        assert matches_glob("src/module/test.py", ["src/**"])
        assert not matches_glob("other/test.py", ["src/**"])
        assert matches_glob("any.py", None)

    def test_is_excluded(self):
        """Exclusion: True for matching pattern, False otherwise, False when
        no patterns given (exclude-nothing default)."""
        assert is_excluded("archive/old.py", ["archive/**"])
        assert not is_excluded("src/main.py", ["archive/**"])
        assert not is_excluded("any.py", None)

    def test_parse_date_valid(self):
        """'2026-05-15' → float timestamp."""
        ts = parse_date("2026-05-15")
        assert isinstance(ts, float)

    def test_parse_date_invalid(self):
        """DD-MM-YYYY format is rejected with a clear error message."""
        with pytest.raises(ValueError, match="Invalid date format"):
            parse_date("15-05-2026")

    def test_resolve_birthtime_returns_iso8601(self, tmp_path):
        """Existing file on ext4 with statx → ISO 8601 birth time."""
        _write_file(tmp_path, "test.py")
        bt = resolve_birthtime("test.py", tmp_path)
        assert bt is not None
        assert "T" in bt
        assert len(bt) == 20
        assert bt.endswith("Z")

    def test_resolve_birthtime_missing_file(self, tmp_path):
        """Non-existent file → None."""
        assert resolve_birthtime("nonexistent.py", tmp_path) is None

    def test_resolve_dir_mtime_returns_iso8601(self, tmp_path):
        """Parent directory of an existing file → valid ISO 8601."""
        _write_file(tmp_path, "sub/test.py")
        bt = resolve_dir_mtime("sub/test.py", tmp_path)
        assert bt is not None
        assert "T" in bt
        assert len(bt) == 20
        assert bt.endswith("Z")

    def test_resolve_dir_mtime_missing_parent(self, tmp_path):
        """Source file under non-existent parent → None."""
        assert resolve_dir_mtime("nonexistent_dir/f.py", tmp_path) is None


# ---------------------------------------------------------------------------
# TestEnricher — integration tests for the enrich() pipeline
# ---------------------------------------------------------------------------


class TestEnricher:
    """End-to-end tests: write a graph.json, run enrich(), check side effects.

    Every test uses tmp_path so the real filesystem is never touched and
    tests never interfere with each other.
    """

    def test_basic_enrichment(self, tmp_path):
        """Two nodes in one file → both stamped, one intra-file edge."""
        graph_dir = tmp_path / "graphify-out"
        graph_dir.mkdir()
        _write_file(tmp_path, "a.py", "x = 1\ny = 2")
        n1 = {
            "id": "a_x", "label": "x",
            "source_file": "a.py", "source_location": "a.py:L1",
        }
        n2 = {
            "id": "a_y", "label": "y",
            "source_file": "a.py", "source_location": "a.py:L2",
        }
        _make_graph_json(graph_dir, [n1, n2])

        stats = enrich(tmp_path)
        assert stats["nodes_enriched"] == 2
        assert stats["edges_intra_file"] == 1
        data = json.loads(
            graph_dir.joinpath("graph.json").read_text(encoding="utf-8")
        )
        assert data["nodes"][0]["file_mtime"] is not None
        assert len(data["links"]) == 1
        assert data["links"][0]["relation"] == "preceded_by"
        assert data["links"][0]["source"] == "a_x"
        assert data["links"][0]["target"] == "a_y"

    def test_ctime_flag(self, tmp_path):
        """use_ctime=True stamp works without crashing."""
        graph_dir = tmp_path / "graphify-out"
        graph_dir.mkdir()
        _write_file(tmp_path, "a.py")
        n1 = {
            "id": "a_x", "label": "x",
            "source_file": "a.py", "source_location": "a.py:L1",
        }
        _make_graph_json(graph_dir, [n1])

        stats_ctime = enrich(tmp_path, use_ctime=True)
        assert stats_ctime["nodes_enriched"] == 1

    def test_dry_run_no_modification(self, tmp_path):
        """dry_run returns stats but does not mutate graph.json on disk."""
        graph_dir = tmp_path / "graphify-out"
        graph_dir.mkdir()
        _write_file(tmp_path, "a.py")
        n1 = {
            "id": "a_x", "label": "x",
            "source_file": "a.py", "source_location": "a.py:L1",
        }
        _make_graph_json(graph_dir, [n1])

        stats = enrich(tmp_path, dry_run=True)
        assert stats["nodes_enriched"] == 1
        assert stats["edges_intra_file"] == 0

        data = json.loads(
            graph_dir.joinpath("graph.json").read_text(encoding="utf-8")
        )
        assert "file_mtime" not in data["nodes"][0]
        assert data["links"] == []

    def test_missing_graph(self, tmp_path):
        """No graphify-out/graph.json → FileNotFoundError."""
        with pytest.raises(FileNotFoundError, match="No graph.json found"):
            enrich(tmp_path)

    def test_missing_source_file(self, tmp_path):
        """Node references a file that doesn't exist → file_mtime = None, not
        a crash."""
        graph_dir = tmp_path / "graphify-out"
        graph_dir.mkdir()
        n1 = {
            "id": "ghost", "label": "ghost",
            "source_file": "deleted.py",
            "source_location": "deleted.py:L1",
        }
        _make_graph_json(graph_dir, [n1])

        stats = enrich(tmp_path)
        assert stats["nodes_enriched"] == 0
        data = json.loads(
            graph_dir.joinpath("graph.json").read_text(encoding="utf-8")
        )
        assert data["nodes"][0]["file_mtime"] is None

    def test_empty_graph(self, tmp_path):
        """Graph with zero nodes → zero edges, no crash."""
        graph_dir = tmp_path / "graphify-out"
        graph_dir.mkdir()
        _make_graph_json(graph_dir, [])

        stats = enrich(tmp_path)
        assert stats["nodes_total"] == 0
        assert stats["edges_total"] == 0

    def test_no_source_files(self, tmp_path):
        """Node without source_file key → skipped, nodes_enriched == 0."""
        graph_dir = tmp_path / "graphify-out"
        graph_dir.mkdir()
        # Node deliberately lacks a source_file field.
        n1 = {"id": "concept", "label": "Concept", "source_location": None}
        _make_graph_json(graph_dir, [n1])

        stats = enrich(tmp_path)
        assert stats["nodes_enriched"] == 0

    def test_idempotency(self, tmp_path):
        """Running enrich() twice on the same graph produces the same edges
        — duplicates are detected and skipped."""
        graph_dir = tmp_path / "graphify-out"
        graph_dir.mkdir()
        _write_file(tmp_path, "a.py", "x\ny\nz")
        nodes = [
            {"id": "a_x", "label": "x", "source_file": "a.py",
             "source_location": "a.py:L1"},
            {"id": "a_y", "label": "y", "source_file": "a.py",
             "source_location": "a.py:L2"},
            {"id": "a_z", "label": "z", "source_file": "a.py",
             "source_location": "a.py:L3"},
        ]
        _make_graph_json(graph_dir, nodes)

        enrich(tmp_path)
        stats2 = enrich(tmp_path)

        assert stats2["edges_deduped"] == 2
        data = json.loads(
            graph_dir.joinpath("graph.json").read_text(encoding="utf-8")
        )
        assert len(data["links"]) == 2

    def test_preserves_existing_edges(self, tmp_path):
        """Graph.json already has edges → they survive enrichment intact."""
        graph_dir = tmp_path / "graphify-out"
        graph_dir.mkdir()
        _write_file(tmp_path, "a.py")
        n1 = {
            "id": "a_x", "label": "x",
            "source_file": "a.py", "source_location": "a.py:L1",
        }
        existing = [{
            "source": "a_x", "target": "other", "relation": "calls",
            "confidence": "EXTRACTED", "confidence_score": 1.0,
            "source_file": "a.py", "weight": 1.0,
        }]
        _make_graph_json(graph_dir, [n1], existing)

        enrich(tmp_path)

        data = json.loads(
            graph_dir.joinpath("graph.json").read_text(encoding="utf-8")
        )
        relations = {e["relation"] for e in data["links"]}
        assert "calls" in relations

    def test_cross_file_edges(self, tmp_path):
        """--cross-file links the first node of the older file to the first
        node of the newer file."""
        graph_dir = tmp_path / "graphify-out"
        graph_dir.mkdir()
        _write_file(tmp_path, "a.py")
        time.sleep(0.1)  # force a measurable mtime gap
        _write_file(tmp_path, "b.py")

        n1 = {
            "id": "a_x", "label": "x",
            "source_file": "a.py", "source_location": "a.py:L1",
        }
        n2 = {
            "id": "b_y", "label": "y",
            "source_file": "b.py", "source_location": "b.py:L1",
        }
        _make_graph_json(graph_dir, [n1, n2])

        stats = enrich(tmp_path, cross_file=True)
        assert stats["edges_cross_file"] == 1

        data = json.loads(
            graph_dir.joinpath("graph.json").read_text(encoding="utf-8")
        )
        cross_edges = [
            e for e in data["links"]
            if e["source"] == "a_x" and e["target"] == "b_y"
        ]
        assert len(cross_edges) == 1

    def test_since_filter(self, tmp_path):
        """--since DATE skips old files (file_mtime=None) and processes
        recent ones normally."""
        graph_dir = tmp_path / "graphify-out"
        graph_dir.mkdir()
        _write_file(tmp_path, "old.py")
        old_ts = calendar.timegm(time.strptime("2020-01-01", "%Y-%m-%d"))
        os.utime(tmp_path / "old.py", (old_ts, old_ts))

        _write_file(tmp_path, "new.py")

        n1 = {
            "id": "old_x", "label": "x",
            "source_file": "old.py", "source_location": "old.py:L1",
        }
        n2 = {
            "id": "new_x", "label": "x",
            "source_file": "new.py", "source_location": "new.py:L1",
        }
        _make_graph_json(graph_dir, [n1, n2])

        stats = enrich(tmp_path, since="2025-01-01")
        assert stats["nodes_enriched"] == 1  # only new.py has a timestamp
        data = json.loads(
            graph_dir.joinpath("graph.json").read_text(encoding="utf-8")
        )
        old_node = next(n for n in data["nodes"] if n["id"] == "old_x")
        new_node = next(n for n in data["nodes"] if n["id"] == "new_x")
        assert old_node["file_mtime"] is None
        assert new_node["file_mtime"] is not None

    def test_include_exclude(self, tmp_path):
        """--include limits which files get stamped; --exclude filters them
        out.  Both pass through the same glob engine."""
        graph_dir = tmp_path / "graphify-out"
        graph_dir.mkdir()
        _write_file(tmp_path, "your/archive/test.py")
        _write_file(tmp_path, "your/module/test.py")

        n1 = {
            "id": "archive_test", "label": "test",
            "source_file": "your/archive/test.py",
            "source_location": "your/archive/test.py:L1",
        }
        n2 = {
            "id": "module_test", "label": "test",
            "source_file": "your/module/test.py",
            "source_location": "your/module/test.py:L1",
        }
        _make_graph_json(graph_dir, [n1, n2])

        # Only archive/ is included → module/ stays unstamped.
        include_stats = enrich(tmp_path, include=["your/archive/**"])
        data = json.loads(
            graph_dir.joinpath("graph.json").read_text(encoding="utf-8")
        )
        archive_node = next(
            n for n in data["nodes"] if n["id"] == "archive_test"
        )
        module_node = next(
            n for n in data["nodes"] if n["id"] == "module_test"
        )
        assert archive_node["file_mtime"] is not None
        assert module_node["file_mtime"] is None

        # Fresh run: exclude module/ → module/ stays unstamped.
        _make_graph_json(graph_dir, [n1, n2])  # reset
        enrich(tmp_path, exclude=["your/module/**"])
        data = json.loads(
            graph_dir.joinpath("graph.json").read_text(encoding="utf-8")
        )
        module_node = next(
            n for n in data["nodes"] if n["id"] == "module_test"
        )
        assert module_node["file_mtime"] is None

    def test_birthtime_flag(self, tmp_path):
        """--use-birthtime stamps file_mtime with st_birthtime (creation)."""
        graph_dir = tmp_path / "graphify-out"
        graph_dir.mkdir()
        _write_file(tmp_path, "a.py")
        n1 = {
            "id": "a_x", "label": "x",
            "source_file": "a.py", "source_location": "a.py:L1",
        }
        _make_graph_json(graph_dir, [n1])

        stats = enrich(tmp_path, use_birthtime=True)
        assert stats["nodes_enriched"] == 1
        data = json.loads(
            graph_dir.joinpath("graph.json").read_text(encoding="utf-8")
        )
        assert data["nodes"][0]["file_mtime"] is not None
        assert "file_ctime" not in data["nodes"][0]

    def test_dir_mtime_flag(self, tmp_path):
        """--include-dir-mtime adds dir_mtime to every stamped node."""
        graph_dir = tmp_path / "graphify-out"
        graph_dir.mkdir()
        _write_file(tmp_path, "a.py")
        n1 = {
            "id": "a_x", "label": "x",
            "source_file": "a.py", "source_location": "a.py:L1",
        }
        _make_graph_json(graph_dir, [n1])

        stats = enrich(tmp_path, include_dir_mtime=True)
        assert stats["nodes_enriched"] == 1
        data = json.loads(
            graph_dir.joinpath("graph.json").read_text(encoding="utf-8")
        )
        assert data["nodes"][0]["file_mtime"] is not None
        assert data["nodes"][0]["dir_mtime"] is not None
        assert "T" in data["nodes"][0]["dir_mtime"]

    def test_links_null_handled(self, tmp_path):
        """graph.json with links: null → doesn't crash."""
        graph_dir = tmp_path / "graphify-out"
        graph_dir.mkdir()
        _write_file(tmp_path, "a.py")
        n1 = {"id": "a_x", "label": "x", "source_file": "a.py", "source_location": "a.py:L1"}
        data = {
            "directed": True, "multigraph": False, "graph": {},
            "nodes": [n1], "links": None, "hyperedges": [],
        }
        p = graph_dir / "graph.json"
        p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        stats = enrich(tmp_path)
        assert stats["nodes_enriched"] == 1

    def test_links_missing_handled(self, tmp_path):
        """graph.json without links key → doesn't crash."""
        graph_dir = tmp_path / "graphify-out"
        graph_dir.mkdir()
        _write_file(tmp_path, "a.py")
        n1 = {"id": "a_x", "label": "x", "source_file": "a.py", "source_location": "a.py:L1"}
        data = {"directed": True, "nodes": [n1]}
        p = graph_dir / "graph.json"
        p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        stats = enrich(tmp_path)
        assert stats["nodes_enriched"] == 1

    def test_empty_source_file_skipped(self, tmp_path):
        """Node with source_file: '' → file_mtime = None, no crash."""
        graph_dir = tmp_path / "graphify-out"
        graph_dir.mkdir()
        n1 = {"id": "empty", "label": "E", "source_file": "", "source_location": "L1"}
        _make_graph_json(graph_dir, [n1])
        stats = enrich(tmp_path)
        assert stats["nodes_enriched"] == 0
        data = json.loads(graph_dir.joinpath("graph.json").read_text(encoding="utf-8"))
        assert data["nodes"][0]["file_mtime"] is None

    def test_birthtime_since_no_crash(self, tmp_path):
        """--use-birthtime + --since doesn't TypeError when birthtime unavailable."""
        graph_dir = tmp_path / "graphify-out"
        graph_dir.mkdir()
        _write_file(tmp_path, "a.py")
        n1 = {"id": "a_x", "label": "x", "source_file": "a.py", "source_location": "a.py:L1"}
        _make_graph_json(graph_dir, [n1])
        stats = enrich(tmp_path, use_birthtime=True, since="2020-01-01")
        assert stats["nodes_enriched"] >= 0

    def test_iso_format_has_z_suffix(self, tmp_path):
        """Enriched file_mtime ends with Z (UTC indicator)."""
        graph_dir = tmp_path / "graphify-out"
        graph_dir.mkdir()
        _write_file(tmp_path, "a.py")
        n1 = {"id": "a_x", "label": "x", "source_file": "a.py", "source_location": "a.py:L1"}
        _make_graph_json(graph_dir, [n1])
        enrich(tmp_path)
        data = json.loads(graph_dir.joinpath("graph.json").read_text(encoding="utf-8"))
        assert data["nodes"][0]["file_mtime"].endswith("Z")

    def test_include_and_exclude_together(self, tmp_path):
        """Both include and exclude filters work simultaneously."""
        graph_dir = tmp_path / "graphify-out"
        graph_dir.mkdir()
        _write_file(tmp_path, "src/main/test.py")
        _write_file(tmp_path, "src/ignore/test.py")
        n1 = {"id": "main", "label": "x", "source_file": "src/main/test.py", "source_location": "src/main/test.py:L1"}
        n2 = {"id": "ignored", "label": "x", "source_file": "src/ignore/test.py", "source_location": "src/ignore/test.py:L1"}
        _make_graph_json(graph_dir, [n1, n2])
        enrich(tmp_path, include=["src/**"], exclude=["src/ignore/**"])
        data = json.loads(graph_dir.joinpath("graph.json").read_text(encoding="utf-8"))
        main_node = next(n for n in data["nodes"] if n["id"] == "main")
        ign_node = next(n for n in data["nodes"] if n["id"] == "ignored")
        assert main_node["file_mtime"] is not None
        assert ign_node["file_mtime"] is None
