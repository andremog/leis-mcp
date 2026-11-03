"""On-disk cache of norm contents, addressed by git blob and filled on demand.

Lookup order: local cache -> raw.githubusercontent.com pinned to the indexed commit -> `git cat-file`.
Downloads are verified against the blob hash before entering the cache.
"""

import hashlib
import logging
import os
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import quote

import httpx

from .. import messages
from ..catalog.git_repository import GitRepository
from ..config import Settings
from ..errors import ContentUnavailableError
from ..models import Norm

log = logging.getLogger(__name__)


class ContentStore:
    _ATTEMPTS = 3
    _BLOB_NAME_LENGTH = 38  # sha1 hex minus the 2-char directory prefix

    def __init__(self, settings: Settings, commit: str, repository: GitRepository, client: httpx.Client | None = None):
        self._raw_base_url = settings.raw_base_url
        self._commit = commit
        self._repository = repository
        self._objects = settings.cache_dir / "objects"
        self._client = client or httpx.Client(timeout=60, follow_redirects=True, headers={"User-Agent": "leis-mcp"})

    @staticmethod
    def blob_hash(data: bytes) -> str:
        """Same hash as `git hash-object`."""
        return hashlib.sha1(b"blob %d\0" % len(data) + data, usedforsecurity=False).hexdigest()

    def path_for(self, blob: str) -> Path:
        return self._objects / blob[:2] / blob[2:]

    def is_cached(self, norm: Norm) -> bool:
        return self.path_for(norm.blob).exists()

    def cached_blobs(self) -> list[str]:
        return [
            path.parent.name + path.name
            for path in self._objects.glob("??/*")
            if len(path.name) == self._BLOB_NAME_LENGTH
        ]

    def read(self, norm: Norm) -> str:
        target = self.path_for(norm.blob)
        if not target.exists():
            self._write(target, self._download(norm))
        return target.read_text("utf-8")

    def prefetch(self, norms: list[Norm], workers: int = 8) -> list[str]:
        """Downloads norms in parallel. Returns one error message per failure."""
        missing = [norm for norm in norms if not self.is_cached(norm)]
        with ThreadPoolExecutor(workers) as executor:
            return [error for error in executor.map(self._try_read, missing) if error]

    def _try_read(self, norm: Norm) -> str | None:
        try:
            self.read(norm)
        except Exception as exc:  # noqa: BLE001 - report and keep going with the others
            return f"{norm.path}: {exc}"
        return None

    def _download(self, norm: Norm) -> bytes:
        data = self._fetch_raw(norm)
        if data is not None:
            return data
        if not self._repository.exists:  # index downloaded as a snapshot: there is no clone to fall back on
            raise ContentUnavailableError(messages.CONTENT_UNAVAILABLE.format(path=norm.path))
        return self._repository.read_blob(norm.blob)

    def _fetch_raw(self, norm: Norm) -> bytes | None:
        url = f"{self._raw_base_url}/{self._commit}/{quote(norm.path)}"
        for attempt in range(1, self._ATTEMPTS + 1):
            try:
                response = self._client.get(url)
            except httpx.HTTPError as exc:
                log.warning("Network error on %s: %s (attempt %d)", url, exc, attempt)
                continue
            if response.status_code == httpx.codes.OK:
                return self._verified(norm, response.content)
            if response.status_code == httpx.codes.NOT_FOUND:
                return None
            log.warning("HTTP %s on %s (attempt %d)", response.status_code, url, attempt)
        return None

    def _verified(self, norm: Norm, data: bytes) -> bytes | None:
        if self.blob_hash(data) == norm.blob:
            return data
        log.warning("Hash mismatch for %s; falling back to git.", norm.path)
        return None

    @staticmethod
    def _write(target: Path, data: bytes) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(dir=target.parent)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(data)
            os.replace(temporary, target)
        except BaseException:
            Path(temporary).unlink(missing_ok=True)
            raise
