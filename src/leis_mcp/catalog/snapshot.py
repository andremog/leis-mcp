"""Prebuilt index published by the `index` workflow, so a first run needs neither git nor a local build.

The release holds `index.json` (commit, built_at, norms, archive, sha256) and the gzip archive it names.

Every stage has a budget. The manifest and the archive each have an aggregate deadline that bounds every
socket operation (DNS, connect, TLS, request, response headers and body), a byte cap and a free-space reserve;
extraction and validation share another deadline and a cap on the expanded database. A budget refusal is
`SnapshotResourceError`, which callers must not answer with a git build. The extracted database is checked
for schema, structural uniqueness of `path`, counts and commit before it atomically replaces the local
index. The parliamentary overlay (`<home>/overlay`) is never touched.
"""

import gzip
import hashlib
import ipaddress
import json
import logging
import os
import re
import shutil
import socket
import sqlite3
import threading
import time
import urllib.request
import zlib
from collections.abc import Callable, Iterator
from contextlib import closing, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpcore
import httpx

from .. import messages
from ..config import CONSTITUTION_PATH, Settings
from ..errors import SnapshotError, SnapshotResourceError
from ..models import IndexStats
from .index import COLUMNS, IndexBuilder
from .path_parser import PathParser

try:  # POSIX; elsewhere concurrent first runs are still kept apart by per-process file names
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None  # type: ignore[assignment]

log = logging.getLogger(__name__)

Clock = Callable[[], float]
_HEX = frozenset("0123456789abcdef")


_QUERY = re.compile(r"((?:https?://|/)[^\s'\"?#]*)[?#][^'\"]*", re.IGNORECASE)
_USERINFO = re.compile(r"(https?://)[^/\s'\"@]+@", re.IGNORECASE)


def scrub_urls(text: str) -> str:
    """Drops query strings, fragments and user info from every URL in `text`."""
    return _USERINFO.sub(r"\1", _QUERY.sub(r"\1", text))


class _RedactQuery(logging.Filter):
    """httpx logs request URLs at INFO and httpcore logs response headers (Location) at DEBUG; signed
    release-asset URLs carry tokens in the query string."""

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        scrubbed = scrub_urls(message)
        if scrubbed != message:
            record.msg, record.args = scrubbed, None
        return True


# Logger filters do not apply to child loggers, so each httpcore module logger gets its own.
for _name in (
    "httpx",
    "httpcore",
    "httpcore.connection",
    "httpcore.http11",
    "httpcore.http2",
    "httpcore.proxy",
    "httpcore.socks",
):
    _logger = logging.getLogger(_name)
    if not any(isinstance(item, _RedactQuery) for item in _logger.filters):
        _logger.addFilter(_RedactQuery())


def _statements(script: str) -> set[str]:
    return {_normal(part) for part in script.split(";") if part.strip().upper().startswith("CREATE")}


def _normal(sql: str) -> str:
    text = re.sub(r"\s+", " ", sql.strip().rstrip(";"))
    return re.sub(r"\s*([(),])\s*", r"\1", text).upper()


_SCHEMA = _statements(IndexBuilder._SCHEMA)
_INDEXES = _statements(IndexBuilder._INDEXES)


@dataclass(frozen=True, slots=True)
class Limits:
    manifest_bytes: int = 64 * 1024
    archive_bytes: int = 512 * 1024 * 1024
    database_bytes: int = 2 * 1024 * 1024 * 1024
    disk_reserve: int = 64 * 1024 * 1024
    manifest_seconds: float = 30.0
    archive_seconds: float = 900.0
    prepare_seconds: float = 600.0  # extraction + validation
    read_seconds: float = 60.0  # inactivity; always clamped to what is left of the stage deadline
    connect_seconds: float = 15.0
    max_redirects: int = 5
    # A publisher packaging error that still looks like an index must not shrink a healthy one.
    min_ratio: float = 0.5


class _Deadline:
    def __init__(self, seconds: float, stage: str, clock: Clock):
        self.seconds = seconds
        self.stage = stage
        self._clock = clock
        self._end = clock() + seconds

    def left(self) -> float:
        return self._end - self._clock()

    def expired(self) -> bool:
        return self.left() <= 0

    def message(self) -> str:
        return messages.SNAPSHOT_TIMEOUT.format(seconds=self.seconds, stage=self.stage)

    def remaining(self) -> float:
        left = self.left()
        if left <= 0:
            raise SnapshotResourceError(self.message())
        return left


class _DeadlineBackend(httpcore.NetworkBackend):
    """Clamps every blocking socket call to the current stage deadline, response headers included.

    httpcore computes one timeout per request; without this, each header or body read gets the whole
    inactivity timeout again and a slow sender can stretch a stage far beyond its deadline.
    """

    def __init__(
        self,
        inner: httpcore.NetworkBackend,
        current: Callable[[], _Deadline | None],
        resolve: Callable[..., list] = socket.getaddrinfo,
    ):
        self._inner = inner
        self._current = current
        self._resolve = resolve

    def clamp(self, timeout: float | None, error: type[Exception]) -> float | None:
        deadline = self._current()
        if deadline is None:
            return timeout
        left = deadline.left()
        if left <= 0:
            raise error(deadline.message())
        return left if timeout is None else min(timeout, left)

    def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        last: Exception | None = None
        for address in self._addresses(host, port):  # TLS still verifies `host`: httpcore passes it as SNI
            try:
                stream = self._inner.connect_tcp(
                    address,
                    port,
                    timeout=self.clamp(timeout, httpcore.ConnectTimeout),
                    local_address=local_address,
                    socket_options=socket_options,
                )
            except (httpcore.ConnectError, httpcore.ConnectTimeout) as exc:  # next address, same deadline
                last = exc
                continue
            return _DeadlineStream(stream, self)
        raise last or httpcore.ConnectError(f"no address for {host}")

    def _addresses(self, host: str, port: int) -> list[str]:
        """Resolves `host` within the stage deadline; `getaddrinfo` itself has no timeout."""
        try:
            ipaddress.ip_address(host)
            return [host]
        except ValueError:
            pass
        limit = self.clamp(None, httpcore.ConnectTimeout)
        outcome: dict[str, Any] = {}

        def run() -> None:
            try:
                outcome["infos"] = self._resolve(host, port, type=socket.SOCK_STREAM)
            except BaseException as exc:  # noqa: BLE001 - surfaced to the caller thread
                outcome["error"] = exc

        worker = threading.Thread(target=run, name="index-dns", daemon=True)
        worker.start()
        worker.join(limit)
        if worker.is_alive():
            deadline = self._current()
            raise httpcore.ConnectTimeout(deadline.message() if deadline else f"DNS timeout for {host}")
        if "error" in outcome:
            raise httpcore.ConnectError(f"DNS failure for {host}") from outcome["error"]
        addresses = list(dict.fromkeys(info[4][0] for info in outcome.get("infos") or []))
        if not addresses:
            raise httpcore.ConnectError(f"no address for {host}")
        return addresses

    def connect_unix_socket(self, path, timeout=None, socket_options=None):
        raise httpcore.ConnectError("unix sockets are not used for the prebuilt index")

    def sleep(self, seconds: float) -> None:
        self._inner.sleep(seconds)


class _DeadlineStream(httpcore.NetworkStream):
    def __init__(self, inner: httpcore.NetworkStream, backend: _DeadlineBackend):
        self._inner = inner
        self._backend = backend

    def read(self, max_bytes: int, timeout: float | None = None) -> bytes:
        return self._inner.read(max_bytes, self._backend.clamp(timeout, httpcore.ReadTimeout))

    def write(self, buffer: bytes, timeout: float | None = None) -> None:
        self._inner.write(buffer, self._backend.clamp(timeout, httpcore.WriteTimeout))

    def close(self) -> None:
        self._inner.close()

    def start_tls(self, ssl_context, server_hostname=None, timeout=None):
        clamped = self._backend.clamp(timeout, httpcore.ConnectTimeout)
        return _DeadlineStream(self._inner.start_tls(ssl_context, server_hostname, clamped), self._backend)

    def get_extra_info(self, info: str) -> Any:
        return self._inner.get_extra_info(info)


class _RoutingTransport(httpx.BaseTransport):
    """`HTTPS_PROXY`/`NO_PROXY` as httpx applies them, with the deadline backend on both routes."""

    def __init__(self, direct: httpx.BaseTransport, proxied: httpx.BaseTransport | None):
        self._direct = direct
        self._proxied = proxied

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        if self._proxied is None or urllib.request.proxy_bypass_environment(request.url.host):
            return self._direct.handle_request(request)
        return self._proxied.handle_request(request)

    def close(self) -> None:
        self._direct.close()
        if self._proxied is not None:
            self._proxied.close()


@dataclass(frozen=True, slots=True)
class _Checked:
    commit: str
    norms: int


class _ArchiveGone(Exception):
    """404 on the archive named by the manifest: the manifest is refreshed once, then it is reported."""


class IndexSnapshot:
    MANIFEST = "index.json"
    _REQUIRED = ("commit", "archive", "sha256")
    _CHUNK = 1 << 20
    _SPACE_CHECK = 32 << 20  # bytes written between free-space checks
    _PROGRESS_OPS = 20_000  # SQLite VM steps between deadline checks
    _ALLOWED_TABLES = frozenset({"norms", "meta", "sqlite_stat1", "sqlite_stat4"})
    _STAT_TABLES = frozenset(
        {
            _normal("CREATE TABLE sqlite_stat1(tbl,idx,stat)"),
            _normal("CREATE TABLE sqlite_stat4(tbl,idx,neq,nlt,ndlt,sample)"),
        }
    )
    _ROW_CHECK_EVERY = 20_000  # rows between deadline checks
    _MAX_VALUE = 1 << 20  # bytes; the longest legitimate value is a path or a commit

    def __init__(
        self,
        settings: Settings,
        client: httpx.Client | None = None,
        *,
        limits: Limits | None = None,
        clock: Clock = time.monotonic,
        network: httpcore.NetworkBackend | None = None,
        resolve: Callable[..., list] = socket.getaddrinfo,
    ):
        self._settings = settings
        self._resolve = resolve
        self._limits = limits or Limits()
        self._clock = clock
        self._local = threading.local()
        self._client = client or self._default_client(network)

    # ---- public API -----------------------------------------------------------------------------------------

    def latest(self) -> dict[str, Any]:
        """Manifest of the snapshot currently published."""
        url = f"{self._settings.index_url}/{self.MANIFEST}"
        chunks: list[bytes] = []
        deadline = _Deadline(self._limits.manifest_seconds, "manifesto", self._clock)
        self._get(url, deadline, self._limits.manifest_bytes, "manifesto", chunks.append)
        try:
            manifest = json.loads(b"".join(chunks))
        except (ValueError, UnicodeDecodeError) as exc:
            raise SnapshotError(messages.SNAPSHOT_CORRUPTED.format(details=self.MANIFEST)) from exc
        if not isinstance(manifest, dict) or not all(isinstance(manifest.get(key), str) for key in self._REQUIRED):
            raise SnapshotError(messages.SNAPSHOT_CORRUPTED.format(details=self.MANIFEST))
        archive = manifest["archive"]
        if not archive or archive.startswith(".") or any(char in archive for char in "/\\?#%"):
            raise SnapshotError(messages.SNAPSHOT_CORRUPTED.format(details=f"{self.MANIFEST}: archive"))
        if len(manifest["sha256"]) != 64 or not set(manifest["sha256"]) <= _HEX:
            raise SnapshotError(messages.SNAPSHOT_CORRUPTED.format(details=f"{self.MANIFEST}: sha256"))
        norms = manifest.get("norms")
        if norms is not None and (not isinstance(norms, int) or isinstance(norms, bool) or norms <= 0):
            raise SnapshotError(messages.SNAPSHOT_CORRUPTED.format(details=f"{self.MANIFEST}: norms"))
        return manifest

    def installed_commit(self) -> str | None:
        """Commit of a usable local index, or None. A damaged index counts as missing, so `update` repairs it."""
        checked = self._usable(self._settings.db_path)
        return checked.commit if checked else None

    def download(self, manifest: dict[str, Any] | None = None) -> IndexStats:
        """Downloads the snapshot, verifies it and replaces the local index."""
        started = self._clock()
        db_path = self._settings.db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._exclusive(db_path):
            manifest = manifest or self.latest()
            current = self._usable(db_path)
            if current is not None and current.commit == manifest["commit"]:  # another caller just installed it
                return IndexStats(current.commit, current.norms, round(self._clock() - started, 1))
            self._purge_partials(db_path)
            try:
                norms = self._download_into(db_path, manifest, current)
            except _ArchiveGone:
                refreshed = self.latest()  # the publisher replaced the archive after the manifest was read
                if refreshed["archive"] == manifest["archive"]:
                    url = self._safe(self._archive_url(manifest))
                    raise SnapshotError(messages.SNAPSHOT_FAILED.format(url=url, details="HTTP 404")) from None
                manifest = refreshed
                try:
                    norms = self._download_into(db_path, manifest, current)
                except _ArchiveGone:
                    url = self._safe(self._archive_url(manifest))
                    raise SnapshotError(messages.SNAPSHOT_FAILED.format(url=url, details="HTTP 404")) from None
        stats = IndexStats(manifest["commit"], norms, round(self._clock() - started, 1))
        log.info("Index downloaded: %s", stats)
        return stats

    # ---- stages -------------------------------------------------------------------------------------------

    def _download_into(self, db_path: Path, manifest: dict[str, Any], current: _Checked | None) -> int:
        # Per-process and per-thread names: concurrent first runs must not write over each other.
        tag = f"{os.getpid()}.{threading.get_ident()}"
        archive = db_path.with_name(f"{db_path.name}.{tag}.gz.partial")
        temporary = db_path.with_name(f"{db_path.name}.{tag}.tmp")
        try:
            self._fetch(self._archive_url(manifest), archive, manifest["sha256"])
            prepare = _Deadline(self._limits.prepare_seconds, "extração e validação", self._clock)
            self._extract(archive, temporary, prepare)
            archive.unlink(missing_ok=True)  # frees its space before validation
            checked = self._validate(temporary, prepare, thorough=True)
            self._accept(checked, manifest, current)
            _fsync(temporary)
            self._install(temporary, db_path)
            _fsync_dir(db_path.parent)
            return checked.norms
        finally:
            archive.unlink(missing_ok=True)
            temporary.unlink(missing_ok=True)

    def _fetch(self, url: str, target: Path, sha256: str) -> None:
        log.info("Downloading %s...", self._safe(url))
        digest = hashlib.sha256()
        deadline = _Deadline(self._limits.archive_seconds, "arquivo", self._clock)
        try:
            with target.open("wb") as handle:
                written = 0

                def sink(chunk: bytes) -> None:
                    nonlocal written
                    digest.update(chunk)
                    handle.write(chunk)
                    if (written + len(chunk)) // self._SPACE_CHECK != written // self._SPACE_CHECK:
                        self._require_space(target.parent, 0)
                    written += len(chunk)

                self._get(url, deadline, self._limits.archive_bytes, "arquivo", sink, target.parent)
        except OSError as exc:  # disk full, permissions
            raise SnapshotResourceError(messages.SNAPSHOT_WRITE_FAILED.format(path=target.parent, details=exc)) from exc
        if digest.hexdigest() != sha256:
            raise SnapshotError(messages.SNAPSHOT_CORRUPTED.format(details="sha256"))

    def _extract(self, archive: Path, target: Path, deadline: _Deadline) -> None:
        limit = self._limits.database_bytes
        written = 0
        try:
            with gzip.open(archive, "rb") as source, target.open("wb") as destination:
                while chunk := source.read(self._CHUNK):
                    deadline.remaining()
                    if written + len(chunk) > limit:
                        raise SnapshotResourceError(messages.SNAPSHOT_TOO_LARGE.format(limit=limit, stage="extraído"))
                    destination.write(chunk)
                    if (written + len(chunk)) // self._SPACE_CHECK != written // self._SPACE_CHECK:
                        self._require_space(target.parent, 0)
                    written += len(chunk)
            deadline.remaining()
        except (gzip.BadGzipFile, EOFError, zlib.error) as exc:
            raise SnapshotError(messages.SNAPSHOT_CORRUPTED.format(details=type(exc).__name__)) from exc
        except OSError as exc:
            raise SnapshotResourceError(messages.SNAPSHOT_WRITE_FAILED.format(path=target.parent, details=exc)) from exc

    def _validate(self, path: Path, deadline: _Deadline | None, *, thorough: bool) -> _Checked:
        """Raises `SnapshotError` unless `path` is an index the service can answer from."""
        try:
            with path.open("rb") as handle:
                header = handle.read(100)
        except OSError as exc:
            raise SnapshotError(messages.SNAPSHOT_UNUSABLE.format(details=type(exc).__name__)) from exc
        if len(header) == 100 and header.startswith(b"SQLite format 3\x00") and 2 in (header[18], header[19]):
            raise SnapshotError(messages.SNAPSHOT_UNUSABLE.format(details="modo WAL"))
        try:
            with closing(sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)) as db:
                db.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, self._MAX_VALUE)  # no huge values, even from expressions
                if deadline is not None:
                    db.set_progress_handler(lambda: 1 if deadline.expired() else 0, self._PROGRESS_OPS)
                try:
                    return self._inspect(db, thorough, deadline)
                except sqlite3.OperationalError as exc:
                    if deadline is not None and deadline.expired():
                        raise SnapshotResourceError(deadline.message()) from exc
                    raise
        except MemoryError as exc:
            raise SnapshotResourceError(messages.SNAPSHOT_UNUSABLE.format(details="memória")) from exc
        except (sqlite3.Error, ValueError, TypeError, RecursionError) as exc:
            raise SnapshotError(messages.SNAPSHOT_UNUSABLE.format(details=type(exc).__name__)) from exc

    def _inspect(self, db: sqlite3.Connection, thorough: bool, deadline: _Deadline | None) -> _Checked:
        def unusable(details: str) -> SnapshotError:
            return SnapshotError(messages.SNAPSHOT_UNUSABLE.format(details=details))

        # Exact schema first, so nothing chosen by the publisher (expression indexes, generated columns,
        # collations, views, triggers) is evaluated by the checks below.
        objects = db.execute("SELECT type, name, tbl_name, sql FROM sqlite_master").fetchall()
        tables = {name: sql for kind, name, _, sql in objects if kind == "table"}
        if any(kind not in ("table", "index") for kind, _, _, _ in objects):
            raise unusable("view/trigger")
        if set(tables) - self._ALLOWED_TABLES or not {"norms", "meta"} <= set(tables):
            raise unusable(f"tabelas {sorted(tables)}")
        if {_normal(tables["norms"] or ""), _normal(tables["meta"] or "")} != _SCHEMA:
            raise unusable("esquema de norms/meta")
        for name in self._ALLOWED_TABLES & set(tables) - {"norms", "meta"}:
            if _normal(tables[name] or "") not in self._STAT_TABLES:
                raise unusable(f"esquema de {name}")
        indexes = {name: (table, sql) for kind, name, table, sql in objects if kind == "index"}
        for name, (table, sql) in indexes.items():
            if sql is None:  # automatic index of meta's PRIMARY KEY
                if table != "meta" or not name.startswith("sqlite_autoindex_meta_"):
                    raise unusable(f"índice {name}")
            elif _normal(sql) not in _INDEXES:
                raise unusable(f"índice {name}")
        if not self._unique_path_index(db):
            raise unusable("sem índice único em path")

        check = db.execute("PRAGMA integrity_check(1)" if thorough else "PRAGMA quick_check(1)").fetchone()
        if not check or check[0] != "ok":
            raise unusable("integrity_check")
        meta = dict(db.execute("SELECT key, value FROM meta").fetchall())
        commit = meta.get("commit")
        if not isinstance(commit, str) or len(commit) != 40 or not set(commit) <= _HEX:
            raise unusable("meta.commit")
        norms = db.execute("SELECT count(*) FROM norms").fetchone()[0]
        if norms <= 0 or str(meta.get("total")) != str(norms):
            raise unusable(f"total {meta.get('total')} != {norms}")
        bad = db.execute(
            "SELECT count(*) FROM norms WHERE path = '' OR path LIKE '/%' OR path LIKE 'parlamentar/%' "
            "OR '/' || path || '/' LIKE '%/../%' OR '/' || path || '/' LIKE '%/./%' "
            "OR typeof(blob) != 'blob' OR length(blob) != 20"
        ).fetchone()[0]
        if bad:
            raise unusable(f"{bad} linhas inválidas")
        if db.execute("SELECT 1 FROM norms WHERE path = ?", [CONSTITUTION_PATH]).fetchone() is None:
            raise unusable(CONSTITUTION_PATH)
        if thorough:
            self._check_rows(db, deadline, unusable)
        return _Checked(commit, norms)

    def _check_rows(self, db: sqlite3.Connection, deadline: _Deadline | None, unusable) -> None:
        """Every row's metadata must be what `PathParser` derives from its path, as in a local build.

        Otherwise a snapshot could label a state norm as `congresso-nacional` and answer for it.
        """
        paths = PathParser()
        columns = COLUMNS[2:]
        cursor = db.execute(f"SELECT path, {', '.join(columns)} FROM norms")
        for count, (path, *values) in enumerate(cursor, 1):
            if count % self._ROW_CHECK_EVERY == 0 and deadline is not None:
                deadline.remaining()
            if not paths.is_indexable(path):
                raise unusable(f"caminho não indexável {path[:80]!r}")
            info = paths.parse(path)
            if tuple(values) != tuple(getattr(info, column) for column in columns):
                raise unusable(f"metadados não derivam do caminho {path[:80]!r}")

    @staticmethod
    def _unique_path_index(db: sqlite3.Connection) -> bool:
        """A full (not partial) UNIQUE index whose only column is `path`, read from SQLite's own metadata."""
        for row in db.execute("PRAGMA index_list(norms)").fetchall():
            name, unique, partial = row[1], row[2], row[4]
            if not unique or partial:
                continue
            columns = [column for (column,) in db.execute("SELECT name FROM pragma_index_info(?)", [name])]
            if columns == ["path"]:
                return True
        return False

    def _accept(self, checked: _Checked, manifest: dict[str, Any], current: _Checked | None) -> None:
        if checked.commit != manifest["commit"]:
            raise SnapshotError(messages.SNAPSHOT_CORRUPTED.format(details=f"commit {checked.commit}"))
        expected = manifest.get("norms")
        if expected is not None and expected != checked.norms:
            raise SnapshotError(messages.SNAPSHOT_CORRUPTED.format(details=f"norms {checked.norms} != {expected}"))
        if current is not None and checked.norms < current.norms * self._limits.min_ratio:
            details = f"{checked.norms} normas contra {current.norms} instaladas"
            raise SnapshotError(messages.SNAPSHOT_UNUSABLE.format(details=details))

    @staticmethod
    def _install(temporary: Path, db_path: Path) -> None:
        try:
            os.replace(temporary, db_path)
        except PermissionError as exc:  # Windows: a running server keeps the index open
            raise SnapshotError(messages.INDEX_IN_USE.format(path=db_path)) from exc

    # ---- HTTP ---------------------------------------------------------------------------------------------

    def _get(
        self,
        url: str,
        deadline: _Deadline,
        limit: int,
        stage: str,
        sink: Callable[[bytes], None],
        space: Path | None = None,
    ) -> None:
        """GET with HTTPS-only manual redirects whose bodies are never read. Budget refusals are resource errors."""
        current = url
        for _ in range(self._limits.max_redirects + 1):
            self._require_https(current)
            deadline.remaining()
            request = self._client.build_request("GET", current, headers={"Accept-Encoding": "identity"})
            self._local.deadline = deadline
            response: httpx.Response | None = None
            try:
                response = self._client.send(request, stream=True, follow_redirects=False)
                if response.is_redirect:
                    location = response.headers.get("location")
                    if not location:
                        raise SnapshotError(
                            messages.SNAPSHOT_FAILED.format(url=self._safe(current), details="redirect")
                        )
                    current = urljoin(current, location)
                    continue
                if response.status_code == 404 and stage == "arquivo":
                    raise _ArchiveGone()
                if response.status_code != 200:
                    details = f"HTTP {response.status_code}"
                    raise SnapshotError(messages.SNAPSHOT_FAILED.format(url=self._safe(current), details=details))
                if response.headers.get("content-encoding", "identity").lower() not in ("identity", ""):
                    raise SnapshotError(messages.SNAPSHOT_CORRUPTED.format(details="content-encoding"))
                declared = response.headers.get("content-length", "")
                if declared.isdigit():
                    if int(declared) > limit:
                        raise SnapshotResourceError(messages.SNAPSHOT_TOO_LARGE.format(limit=limit, stage=stage))
                    if space is not None:
                        self._require_space(space, int(declared))
                total = 0
                for chunk in response.iter_bytes():  # as read from the socket, so caps hold per read; identity only
                    deadline.remaining()
                    total += len(chunk)
                    if total > limit:
                        raise SnapshotResourceError(messages.SNAPSHOT_TOO_LARGE.format(limit=limit, stage=stage))
                    sink(chunk)
                deadline.remaining()
                return
            except httpx.TimeoutException as exc:
                raise SnapshotResourceError(deadline.message()) from exc
            except (httpx.HTTPError, httpx.InvalidURL, ValueError, RecursionError) as exc:
                details = type(exc).__name__  # httpx messages can carry the signed URL
                raise SnapshotError(messages.SNAPSHOT_FAILED.format(url=self._safe(current), details=details)) from exc
            finally:
                self._local.deadline = None
                if response is not None:
                    response.close()
        raise SnapshotError(messages.SNAPSHOT_FAILED.format(url=self._safe(url), details="redirects"))

    def _default_client(self, network: httpcore.NetworkBackend | None) -> httpx.Client:
        limits = self._limits
        ssl_context = httpx.create_ssl_context(trust_env=True)
        proxy = urllib.request.getproxies_environment().get("https")
        direct = self._wrap(httpx.HTTPTransport(verify=ssl_context, retries=0, trust_env=False), network)
        proxied = None
        if proxy:
            transport = httpx.HTTPTransport(verify=ssl_context, proxy=proxy, retries=0, trust_env=False)
            proxied = self._wrap(transport, network)
        return httpx.Client(
            transport=_RoutingTransport(direct, proxied),
            timeout=httpx.Timeout(limits.read_seconds, connect=limits.connect_seconds, pool=limits.connect_seconds),
            follow_redirects=False,
            trust_env=False,
            headers={"User-Agent": "leis-mcp"},
        )

    def _wrap(self, transport: httpx.HTTPTransport, network: httpcore.NetworkBackend | None) -> httpx.HTTPTransport:
        pool = transport._pool  # httpcore.ConnectionPool or HTTPProxy: both keep the backend in this attribute
        inner = network or getattr(pool, "_network_backend", None)
        if not isinstance(inner, httpcore.NetworkBackend):  # fail closed: no download without the socket deadline
            raise SnapshotError(
                messages.SNAPSHOT_FAILED.format(url=self._safe(self._settings.index_url), details="transport")
            )
        pool._network_backend = _DeadlineBackend(inner, lambda: getattr(self._local, "deadline", None), self._resolve)
        return transport

    # ---- helpers ------------------------------------------------------------------------------------------

    def _archive_url(self, manifest: dict[str, Any]) -> str:
        return f"{self._settings.index_url}/{manifest['archive']}"

    def _usable(self, db_path: Path) -> _Checked | None:
        if not db_path.exists():
            return None
        try:
            return self._validate(db_path, None, thorough=False)
        except SnapshotError as exc:
            log.warning("Local index at %s is not usable (%s); it will be replaced", db_path, exc)
            return None

    def _require_space(self, directory: Path, incoming: int) -> None:
        try:
            free = shutil.disk_usage(directory).free
        except OSError as exc:
            raise SnapshotResourceError(messages.SNAPSHOT_WRITE_FAILED.format(path=directory, details=exc)) from exc
        if free - incoming < self._limits.disk_reserve:
            raise SnapshotResourceError(
                messages.SNAPSHOT_NO_SPACE.format(path=directory, reserve=self._limits.disk_reserve)
            )

    @classmethod
    def _require_https(cls, url: str) -> None:
        parts = urlsplit(url)
        if parts.scheme != "https" or not parts.hostname:
            raise SnapshotError(messages.SNAPSHOT_REFUSED_URL.format(url=cls._safe(url), reason="só https"))

    @staticmethod
    def _safe(url: str) -> str:
        """URL without user info, query or fragment: signed redirect targets and tokens stay out of logs."""
        parts = urlsplit(url)
        port = f":{parts.port}" if parts.port else ""
        return f"{parts.scheme}://{parts.hostname or ''}{port}{parts.path}"

    @staticmethod
    def _purge_partials(db_path: Path) -> None:
        """Under the exclusive lock, leftovers of interrupted downloads belong to no one."""
        name = db_path.name
        for pattern in (f"{name}.*.gz.partial", f"{name}.*.tmp", f"{name}.*.tmp-shm", f"{name}.*.tmp-wal"):
            for leftover in db_path.parent.glob(pattern):
                leftover.unlink(missing_ok=True)

    @staticmethod
    @contextmanager
    def _exclusive(db_path: Path) -> Iterator[None]:
        if fcntl is None:  # pragma: no cover
            yield
            return
        with db_path.with_name(f"{db_path.name}.lock").open("a") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)


def _fsync(path: Path) -> None:
    with path.open("rb") as handle:
        os.fsync(handle.fileno())


def _fsync_dir(path: Path) -> None:
    if os.name != "posix":  # pragma: no cover
        return
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
