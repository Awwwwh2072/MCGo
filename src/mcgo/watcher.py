"""Filesystem watcher using watchdog with debounced callback."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Callable, Optional

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from .ignore import IgnoreRules


class DirectoryWatcher:
    """Watches a directory tree for changes and invokes a callback after a debounce period."""

    def __init__(
        self,
        path: str,
        callback: Callable[[], None],
        ignore_rules: Optional[IgnoreRules] = None,
        debounce_seconds: float = 1.0,
    ):
        self._path = str(Path(path).resolve())
        self._callback = callback
        self._ignore_rules = ignore_rules
        self._debounce_seconds = debounce_seconds
        self._observer: Optional[Observer] = None
        self._timer: Optional[threading.Timer] = None
        self._lock = threading.Lock()

    def start(self) -> None:
        handler = _DebouncedHandler(self._on_event, self._ignore_rules, self._path)
        self._observer = Observer()
        self._observer.schedule(handler, self._path, recursive=True)
        self._observer.start()

    def stop(self) -> None:
        if self._observer:
            self._observer.stop()
            self._observer.join()
            self._observer = None
        with self._lock:
            if self._timer:
                self._timer.cancel()
                self._timer = None

    def _on_event(self) -> None:
        """Called by the handler when a filesystem change is detected."""
        with self._lock:
            if self._timer:
                self._timer.cancel()
            self._timer = threading.Timer(self._debounce_seconds, self._fire)
            self._timer.start()

    def _fire(self) -> None:
        """Fire the actual callback after debounce."""
        with self._lock:
            self._timer = None
        self._callback()


class _DebouncedHandler(FileSystemEventHandler):
    """Watchdog event handler that filters ignored paths and debounces events."""

    def __init__(self, on_event: Callable[[], None], ignore_rules: Optional[IgnoreRules], base_path: str):
        self._on_event = on_event
        self._ignore_rules = ignore_rules
        self._base_path = str(Path(base_path).resolve())

    def dispatch(self, event: FileSystemEvent) -> None:
        # Filter ignored paths to avoid unnecessary tree rebuilds
        if self._ignore_rules:
            path = getattr(event, 'src_path', '') or getattr(event, 'dest_path', '') or ''
            if path:
                try:
                    rel = str(Path(path).relative_to(self._base_path))
                    if self._ignore_rules.is_ignored(rel, is_dir=event.is_directory):
                        return
                except ValueError:
                    pass
        self._on_event()

    def on_created(self, event: FileSystemEvent) -> None:
        self.dispatch(event)

    def on_modified(self, event: FileSystemEvent) -> None:
        self.dispatch(event)

    def on_deleted(self, event: FileSystemEvent) -> None:
        self.dispatch(event)

    def on_moved(self, event: FileSystemEvent) -> None:
        self.dispatch(event)
