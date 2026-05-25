"""Tests for mcgo.tree – file tree scanning, hashing, diff, path mapping."""

import json

import pytest

from mcgo.ignore import IgnoreRules
from mcgo.tree import FileTree, is_binary_fallback


# ---------------------------------------------------------------------------
# Path mapping
# ---------------------------------------------------------------------------

class TestMapRemoteToLocal:
    def test_clientmods_maps_to_mods(self):
        assert FileTree.map_remote_to_local("clientmods/foo/bar.lua") == "mods/foo/bar.lua"

    def test_clientmods_root_file(self):
        assert FileTree.map_remote_to_local("clientmods/init.lua") == "mods/init.lua"

    def test_other_path_unchanged(self):
        assert FileTree.map_remote_to_local("config/settings.json") == "config/settings.json"

    def test_empty_path(self):
        assert FileTree.map_remote_to_local("") == ""


# ---------------------------------------------------------------------------
# Base path
# ---------------------------------------------------------------------------

class TestBasePath:
    def test_resolves_relative(self):
        ft = FileTree(".")
        assert ft.base_path.endswith(".") is False  # resolved
        import os
        assert os.path.isabs(ft.base_path)


# ---------------------------------------------------------------------------
# Scan
# ---------------------------------------------------------------------------

class TestScan:
    def test_empty_directory(self, tmp_path):
        ft = FileTree(str(tmp_path))
        result = ft.scan()
        assert result["version"] == 1
        assert result["files"] == {}
        assert result["directories"] == []

    def test_single_file(self, tmp_path):
        f = tmp_path / "hello.txt"
        f.write_text("Hello, world!", encoding="utf-8")
        ft = FileTree(str(tmp_path))
        result = ft.scan()
        assert "hello.txt" in result["files"]
        info = result["files"]["hello.txt"]
        assert "size" in info
        assert "mtime" in info
        assert "sha256" in info
        assert "is_binary" in info
        assert "should_compress" in info
        assert info["size"] == len("Hello, world!")
        assert info["is_binary"] is False

    def test_nested_directories(self, tmp_path):
        d = tmp_path / "sub"
        d.mkdir()
        (d / "nested.txt").write_text("nested", encoding="utf-8")
        ft = FileTree(str(tmp_path))
        result = ft.scan()
        assert "sub" in result["directories"]
        assert "sub/nested.txt" in result["files"]

    def test_ignore_rules_filter(self, tmp_path):
        f1 = tmp_path / "keep.txt"
        f1.write_text("keep", encoding="utf-8")
        f2 = tmp_path / "skip.tmp"
        f2.write_text("skip", encoding="utf-8")
        ignore = tmp_path / ".mcgoignore"
        ignore.write_text("*.tmp\n", encoding="utf-8")
        rules = IgnoreRules(str(ignore), str(tmp_path))
        ft = FileTree(str(tmp_path))
        result = ft.scan(ignore_rules=rules)
        assert "keep.txt" in result["files"]
        assert "skip.tmp" not in result["files"]

    def test_ignore_directories(self, tmp_path):
        d = tmp_path / "build"
        d.mkdir()
        (d / "output.o").write_text("obj", encoding="utf-8")
        ignore = tmp_path / ".mcgoignore"
        ignore.write_text("build/\n", encoding="utf-8")
        rules = IgnoreRules(str(ignore), str(tmp_path))
        ft = FileTree(str(tmp_path))
        result = ft.scan(ignore_rules=rules)
        assert "build" not in result["directories"]
        assert "build/output.o" not in result["files"]


# ---------------------------------------------------------------------------
# SHA-256 hashing
# ---------------------------------------------------------------------------

class TestHashFile:
    def test_known_value(self, tmp_path):
        import hashlib
        content = b"test data for hashing"
        expected = hashlib.sha256(content).hexdigest()
        f = tmp_path / "data.bin"
        f.write_bytes(content)
        ft = FileTree(str(tmp_path))
        assert ft._hash_file(str(f)) == expected

    def test_large_file(self, tmp_path):
        content = b"A" * 1_000_000
        f = tmp_path / "large.bin"
        f.write_bytes(content)
        ft = FileTree(str(tmp_path))
        result = ft._hash_file(str(f))
        assert len(result) == 64


# ---------------------------------------------------------------------------
# Binary detection
# ---------------------------------------------------------------------------

class TestDetectBinary:
    def test_with_null_bytes(self, tmp_path):
        f = tmp_path / "binary.bin"
        f.write_bytes(b"text\x00binary")
        ft = FileTree(str(tmp_path))
        assert ft._detect_binary(str(f)) is True

    def test_pure_text(self, tmp_path):
        f = tmp_path / "text.txt"
        f.write_text("Hello, World!", encoding="utf-8")
        ft = FileTree(str(tmp_path))
        assert ft._detect_binary(str(f)) is False

    def test_empty_file(self, tmp_path):
        f = tmp_path / "empty.bin"
        f.write_bytes(b"")
        ft = FileTree(str(tmp_path))
        assert ft._detect_binary(str(f)) is False


# ---------------------------------------------------------------------------
# is_binary_fallback
# ---------------------------------------------------------------------------

class TestIsBinaryFallback:
    def test_known_compressed_ext(self):
        assert is_binary_fallback("archive.zip") is True
        assert is_binary_fallback("image.png") is True
        assert is_binary_fallback("doc.pdf") is True

    def test_unknown_ext(self):
        assert is_binary_fallback("script.lua") is False
        assert is_binary_fallback("config.toml") is False

    def test_case_insensitive(self):
        assert is_binary_fallback("IMAGE.PNG") is True


# ---------------------------------------------------------------------------
# JSON serialization
# ---------------------------------------------------------------------------

class TestJson:
    def test_roundtrip(self, tmp_path):
        ft = FileTree(str(tmp_path))
        (tmp_path / "a.txt").write_text("aaa", encoding="utf-8")
        tree = ft.scan()
        json_str = ft.to_json(tree)
        parsed = FileTree.from_json(json_str)
        assert parsed == tree

    def test_preserves_structure(self):
        tree = {"version": 1, "files": {"a.txt": {"sha256": "abc123"}}}
        json_str = FileTree.to_json(FileTree("."), tree)
        assert "sha256" in json_str


# ---------------------------------------------------------------------------
# Diff computation
# ---------------------------------------------------------------------------

class TestDiff:
    def test_both_empty(self):
        assert FileTree.diff({"files": {}}, {"files": {}}) == []

    def test_all_missing(self):
        remote = {"files": {"a.txt": {"sha256": "aaa"}}}
        local = {"files": {}}
        result = FileTree.diff(local, remote)
        assert len(result) == 1
        assert result[0]["reason"] == "missing"
        assert result[0]["server_path"] == "a.txt"

    def test_all_matching(self):
        tree = {"files": {"a.txt": {"sha256": "abc123"}}}
        assert FileTree.diff(tree, tree) == []

    def test_changed_file(self):
        remote = {"files": {"a.txt": {"sha256": "aaa"}}}
        local = {"files": {"a.txt": {"sha256": "bbb"}}}
        result = FileTree.diff(local, remote)
        assert len(result) == 1
        assert result[0]["reason"] == "changed"

    def test_mixed(self):
        remote = {
            "files": {
                "a.txt": {"sha256": "aaa"},
                "b.txt": {"sha256": "bbb"},
                "c.txt": {"sha256": "ccc"},
            }
        }
        local = {
            "files": {
                "a.txt": {"sha256": "aaa"},  # matching
                "b.txt": {"sha256": "xxx"},  # changed
                # c.txt missing
            }
        }
        result = FileTree.diff(local, remote)
        assert len(result) == 2
        reasons = {r["server_path"]: r["reason"] for r in result}
        assert reasons["b.txt"] == "changed"
        assert reasons["c.txt"] == "missing"

    def test_path_mapping(self):
        """Remote clientmods/ path should be mapped to mods/ for local comparison."""
        remote = {"files": {"clientmods/data.lua": {"sha256": "abc"}}}
        local = {"files": {"mods/data.lua": {"sha256": "abc"}}}
        assert FileTree.diff(local, remote) == []

    def test_path_mapping_missing(self):
        remote = {"files": {"clientmods/data.lua": {"sha256": "abc"}}}
        local = {"files": {}}
        result = FileTree.diff(local, remote)
        assert len(result) == 1
        assert result[0]["local_path"] == "mods/data.lua"

    def test_ignored_by_rules(self, tmp_path):
        remote = {"files": {"skip.tmp": {"sha256": "abc"}}}
        local = {"files": {}}
        ignore = tmp_path / ".mcgoignore"
        ignore.write_text("*.tmp\n", encoding="utf-8")
        rules = IgnoreRules(str(ignore), str(tmp_path))
        assert FileTree.diff(local, remote, ignore_rules=rules) == []
