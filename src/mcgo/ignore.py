""".mcgoignore pattern engine compatible with gitignore syntax subset."""

from __future__ import annotations

import fnmatch
import os
import re
from pathlib import Path
from typing import Optional


class IgnoreRules:
    """Parses .mcgoignore files and matches paths against gitignore-style rules."""

    def __init__(self, ignore_file_path: Optional[str], base_dir: str):
        self._rules: list[tuple[re.Pattern, bool]] = []  # (pattern, is_negation)
        self._base_dir = base_dir
        if ignore_file_path:
            self._load(ignore_file_path)

    def _load(self, path: str) -> None:
        p = Path(path)
        if not p.exists():
            return
        ignore_dir = str(p.parent)
        lines = p.read_text(encoding="utf-8").splitlines()
        for line in lines:
            line = line.rstrip("\r\n")
            # Strip trailing whitespace, skip empty lines and comments
            stripped = line.rstrip()
            if not stripped or stripped.startswith("#"):
                continue

            negation = False
            pattern_str = stripped

            if pattern_str.startswith("!"):
                negation = True
                pattern_str = pattern_str[1:]

            # Handle trailing slash => directory only (we'll check is_dir at match time)
            dir_only = False
            if pattern_str.endswith("/"):
                dir_only = True
                pattern_str = pattern_str[:-1]

            # Build regex
            regex = self._pattern_to_regex(pattern_str, ignore_dir, dir_only)
            self._rules.append((regex, negation))

    def _pattern_to_regex(self, pattern: str, base_dir: str, dir_only: bool) -> re.Pattern:
        """Convert a gitignore pattern to a compiled regex."""
        anchored = pattern.startswith("/")
        if anchored:
            pattern = pattern[1:]

        pattern = pattern.replace("\\", "/")
        parts = pattern.split("/")

        result: list[str] = []
        for i, part in enumerate(parts):
            if part == "**":
                if i == 0:
                    # Leading **/: match optional directory prefix
                    result.append(r"(?:.*/)?")
                elif i == len(parts) - 1:
                    # Trailing /**: match optional trailing path
                    result.append(r"(?:/.*)?")
                else:
                    # ** between parts: match zero or more directory levels
                    # Includes the leading / so a/**/b correctly matches a/b too
                    result.append(r"/(?:[^/]*/)*")
            else:
                if i > 0 and parts[i - 1] != "**":
                    result.append("/")
                if "**" in part:
                    result.append(re.escape(part).replace(r"\*\*", r".*"))
                else:
                    result.append(self._glob_to_regex(part))

        full_pattern = "".join(result)

        if pattern == "**":
            full_pattern = r".*"

        if anchored:
            full_pattern = "^" + full_pattern
        else:
            full_pattern = r"(?:^|.*/)" + full_pattern

        if dir_only:
            full_pattern += r"(?:/.*)?$"
        else:
            full_pattern += r"$"

        return re.compile(full_pattern)

    @staticmethod
    def _glob_to_regex(glob: str) -> str:
        """Convert a single glob component (no slashes) to a regex fragment."""
        result = []
        i = 0
        while i < len(glob):
            c = glob[i]
            if c == "*":
                # Check for character class like *.[ch]
                result.append(r"[^/]*")
            elif c == "?":
                result.append(r"[^/]")
            elif c == "[":
                j = i + 1
                if j < len(glob) and glob[j] == "]":
                    j += 1
                while j < len(glob) and glob[j] != "]":
                    j += 1
                if j >= len(glob):
                    result.append(re.escape("["))
                else:
                    # Copy the bracket expression as-is
                    bracket = glob[i:j + 1]
                    result.append(re.escape(bracket).replace(r"\[", "[").replace(r"\]", "]"))
                    i = j
            else:
                result.append(re.escape(c))
            i += 1
        return "".join(result)

    def is_ignored(self, relative_path: str, is_dir: bool = False) -> bool:
        """Check whether a relative path should be ignored.
        relative_path uses forward slashes and is relative to base_dir.
        """
        ignored = False
        for regex, negation in self._rules:
            if regex.match(relative_path):
                ignored = not negation
        return ignored
