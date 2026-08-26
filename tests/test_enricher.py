"""Tests for graphify_temporal — fs utilities and end-to-end enrichment."""

import calendar
import json
import os
import shutil
import subprocess
import time
import tempfile
from pathlib import Path

import pytest

from graphify_temporal.enricher import enrich
from graphify_temporal.fs import (
    resolve_mtime, resolve_birthtime, resolve_dir_mtime,
    matches_glob, is_excluded, parse_date,
)
from graphify_temporal import git_source


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


def _make_git_repo(root: Path) -> None:
    """Init a git repo at root with a fixed test identity."""
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=root, check=True)


def _git_commit(root: Path, message: str = "commit", when: str | None = None) -> None:
    """Stage everything and commit, optionally pinning author/committer date
    (YYYY-MM-DDTHH:MM:SSZ) so ordering in tests is deterministic instead of
    depending on real wall-clock timing between two commits."""
    env = dict(os.environ)
    if when:
        env["GIT_AUTHOR_DATE"] = when
        env["GIT_COMMITTER_DATE"] = when
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", message], cwd=root, check=True, env=env)


requires_git = pytest.mark.skipif(
    shutil.which("git") is None, reason="git not installed"
)


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


# ---------------------------------------------------------------------------
# TestGitSource — unit tests for graphify_temporal.git_source
# ---------------------------------------------------------------------------


@requires_git
class TestGitSource:
    """Unit tests for git_source — no graph.json needed, just a temp repo."""

    def test_git_available_true_when_installed(self):
        assert git_source.git_available() is True

    def test_find_repo_root_in_git_repo(self, tmp_path):
        _make_git_repo(tmp_path)
        assert git_source.find_repo_root(tmp_path) == tmp_path.resolve()

    def test_find_repo_root_not_a_repo(self, tmp_path):
        assert git_source.find_repo_root(tmp_path) is None

    def test_find_repo_root_monorepo_subdir(self, tmp_path):
        """A subdirectory of the repo still resolves to the real root —
        the case that matters for enrich() being called with root pointing
        at a subfolder of a larger monorepo."""
        _make_git_repo(tmp_path)
        sub = tmp_path / "sub"
        sub.mkdir()
        assert git_source.find_repo_root(sub) == tmp_path.resolve()

    def test_resolve_file_date_last_touch(self, tmp_path):
        """Two commits touching the same file → 'last' mode returns the
        second commit's date, not the first."""
        _make_git_repo(tmp_path)
        _write_file(tmp_path, "a.py", "v1")
        _git_commit(tmp_path, "first", when="2023-01-01T00:00:00Z")
        _write_file(tmp_path, "a.py", "v2")
        _git_commit(tmp_path, "second", when="2024-06-15T00:00:00Z")

        date = git_source.resolve_file_date(tmp_path, "a.py", mode="last")
        assert date == "2024-06-15T00:00:00Z"

    def test_resolve_file_date_first_creation(self, tmp_path):
        _make_git_repo(tmp_path)
        _write_file(tmp_path, "a.py", "v1")
        _git_commit(tmp_path, "first", when="2023-01-01T00:00:00Z")
        _write_file(tmp_path, "a.py", "v2")
        _git_commit(tmp_path, "second", when="2024-06-15T00:00:00Z")

        date = git_source.resolve_file_date(tmp_path, "a.py", mode="first")
        assert date == "2023-01-01T00:00:00Z"

    def test_resolve_file_date_untracked_file(self, tmp_path):
        _make_git_repo(tmp_path)
        _write_file(tmp_path, "untracked.py")
        assert git_source.resolve_file_date(tmp_path, "untracked.py") is None

    def test_is_shallow_repo_false_normal_clone(self, tmp_path):
        _make_git_repo(tmp_path)
        assert git_source.is_shallow_repo(tmp_path) is False

    def test_is_shallow_repo_true(self, tmp_path):
        _make_git_repo(tmp_path)
        (tmp_path / ".git" / "shallow").touch()
        assert git_source.is_shallow_repo(tmp_path) is True

    def test_blame_file_line_dates(self, tmp_path):
        """Line-level attribution: only the modified line shows the newer
        commit's date, untouched lines keep the original commit's date."""
        _make_git_repo(tmp_path)
        _write_file(tmp_path, "a.py", "line1\nline2\nline3\n")
        _git_commit(tmp_path, "first", when="2023-01-01T00:00:00Z")
        _write_file(tmp_path, "a.py", "line1\nCHANGED\nline3\n")
        _git_commit(tmp_path, "second", when="2024-06-15T00:00:00Z")

        blame = git_source.blame_file(tmp_path, "a.py")
        assert blame is not None
        assert blame[1] == "2023-01-01T00:00:00Z"
        assert blame[2] == "2024-06-15T00:00:00Z"
        assert blame[3] == "2023-01-01T00:00:00Z"

    def test_blame_file_untracked(self, tmp_path):
        _make_git_repo(tmp_path)
        _write_file(tmp_path, "untracked.py")
        assert git_source.blame_file(tmp_path, "untracked.py") is None

    def test_resolve_line_date_lookup(self):
        blame_map = {1: "2023-01-01T00:00:00Z", 2: "2024-06-15T00:00:00Z"}
        assert git_source.resolve_line_date(blame_map, 1) == "2023-01-01T00:00:00Z"
        assert git_source.resolve_line_date(blame_map, 2) == "2024-06-15T00:00:00Z"
        assert git_source.resolve_line_date(blame_map, 99) is None
        assert git_source.resolve_line_date(blame_map, 0) is None
        assert git_source.resolve_line_date(None, 1) is None


# ---------------------------------------------------------------------------
# TestEnrichGit — integration tests for enrich(use_git=True)
# ---------------------------------------------------------------------------


@requires_git
class TestEnrichGit:
    """Integration tests for the --git enrichment path."""

    def test_enrich_use_git_basic(self, tmp_path):
        """git_commit_date reflects the real commit date, not the stat
        mtime — proven by bumping the file's mtime to "now" after commit
        (simulating a fresh clone) and asserting git_commit_date still
        shows the original 2023 commit date, not the bumped mtime."""
        _make_git_repo(tmp_path)
        _write_file(tmp_path, "a.py", "def foo():\n    pass\n")
        _git_commit(tmp_path, "first", when="2023-01-01T00:00:00Z")
        os.utime(tmp_path / "a.py", None)  # bump mtime to "now"

        graph_dir = tmp_path / "graphify-out"
        graph_dir.mkdir()
        n1 = {"id": "foo", "label": "foo", "source_file": "a.py", "source_location": "a.py:L1"}
        _make_graph_json(graph_dir, [n1])

        enrich(tmp_path, use_git=True, quiet=True)
        data = json.loads(graph_dir.joinpath("graph.json").read_text(encoding="utf-8"))
        node = data["nodes"][0]
        assert node["git_commit_date"] == "2023-01-01T00:00:00Z"
        assert node["file_mtime"] == "2023-01-01T00:00:00Z"
        assert node["git_author"] == "Test"

    def test_enrich_use_git_falls_back_when_not_a_repo(self, tmp_path):
        """No git repo at all → behaves exactly like use_git=False: no
        crash, file_mtime from stat, no git_commit_date field."""
        graph_dir = tmp_path / "graphify-out"
        graph_dir.mkdir()
        _write_file(tmp_path, "a.py")
        n1 = {"id": "foo", "label": "foo", "source_file": "a.py", "source_location": "a.py:L1"}
        _make_graph_json(graph_dir, [n1])

        stats = enrich(tmp_path, use_git=True, quiet=True)
        data = json.loads(graph_dir.joinpath("graph.json").read_text(encoding="utf-8"))
        node = data["nodes"][0]
        assert node["file_mtime"] is not None
        assert "git_commit_date" not in node
        assert stats["nodes_enriched"] == 1

    def test_enrich_use_git_untracked_file_falls_back_to_stat(self, tmp_path):
        """Repo exists, but one file is uncommitted — that file gets stat
        mtime and no git_commit_date, while its committed sibling gets both."""
        _make_git_repo(tmp_path)
        _write_file(tmp_path, "tracked.py")
        _git_commit(tmp_path, "first", when="2023-01-01T00:00:00Z")
        _write_file(tmp_path, "untracked.py")

        graph_dir = tmp_path / "graphify-out"
        graph_dir.mkdir()
        n1 = {"id": "a", "label": "a", "source_file": "tracked.py", "source_location": "tracked.py:L1"}
        n2 = {"id": "b", "label": "b", "source_file": "untracked.py", "source_location": "untracked.py:L1"}
        _make_graph_json(graph_dir, [n1, n2])

        enrich(tmp_path, use_git=True, quiet=True)
        data = json.loads(graph_dir.joinpath("graph.json").read_text(encoding="utf-8"))
        tracked = next(n for n in data["nodes"] if n["id"] == "a")
        untracked = next(n for n in data["nodes"] if n["id"] == "b")
        assert tracked["git_commit_date"] == "2023-01-01T00:00:00Z"
        assert untracked["file_mtime"] is not None
        assert "git_commit_date" not in untracked

    def test_enrich_use_git_cross_file_uses_git_dates(self, tmp_path):
        """--cross-file ordering follows git commit dates, not stat mtimes —
        both files' mtimes are bumped to "now" after commit (simulating a
        clone where every file gets ~the same checkout timestamp), yet the
        preceded_by edge still points from the file committed first."""
        _make_git_repo(tmp_path)
        _write_file(tmp_path, "old.py", "x")
        _git_commit(tmp_path, "old commit", when="2023-01-01T00:00:00Z")
        _write_file(tmp_path, "new.py", "y")
        _git_commit(tmp_path, "new commit", when="2024-06-15T00:00:00Z")
        # Simulate post-clone checkout: both files now share ~the same mtime.
        os.utime(tmp_path / "old.py", None)
        os.utime(tmp_path / "new.py", None)

        graph_dir = tmp_path / "graphify-out"
        graph_dir.mkdir()
        n1 = {"id": "old_node", "label": "old", "source_file": "old.py", "source_location": "old.py:L1"}
        n2 = {"id": "new_node", "label": "new", "source_file": "new.py", "source_location": "new.py:L1"}
        _make_graph_json(graph_dir, [n1, n2])

        enrich(tmp_path, use_git=True, cross_file=True, quiet=True)
        data = json.loads(graph_dir.joinpath("graph.json").read_text(encoding="utf-8"))
        cross_edges = [e for e in data["links"] if e["source_file"] == "old.py"]
        assert len(cross_edges) == 1
        assert cross_edges[0]["source"] == "old_node"
        assert cross_edges[0]["target"] == "new_node"

    def test_enrich_use_git_no_binary(self, tmp_path, monkeypatch):
        """git binary missing → graceful fallback, no exception."""
        monkeypatch.setattr(git_source, "which", lambda name: None)
        git_source.git_available.cache_clear()

        graph_dir = tmp_path / "graphify-out"
        graph_dir.mkdir()
        _write_file(tmp_path, "a.py")
        n1 = {"id": "foo", "label": "foo", "source_file": "a.py", "source_location": "a.py:L1"}
        _make_graph_json(graph_dir, [n1])

        stats = enrich(tmp_path, use_git=True, quiet=True)
        data = json.loads(graph_dir.joinpath("graph.json").read_text(encoding="utf-8"))
        assert data["nodes"][0]["file_mtime"] is not None
        assert stats["nodes_enriched"] == 1
        git_source.git_available.cache_clear()
