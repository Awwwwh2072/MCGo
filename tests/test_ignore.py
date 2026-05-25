"""Tests for mcgo.ignore – .mcgoignore pattern engine and built-in role rules."""

import pytest

from mcgo.ignore import IgnoreRules


# ---------------------------------------------------------------------------
# Built-in role-based ignore
# ---------------------------------------------------------------------------

class TestBuiltinIgnore:
    def test_server_ignores_server_prefix_in_mods(self):
        rules = IgnoreRules(None, "/base", role="server")
        assert rules.is_ignored("mods/server-config.lua") is True

    def test_server_does_not_ignore_other_files_in_mods(self):
        rules = IgnoreRules(None, "/base", role="server")
        assert rules.is_ignored("mods/shared-data.lua") is False

    def test_client_ignores_client_prefix_in_mods(self):
        rules = IgnoreRules(None, "/base", role="client")
        assert rules.is_ignored("mods/client-config.lua") is True

    def test_client_does_not_ignore_other_files_in_mods(self):
        rules = IgnoreRules(None, "/base", role="client")
        assert rules.is_ignored("mods/shared-data.lua") is False

    def test_not_in_mods_directory(self):
        rules = IgnoreRules(None, "/base", role="server")
        assert rules.is_ignored("other/server-config.lua") is False

    def test_directories_never_builtin_ignored(self):
        rules = IgnoreRules(None, "/base", role="server")
        assert rules.is_ignored("mods/server-folder", is_dir=True) is False

    def test_no_role_ignores_nothing_builtin(self):
        rules = IgnoreRules(None, "/base", role=None)
        assert rules.is_ignored("mods/server-config.lua") is False


# ---------------------------------------------------------------------------
# Pattern matching (via public is_ignored with temp ignore file)
# ---------------------------------------------------------------------------

def _make_rules(tmp_path, lines: str, role=None):
    """Helper: write an ignore file and return IgnoreRules."""
    ignore_path = tmp_path / ".mcgoignore"
    ignore_path.write_text(lines, encoding="utf-8")
    return IgnoreRules(str(ignore_path), str(tmp_path), role=role)


class TestPatternMatching:
    def test_simple_glob_star(self, tmp_path):
        rules = _make_rules(tmp_path, "*.log\n")
        assert rules.is_ignored("error.log") is True
        assert rules.is_ignored("error.txt") is False

    def test_question_mark(self, tmp_path):
        rules = _make_rules(tmp_path, "file?.txt\n")
        assert rules.is_ignored("file1.txt") is True
        assert rules.is_ignored("file10.txt") is False

    def test_character_class(self, tmp_path):
        rules = _make_rules(tmp_path, "file[abc].txt\n")
        assert rules.is_ignored("filea.txt") is True
        assert rules.is_ignored("filed.txt") is False

    def test_anchored_pattern_root_only(self, tmp_path):
        rules = _make_rules(tmp_path, "/config.toml\n")
        assert rules.is_ignored("config.toml") is True
        assert rules.is_ignored("sub/config.toml") is False

    def test_double_star_matches_directories(self, tmp_path):
        rules = _make_rules(tmp_path, "**/*.tmp\n")
        assert rules.is_ignored("a.tmp") is True
        assert rules.is_ignored("dir/a.tmp") is True
        assert rules.is_ignored("dir/sub/a.tmp") is True

    def test_double_star_middle(self, tmp_path):
        rules = _make_rules(tmp_path, "a/**/b.txt\n")
        assert rules.is_ignored("a/b.txt") is True
        assert rules.is_ignored("a/x/b.txt") is True
        assert rules.is_ignored("a/x/y/b.txt") is True
        assert rules.is_ignored("z/b.txt") is False

    def test_trailing_slash_directory_only(self, tmp_path):
        rules = _make_rules(tmp_path, "build/\n")
        assert rules.is_ignored("build/output.o") is True
        # Directory-only: this is checking files, should still match because
        # the trailing slash means "match anything inside the directory"
        pass

    def test_negation(self, tmp_path):
        rules = _make_rules(tmp_path, "*.log\n!important.log\n")
        assert rules.is_ignored("error.log") is True
        assert rules.is_ignored("important.log") is False

    def test_comment_lines_skipped(self, tmp_path):
        rules = _make_rules(tmp_path, "# This is a comment\n*.bak\n")
        assert rules.is_ignored("file.bak") is True

    def test_empty_lines_skipped(self, tmp_path):
        rules = _make_rules(tmp_path, "\n\n*.bak\n\n")
        assert rules.is_ignored("file.bak") is True

    def test_no_ignore_file(self):
        rules = IgnoreRules(None, "/base", role=None)
        assert rules.is_ignored("anything.txt") is False

    def test_missing_file_no_error(self, tmp_path):
        rules = IgnoreRules(str(tmp_path / "nonexistent.mcgoignore"), str(tmp_path))
        assert rules.is_ignored("anything.txt") is False

    def test_literal_pattern(self, tmp_path):
        rules = _make_rules(tmp_path, "secret.key\n")
        assert rules.is_ignored("secret.key") is True
        assert rules.is_ignored("other.key") is False

    def test_globstar_matches_everything(self, tmp_path):
        rules = _make_rules(tmp_path, "**\n!keep.txt\n")
        assert rules.is_ignored("anything.txt") is True
        assert rules.is_ignored("dir/file.txt") is True
        assert rules.is_ignored("keep.txt") is False


class TestRoleWithIgnoreFile:
    def test_user_rule_overrides_builtin(self, tmp_path):
        """User ! rule can un-ignore a server-specific file."""
        rules = _make_rules(tmp_path, "!server-keep.cfg\n", role="server")
        assert rules.is_ignored("mods/server-keep.cfg") is False

    def test_user_rule_adds_to_builtin(self, tmp_path):
        """User can add extra patterns on top of built-in."""
        rules = _make_rules(tmp_path, "*.tmp\n", role="server")
        assert rules.is_ignored("mods/server-config.lua") is True  # builtin
        assert rules.is_ignored("mods/temp.tmp") is True  # user rule
