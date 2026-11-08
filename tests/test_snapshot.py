import gzip
import hashlib
import json
import sqlite3
import threading
from contextlib import closing

import httpx
import pytest

from leis_mcp.catalog import IndexSnapshot
from leis_mcp.config import Settings
from leis_mcp.errors import IndexPreparingError, SnapshotError
from leis_mcp.services import ServiceProvider

COMMIT = "a" * 40
INDEX_URL = "https://example.test/index"


def index_bytes(tmp_path, commit=COMMIT, norms=3) -> bytes:
    path = tmp_path / "built.sqlite"
    with closing(sqlite3.connect(path)) as db:
        db.executescript("CREATE TABLE norms (path TEXT); CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);")
        db.executemany("INSERT INTO norms VALUES (?)", [(f"n{i}.md",) for i in range(norms)])
        db.execute("INSERT INTO meta VALUES ('commit', ?)", [commit])
        db.commit()
    return path.read_bytes()


def published(archive: bytes, **overrides) -> dict[str, bytes]:
    manifest = {"commit": COMMIT, "archive": "index-aaaa.sqlite.gz", "sha256": hashlib.sha256(archive).hexdigest()}
    manifest.update(overrides)
    return {"/index/index.json": json.dumps(manifest).encode(), f"/index/{manifest['archive']}": archive}


def snapshot(tmp_path, files: dict[str, bytes]) -> tuple[IndexSnapshot, Settings]:
    def handler(request: httpx.Request) -> httpx.Response:
        body = files.get(request.url.path)
        return httpx.Response(200, content=body) if body is not None else httpx.Response(404)

    settings = Settings(home=tmp_path / "home", index_url=INDEX_URL)
    return IndexSnapshot(settings, httpx.Client(transport=httpx.MockTransport(handler))), settings


def test_download_installs_verified_index(tmp_path):
    snap, settings = snapshot(tmp_path, published(gzip.compress(index_bytes(tmp_path))))
    stats = snap.download()
    assert (stats.commit, stats.norms) == (COMMIT, 3)
    assert snap.installed_commit() == COMMIT
    assert sorted(path.name for path in settings.home.iterdir()) == ["index.sqlite"]


def test_wrong_checksum_keeps_previous_index(tmp_path):
    snap, settings = snapshot(tmp_path, published(gzip.compress(index_bytes(tmp_path)), sha256="0" * 64))
    settings.home.mkdir()
    settings.db_path.write_bytes(b"previous")
    with pytest.raises(SnapshotError, match="sha256"):
        snap.download()
    assert settings.db_path.read_bytes() == b"previous"
    assert sorted(path.name for path in settings.home.iterdir()) == ["index.sqlite"]


@pytest.mark.parametrize("archive", [b"not gzip", gzip.compress(b"not sqlite")])
def test_corrupted_archive_is_rejected(tmp_path, archive):
    snap, settings = snapshot(tmp_path, published(archive))
    with pytest.raises(SnapshotError, match="corrompido"):
        snap.download()
    assert not settings.db_path.exists()


def test_archive_from_another_commit_is_rejected(tmp_path):
    snap, settings = snapshot(tmp_path, published(gzip.compress(index_bytes(tmp_path, commit="b" * 40))))
    with pytest.raises(SnapshotError, match="commit"):
        snap.download()
    assert not settings.db_path.exists()


@pytest.mark.parametrize("manifest", [b"{}", b"[]", b"not json"])
def test_invalid_manifest(tmp_path, manifest):
    snap, _ = snapshot(tmp_path, {"/index/index.json": manifest})
    with pytest.raises(SnapshotError):
        snap.latest()


def test_missing_release_reports_url(tmp_path):
    snap, _ = snapshot(tmp_path, {})
    with pytest.raises(SnapshotError, match="example.test/index/index.json"):
        snap.latest()


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


def test_damaged_local_index_counts_as_missing(tmp_path):
    snap, settings = snapshot(tmp_path, {})
    settings.home.mkdir()
    settings.db_path.write_bytes(b"not sqlite")
    assert snap.installed_commit() is None
