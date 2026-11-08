import gzip
import hashlib
import json
import sqlite3
import threading
import time
from contextlib import closing
from pathlib import Path

import httpcore
import httpx
import pytest

from leis_mcp.catalog import IndexSnapshot
from leis_mcp.catalog.index import COLUMNS, IndexBuilder
from leis_mcp.catalog.path_parser import PathParser
from leis_mcp.catalog.snapshot import Limits
from leis_mcp.cli import Cli
from leis_mcp.config import Settings
from leis_mcp.errors import GitError, IndexPreparingError, SnapshotError, SnapshotResourceError
from leis_mcp.models import IndexStats
from leis_mcp.services import ServiceProvider

COMMIT = "a" * 40
OTHER = "b" * 40
INDEX_URL = "https://example.test/index"
SCHEMA = IndexBuilder._SCHEMA.replace("PRAGMA journal_mode = OFF;", "").replace("PRAGMA synchronous = OFF;", "")
SCHEMA += "CREATE UNIQUE INDEX ix_path ON norms(path); CREATE INDEX ix_number ON norms(number, year);"
_built = iter(range(10**6))
_PATHS = PathParser()


def rows(paths: list[str]) -> list[tuple]:
    return [(p, hashlib.sha1(p.encode()).digest(), *(getattr(_PATHS.parse(p), c) for c in COLUMNS[2:])) for p in paths]


def index_bytes(tmp_path: Path, commit=COMMIT, norms=3, extra: str = "") -> bytes:
    path = tmp_path / f"built-{next(_built)}.sqlite"
    with closing(sqlite3.connect(path)) as db:
        db.executescript(SCHEMA)
        paths = ["constituicao.md"] + [
            f"federal/leis-ordinarias/2000/lei-{i + 1}-2000-01-01.md" for i in range(norms - 1)
        ]
        db.executemany(
            f"INSERT INTO norms ({', '.join(COLUMNS)}) VALUES ({', '.join('?' * len(COLUMNS))})", rows(paths)
        )
        db.executemany("INSERT INTO meta VALUES (?, ?)", [("commit", commit), ("total", str(norms))])
        db.commit()
        if extra:
            db.executescript(extra)
            db.commit()
    return path.read_bytes()


def published(archive: bytes, name="index-aaaa.sqlite.gz", **overrides) -> dict[str, bytes]:
    manifest = {"commit": COMMIT, "archive": name, "sha256": hashlib.sha256(archive).hexdigest()}
    manifest.update(overrides)
    return {"/index/index.json": json.dumps(manifest).encode(), f"/index/{name}": archive}


def snapshot(tmp_path, files: dict[str, bytes], limits: Limits | None = None, log: list | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        if log is not None:
            log.append(request.url.path)
        body = files.get(request.url.path)
        return httpx.Response(200, content=body) if body is not None else httpx.Response(404)

    settings = Settings(home=tmp_path / "home", index_url=INDEX_URL)
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return IndexSnapshot(settings, client, limits=limits), settings


def install_previous(settings: Settings, tmp_path: Path, commit=OTHER, norms=3) -> bytes:
    settings.home.mkdir(parents=True, exist_ok=True)
    data = index_bytes(tmp_path, commit=commit, norms=norms)
    settings.db_path.write_bytes(data)
    pointer = settings.overlay_dir / "current.json"
    pointer.parent.mkdir(parents=True, exist_ok=True)
    pointer.write_text('{"version": "p-keep"}')
    return data


def home_files(settings: Settings) -> list[str]:
    if not settings.home.exists():
        return []
    return sorted(path.name for path in settings.home.iterdir() if path.is_file() and path.suffix != ".lock")


def assert_previous_kept(settings: Settings, previous: bytes):
    assert settings.db_path.read_bytes() == previous
    assert home_files(settings) == ["index.sqlite"]
    assert (settings.overlay_dir / "current.json").read_text() == '{"version": "p-keep"}'


# ---- happy path -------------------------------------------------------------------------------------------------


def test_download_installs_verified_index(tmp_path):
    snap, settings = snapshot(tmp_path, published(gzip.compress(index_bytes(tmp_path)), norms=3))
    stats = snap.download()
    assert (stats.commit, stats.norms) == (COMMIT, 3)
    assert snap.installed_commit() == COMMIT
    assert home_files(settings) == ["index.sqlite"]


def test_download_keeps_overlay_and_replaces_upstream(tmp_path):
    snap, settings = snapshot(tmp_path, published(gzip.compress(index_bytes(tmp_path, norms=4))))
    install_previous(settings, tmp_path)
    snap.download()
    assert snap.installed_commit() == COMMIT
    assert (settings.overlay_dir / "current.json").read_text() == '{"version": "p-keep"}'


def test_concurrent_downloads_leave_one_valid_index(tmp_path):
    snap, settings = snapshot(tmp_path, published(gzip.compress(index_bytes(tmp_path))))
    errors = []

    def run():
        try:
            snap.download()
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=run) for _ in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)
    assert errors == []
    assert snap.installed_commit() == COMMIT
    assert home_files(settings) == ["index.sqlite"]


# ---- rejected archives keep the previous index ------------------------------------------------------------------


def test_wrong_checksum_keeps_previous_index(tmp_path):
    snap, settings = snapshot(tmp_path, published(gzip.compress(index_bytes(tmp_path)), sha256="0" * 64))
    previous = install_previous(settings, tmp_path)
    with pytest.raises(SnapshotError, match="sha256"):
        snap.download()
    assert_previous_kept(settings, previous)


@pytest.mark.parametrize("archive", [b"not gzip", gzip.compress(b"not sqlite")])
def test_corrupted_archive_is_rejected(tmp_path, archive):
    snap, settings = snapshot(tmp_path, published(archive))
    with pytest.raises(SnapshotError):
        snap.download()
    assert not settings.db_path.exists()


def test_truncated_sqlite_is_rejected(tmp_path):
    data = index_bytes(tmp_path, norms=400)
    snap, settings = snapshot(tmp_path, published(gzip.compress(data[: len(data) // 2])))
    previous = install_previous(settings, tmp_path)
    with pytest.raises(SnapshotError):
        snap.download()
    assert_previous_kept(settings, previous)


def test_archive_from_another_commit_is_rejected(tmp_path):
    snap, settings = snapshot(tmp_path, published(gzip.compress(index_bytes(tmp_path, commit="c" * 40))))
    with pytest.raises(SnapshotError, match="commit"):
        snap.download()
    assert not settings.db_path.exists()


UNUSABLE = {
    "view": "CREATE VIEW v AS SELECT path FROM norms;",
    "trigger": "CREATE TRIGGER t AFTER INSERT ON meta BEGIN SELECT 1; END;",
    "extra table": "CREATE TABLE evil (x);",
    "duplicate path behind a decoy unique index": (
        "DROP INDEX ix_path; UPDATE norms SET path = 'constituicao.md';"
        " CREATE UNIQUE INDEX path_decoy ON norms(number);"
    ),
    "partial unique index": "DROP INDEX ix_path; CREATE UNIQUE INDEX ix_path ON norms(path) WHERE sphere = 'x';",
    "empty path": "UPDATE norms SET path = '' WHERE rowid = 2;",
    "traversal": "UPDATE norms SET path = 'federal/../../etc' WHERE rowid = 2;",
    "text blob": "UPDATE norms SET blob = 'abc' WHERE rowid = 2;",
    "missing constitution": "UPDATE norms SET path = 'outra.md' WHERE path = 'constituicao.md';",
    "bad stored total": "UPDATE meta SET value = '99' WHERE key = 'total';",
    "missing column": "DROP INDEX ix_number; ALTER TABLE norms DROP COLUMN agency;",
    "expression index (memory bomb)": "CREATE INDEX ix_e ON norms(length(printf('%.*c', 400000000, 'x')));",
    "index not built by leis-mcp": "CREATE INDEX ix_extra ON norms(city);",
    "state norm labelled as Congress": "UPDATE norms SET agency = 'congresso-nacional' WHERE rowid = 2;",
    "row in the overlay namespace": (
        "UPDATE norms SET path = 'parlamentar/federal/congresso-nacional/resolucoes/2002/resolucao-1-2002.md'"
        " WHERE rowid = 2;"
    ),
    "path the builder would skip": "UPDATE norms SET path = 'federal/leis-ordinarias/2000/_falhas.md' WHERE rowid = 2;",
    "wal mode": "PRAGMA journal_mode = WAL;",
    "extra column": "ALTER TABLE norms ADD COLUMN extra TEXT;",
    "generated column": "ALTER TABLE norms ADD COLUMN g TEXT GENERATED ALWAYS AS (upper(path)) VIRTUAL;",
    "value over 1 MiB": "UPDATE meta SET value = printf('%.*c', 2097152, 'x') WHERE key = 'built_at'; "
    "INSERT OR REPLACE INTO meta VALUES ('built_at', printf('%.*c', 2097152, 'x'));",
    "forged statistics table": (
        "ANALYZE; PRAGMA writable_schema = ON; UPDATE sqlite_master"
        " SET sql = 'CREATE TABLE sqlite_stat1(tbl,idx,stat,extra)' WHERE name = 'sqlite_stat1';"
    ),
}


@pytest.mark.parametrize("extra", UNUSABLE.values(), ids=UNUSABLE.keys())
def test_unusable_index_never_replaces_healthy_one(tmp_path, extra):
    snap, settings = snapshot(tmp_path, published(gzip.compress(index_bytes(tmp_path, extra=extra))))
    previous = install_previous(settings, tmp_path)
    with pytest.raises(SnapshotError):
        snap.download()
    assert_previous_kept(settings, previous)


def info_value(value):
    return "NULL" if value is None else (str(value) if isinstance(value, int) else f"'{value}'")


def test_name_with_double_dot_inside_a_segment_is_accepted(tmp_path):
    real = "reguladores/anac/portarias/2021/portaria-n-5..868-de-6-de-setembro-de-2021-343832672.md"
    info = _PATHS.parse(real)
    sets = ", ".join(f"{c} = {info_value(getattr(info, c))}" for c in COLUMNS[2:])
    extra = f"UPDATE norms SET path = '{real}', {sets} WHERE rowid = 2;"
    snap, _ = snapshot(tmp_path, published(gzip.compress(index_bytes(tmp_path, extra=extra))))
    assert snap.download().norms == 3


def test_manifest_norm_count_must_match(tmp_path):
    snap, settings = snapshot(tmp_path, published(gzip.compress(index_bytes(tmp_path)), norms=4))
    previous = install_previous(settings, tmp_path)
    with pytest.raises(SnapshotError, match="norms 3 != 4"):
        snap.download()
    assert_previous_kept(settings, previous)


def test_index_much_smaller_than_installed_is_refused(tmp_path):
    snap, settings = snapshot(tmp_path, published(gzip.compress(index_bytes(tmp_path, norms=3))))
    previous = install_previous(settings, tmp_path, norms=10)
    with pytest.raises(SnapshotError, match="3 normas contra 10"):
        snap.download()
    assert_previous_kept(settings, previous)


@pytest.mark.parametrize(
    "manifest",
    [
        b"{}",
        b"[]",
        b"not json",
        json.dumps({"commit": COMMIT, "archive": "../x.gz", "sha256": "0" * 64}).encode(),
        json.dumps({"commit": COMMIT, "archive": "x.gz", "sha256": "zz"}).encode(),
        json.dumps({"commit": COMMIT, "archive": "x.gz", "sha256": "0" * 64, "norms": -1}).encode(),
    ],
)
def test_invalid_manifest(tmp_path, manifest):
    snap, _ = snapshot(tmp_path, {"/index/index.json": manifest})
    with pytest.raises(SnapshotError):
        snap.latest()


def test_missing_release_reports_url(tmp_path):
    snap, _ = snapshot(tmp_path, {})
    with pytest.raises(SnapshotError, match="example.test/index/index.json"):
        snap.latest()


# ---- update and repair ------------------------------------------------------------------------------------------


def test_damaged_local_index_counts_as_missing(tmp_path):
    snap, settings = snapshot(tmp_path, {})
    settings.home.mkdir()
    settings.db_path.write_bytes(b"not sqlite")
    assert snap.installed_commit() is None


def test_same_commit_but_broken_install_is_repaired(tmp_path, monkeypatch, capsys):
    snap, settings = snapshot(tmp_path, published(gzip.compress(index_bytes(tmp_path))))
    settings.home.mkdir()
    settings.db_path.write_bytes(index_bytes(tmp_path, extra="DROP INDEX ix_path;"))  # right commit, unusable
    cli = Cli()
    cli._settings = settings
    monkeypatch.setattr(cli, "_snapshot", lambda: snap)
    cli.run(["update"])
    assert json.loads(capsys.readouterr().out)["norms"] == 3
    assert snap.installed_commit() == COMMIT


def test_archive_gone_refreshes_manifest_once(tmp_path):
    new = gzip.compress(index_bytes(tmp_path))
    stale = {"commit": COMMIT, "archive": "index-old.sqlite.gz", "sha256": hashlib.sha256(new).hexdigest()}
    requests: list[str] = []
    snap, _ = snapshot(tmp_path, published(new, name="index-new.sqlite.gz"), log=requests)
    snap.download(stale)
    assert requests == ["/index/index-old.sqlite.gz", "/index/index.json", "/index/index-new.sqlite.gz"]


# ---- size, disk and redirect budgets ----------------------------------------------------------------------------


def test_manifest_over_cap_is_a_resource_error(tmp_path):
    snap, _ = snapshot(tmp_path, {"/index/index.json": b" " * 2048}, Limits(manifest_bytes=1024))
    with pytest.raises(SnapshotResourceError, match="limite"):
        snap.latest()


def test_archive_over_cap_is_a_resource_error(tmp_path):
    archive = gzip.compress(index_bytes(tmp_path))
    snap, settings = snapshot(tmp_path, published(archive), Limits(archive_bytes=len(archive) - 1))
    previous = install_previous(settings, tmp_path)
    with pytest.raises(SnapshotResourceError, match="limite"):
        snap.download()
    assert_previous_kept(settings, previous)


def test_expansion_over_cap_is_a_resource_error(tmp_path):
    data = index_bytes(tmp_path)
    snap, settings = snapshot(tmp_path, published(gzip.compress(data)), Limits(database_bytes=len(data) - 1))
    previous = install_previous(settings, tmp_path)
    with pytest.raises(SnapshotResourceError, match="extraído"):
        snap.download()
    assert_previous_kept(settings, previous)


def test_low_disk_is_a_resource_error(tmp_path):
    snap, settings = snapshot(tmp_path, published(gzip.compress(index_bytes(tmp_path))), Limits(disk_reserve=1 << 62))
    previous = install_previous(settings, tmp_path)
    with pytest.raises(SnapshotResourceError, match="Espaço"):
        snap.download()
    assert_previous_kept(settings, previous)


class TrackedStream(httpx.SyncByteStream):
    def __init__(self, data: bytes):
        self.data = data
        self.consumed = 0
        self.closed = False

    def __iter__(self):
        for i in range(0, len(self.data), 1024):
            self.consumed += 1024
            yield self.data[i : i + 1024]

    def close(self):
        self.closed = True


def test_redirect_body_is_closed_unread_and_https_only(tmp_path):
    body = TrackedStream(b"x" * (5 << 20))
    target = json.dumps({"commit": COMMIT, "archive": "a.gz", "sha256": "0" * 64}).encode()

    def handler(request):
        if request.url.host == "example.test":
            return httpx.Response(302, headers={"location": "https://cdn.test/m?sig=SECRET"}, stream=body)
        return httpx.Response(200, content=target)

    settings = Settings(home=tmp_path, index_url=INDEX_URL)
    client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)
    assert IndexSnapshot(settings, client).latest()["archive"] == "a.gz"
    assert body.consumed == 0 and body.closed

    plain = httpx.MockTransport(lambda request: httpx.Response(302, headers={"location": "http://cdn.test/m"}))
    with pytest.raises(SnapshotError, match="só https"):
        IndexSnapshot(settings, httpx.Client(transport=plain)).latest()


def test_errors_and_logs_omit_signed_query(tmp_path, caplog):
    def handler(request):
        if request.url.host == "example.test":
            return httpx.Response(302, headers={"location": "https://user:pw@cdn.test/m?sig=SECRET"})
        return httpx.Response(500)

    settings = Settings(home=tmp_path, index_url=INDEX_URL)
    snap = IndexSnapshot(settings, httpx.Client(transport=httpx.MockTransport(handler)))
    with caplog.at_level("INFO"), pytest.raises(SnapshotError) as raised:
        snap.latest()
    assert "SECRET" not in str(raised.value) and "pw" not in str(raised.value)
    assert "SECRET" not in caplog.text


# ---- deadlines through the real httpx/httpcore stack ------------------------------------------------------------


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class FakeStream(httpcore.NetworkStream):
    """Honors the timeout httpcore passes on every read, as a real socket does. No real network."""

    def __init__(self, clock: Clock, script: list[tuple[float, bytes]], timeouts: list):
        self.clock = clock
        self.script = list(script)
        self.timeouts = timeouts
        self.closed = False

    def read(self, max_bytes, timeout=None):
        self.timeouts.append(timeout)
        if not self.script:
            return b""
        delay, data = self.script[0]
        if timeout is not None and delay > timeout:
            self.clock.now += timeout
            self.script[0] = (delay - timeout, data)
            raise httpcore.ReadTimeout("timed out")
        self.clock.now += delay
        self.script.pop(0)
        return data

    def write(self, buffer, timeout=None):
        pass

    def close(self):
        self.closed = True

    tls_delay = 0.0

    def start_tls(self, ssl_context, server_hostname=None, timeout=None):
        if timeout is not None and self.tls_delay > timeout:
            self.clock.now += timeout
            raise httpcore.ConnectTimeout("handshake timed out")
        self.clock.now += self.tls_delay
        self.server_hostname = server_hostname
        return self

    def get_extra_info(self, info):
        return None


class FakeNetwork(httpcore.NetworkBackend):
    def __init__(self, clock: Clock, scripts: list[list[tuple[float, bytes]]], connect_delay=0.0, tls_delay=0.0):
        self.clock = clock
        self.scripts = list(scripts)
        self.timeouts: list = []
        self.streams: list[FakeStream] = []
        self.connect_delay = connect_delay
        self.tls_delay = tls_delay
        self.hosts: list[str] = []

    def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        self.hosts.append(host)
        if timeout is not None and self.connect_delay > timeout:
            self.clock.now += timeout
            raise httpcore.ConnectTimeout("connect timed out")
        self.clock.now += self.connect_delay
        stream = FakeStream(self.clock, self.scripts.pop(0), self.timeouts)
        stream.tls_delay = self.tls_delay
        self.streams.append(stream)
        return stream

    def sleep(self, seconds):
        self.clock.now += seconds


def response_bytes(body: bytes) -> bytes:
    return b"HTTP/1.1 200 OK\r\nContent-Length: %d\r\nConnection: close\r\n\r\n" % len(body) + body


@pytest.fixture
def no_proxy(monkeypatch):
    for name in ("HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.delenv(name, raising=False)


def fake_dns(host, port, type=None):  # noqa: A002
    return [(2, 1, 6, "", ("192.0.2.10", port))]


def real_stack(tmp_path, scripts, limits: Limits, network=None):
    clock = Clock()
    network = network or FakeNetwork(clock, scripts)
    network.clock = clock
    for stream in getattr(network, "streams", []):
        stream.clock = clock
    settings = Settings(home=tmp_path / "home", index_url=INDEX_URL)
    snap = IndexSnapshot(settings, limits=limits, clock=clock, network=network, resolve=fake_dns)
    return snap, clock, network, settings


def test_slow_headers_are_cut_at_the_stage_deadline(tmp_path, no_proxy):
    head = response_bytes(b"{}")
    script = [(0, head[:10])] + [(50, head[10 + i : 11 + i]) for i in range(20)] + [(50, head[30:])]
    snap, clock, network, _ = real_stack(tmp_path, [script], Limits(manifest_seconds=120, read_seconds=60))
    with pytest.raises(SnapshotResourceError, match="120 s"):
        snap.latest()
    assert clock.now <= 120
    assert network.timeouts[-1] < 60  # the last read only got what was left of the deadline
    assert all(stream.closed for stream in network.streams)


def test_slow_body_is_cut_at_the_stage_deadline(tmp_path, no_proxy):
    body = b"x" * 20
    head = response_bytes(body)[: -len(body)]
    script = [(0, head)] + [(50, body[i : i + 1]) for i in range(20)]
    limits = Limits(archive_seconds=120, read_seconds=60)
    snap, clock, network, settings = real_stack(tmp_path, [script], limits)
    previous = install_previous(settings, tmp_path)
    manifest = {"commit": COMMIT, "archive": "a.gz", "sha256": "0" * 64}
    with pytest.raises(SnapshotResourceError, match="arquivo"):
        snap.download(manifest)
    assert clock.now <= 120
    assert network.timeouts[-1] < 60
    assert all(stream.closed for stream in network.streams)
    assert_previous_kept(settings, previous)


def test_real_stack_installs_when_in_time(tmp_path, no_proxy):
    archive = gzip.compress(index_bytes(tmp_path))
    manifest = json.dumps({"commit": COMMIT, "archive": "a.gz", "sha256": hashlib.sha256(archive).hexdigest()})
    scripts = [[(1, response_bytes(manifest.encode()))], [(1, response_bytes(archive))]]
    snap, _, _, _ = real_stack(tmp_path, scripts, Limits())
    assert snap.download().commit == COMMIT


# ---- no git build after a budget refusal ------------------------------------------------------------------------


class BodyTimeout(httpx.SyncByteStream):
    def __iter__(self):
        yield b"{"
        raise httpx.ReadTimeout("slow body")


def cli_with(tmp_path, monkeypatch, handler):
    settings = Settings(home=tmp_path / "home", index_url=INDEX_URL)
    snap = IndexSnapshot(settings, httpx.Client(transport=httpx.MockTransport(handler)))
    cli = Cli()
    cli._settings = settings
    builds: list[str] = []

    class Builder:
        def build(self, commit=None):
            builds.append("git")
            return IndexStats(COMMIT, 1, 0.0)

    monkeypatch.setattr(cli, "_snapshot", lambda: snap)
    monkeypatch.setattr(cli, "_builder", lambda: Builder())
    monkeypatch.setattr("leis_mcp.cli.shutil.which", lambda name: "/usr/bin/git")
    return cli, builds, settings


@pytest.mark.parametrize("stage", ["opening", "manifest body", "archive body"])
def test_timeouts_never_fall_back_to_git(tmp_path, monkeypatch, stage):
    manifest = json.dumps({"commit": COMMIT, "archive": "a.gz", "sha256": "0" * 64}).encode()

    def handler(request):
        if stage == "opening":
            raise httpx.ConnectTimeout("handshake")
        if request.url.path.endswith("index.json") and stage == "archive body":
            return httpx.Response(200, content=manifest)
        return httpx.Response(200, stream=BodyTimeout())

    cli, builds, settings = cli_with(tmp_path, monkeypatch, handler)
    with pytest.raises(SnapshotResourceError):
        cli._prepare_index()
    assert builds == []
    assert not settings.db_path.exists()
    assert home_files(settings) == []


def test_unavailable_snapshot_still_falls_back_to_git(tmp_path, monkeypatch):
    cli, builds, _ = cli_with(tmp_path, monkeypatch, lambda request: httpx.Response(404))
    cli._prepare_index()
    assert builds == ["git"]


# ---- provider ---------------------------------------------------------------------------------------------------


def test_provider_answers_while_index_is_prepared(tmp_path):
    settings = Settings(home=tmp_path)
    release = threading.Event()

    def prepare():
        release.wait(5)
        settings.db_path.write_bytes(b"ready")

    provider = ServiceProvider(settings, factory=lambda: "service", prepare=prepare)
    provider.start()
    with pytest.raises(IndexPreparingError, match="Preparando"):
        provider.get()
    release.set()
    provider._worker.join(5)
    assert provider.get() == "service"


def test_provider_reports_failure_and_retries(tmp_path):
    settings = Settings(home=tmp_path)
    attempts = []

    def prepare():
        attempts.append(1)
        if len(attempts) == 1:
            raise SnapshotError("sem rede")
        settings.db_path.write_bytes(b"ready")

    provider = ServiceProvider(settings, factory=lambda: "service", prepare=prepare)
    provider.start()
    provider._worker.join(5)
    for _ in range(2):  # within RETRY_AFTER the failure is reported without a new attempt
        with pytest.raises(IndexPreparingError, match="sem rede"):
            provider.get()
    assert len(attempts) == 1
    provider.RETRY_AFTER = 0
    with pytest.raises(IndexPreparingError, match="Preparando"):
        provider.get()
    provider._worker.join(5)
    assert provider.get() == "service"
    assert len(attempts) == 2


# ---- QA follow-ups: each test fails if its protection is removed -------------------------------------------------


def test_slow_connect_and_tls_are_cut_at_the_stage_deadline(tmp_path, no_proxy):
    for delays in ({"connect_delay": 500}, {"tls_delay": 500}):
        clock = Clock()
        network = FakeNetwork(clock, [[(0, response_bytes(b"{}"))]], **delays)
        limits = Limits(manifest_seconds=120, connect_seconds=10_000)
        snap, clock, _, _ = real_stack(tmp_path, [], limits, network=network)
        with pytest.raises(SnapshotResourceError, match="120 s"):
            snap.latest()
        assert clock.now <= 120


def test_tls_verifies_the_hostname_while_connecting_to_the_resolved_address(tmp_path, no_proxy):
    clock = Clock()
    manifest = json.dumps({"commit": COMMIT, "archive": "a.gz", "sha256": "0" * 64}).encode()
    network = FakeNetwork(clock, [[(0, response_bytes(manifest))]])
    snap, *_ = real_stack(tmp_path, [], Limits(), network=network)
    snap.latest()
    assert network.hosts == ["192.0.2.10"]
    assert network.streams[0].server_hostname == "example.test"


def test_dns_lookup_is_bounded_by_the_stage_deadline(tmp_path, no_proxy):
    release = threading.Event()

    def hanging_dns(host, port, type=None):  # noqa: A002
        release.wait(30)
        return fake_dns(host, port)

    settings = Settings(home=tmp_path, index_url=INDEX_URL)
    snap = IndexSnapshot(settings, limits=Limits(manifest_seconds=0.5), resolve=hanging_dns)
    started = time.monotonic()
    try:
        with pytest.raises(SnapshotResourceError):
            snap.latest()
        assert time.monotonic() - started < 3
    finally:
        release.set()


def test_slow_headers_through_the_proxy_route_are_cut(tmp_path, monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.test:3128")
    for name in ("NO_PROXY", "no_proxy", "https_proxy"):
        monkeypatch.delenv(name, raising=False)
    head = response_bytes(b"{}")
    connect = b"HTTP/1.1 200 Connection established\r\n\r\n"
    script = [(0, connect), (0, head[:10])] + [(50, head[10 + i : 11 + i]) for i in range(20)]
    snap, clock, network, _ = real_stack(tmp_path, [script], Limits(manifest_seconds=120, read_seconds=60))
    with pytest.raises(SnapshotResourceError, match="120 s"):
        snap.latest()
    assert clock.now <= 120
    assert network.hosts == ["192.0.2.10"]  # the proxy connection went through the deadline backend


class ChunkedStream(httpx.SyncByteStream):
    def __init__(self, size: int, piece: int):
        self.size, self.piece, self.sent = size, piece, 0

    def __iter__(self):
        while self.sent < self.size:
            self.sent += self.piece
            yield b" " * self.piece


def test_byte_cap_is_checked_per_network_read(tmp_path):
    stream = ChunkedStream(4 << 20, 4096)
    transport = httpx.MockTransport(lambda request: httpx.Response(200, stream=stream))
    snap = IndexSnapshot(Settings(home=tmp_path, index_url=INDEX_URL), httpx.Client(transport=transport))
    with pytest.raises(SnapshotResourceError, match="limite"):
        snap.latest()
    assert stream.sent <= Limits().manifest_bytes + 4096


def test_forged_unique_flag_is_caught_by_integrity_check(tmp_path):
    extra = (
        "DROP INDEX ix_path; UPDATE norms SET path = 'constituicao.md';"
        " CREATE INDEX ix_path ON norms(path); PRAGMA writable_schema = ON;"
        " UPDATE sqlite_master SET sql = 'CREATE UNIQUE INDEX ix_path ON norms(path)' WHERE name = 'ix_path';"
    )
    snap, settings = snapshot(tmp_path, published(gzip.compress(index_bytes(tmp_path, extra=extra))))
    previous = install_previous(settings, tmp_path)
    with pytest.raises(SnapshotError, match="integrity_check"):
        snap.download()
    assert_previous_kept(settings, previous)


def staged(tmp_path, limits):
    clock = Clock()
    files = published(gzip.compress(index_bytes(tmp_path, norms=50)))

    def handler(request):
        body = files.get(request.url.path)
        return httpx.Response(200, content=body) if body is not None else httpx.Response(404)

    settings = Settings(home=tmp_path / "home", index_url=INDEX_URL)
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return IndexSnapshot(settings, client, limits=limits, clock=clock), clock, settings


def test_extraction_is_cut_at_the_prepare_deadline(tmp_path, monkeypatch):
    snap, clock, settings = staged(tmp_path, Limits(prepare_seconds=10))
    validated, reads = [], []
    original_read = gzip.GzipFile.read

    def slow_read(self, size=-1):
        reads.append(size)
        clock.now += 20
        return original_read(self, size)

    monkeypatch.setattr(gzip.GzipFile, "read", slow_read)
    monkeypatch.setattr(snap, "_CHUNK", 1024)  # many reads: the cut must come at the first late one
    monkeypatch.setattr(snap, "_validate", lambda *a, **k: validated.append(1))
    with pytest.raises(SnapshotResourceError, match="extração"):
        snap.download()
    assert len(reads) == 1
    assert validated == []
    assert home_files(settings) == []


def test_validation_is_cut_at_the_prepare_deadline(tmp_path, monkeypatch):
    snap, clock, settings = staged(tmp_path, Limits(prepare_seconds=10))
    extract = snap._extract

    def then_late(*args):
        extract(*args)
        clock.now += 60  # validation starts after the deadline: the SQLite progress handler must stop it

    monkeypatch.setattr(snap, "_extract", then_late)
    monkeypatch.setattr(snap, "_PROGRESS_OPS", 1)
    with pytest.raises(SnapshotResourceError, match="validação"):
        snap.download()
    assert home_files(settings) == []


def test_provider_repairs_an_unreadable_index_in_the_background(tmp_path):
    settings = Settings(home=tmp_path)
    settings.db_path.write_bytes(b"not sqlite")
    repaired = threading.Event()
    calls = []

    def factory():
        calls.append(1)
        if not repaired.is_set():
            raise sqlite3.DatabaseError("file is not a database")
        return "service"

    def prepare():
        settings.db_path.write_bytes(b"repaired")
        repaired.set()

    provider = ServiceProvider(settings, factory=factory, prepare=prepare)
    with pytest.raises(IndexPreparingError, match="ilegível"):
        provider.get()
    provider._worker.join(5)
    assert provider.get() == "service"


def test_failed_repair_waits_before_downloading_again(tmp_path):
    settings = Settings(home=tmp_path)
    settings.db_path.write_bytes(b"not sqlite")
    attempts = []

    def factory():
        raise sqlite3.DatabaseError("file is not a database")

    provider = ServiceProvider(settings, factory=factory, prepare=lambda: attempts.append(1))
    with pytest.raises(IndexPreparingError):
        provider.get()
    provider._worker.join(5)
    for _ in range(10):
        with pytest.raises(IndexPreparingError):
            provider.get()
    assert attempts == [1]


def test_debug_logs_of_httpcore_omit_signed_location(tmp_path, caplog, no_proxy):
    redirect = b"HTTP/1.1 302 Found\r\nLocation: https://cdn.test/a?sig=SECRET&jwt=TOKEN\r\nContent-Length: 0\r\n\r\n"
    clock = Clock()
    network = FakeNetwork(clock, [[(0, redirect)], [(0, response_bytes(b"{}"))]])
    snap, *_ = real_stack(tmp_path, [], Limits(), network=network)
    with caplog.at_level("DEBUG"), pytest.raises(SnapshotError):
        snap.latest()
    assert "Location" in caplog.text or "location" in caplog.text
    assert "SECRET" not in caplog.text and "TOKEN" not in caplog.text


def test_provider_does_not_hand_git_details_to_clients(tmp_path):
    settings = Settings(home=tmp_path)

    def prepare():
        raise GitError("clone", "fatal: could not read from /data/repo.partial: token=abc")

    provider = ServiceProvider(settings, factory=lambda: "service", prepare=prepare)
    provider.start()
    provider._worker.join(5)
    with pytest.raises(IndexPreparingError) as raised:
        provider.get()
    assert "token" not in str(raised.value) and "/data" not in str(raised.value)
    assert "log do servidor" in str(raised.value)


def test_clone_log_drops_credentials():
    from leis_mcp.catalog.git_repository import _without_credentials

    assert _without_credentials("https://user:tok@github.com/o/r") == "https://github.com/o/r"
    assert _without_credentials("https://github.com/ctresb/leis") == "https://github.com/ctresb/leis"


@pytest.mark.parametrize("address", ["fec0::1", "ff02::1", "224.0.0.1"])
def test_collector_refuses_site_local_and_multicast(address):
    from leis_mcp.overlay.allowlist import FetchRejected, validate_url

    def resolve(host, port, type=None):  # noqa: A002
        return [(10, 1, 6, "", (address, port))]

    with pytest.raises(FetchRejected):
        validate_url("https://www2.camara.leg.br/x", resolve=resolve)


def test_real_statistics_table_is_accepted(tmp_path):
    snap, _ = snapshot(tmp_path, published(gzip.compress(index_bytes(tmp_path, extra="ANALYZE;"))))
    assert snap.download().norms == 3


def test_row_check_is_cut_at_the_prepare_deadline(tmp_path, monkeypatch):
    snap, clock, settings = staged(tmp_path, Limits(prepare_seconds=10))
    parse = PathParser.parse

    def slow_parse(self, path):
        clock.now += 1
        return parse(self, path)

    monkeypatch.setattr(PathParser, "parse", slow_parse)
    monkeypatch.setattr(snap, "_ROW_CHECK_EVERY", 5)
    with pytest.raises(SnapshotResourceError, match="validação"):
        snap.download()
    assert home_files(settings) == []


def test_next_address_is_tried_after_a_connect_timeout(tmp_path, no_proxy):
    clock = Clock()
    manifest = json.dumps({"commit": COMMIT, "archive": "a.gz", "sha256": "0" * 64}).encode()

    class FirstMute(FakeNetwork):
        def connect_tcp(self, host, port, timeout=None, **kwargs):
            if host == "2001:db8::1":
                self.hosts.append(host)
                self.clock.now += timeout
                raise httpcore.ConnectTimeout("silent")
            return super().connect_tcp(host, port, timeout, **kwargs)

    def two_addresses(host, port, type=None):  # noqa: A002
        return [(10, 1, 6, "", ("2001:db8::1", port, 0, 0)), (2, 1, 6, "", ("192.0.2.10", port))]

    network = FirstMute(clock, [[(0, response_bytes(manifest))]])
    settings = Settings(home=tmp_path, index_url=INDEX_URL)
    snap = IndexSnapshot(
        settings, limits=Limits(connect_seconds=15), clock=clock, network=network, resolve=two_addresses
    )
    assert snap.latest()["archive"] == "a.gz"
    assert network.hosts == ["2001:db8::1", "192.0.2.10"]


def test_update_falls_back_to_git_when_the_snapshot_is_unusable(tmp_path, monkeypatch, capsys):
    archive = gzip.compress(index_bytes(tmp_path, extra="ALTER TABLE norms ADD COLUMN extra TEXT;"))
    files = published(archive)
    cli, builds, settings = cli_with(
        tmp_path, monkeypatch, lambda request: httpx.Response(200, content=files[request.url.path])
    )
    monkeypatch.setattr(cli._repository, "clone", lambda: None)
    monkeypatch.setattr(cli._repository, "fetch_latest", lambda: COMMIT)
    cli.run(["update"])
    assert builds == ["git"]


def test_update_never_falls_back_to_git_after_a_budget_refusal(tmp_path, monkeypatch):
    cli, builds, _ = cli_with(tmp_path, monkeypatch, lambda request: httpx.Response(200, stream=BodyTimeout()))
    with pytest.raises(SystemExit):
        cli.run(["update"])
    assert builds == []


@pytest.mark.parametrize(
    "line",
    [
        "Location: HTTPS://cdn.test/a?sig=SECRET",
        "(b'Location', b'/rel/arc2?token=SECRET')",
        "https://user:SECRET@host/p",
        "HTTPS://user:SECRET@host/p",
        "GET https://cdn.test/a?X-Amz-Signature=SECRET&x=1",
    ],
)
def test_url_scrubbing(line):
    from leis_mcp.catalog.snapshot import scrub_urls

    assert "SECRET" not in scrub_urls(line)
