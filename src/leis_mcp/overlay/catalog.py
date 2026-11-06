"""Reads the parliamentary index beside the upstream one and serves its files from disk."""

import logging
import sqlite3
from pathlib import Path

from ..catalog.git_repository import GitRepository
from ..catalog.index import NormIndex
from ..config import Settings
from ..content.store import ContentStore
from ..errors import IndexMissingError
from ..models import Norm, NormQuery, Reference
from .store import ActiveOverlay, OverlayStore

log = logging.getLogger(__name__)


class LayeredContentStore:
    """Upstream blob cache, with parliamentary files answered from the active overlay."""

    def __init__(self, upstream: ContentStore, active: ActiveOverlay | None):
        self._upstream = upstream
        self._by_blob = active.files_by_blob() if active else {}
        self._by_path = active.files_by_path() if active else {}

    def path_for(self, blob: str) -> Path:
        if blob in self._by_blob:
            return self._by_blob[blob]
        return self._upstream.path_for(blob)

    def is_cached(self, norm: Norm) -> bool:
        return norm.path in self._by_path or norm.blob in self._by_blob or self._upstream.is_cached(norm)

    def cached_blobs(self) -> list[str]:
        return [*self._by_blob.keys(), *self._upstream.cached_blobs()]

    def read(self, norm: Norm) -> str:
        path = self._by_path.get(norm.path) or self._by_blob.get(norm.blob)
        if path is not None:
            return path.read_text(encoding="utf-8")
        return self._upstream.read(norm)

    def prefetch(self, norms: list[Norm], workers: int = 8) -> list[str]:
        missing = [norm for norm in norms if norm.path not in self._by_path and norm.blob not in self._by_blob]
        return self._upstream.prefetch(missing, workers)


class CompositeIndex:
    """Upstream norms plus the overlay. Same query shape as `NormIndex`."""

    def __init__(self, upstream: NormIndex, overlay: NormIndex | None):
        self._upstream = upstream
        self._overlay = overlay

    @property
    def metadata(self) -> dict[str, str]:
        data = dict(self._upstream.metadata)
        if self._overlay is not None:
            data["overlay_version"] = self._overlay.commit
            data["overlay_norms"] = self._overlay.metadata.get("total", "0")
        return data

    @property
    def commit(self) -> str:
        return self._upstream.commit

    def by_path(self, path: str) -> Norm | None:
        if self._overlay is not None and path.startswith("parlamentar/"):
            return self._overlay.by_path(path)
        found = self._upstream.by_path(path)
        if found is not None:
            return found
        return self._overlay.by_path(path) if self._overlay is not None else None

    def by_blobs(self, blobs: list[str], query: NormQuery | None = None) -> list[Norm]:
        found = self._upstream.by_blobs(blobs, query)
        if self._overlay is None:
            return found
        return _dedupe([*self._overlay.by_blobs(blobs, query), *found])

    def find(self, query: NormQuery, limit: int = 20) -> list[Norm]:
        found = self._upstream.find(query, limit)
        if self._overlay is not None:
            found = [*self._overlay.find(query, limit), *found]
        found.sort(key=lambda norm: _rank(norm, query))
        return _dedupe(found)[:limit]

    def browse(self, query: NormQuery, limit: int = 50, offset: int = 0) -> list[Norm]:
        upstream = self._upstream.browse(query, limit=offset + limit, offset=0)
        overlay = self._overlay.browse(query, limit=10_000, offset=0) if self._overlay is not None else []
        merged = sorted([*overlay, *upstream], key=lambda norm: (-(norm.year or 0), norm.date or "", norm.path))
        return _dedupe(merged)[offset : offset + limit]

    def count_by(self, column: str, query: NormQuery) -> dict[str, int]:
        counts = dict(self._upstream.count_by(column, query))
        if self._overlay is not None:
            for key, value in self._overlay.count_by(column, query).items():
                counts[key] = counts.get(key, 0) + value
        return dict(sorted(counts.items()))

    def count(self, query: NormQuery) -> int:
        extra = self._overlay.count(query) if self._overlay is not None else 0
        return self._upstream.count(query) + extra


def attach(
    settings: Settings, repository: GitRepository
) -> tuple[CompositeIndex, LayeredContentStore, dict[str, Reference]]:
    """Opens upstream plus the overlay. A broken overlay does not take down the upstream corpus."""
    active: ActiveOverlay | None
    try:
        active = OverlayStore(settings).ensure_active()
    except (OSError, sqlite3.Error, IndexMissingError):
        log.exception("Parliamentary overlay unavailable; serving the upstream index only")
        active = None
    upstream = NormIndex(settings)
    overlay_index: NormIndex | None = None
    if active is not None:
        try:
            overlay_index = NormIndex(settings, database=active.db_path)
        except (OSError, sqlite3.Error, IndexMissingError):
            log.exception("Parliamentary overlay index unreadable; serving the upstream index only")
            active = None
    aliases = active.alias_map() if active is not None else {}
    store = LayeredContentStore(ContentStore(settings, upstream.commit, repository), active)
    return CompositeIndex(upstream, overlay_index), store, aliases


def _dedupe(norms: list[Norm]) -> list[Norm]:
    seen: set[str] = set()
    unique: list[Norm] = []
    for norm in norms:
        if norm.path in seen:
            continue
        seen.add(norm.path)
        unique.append(norm)
    return unique


def _rank(norm: Norm, query: NormQuery) -> tuple:
    organ = 0 if query.agency and norm.agency == query.agency else 1
    return (
        organ,
        _position(norm.sphere, NormIndex._SPHERE_RANK),
        _position(norm.kind, NormIndex._KIND_RANK),
        -(norm.year or 0),
        norm.path,
    )


def _position(value: str | None, order: tuple[str, ...]) -> int:
    return order.index(value) if value in order else len(order)
