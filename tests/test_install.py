"""Tests for graphify_temporal install (auto-client detection + injection)."""

import json
from pathlib import Path

import pytest

from graphify_temporal.install import (
    detect,
    install,
    uninstall,
    _replace_block,
    _append_block,
    _inject_block,
    _remove_block,
    _INSTRUCTION_BLOCK,
    _SKILL_TEMPLATE,
    _SKILL_PATHS,
    _SKILL_VERSION_MARKER,
)


# ---------------------------------------------------------------------------
# block manipulation helpers (no filesystem)
# ---------------------------------------------------------------------------

_BLOCK = """\
## graphify-temporal

some instructions here
"""

_TEXT_BEFORE = "# My Project\n\nsome intro\n"
_TEXT_AFTER = "\n## Other section\nother content\n"


class TestBlockHelpers:
    def test_replace_block_middle(self):
        text = _TEXT_BEFORE + _BLOCK + _TEXT_AFTER
        result = _replace_block(text, replacement="[REPLACED]\n")
        expected = _TEXT_BEFORE + "[REPLACED]\n\n" + _TEXT_AFTER.lstrip("\n")
        assert result == expected

    def test_replace_block_at_end(self):
        text = _TEXT_BEFORE + _BLOCK
        result = _replace_block(text, replacement="[REPLACED]\n")
        assert result == _TEXT_BEFORE + "[REPLACED]\n"

    def test_replace_block_not_present(self):
        text = _TEXT_BEFORE + _TEXT_AFTER
        result = _replace_block(text, replacement="[REPLACED]\n")
        assert result == text  # unchanged

    def test_append_block_empty(self):
        result = _append_block("")
        assert result == _INSTRUCTION_BLOCK + "\n"

    def test_append_block_existing_content(self):
        result = _append_block("# Hello\n")
        assert result == "# Hello\n\n" + _INSTRUCTION_BLOCK + "\n"

    def test_append_block_already_ends_with_newline(self):
        result = _append_block("# Hello\n\n")
        assert result == "# Hello\n\n" + _INSTRUCTION_BLOCK + "\n"


# ---------------------------------------------------------------------------
# filesystem tests
# ---------------------------------------------------------------------------


class TestInstall:
    def test_detect_finds_clients(self, tmp_path):
        """Unique markers → distinct clients detected."""
        (tmp_path / "CLAUDE.md").write_text("# claude\n")
        (tmp_path / ".opencode").mkdir()
        (tmp_path / ".codex").mkdir()
        (tmp_path / "GEMINI.md").write_text("# gemini\n")
        (tmp_path / ".cursorrules").write_text("# cursor\n")
        (tmp_path / ".codebuddy").mkdir()
        (tmp_path / ".github").mkdir()
        (tmp_path / ".github" / "copilot-instructions.md").write_text("# copilot\n")
        (tmp_path / ".windsurf").mkdir()
        (tmp_path / ".aider").mkdir()
        (tmp_path / ".kilo").mkdir()
        (tmp_path / ".trae").mkdir()
        found = detect(tmp_path)
        assert "claude" in found
        assert "opencode" in found
        assert "codex" in found
        assert "gemini" in found
        assert "cursor" in found
        assert "codebuddy" in found
        assert "copilot" in found
        assert "windsurf" in found
        assert "aider" in found
        assert "kilo" in found
        assert "trae" in found

    def test_detect_empty_project(self, tmp_path):
        """No markers → empty list."""
        assert detect(tmp_path) == []

    def test_install_creates_block(self, tmp_path):
        """Install on project with CLAUDE.md creates the block."""
        (tmp_path / "CLAUDE.md").write_text("# existing\n")
        results = install(tmp_path, clients=["claude"])
        assert results == {"claude": True}
        text = (tmp_path / "CLAUDE.md").read_text()
        assert "## graphify-temporal" in text
        assert "graphify-temporal enrich" in text

    def test_install_idempotent(self, tmp_path):
        """Running install twice doesn't duplicate the block."""
        (tmp_path / "CLAUDE.md").write_text("# existing\n")
        install(tmp_path, clients=["claude"])
        install(tmp_path, clients=["claude"])
        text = (tmp_path / "CLAUDE.md").read_text()
        assert text.count("## graphify-temporal") == 1

    def test_install_updates_stale_block(self, tmp_path):
        """When the block already exists but differs, it gets replaced."""
        (tmp_path / "CLAUDE.md").write_text(
            "# existing\n\n## graphify-temporal\n\nold instructions\n\n## Other\n"
        )
        install(tmp_path, clients=["claude"])
        text = (tmp_path / "CLAUDE.md").read_text()
        assert "old instructions" not in text
        assert "graphify-temporal enrich" in text

    def test_install_creates_missing_file(self, tmp_path):
        """Install creates CLAUDE.md if it doesn't exist."""
        install(tmp_path, clients=["claude"])
        assert (tmp_path / "CLAUDE.md").exists()
        text = (tmp_path / "CLAUDE.md").read_text()
        assert "## graphify-temporal" in text

    def test_uninstall_removes_block(self, tmp_path):
        """Uninstall strips the graphify-temporal block."""
        (tmp_path / "CLAUDE.md").write_text(
            "# existing\n\n## graphify-temporal\n\nstuff\n\n## Other\nother\n"
        )
        results = uninstall(tmp_path, clients=["claude"])
        assert results == {"claude": True}
        text = (tmp_path / "CLAUDE.md").read_text()
        assert "## graphify-temporal" not in text
        assert "## Other" in text

    def test_uninstall_noop_when_no_block(self, tmp_path):
        """Uninstall succeeds even when block was never there."""
        (tmp_path / "CLAUDE.md").write_text("# just text\n")
        results = uninstall(tmp_path, clients=["claude"])
        assert results == {"claude": True}
        text = (tmp_path / "CLAUDE.md").read_text()
        assert text == "# just text\n"

    def test_uninstall_noop_when_no_file(self, tmp_path):
        """Uninstall succeeds even when instruction file doesn't exist."""
        results = uninstall(tmp_path, clients=["claude"])
        assert results == {"claude": True}

    def test_opencode_plugin_installed(self, tmp_path):
        """Install for opencode creates plugin.js and registers in opencode.json."""
        (tmp_path / ".opencode").mkdir()
        (tmp_path / ".opencode" / "opencode.json").write_text(
            json.dumps({"plugin": ["existing.js"]})
        )
        results = install(tmp_path, clients=["opencode"])
        assert results == {"opencode": True}
        plugin_path = tmp_path / ".opencode" / "plugins" / "graphify-temporal.js"
        assert plugin_path.exists()
        cfg = json.loads((tmp_path / ".opencode" / "opencode.json").read_text())
        assert ".opencode/plugins/graphify-temporal.js" in cfg["plugin"]

    def test_opencode_plugin_uninstalled(self, tmp_path):
        """Uninstall removes the plugin from opencode.json and deletes the JS."""
        (tmp_path / ".opencode").mkdir()
        plugins_dir = tmp_path / ".opencode" / "plugins"
        plugins_dir.mkdir(parents=True)
        (plugins_dir / "graphify-temporal.js").write_text("// plugin\n")
        (tmp_path / ".opencode" / "opencode.json").write_text(
            json.dumps({"plugin": [".opencode/plugins/graphify-temporal.js"]})
        )
        results = uninstall(tmp_path, clients=["opencode"])
        assert results == {"opencode": True}
        assert not (plugins_dir / "graphify-temporal.js").exists()
        cfg = json.loads((tmp_path / ".opencode" / "opencode.json").read_text())
        assert ".opencode/plugins/graphify-temporal.js" not in cfg["plugin"]

    def test_cursor_writes_to_rules_mdc(self, tmp_path):
        """Cursor install writes to .cursor/rules/graphify-temporal.mdc."""
        (tmp_path / ".cursor").mkdir()
        results = install(tmp_path, clients=["cursor"])
        assert results == {"cursor": True}
        path = tmp_path / ".cursor" / "rules" / "graphify-temporal.mdc"
        assert path.exists()
        assert "## graphify-temporal" in path.read_text()

    def test_windsurf_writes_to_rules_md(self, tmp_path):
        """Windsurf install writes to .windsurf/rules/graphify-temporal.md."""
        (tmp_path / ".windsurf").mkdir()
        results = install(tmp_path, clients=["windsurf"])
        assert results == {"windsurf": True}
        path = tmp_path / ".windsurf" / "rules" / "graphify-temporal.md"
        assert path.exists()
        assert "## graphify-temporal" in path.read_text()

    def test_level1_heading_preserved_on_uninstall(self, tmp_path):
        """Uninstall preserves content after a level-1 (#) heading."""
        (tmp_path / "CLAUDE.md").write_text(
            "# Top\n\n## graphify-temporal\n\nstuff\n\n# Bottom\n\nend\n"
        )
        uninstall(tmp_path, clients=["claude"])
        text = (tmp_path / "CLAUDE.md").read_text()
        assert "## graphify-temporal" not in text
        assert "# Bottom" in text
        assert "end" in text

    def test_malformed_plugin_key_handled(self, tmp_path):
        """opencode.json with plugin: 'string' → install doesn't crash."""
        (tmp_path / ".opencode").mkdir()
        (tmp_path / ".opencode" / "opencode.json").write_text('{"plugin": "not-a-list"}')
        results = install(tmp_path, clients=["opencode"])
        assert results == {"opencode": True}

    def test_malformed_plugin_key_uninstall_handled(self, tmp_path):
        """opencode.json with plugin: 42 → uninstall doesn't crash."""
        (tmp_path / ".opencode").mkdir()
        (tmp_path / ".opencode" / "opencode.json").write_text('{"plugin": 42}')
        results = uninstall(tmp_path, clients=["opencode"])
        assert results == {"opencode": True}

    def test_install_cli_help(self):
        """CLI install --help doesn't crash."""
        import subprocess, sys
        result = subprocess.run(
            [sys.executable, "-m", "graphify_temporal", "install", "--help"],
            capture_output=True, text=True,
        )
        assert result.returncode == 0
        assert "--platform" in result.stdout

    def test_uninstall_cli_help(self):
        """CLI uninstall --help doesn't crash."""
        import subprocess, sys
        result = subprocess.run(
            [sys.executable, "-m", "graphify_temporal", "uninstall", "--help"],
            capture_output=True, text=True,
        )
        assert result.returncode == 0
        assert "--platform" in result.stdout

    def test_instruction_block_mentions_git_flag(self):
        """Regression guard: a previous drift left --git undocumented in the
        installer's template while it was already documented in the real
        AGENTS.md — a fresh install would have silently overwritten it."""
        assert "--git" in _INSTRUCTION_BLOCK

    def test_instruction_block_mentions_impact(self):
        """The impact subcommand must be documented so agents know to reach
        for it proactively during debugging (no runtime hook exists for
        most clients — this prose is the only lever)."""
        assert "graphify-temporal impact" in _INSTRUCTION_BLOCK


# ---------------------------------------------------------------------------
# skill installation (OpenCode + Claude Code)
# ---------------------------------------------------------------------------


class TestSkillInstall:
    def _skill_dir(self, root: Path, client: str) -> Path:
        return root / _SKILL_PATHS[client]

    @pytest.mark.parametrize("client", ["opencode", "claude"])
    def test_skill_installed(self, tmp_path, client):
        """Install writes SKILL.md + version marker into the client dir."""
        results = install(tmp_path, clients=[client])
        assert results == {client: True}
        skill_dir = self._skill_dir(tmp_path, client)
        skill_file = skill_dir / "SKILL.md"
        assert skill_file.exists()
        assert skill_file.read_text() == _SKILL_TEMPLATE
        assert (skill_dir / _SKILL_VERSION_MARKER).exists()

    def test_skill_install_idempotent(self, tmp_path):
        """Second install with same version leaves the skill untouched."""
        (tmp_path / ".opencode").mkdir()
        install(tmp_path, clients=["opencode"])
        skill_file = self._skill_dir(tmp_path, "opencode") / "SKILL.md"
        before = skill_file.read_text()
        install(tmp_path, clients=["opencode"])
        assert skill_file.read_text() == before

    def test_skill_reinstalled_on_version_change(self, tmp_path):
        """A version marker mismatch rewrites stale skill content."""
        (tmp_path / ".opencode").mkdir()
        install(tmp_path, clients=["opencode"])
        skill_dir = self._skill_dir(tmp_path, "opencode")
        (skill_dir / _SKILL_VERSION_MARKER).write_text("0.0.0\n")
        (skill_dir / "SKILL.md").write_text("stale skill content\n")
        install(tmp_path, clients=["opencode"])
        text = (skill_dir / "SKILL.md").read_text()
        assert text == _SKILL_TEMPLATE
        assert "stale skill content" not in text

    @pytest.mark.parametrize("client", ["opencode", "claude"])
    def test_uninstall_removes_skill(self, tmp_path, client):
        """Uninstall deletes the skill dir for the client."""
        install(tmp_path, clients=[client])
        uninstall(tmp_path, clients=[client])
        assert not self._skill_dir(tmp_path, client).exists()

    def test_uninstall_skill_noop_when_absent(self, tmp_path):
        """Uninstall succeeds when the skill was never installed."""
        (tmp_path / ".opencode").mkdir()
        results = uninstall(tmp_path, clients=["opencode"])
        assert results == {"opencode": True}

    def test_uninstall_preserves_user_files_in_skill_dir(self, tmp_path):
        """Skill dir with foreign files is not deleted on uninstall."""
        (tmp_path / ".opencode").mkdir()
        install(tmp_path, clients=["opencode"])
        skill_dir = self._skill_dir(tmp_path, "opencode")
        (skill_dir / "user-notes.md").write_text("# mine\n")
        uninstall(tmp_path, clients=["opencode"])
        assert skill_dir.exists()
        assert (skill_dir / "user-notes.md").exists()
        assert not (skill_dir / "SKILL.md").exists()
        assert not (skill_dir / _SKILL_VERSION_MARKER).exists()

    def test_clients_without_skill_support_untouched(self, tmp_path):
        """Gemini gets the instruction block but no skill directory."""
        (tmp_path / "GEMINI.md").write_text("# g\n")
        results = install(tmp_path, clients=["gemini"])
        assert results == {"gemini": True}
        assert not (tmp_path / ".claude" / "skills").exists()
        assert not (tmp_path / ".opencode" / "skills").exists()
        assert "## graphify-temporal" in (tmp_path / "GEMINI.md").read_text()

    def test_skill_template_covers_all_subcommands(self):
        """Regression guard: every subcommand must be documented in the skill
        — the skill description is the only trigger surface agents see."""
        for cmd in ("enrich", "query", "timeline", "stats", "impact", "install"):
            assert f"graphify-temporal {cmd}" in _SKILL_TEMPLATE

    def test_skill_template_has_frontmatter(self):
        """The skill must have name + description frontmatter to be
        discoverable by both OpenCode and Claude Code."""
        assert _SKILL_TEMPLATE.startswith("---\n")
        assert "name: graphify-temporal" in _SKILL_TEMPLATE
        assert "description:" in _SKILL_TEMPLATE
