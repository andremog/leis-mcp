"""Failure injection for overlay activation, startup fallback, identity and path confinement."""

import json
import re
import shutil
import sqlite3
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from leis_mcp.catalog.index import IndexBuilder
from leis_mcp.config import Settings
from leis_mcp.content.store import ContentStore
from leis_mcp.overlay.store import (
    OverlayError,
    OverlayStore,
    collection_protected,
    package_bundle,
    version_id,
    write_bundle,
)
from leis_mcp.server import LeisServer
from leis_mcp.services import LegislationService

ART_7 = "quadragésimo segundo"


def _upstream(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as db:
        db.executescript(IndexBuilder._SCHEMA)
        db.executescript(IndexBuilder._INDEXES)
        db.execute(
            "INSERT INTO norms VALUES (?, ?, 'estaduais', 'resolucoes', 2002, '1', '2002-03-01', 'DF', NULL, NULL)",
            ("estaduais/DF/resolucoes/2002/resolucao-1-2002.md", bytes.fromhex("aa" * 20)),
        )
        db.executemany(
            "INSERT INTO meta VALUES (?, ?)",
            [("commit", "upstream-test"), ("built_at", "2026-01-01T00:00:00Z"), ("total", "1")],
        )
        db.commit()


def _settings(tmp_path: Path) -> Settings:
    settings = Settings(home=tmp_path)
    _upstream(settings.db_path)
    cache = settings.cache_dir / "objects" / "aa" / ("aa" * 19)
    cache.parent.mkdir(parents=True)
    cache.write_text("---\ntitulo: DF\n---\n\nArt. 1º Homônimo.\n", encoding="utf-8")
    return settings


def _service(settings: Settings) -> LegislationService:
    return LegislationService.from_settings(settings)


def test_bad_hash_never_activates_and_keeps_the_healthy_version(tmp_path):
    settings = _settings(tmp_path)
    store = OverlayStore(settings)
    broken = tmp_path / "broken"
    shutil.copytree(package_bundle(), broken)
    manifest_path = broken / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["norms"][0]["blob"] = "0" * 40
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(OverlayError):
        store.activate_tree(broken, origin="manual")
    with pytest.raises(OverlayError):
        store.activate_tree(broken, origin="manual")
    assert not (settings.overlay_dir / "active.json").exists()
    assert store.status()["ativo"] is False
    staging = settings.overlay_dir / "staging"
    assert list(staging.glob("*")) == [] if staging.exists() else True

    healthy = store.activate_tree(package_bundle(), origin="bundle")
    version = healthy.version
    published = settings.overlay_dir / "versions" / version / "index.sqlite"
    with sqlite3.connect(published) as db:
        db.execute("DELETE FROM norms")
        db.commit()
    # A later good activation must not adopt the half-built index.
    repaired = store.activate_tree(package_bundle(), origin="bundle")
    assert repaired.indexed_rows == 6
    assert store.status()["normas"] == 6
    assert ART_7 in _service(settings).read_provision("resolucao 1/2002-cn", "7")["dispositivo"]["texto"]


def test_schema_only_database_is_not_reported_as_usable(tmp_path):
    settings = _settings(tmp_path)
    store = OverlayStore(settings)
    active = store.activate_tree(package_bundle(), origin="bundle")
    database = active.db_path
    with sqlite3.connect(database) as db:
        db.execute("DELETE FROM norms")
        db.execute("UPDATE meta SET value = '0' WHERE key = 'total'")
        db.commit()
    assert store.status()["ativo"] is False
    service = _service(settings)
    assert "overlay_version" not in service.status()
    found = service.find_norms("Resolução nº 1/2002 do Congresso Nacional")
    assert found["total"] == 0
    assert service.find_norms(number="1", year=2002, kind="resolucoes", sphere="estaduais")["resultados"][0][
        "caminho"
    ].startswith("estaduais/DF/")
    app = LeisServer(service).http_app(api_key="test-key")
    client = TestClient(app)
    assert client.get("/health").status_code == 200
    assert client.post("/mcp").status_code == 401


def test_malformed_pointer_manifest_and_missing_version_fall_back(tmp_path):
    settings = _settings(tmp_path)
    store = OverlayStore(settings)
    store.activate_tree(package_bundle(), origin="manual")
    pointer = settings.overlay_dir / "active.json"
    pointer.write_text("{", encoding="utf-8")
    service = _service(settings)
    assert "overlay_version" not in service.status()
    assert service.find_norms("resolucao 1/2002-cn")["total"] == 0

    pointer.write_text(
        json.dumps({"version": "p-" + "ab" * 10, "origin": "manual", "previous": None}), encoding="utf-8"
    )
    service = _service(settings)
    assert service.find_norms(number="1", year=2002, kind="resolucao", sphere="federal")["total"] == 0

    version = json.loads(package_bundle().joinpath("manifest.json").read_text(encoding="utf-8"))["version"]
    published = settings.overlay_dir / "versions" / version
    if published.exists():
        (published / "manifest.json").write_text("[]", encoding="utf-8")
        pointer.write_text(json.dumps({"version": version, "origin": "manual", "previous": None}), encoding="utf-8")
        assert _service(settings).find_norms("Resolução nº 1/2002 do Congresso Nacional")["total"] == 0


def test_metadata_change_activates_and_timestamp_does_not(tmp_path):
    settings = _settings(tmp_path)
    store = OverlayStore(settings)
    first = store.activate_tree(package_bundle(), origin="bundle")
    revised = tmp_path / "revised"
    shutil.copytree(package_bundle(), revised)
    manifest = json.loads((revised / "manifest.json").read_text(encoding="utf-8"))
    item = manifest["norms"][0]
    document = revised / "norms" / item["path"]
    text = document.read_text(encoding="utf-8")
    text = text.replace("natureza_texto: consolidado_pela_fonte", "natureza_texto: historico", 1)
    text = re.sub(
        r"versao_texto:.*(?:\n  .*)*",
        "versao_texto: publicação original (texto histórico, não consolidado)",
        text,
        count=1,
    )
    document.write_text(text, encoding="utf-8")
    item["text_nature"] = "historico"
    item["text_version"] = "publicação original (texto histórico, não consolidado)"
    item["provenance"]["text_nature"] = "historico"
    item["blob"] = ContentStore.blob_hash(text.encode())
    manifest["version"] = version_id(manifest["norms"])
    (revised / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    provenance = revised / "provenance" / f"{item['canonical_id'].replace(':', '_')}.json"
    provenance.write_text(json.dumps(item["provenance"], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    activated = store.activate_tree(revised, origin="manual")
    assert activated.version != first.version
    read = _service(settings).read_provision("resolucao 1/2002-cn", "7")
    assert read["norma"]["natureza_texto"] == "historico"
    assert any("NÃO é a versão compilada" in notice for notice in read["avisos"])
    assert any("Vigência não verificada" in notice for notice in read["avisos"])

    served_at = read["norma"]["coletado_em"]
    same = tmp_path / "timestamp"
    shutil.copytree(revised, same)
    again = json.loads((same / "manifest.json").read_text(encoding="utf-8"))
    again["norms"][0]["fetched_at"] = "1999-01-01T00:00:00Z"
    again["norms"][0]["provenance"]["fetched_at"] = "1999-01-01T00:00:00Z"
    (same / "manifest.json").write_text(json.dumps(again, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (same / "provenance" / provenance.name).write_text(
        json.dumps(again["norms"][0]["provenance"], ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    assert version_id(again["norms"]) == activated.version
    store.activate_tree(same, origin="manual")
    reread = _service(settings).read_provision("resolucao 1/2002-cn", "7")
    assert reread["norma"]["coletado_em"] == served_at
    assert reread["norma"]["natureza_texto"] == "historico"


def test_origin_follows_explicit_activation_and_not_a_later_bundle(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    store = OverlayStore(settings)
    store.activate_tree(package_bundle(), origin="bundle")
    store.activate_tree(package_bundle(), origin="manual")
    assert store.status()["origem"] == "manual"
    newer = _tiny_package(tmp_path / "newer-package")
    monkeypatch.setattr("leis_mcp.overlay.store.package_bundle", lambda: newer)
    assert store.ensure_active().version != json.loads((newer / "manifest.json").read_text())["version"]
    store.activate_tree(package_bundle(), origin="bundle")
    # package_bundle is patched, so this activates the tiny package as bundle.
    assert store.status()["origem"] == "bundle"
    newest = _tiny_package(tmp_path / "newest-package", sentence="Texto mais novo da imagem.")
    monkeypatch.setattr("leis_mcp.overlay.store.package_bundle", lambda: newest)
    upgraded = store.ensure_active()
    assert upgraded.version == json.loads((newest / "manifest.json").read_text())["version"]
    assert "mais novo" in _service(settings).read_provision("norma piloto", "1")["dispositivo"]["texto"]


def test_rollback_validates_before_changing_the_pointer(tmp_path):
    settings = _settings(tmp_path)
    store = OverlayStore(settings)
    first = _tiny_package(tmp_path / "b1", sentence="Texto alfa da primeira versão.")
    second = _tiny_package(tmp_path / "b2", sentence="Texto beta da segunda versão.")
    store.activate_tree(first, origin="manual")
    store.activate_tree(second, origin="manual")
    previous = store.status()["anterior"]
    published = settings.overlay_dir / "versions" / previous
    (published / "manifest.json").write_text("{}", encoding="utf-8")
    before = (settings.overlay_dir / "active.json").read_bytes()
    with pytest.raises(OverlayError):
        store.rollback()
    assert (settings.overlay_dir / "active.json").read_bytes() == before
    assert "Texto beta" in _service(settings).read_provision("norma piloto", "1")["dispositivo"]["texto"]


def test_activation_stops_when_free_space_is_below_the_tree_cap(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    monkeypatch.setattr(
        "leis_mcp.overlay.store.shutil.disk_usage",
        lambda path: type("Usage", (), {"total": 10, "used": 9, "free": 1})(),
    )
    with pytest.raises(OverlayError, match="Espaço livre"):
        OverlayStore(settings).activate_tree(package_bundle(), origin="bundle")
    assert not (settings.overlay_dir / "active.json").exists()


def test_malformed_manifest_fields_and_index_values_do_not_abort_boot(tmp_path):
    settings = _settings(tmp_path)
    store = OverlayStore(settings)
    active = store.activate_tree(package_bundle(), origin="manual")
    pointer = (settings.overlay_dir / "active.json").read_bytes()
    manifest_path = active.root / "manifest.json"
    original = manifest_path.read_text(encoding="utf-8")

    def boot_without_overlay(corrupt) -> None:
        manifest_path.write_text(corrupt if isinstance(corrupt, str) else json.dumps(corrupt), encoding="utf-8")
        assert store.status()["ativo"] is False
        service = _service(settings)
        assert "overlay_version" not in service.status()
        assert service.find_norms("Resolução nº 1/2002 do Congresso Nacional")["total"] == 0
        assert (settings.overlay_dir / "active.json").read_bytes() == pointer
        app = LeisServer(service).http_app(api_key="test-key")
        client = TestClient(app)
        assert client.get("/health").status_code == 200
        assert client.post("/mcp").status_code == 401

    payload = json.loads(original)
    del payload["norms"][0]["blob"]
    boot_without_overlay(payload)
    payload = json.loads(original)
    del payload["norms"][0]["provenance"]
    boot_without_overlay(payload)
    payload = json.loads(original)
    payload["norms"][0]["aliases"] = "resolucao"
    boot_without_overlay(payload)
    payload = json.loads(original)
    payload["norms"][0]["year"] = "dois mil"
    boot_without_overlay(payload)
    payload = json.loads(original)
    payload["norms"][0]["vigencia"] = None
    boot_without_overlay(payload)
    payload = json.loads(original)
    payload["norms"][0]["blob"] = "not-a-git-blob"
    boot_without_overlay(payload)
    manifest_path.write_text(original, encoding="utf-8")
    with sqlite3.connect(active.db_path) as db:
        db.execute("UPDATE meta SET value = 'nonnumeric' WHERE key = 'total'")
        db.commit()
    assert store.status()["ativo"] is False
    assert _service(settings).find_norms("resolucao 1/2002-cn")["total"] == 0
    with sqlite3.connect(active.db_path) as db:
        db.execute("UPDATE meta SET value = 'oops' WHERE key = 'total'")
        db.commit()
    assert store.status()["ativo"] is False
    assert _service(settings).find_norms("resolucao 1/2002-cn")["total"] == 0
    with sqlite3.connect(active.db_path) as db:
        db.execute("UPDATE meta SET value = NULL WHERE key = 'total'")
        db.commit()
    assert store.status()["ativo"] is False
    manifest_path.write_text(original, encoding="utf-8")
    (active.root / "provenance").joinpath(
        active.manifest["norms"][0]["canonical_id"].replace(":", "_") + ".json"
    ).write_text("{", encoding="utf-8")
    assert store.status()["ativo"] is False
    repaired = store.activate_tree(package_bundle(), origin="manual")
    assert repaired.indexed_rows == 6
    assert ART_7 in _service(settings).read_provision("resolucao 1/2002-cn", "7")["dispositivo"]["texto"]


def test_collect_cannot_replace_an_active_snapshot_and_relative_bundle_works(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    store = OverlayStore(settings)
    active = store.activate_tree(package_bundle(), origin="bundle")
    ready = (active.root / "READY").read_bytes()
    database = (active.root / "index.sqlite").read_bytes()
    pointer = (settings.overlay_dir / "active.json").read_bytes()
    protected = collection_protected(settings)
    previous_id = "p-" + "ab" * 10
    previous = settings.overlay_dir / "versions" / previous_id
    shutil.copytree(active.root, previous)
    inactive_id = "p-" + "cd" * 10
    inactive = settings.overlay_dir / "versions" / inactive_id
    shutil.copytree(active.root, inactive)
    for target in (active.root, previous, inactive):
        ready_bytes = (target / "READY").read_bytes()
        database_bytes = (target / "index.sqlite").read_bytes()
        with pytest.raises(OverlayError):
            write_bundle(target, [], [], replace=True, protected=protected)
        assert (target / "READY").read_bytes() == ready_bytes
        assert (target / "index.sqlite").read_bytes() == database_bytes
        assert (settings.overlay_dir / "active.json").read_bytes() == pointer
    link = tmp_path / "linked-snapshot"
    link.symlink_to(active.root, target_is_directory=True)
    with pytest.raises(OverlayError):
        write_bundle(link, [], [], replace=True, protected=protected)
    parent_link = tmp_path / "linked-versions"
    parent_link.symlink_to(settings.overlay_dir / "versions", target_is_directory=True)
    with pytest.raises(OverlayError):
        write_bundle(parent_link / active.version, [], [], replace=True, protected=protected)
    marker = settings.home / "keep-me"
    marker.write_text("ok", encoding="utf-8")
    with pytest.raises(OverlayError):
        write_bundle(settings.home.parent, [], [], replace=True, protected=protected)
    assert marker.read_text(encoding="utf-8") == "ok"
    assert (active.root / "READY").read_bytes() == ready
    assert (active.root / "index.sqlite").read_bytes() == database
    assert (settings.overlay_dir / "active.json").read_bytes() == pointer

    monkeypatch.chdir(tmp_path)
    relative = _tiny_package(Path("relative-bundle"), sentence="Texto do pacote relativo.")
    assert (tmp_path / "relative-bundle" / "manifest.json").is_file()
    assert relative == Path("relative-bundle") or (tmp_path / "relative-bundle").exists()
    write_bundle(
        Path("relative-bundle"), _norms_from(tmp_path / "relative-bundle", "Texto substituído."), [], replace=True
    )
    assert "Texto substituído." in (tmp_path / "relative-bundle" / "norms").rglob(
        "*.md"
    ).__iter__().__next__().read_text(encoding="utf-8")


def test_canonical_id_and_versions_symlink_do_not_write_outside(tmp_path):
    settings = _settings(tmp_path)
    outside = tmp_path / "outside-provenance.json"
    planted = tmp_path / "bad-id"
    shutil.copytree(package_bundle(), planted)
    manifest = json.loads((planted / "manifest.json").read_text(encoding="utf-8"))
    manifest["norms"][0]["canonical_id"] = str(outside)
    (planted / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(OverlayError):
        OverlayStore(settings).activate_tree(planted, origin="manual")
    assert not outside.exists()
    norms = _norms_from(tmp_path / "unused", "Texto interno.")
    norms[0]["canonical_id"] = str(outside)
    norms[0]["provenance"]["canonical_id"] = str(outside)
    with pytest.raises(OverlayError):
        write_bundle(tmp_path / "escaped-bundle", norms, [])
    assert not outside.exists()
    assert not (tmp_path / "escaped-bundle").exists()

    external = tmp_path / "external-versions"
    external.mkdir()
    versions = settings.overlay_dir / "versions"
    versions.mkdir(parents=True, exist_ok=True)
    if versions.exists() and not versions.is_symlink():
        shutil.rmtree(versions)
    versions.symlink_to(external, target_is_directory=True)
    with pytest.raises(OverlayError):
        OverlayStore(settings).activate_tree(package_bundle(), origin="bundle")
    assert list(external.iterdir()) == []
    assert not (settings.overlay_dir / "active.json").exists()


def test_renamed_version_and_duplicate_index_rows_are_unusable(tmp_path):
    settings = _settings(tmp_path)
    store = OverlayStore(settings)
    active = store.activate_tree(package_bundle(), origin="manual")
    pointer = json.loads((settings.overlay_dir / "active.json").read_text(encoding="utf-8"))
    renamed = "p-" + "ab" * 10
    (settings.overlay_dir / "versions" / active.version).rename(settings.overlay_dir / "versions" / renamed)
    pointer["version"] = renamed
    (settings.overlay_dir / "active.json").write_text(json.dumps(pointer), encoding="utf-8")
    assert store.status()["ativo"] is False
    with pytest.raises(OverlayError):
        store.ensure_active()
    assert _service(settings).find_norms("resolucao 1/2002-cn")["total"] == 0

    store.activate_tree(package_bundle(), origin="manual")
    fresh = OverlayStore(settings).ensure_active()
    with sqlite3.connect(fresh.db_path) as db:
        db.execute("DROP INDEX ix_path")
        row = db.execute(
            "SELECT path, blob, sphere, kind, year, number, date, state, city, agency FROM norms LIMIT 1"
        ).fetchone()
        db.execute("DELETE FROM norms")
        db.executemany(
            "INSERT INTO norms (path, blob, sphere, kind, year, number, date, state, city, agency) VALUES (?,?,?,?,?,?,?,?,?,?)",
            [row for _ in range(6)],
        )
        db.commit()
    assert store.status()["ativo"] is False
    with pytest.raises(OverlayError):
        store.ensure_active()
    assert _service(settings).find_norms("Resolução nº 1/2002 do Congresso Nacional")["total"] == 0
    store.activate_tree(package_bundle(), origin="manual")
    shifted = store.ensure_active()
    with sqlite3.connect(shifted.db_path) as db:
        db.execute(
            "UPDATE norms SET path = ? WHERE rowid = (SELECT min(rowid) FROM norms)",
            ("parlamentar/federal/congresso-nacional/resolucoes/2002/resolucao-9-2002.md",),
        )
        db.commit()
    assert store.status()["ativo"] is False
    assert _service(settings).find_norms("resolucao 1/2002-cn")["total"] == 0


def _norms_from(directory: Path, sentence: str):
    from leis_mcp.overlay.normalizer import html_to_markdown
    from leis_mcp.overlay.store import built_norm

    item = {
        "canonical_id": "br:federal:congresso-nacional:resolucao:9:1999",
        "organ": "congresso-nacional",
        "sphere": "federal",
        "kind": "resolucoes",
        "type_label": "Resolução",
        "number": "9",
        "year": 1999,
        "date": "1999-01-01",
        "title": "Norma piloto",
        "summary": "Texto de teste.",
        "source_url": "https://www2.camara.leg.br/legin/teste.html",
        "text_nature": "consolidado_pela_fonte",
        "text_version": "compilada pela fonte oficial; vigência não verificada de forma independente",
        "aliases": ["norma piloto"],
        "expect": {"min_articles": 1},
    }
    html = f"<p>Art. 1º {sentence}</p>"
    return [
        built_norm(
            item,
            html_to_markdown(html),
            raw=html.encode(),
            fetched_at="2026-01-01T00:00:00Z",
            final_url=item["source_url"],
        )
    ]


def test_paths_symlinks_and_collect_destination_are_confined(tmp_path):
    settings = _settings(tmp_path)
    store = OverlayStore(settings)
    outside = tmp_path / "outside.md"
    outside.write_text("segredo\n", encoding="utf-8")
    planted = tmp_path / "planted"
    shutil.copytree(package_bundle(), planted)
    manifest = json.loads((planted / "manifest.json").read_text(encoding="utf-8"))
    manifest["norms"][0]["path"] = "../outside.md"
    (planted / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    before = (
        (settings.overlay_dir / "active.json").read_bytes() if (settings.overlay_dir / "active.json").exists() else None
    )
    with pytest.raises(OverlayError):
        store.activate_tree(planted, origin="manual")
    assert outside.read_text(encoding="utf-8") == "segredo\n"
    current = (
        (settings.overlay_dir / "active.json").read_bytes() if (settings.overlay_dir / "active.json").exists() else None
    )
    assert current == before

    linked = tmp_path / "linked"
    shutil.copytree(package_bundle(), linked)
    target = next((linked / "norms").rglob("*.md"))
    target.unlink()
    target.symlink_to(outside)
    with pytest.raises(OverlayError):
        store.activate_tree(linked, origin="manual")
    assert outside.read_text(encoding="utf-8") == "segredo\n"

    marker = settings.home / "index.sqlite"
    marker.write_text("keep", encoding="utf-8")
    with pytest.raises(OverlayError):
        write_bundle(settings.home, [], [])
    assert marker.read_text(encoding="utf-8") == "keep"
    unrelated = tmp_path / "notes"
    unrelated.mkdir()
    (unrelated / "keep.txt").write_text("stay", encoding="utf-8")
    with pytest.raises(OverlayError):
        write_bundle(unrelated, [], [])
    assert (unrelated / "keep.txt").read_text(encoding="utf-8") == "stay"


def _tiny_package(directory: Path, sentence: str = "Texto alfa da primeira versão.") -> Path:
    from leis_mcp.overlay.normalizer import html_to_markdown
    from leis_mcp.overlay.store import built_norm

    item = {
        "canonical_id": "br:federal:congresso-nacional:resolucao:9:1999",
        "organ": "congresso-nacional",
        "sphere": "federal",
        "kind": "resolucoes",
        "type_label": "Resolução",
        "number": "9",
        "year": 1999,
        "date": "1999-01-01",
        "title": "Norma piloto",
        "summary": "Texto de teste.",
        "source_url": "https://www2.camara.leg.br/legin/teste.html",
        "text_nature": "consolidado_pela_fonte",
        "text_version": "compilada pela fonte oficial; vigência não verificada de forma independente",
        "aliases": ["norma piloto"],
        "expect": {"min_articles": 1},
    }
    html = f"<p>Art. 1º {sentence}</p>"
    norm = built_norm(
        item, html_to_markdown(html), raw=html.encode(), fetched_at="2026-01-01T00:00:00Z", final_url=item["source_url"]
    )
    write_bundle(directory, [norm], [])
    return directory
