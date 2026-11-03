"""Prebuilt index published by the `index` workflow, so a first run needs neither git nor a local build.

The release holds `index.json` (commit, built_at, norms, archive, sha256) and the gzip archive it names.
The archive is checked against the sha256 and its `meta.commit` before it is swapped in atomically.
"""

import gzip
import hashlib
import logging
import os
import shutil
import sqlite3
import time
import zlib
from contextlib import closing
from pathlib import Path
from typing import Any

import httpx

from .. import messages
from ..config import Settings
from ..errors import SnapshotError
from ..models import IndexStats

log = logging.getLogger(__name__)


class IndexSnapshot:
    MANIFEST = "index.json"
    _REQUIRED = ("commit", "archive", "sha256")
    _CHUNK = 1 << 20

    def __init__(self, settings: Settings, client: httpx.Client | None = None):
        self._settings = settings
        self._client = client or httpx.Client(timeout=60, follow_redirects=True, headers={"User-Agent": "leis-mcp"})

    def latest(self) -> dict[str, Any]:
        """Manifest of the snapshot currently published."""
        url = f"{self._settings.index_url}/{self.MANIFEST}"
        try:
            response = self._client.get(url)
            response.raise_for_status()
            manifest = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise SnapshotError(messages.SNAPSHOT_FAILED.format(url=url, details=exc)) from exc
        if not isinstance(manifest, dict) or not all(isinstance(manifest.get(key), str) for key in self._REQUIRED):
            raise SnapshotError(messages.SNAPSHOT_CORRUPTED.format(details=self.MANIFEST))
        return manifest

    def installed_commit(self) -> str | None:
        """Commit of the local index, or None without one. Closes the file so it can be replaced on Windows."""
        db_path = self._settings.db_path
        if not db_path.exists():
            return None
        try:
            with closing(sqlite3.connect(f"{db_path.resolve().as_uri()}?mode=ro", uri=True)) as db:
                row = db.execute("SELECT value FROM meta WHERE key = 'commit'").fetchone()
        except sqlite3.Error:  # a damaged index counts as missing, so `update` replaces it
            return None
        return row[0] if row else None

    def download(self, manifest: dict[str, Any] | None = None) -> IndexStats:
        """Downloads the snapshot, verifies it and replaces the local index."""
        manifest = manifest or self.latest()
        started = time.monotonic()
        db_path = self._settings.db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        # Per-process names: two clients starting on the same first run must not write over each other.
        archive = db_path.with_name(f"{db_path.name}.{os.getpid()}.gz.partial")
        temporary = db_path.with_name(f"{db_path.name}.{os.getpid()}.tmp")
        try:
            self._fetch(f"{self._settings.index_url}/{manifest['archive']}", archive, manifest["sha256"])
            self._extract(archive, temporary)
            norms = self._check(temporary, manifest["commit"])
            self._install(temporary, db_path)
        finally:
            archive.unlink(missing_ok=True)
            temporary.unlink(missing_ok=True)
        stats = IndexStats(manifest["commit"], norms, round(time.monotonic() - started, 1))
        log.info("Index downloaded: %s", stats)
        return stats

    def _fetch(self, url: str, target: Path, sha256: str) -> None:
        log.info("Downloading %s...", url)
        digest = hashlib.sha256()
        try:
            with self._client.stream("GET", url) as response, target.open("wb") as handle:
                response.raise_for_status()
                for chunk in response.iter_bytes(self._CHUNK):
                    digest.update(chunk)
                    handle.write(chunk)
        except httpx.HTTPError as exc:
            raise SnapshotError(messages.SNAPSHOT_FAILED.format(url=url, details=exc)) from exc
        except OSError as exc:  # disk full, permissions
            raise SnapshotError(messages.SNAPSHOT_WRITE_FAILED.format(path=target.parent, details=exc)) from exc
        if digest.hexdigest() != sha256:
            raise SnapshotError(messages.SNAPSHOT_CORRUPTED.format(details="sha256"))

    @classmethod
    def _extract(cls, archive: Path, target: Path) -> None:
        try:
            with gzip.open(archive, "rb") as source, target.open("wb") as destination:
                shutil.copyfileobj(source, destination, cls._CHUNK)
        except (gzip.BadGzipFile, EOFError, zlib.error) as exc:
            raise SnapshotError(messages.SNAPSHOT_CORRUPTED.format(details=exc)) from exc
        except OSError as exc:
            raise SnapshotError(messages.SNAPSHOT_WRITE_FAILED.format(path=target.parent, details=exc)) from exc

    @staticmethod
    def _install(temporary: Path, db_path: Path) -> None:
        try:
            os.replace(temporary, db_path)
        except PermissionError as exc:  # Windows: a running server keeps the index open
            raise SnapshotError(messages.INDEX_IN_USE.format(path=db_path)) from exc

    @staticmethod
    def _check(path: Path, commit: str) -> int:
        """Returns the number of norms after confirming the archive is the index of `commit`."""
        try:
            with closing(sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)) as db:
                meta = dict(db.execute("SELECT key, value FROM meta").fetchall())
                norms = db.execute("SELECT count(*) FROM norms").fetchone()[0]
        except sqlite3.Error as exc:
            raise SnapshotError(messages.SNAPSHOT_CORRUPTED.format(details=exc)) from exc
        if meta.get("commit") != commit:
            raise SnapshotError(messages.SNAPSHOT_CORRUPTED.format(details=f"commit {meta.get('commit')}"))
        return norms
