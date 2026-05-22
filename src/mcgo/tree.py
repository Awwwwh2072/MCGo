"""File tree scanning, JSON serialization, and client-side diff computation."""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any, Optional

from .ignore import IgnoreRules

# Extensions that are already efficiently compressed – skip zlib re-compression
_COMPRESSED_EXTENSIONS: set[str] = {
    ".zip", ".jar", ".war", ".ear", ".gz", ".bz2", ".xz", ".7z", ".rar",
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".webm",
    ".mp4", ".mp3", ".ogg", ".flac", ".avi", ".mkv",
    ".pdf", ".docx", ".xlsx", ".pptx", ".odt", ".ods",
    ".tgz", ".tar.gz", ".tar.bz2", ".tar.xz",
}

_CHUNK_SIZE = 65536  # 64KB read chunks for hashing

# Server-side `clientmods/` maps to client-side `mods/` for diff and download paths.
_REMOTE_CLIENTMODS_PREFIX = "clientmods/"
_LOCAL_MODS_PREFIX = "mods/"


class FileTree:
    """Scans a directory and produces/comparses JSON file trees."""

    def __init__(self, base_path: str):
        self._base_path = Path(base_path).resolve()

    @property
    def base_path(self) -> str:
        return str(self._base_path)

    def scan(self, ignore_rules: Optional[IgnoreRules] = None) -> dict[str, Any]:
        """Walk the base directory and build a file tree dict."""
        files: dict[str, dict] = {}
        directories: list[str] = []

        for dirpath, dirnames, filenames in os.walk(self._base_path):
            # Compute relative path from base
            rel_dir = str(Path(dirpath).relative_to(self._base_path))
            if rel_dir == ".":
                rel_dir = ""

            # Filter directories in-place for ignored dirs
            kept_dirnames = []
            for d in dirnames:
                dir_rel = f"{rel_dir}/{d}".lstrip("/") if rel_dir else d
                if ignore_rules and ignore_rules.is_ignored(dir_rel, is_dir=True):
                    continue
                kept_dirnames.append(d)
                directories.append(dir_rel)
            dirnames[:] = kept_dirnames

            for fname in sorted(filenames):
                file_rel = f"{rel_dir}/{fname}".lstrip("/") if rel_dir else fname
                if ignore_rules and ignore_rules.is_ignored(file_rel, is_dir=False):
                    continue

                full_path = str(Path(dirpath) / fname)
                try:
                    stat = os.stat(full_path)
                    sha256_hash = self._hash_file(full_path)
                    is_binary = self._detect_binary(full_path)
                    ext = os.path.splitext(fname)[1].lower()
                    should_compress = ext not in _COMPRESSED_EXTENSIONS and not is_binary_fallback(full_path)

                    files[file_rel] = {
                        "size": stat.st_size,
                        "mtime": stat.st_mtime,
                        "sha256": sha256_hash,
                        "is_binary": is_binary,
                        "should_compress": should_compress,
                    }
                except (OSError, PermissionError) as e:
                    # Skip files we can't read
                    files[file_rel] = {
                        "size": 0,
                        "mtime": 0,
                        "sha256": "",
                        "is_binary": False,
                        "should_compress": False,
                        "error": str(e),
                    }

        return {
            "version": 1,
            "timestamp": time.time(),
            "base_path": str(self._base_path),
            "files": dict(sorted(files.items())),
            "directories": sorted(directories),
        }

    def to_json(self, tree: dict) -> str:
        return json.dumps(tree, ensure_ascii=False, indent=2)

    @staticmethod
    def from_json(json_str: str) -> dict:
        return json.loads(json_str)

    @staticmethod
    def map_remote_to_local(remote_path: str) -> str:
        """Map a path from the server file tree to the client local relative path."""
        if remote_path.startswith(_REMOTE_CLIENTMODS_PREFIX):
            return _LOCAL_MODS_PREFIX + remote_path[len(_REMOTE_CLIENTMODS_PREFIX):]
        return remote_path

    @staticmethod
    def diff(local_tree: dict, remote_tree: dict) -> list[dict]:
        """Compare local tree against remote tree.
        Returns a list of entries that need to be downloaded:
        [{"server_path": str, "local_path": str, "reason": "missing"|"changed"}, ...]
        """
        to_fetch: list[dict] = []
        remote_files: dict = remote_tree.get("files", {})

        for path, remote_info in remote_files.items():
            local_path = FileTree.map_remote_to_local(path)
            local_info = local_tree.get("files", {}).get(local_path)
            if local_info is None:
                to_fetch.append({"server_path": path, "local_path": local_path, "reason": "missing"})
            elif local_info.get("sha256") != remote_info.get("sha256"):
                to_fetch.append({"server_path": path, "local_path": local_path, "reason": "changed"})

        return to_fetch

    def _hash_file(self, filepath: str) -> str:
        """Compute SHA-256 hash of a file."""
        sha = hashlib.sha256()
        with open(filepath, "rb") as f:
            while True:
                chunk = f.read(_CHUNK_SIZE)
                if not chunk:
                    break
                sha.update(chunk)
        return sha.hexdigest()

    def _detect_binary(self, filepath: str) -> bool:
        """Check if a file is binary by scanning for null bytes in the first 512 bytes."""
        try:
            with open(filepath, "rb") as f:
                head = f.read(512)
            return b"\x00" in head
        except (OSError, PermissionError):
            return False


def is_binary_fallback(filepath: str) -> bool:
    """Fallback binary check for files we couldn't read during tree scan."""
    return Path(filepath).suffix.lower() in _COMPRESSED_EXTENSIONS
