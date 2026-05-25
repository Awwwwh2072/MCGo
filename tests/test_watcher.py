"""Tests for mcgo.watcher – DirectoryWatcher and DebouncedHandler."""

import threading
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from mcgo.ignore import IgnoreRules
from mcgo.watcher import DirectoryWatcher, _DebouncedHandler


# ---------------------------------------------------------------------------
# DebouncedHandler dispatch filtering
# ---------------------------------------------------------------------------

def _make_event(src_path="", dest_path="", is_directory=False):
    """Create a mock FileSystemEvent."""
    event = MagicMock()
    event.src_path = src_path
    event.dest_path = dest_path
    event.is_directory = is_directory
    return event


class TestDebouncedHandler:
    def test_dispatch_ignored_path(self, tmp_path):
        base = str(tmp_path)
        ignore = IgnoreRules(None, base, role=None)
        handler = _DebouncedHandler(MagicMock(), ignore, base)

        ignore.is_ignored = MagicMock(return_value=True)
        event = _make_event(src_path=str(Path(base) / "skip.tmp"))
        handler.dispatch(event)
        assert not handler._on_event.called

    def test_dispatch_non_ignored_path(self, tmp_path):
        base = str(tmp_path)
        ignore = IgnoreRules(None, base, role=None)
        handler = _DebouncedHandler(MagicMock(), ignore, base)

        ignore.is_ignored = MagicMock(return_value=False)
        event = _make_event(src_path=str(Path(base) / "keep.txt"))
        handler.dispatch(event)
        assert handler._on_event.called

    def test_dispatch_no_ignore_rules(self, tmp_path):
        base = str(tmp_path)
        handler = _DebouncedHandler(MagicMock(), None, base)
        event = _make_event(src_path=str(Path(base) / "any.txt"))
        handler.dispatch(event)
        assert handler._on_event.called

    def test_dispatch_no_path(self, tmp_path):
        base = str(tmp_path)
        handler = _DebouncedHandler(MagicMock(), None, base)
        event = _make_event(src_path="", dest_path="")
        handler.dispatch(event)
        assert handler._on_event.called

    def test_dispatch_uses_dest_path(self, tmp_path):
        base = str(tmp_path)
        ignore = IgnoreRules(None, base, role=None)
        handler = _DebouncedHandler(MagicMock(), ignore, base)
        ignore.is_ignored = MagicMock(return_value=False)
        event = _make_event(src_path="", dest_path=str(Path(base) / "moved.txt"))
        handler.dispatch(event)
        assert handler._on_event.called

    def test_on_created_dispatches(self, tmp_path):
        base = str(tmp_path)
        handler = _DebouncedHandler(MagicMock(), None, base)
        event = _make_event(src_path=str(Path(base) / "new.txt"))
        handler.on_created(event)
        assert handler._on_event.called

    def test_on_modified_dispatches(self, tmp_path):
        base = str(tmp_path)
        handler = _DebouncedHandler(MagicMock(), None, base)
        event = _make_event(src_path=str(Path(base) / "changed.txt"))
        handler.on_modified(event)
        assert handler._on_event.called

    def test_on_deleted_dispatches(self, tmp_path):
        base = str(tmp_path)
        handler = _DebouncedHandler(MagicMock(), None, base)
        event = _make_event(src_path=str(Path(base) / "removed.txt"))
        handler.on_deleted(event)
        assert handler._on_event.called

    def test_on_moved_dispatches(self, tmp_path):
        base = str(tmp_path)
        handler = _DebouncedHandler(MagicMock(), None, base)
        event = _make_event(
            src_path=str(Path(base) / "old.txt"),
            dest_path=str(Path(base) / "new.txt"),
        )
        handler.on_moved(event)
        assert handler._on_event.called

    def test_relative_path_not_under_base_does_not_crash(self, tmp_path):
        base = str(tmp_path)
        other = str(tmp_path / "other")
        Path(other).mkdir(exist_ok=True)
        ignore = IgnoreRules(None, base, role=None)
        callback = MagicMock()
        handler = _DebouncedHandler(callback, ignore, base)
        event = _make_event(src_path=str(Path(other) / "file.txt"))
        handler.dispatch(event)


# ---------------------------------------------------------------------------
# DirectoryWatcher lifecycle
# ---------------------------------------------------------------------------

class TestDirectoryWatcher:
    def test_start_stop(self, tmp_path):
        d = tmp_path / "watched"
        d.mkdir()
        callback = MagicMock()
        watcher = DirectoryWatcher(str(d), callback, debounce_seconds=0.1)
        watcher.start()
        time.sleep(0.05)
        watcher.stop()

    def test_stop_without_start(self, tmp_path):
        callback = MagicMock()
        watcher = DirectoryWatcher(str(tmp_path), callback)
        watcher.stop()  # no-op

    def test_callback_fires_after_debounce(self, tmp_path):
        d = tmp_path / "watched"
        d.mkdir()
        event = threading.Event()

        def cb():
            event.set()

        watcher = DirectoryWatcher(str(d), cb, debounce_seconds=0.1)
        watcher.start()
        time.sleep(0.1)

        (d / "new_file.txt").write_text("data", encoding="utf-8")
        assert event.wait(timeout=3.0)

        watcher.stop()
