"""SQLite index of norm paths, built from `git ls-tree` without downloading content."""

import logging
import os
import sqlite3
import threading
import time
from collections.abc import Iterator
from contextlib import closing
from functools import cached_property
from itertools import batched, chain
from pathlib import Path
from typing import Any

from .. import messages
from ..config import Settings
from ..errors import IndexMissingError
from ..models import IndexStats, Norm, NormQuery
from .git_repository import GitRepository
from .path_parser import PathParser

log = logging.getLogger(__name__)

COLUMNS = ("path", "blob", "sphere", "kind", "year", "number", "date", "state", "city", "agency")


class IndexBuilder:
    _SCHEMA = """
        PRAGMA journal_mode = OFF;
        PRAGMA synchronous = OFF;
        CREATE TABLE norms (
            path TEXT NOT NULL, blob BLOB NOT NULL, sphere TEXT NOT NULL, kind TEXT, year INTEGER,
            number TEXT, date TEXT, state TEXT, city TEXT, agency TEXT
        );
        CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
    """
    _INDEXES = """
        CREATE UNIQUE INDEX ix_path ON norms(path);
        CREATE INDEX ix_number ON norms(number, year);
        CREATE INDEX ix_browse ON norms(sphere, kind, year);
        CREATE INDEX ix_blob ON norms(blob);
    """
    _INSERT = f"INSERT INTO norms ({', '.join(COLUMNS)}) VALUES ({', '.join('?' * len(COLUMNS))})"

    def __init__(self, settings: Settings, repository: GitRepository, paths: PathParser):
        self._settings = settings
        self._repository = repository
        self._paths = paths

    def build(self, commit: str | None = None) -> IndexStats:
        """Builds the index into a temporary file and swaps it in atomically."""
        self._repository.clone()
        commit = commit or self._repository.head()
        started = time.monotonic()
        temporary = self._settings.db_path.with_suffix(".tmp")
        temporary.unlink(missing_ok=True)
        with closing(sqlite3.connect(temporary)) as db:
            db.executescript(self._SCHEMA)
            db.executemany(self._INSERT, self._rows(commit))
            db.executescript(self._INDEXES)
            total = db.execute("SELECT count(*) FROM norms").fetchone()[0]
            built_at = time.strftime("%Y-%m-%dT%H:%M:%S%z")
            db.executemany(
                "INSERT INTO meta VALUES (?, ?)", [("commit", commit), ("built_at", built_at), ("total", str(total))]
            )
            db.commit()
            db.execute("ANALYZE")
        os.replace(temporary, self._settings.db_path)
        stats = IndexStats(commit, total, round(time.monotonic() - started, 1))
        log.info("Index built: %s", stats)
        return stats

    def _rows(self, commit: str) -> Iterator[tuple[Any, ...]]:
        entries = ((path, blob) for path, blob in self._repository.list_tree(commit) if self._paths.is_indexable(path))
        for path, blob in entries:
            info = self._paths.parse(path)
            yield (path, bytes.fromhex(blob), *(getattr(info, column) for column in COLUMNS[2:]))


class NormIndex:
    """Read-only queries over the index. Thread-safe."""

    _SPHERE_RANK = ("federal", "estaduais", "municipais", "reguladores", "jurisprudencia")
    _KIND_RANK = (
        "constituicao", "leis-ordinarias", "leis-complementares", "decretos-lei",
        "emendas-constitucionais", "medidas-provisorias", "leis-delegadas", "decretos",
    )  # fmt: skip
    GROUPABLE = frozenset({"sphere", "kind", "year", "state", "city", "agency"})
    _EQUALITY_COLUMNS = ("number", "kind", "sphere", "state", "city", "agency")
    _SELECT = f"SELECT {', '.join(COLUMNS)} FROM norms"
    _BATCH = 500  # stays below SQLite's bound-parameter limit

    def __init__(self, settings: Settings, *, database: Path | None = None):
        path = database or settings.db_path
        if not path.exists():
            raise IndexMissingError(messages.INDEX_MISSING.format(path=path))
        uri = f"{path.resolve().as_uri()}?mode=ro"
        self._db = sqlite3.connect(uri, uri=True, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._lock = threading.Lock()

    @cached_property
    def metadata(self) -> dict[str, str]:
        return {row["key"]: row["value"] for row in self._query("SELECT key, value FROM meta")}

    @property
    def commit(self) -> str:
        return self.metadata["commit"]

    def by_path(self, path: str) -> Norm | None:
        rows = self._query(f"{self._SELECT} WHERE path = ?", [path])
        return self._norm(rows[0]) if rows else None

    def by_blobs(self, blobs: list[str], query: NormQuery | None = None) -> list[Norm]:
        """Norms stored in any of `blobs` that also match `query`."""
        batches = batched(blobs, self._BATCH)
        return list(chain.from_iterable(self._by_blob_batch(batch, query or NormQuery()) for batch in batches))

    def find(self, query: NormQuery, limit: int = 20) -> list[Norm]:
        """Matching norms, most relevant first: federal before other spheres, laws before decrees."""
        conditions, args = self._conditions(query)
        order = f"{self._rank('sphere', self._SPHERE_RANK)}, {self._rank('kind', self._KIND_RANK)}, year DESC, path"
        sql = f"{self._SELECT}{self._where(conditions)} ORDER BY {order} LIMIT ?"
        return [self._norm(row) for row in self._query(sql, [*args, limit])]

    def browse(self, query: NormQuery, limit: int = 50, offset: int = 0) -> list[Norm]:
        conditions, args = self._conditions(query)
        sql = f"{self._SELECT}{self._where(conditions)} ORDER BY year DESC, date DESC, path LIMIT ? OFFSET ?"
        return [self._norm(row) for row in self._query(sql, [*args, limit, offset])]

    def count_by(self, column: str, query: NormQuery) -> dict[str, int]:
        if column not in self.GROUPABLE:
            raise ValueError(f"Cannot group by {column!r}")
        conditions, args = self._conditions(query)
        where = self._where([*conditions, f"{column} IS NOT NULL"])
        rows = self._query(f"SELECT {column}, count(*) FROM norms{where} GROUP BY {column} ORDER BY {column}", args)
        return {str(row[0]): row[1] for row in rows}

    def count(self, query: NormQuery) -> int:
        conditions, args = self._conditions(query)
        return self._query(f"SELECT count(*) FROM norms{self._where(conditions)}", args)[0][0]

    def _by_blob_batch(self, blobs: tuple[str, ...], query: NormQuery) -> list[Norm]:
        conditions, args = self._conditions(query)
        where = self._where([*conditions, f"blob IN ({self._placeholders(len(blobs))})"])
        return [self._norm(row) for row in self._query(f"{self._SELECT}{where}", [*args, *map(bytes.fromhex, blobs)])]

    def _query(self, sql: str, args: list[Any] | None = None) -> list[sqlite3.Row]:
        with self._lock:
            return self._db.execute(sql, args or []).fetchall()

    @classmethod
    def _conditions(cls, query: NormQuery) -> tuple[list[str], list[Any]]:
        values = {column: value for column in cls._EQUALITY_COLUMNS if (value := getattr(query, column))}
        conditions = [f"{column} = ?" for column in values]
        if query.years:
            conditions.append(f"year IN ({cls._placeholders(len(query.years))})")
        return conditions, [*values.values(), *query.years]

    @staticmethod
    def _where(conditions: list[str]) -> str:
        return f" WHERE {' AND '.join(conditions)}" if conditions else ""

    @staticmethod
    def _placeholders(count: int) -> str:
        return ",".join("?" * count)

    @staticmethod
    def _rank(column: str, values: tuple[str, ...]) -> str:
        cases = " ".join(f"WHEN '{value}' THEN {rank}" for rank, value in enumerate(values))
        return f"CASE {column} {cases} ELSE {len(values)} END"

    @staticmethod
    def _norm(row: sqlite3.Row) -> Norm:
        return Norm(**{**dict(row), "blob": row["blob"].hex()})
