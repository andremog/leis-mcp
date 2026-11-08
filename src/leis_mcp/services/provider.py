"""Hands out the `LegislationService` once the index exists, preparing a missing index in the background.

Lets the MCP server answer the handshake right away: the first run downloads (or builds) a ~350 MB index,
and clients such as Claude Desktop give up on servers that take minutes to start.
"""

import logging
import sqlite3
import threading
import time
from collections.abc import Callable

from .. import messages
from ..config import Settings
from ..errors import IndexMissingError, IndexPreparingError, SnapshotError
from .legislation import LegislationService

log = logging.getLogger(__name__)


class ServiceProvider:
    RETRY_AFTER = 60.0  # seconds; offline, every tool call would otherwise restart a ~100 MB download

    def __init__(self, settings: Settings, factory: Callable[[], LegislationService], prepare: Callable[[], object]):
        self._settings = settings
        self._factory = factory
        self._prepare = prepare
        self._service: LegislationService | None = None
        self._error: Exception | None = None
        self._failed_at = 0.0
        self._worker: threading.Thread | None = None
        self._lock = threading.Lock()

    def start(self) -> None:
        """Starts preparing the index in the background when it is missing."""
        with self._lock:
            self._start_worker()

    def get(self) -> LegislationService:
        """The service, or `IndexPreparingError` while the index is not ready yet."""
        with self._lock:
            if self._service is None:
                self._service = self._create()
            return self._service

    def _create(self) -> LegislationService:
        repair = False
        if self._settings.db_path.exists():  # the index is swapped in atomically, so it is complete when present
            try:
                return self._factory()
            except (sqlite3.DatabaseError, IndexMissingError) as exc:  # damaged on disk: prepare a new one
                log.warning("Local index unreadable (%s); preparing a new one in the background", type(exc).__name__)
                repair = True
        if self._worker is not None and self._worker.is_alive():
            raise IndexPreparingError(messages.INDEX_PREPARING)
        error = self._error
        if error is not None and time.monotonic() - self._failed_at < self.RETRY_AFTER:
            raise IndexPreparingError(messages.INDEX_FAILED.format(error=self._public(error))) from error
        self._error = None
        self._start_worker(repair)  # first attempt, or a retry once RETRY_AFTER has passed
        raise IndexPreparingError(messages.INDEX_PREPARING)

    @staticmethod
    def _public(error: Exception) -> str:
        """Snapshot messages are written for users; other failures (git stderr, paths) stay in the server log."""
        return str(error) if isinstance(error, SnapshotError) else messages.INDEX_FAILED_DETAILS_IN_LOG

    def _start_worker(self, repair: bool = False) -> None:
        busy = self._worker is not None and self._worker.is_alive()
        if busy or (self._settings.db_path.exists() and not repair):
            return
        self._worker = threading.Thread(target=self._run, name="index-prepare", daemon=True)
        self._worker.start()

    def _run(self) -> None:
        log.info("Index missing: preparing it in the background (first run only)...")
        try:
            self._prepare()
        except Exception as exc:  # noqa: BLE001 - reported by the next tool call
            log.exception("Index preparation failed")
            with self._lock:
                self._error = exc
                self._failed_at = time.monotonic()
