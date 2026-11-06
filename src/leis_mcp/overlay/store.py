"""Versioned overlay directory: stage, activate by replacing a pointer, roll back.

The upstream index is a different file (`index.sqlite`). Rebuilding it leaves this directory in place.
"""

import hashlib
import json
import os
import shutil
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..config import Settings
from ..content.front_matter import FrontMatterParser
from ..errors import LeisMcpError
from ..legal_text.document_parser import DocumentParser
from ..models import Reference
from .render import git_blob, render_document, repository_path

_SCHEMA = """
    CREATE TABLE norms (
        path TEXT NOT NULL, blob BLOB NOT NULL, sphere TEXT NOT NULL, kind TEXT, year INTEGER,
        number TEXT, date TEXT, state TEXT, city TEXT, agency TEXT
    );
    CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
    CREATE UNIQUE INDEX ix_path ON norms(path);
    CREATE INDEX ix_number ON norms(number, year);
    CREATE INDEX ix_browse ON norms(sphere, kind, year);
    CREATE INDEX ix_blob ON norms(blob);
"""


class OverlayError(LeisMcpError):
    pass


def package_bundle() -> Path:
    return Path(__file__).resolve().parent.parent / "data" / "parliamentary" / "bundle"


def body_sha256(body: str) -> str:
    return hashlib.sha256(body.encode()).hexdigest()


def version_id(entries: list[dict[str, str]]) -> str:
    payload = "\n".join(
        f"{item['canonical_id']} {item['body_sha256']}"
        for item in sorted(entries, key=lambda item: item["canonical_id"])
    )
    return "p-" + hashlib.sha256(payload.encode()).hexdigest()[:20]


@dataclass(frozen=True, slots=True)
class ActiveOverlay:
    root: Path
    version: str
    origin: str
    manifest: dict[str, Any]

    @property
    def db_path(self) -> Path:
        return self.root / "index.sqlite"

    def files_by_blob(self) -> dict[str, Path]:
        return {item["blob"]: self.root / "norms" / item["path"] for item in self.manifest["norms"]}

    def files_by_path(self) -> dict[str, Path]:
        return {item["path"]: self.root / "norms" / item["path"] for item in self.manifest["norms"]}

    def alias_map(self) -> dict[str, Reference]:
        mapping: dict[str, Reference] = {}
        for item in self.manifest["norms"]:
            reference = Reference(
                path=item["path"],
                number=str(item["number"]),
                years=(int(item["year"]),) if item.get("year") else (),
                kind=item["kind"],
                sphere=item["sphere"],
                organ=item["organ"],
            )
            for alias in item.get("aliases", []):
                mapping[alias] = reference
        return mapping


class OverlayStore:
    def __init__(self, settings: Settings):
        self.root = settings.overlay_dir

    @property
    def versions(self) -> Path:
        return self.root / "versions"

    @property
    def pointer(self) -> Path:
        return self.root / "active.json"

    def ensure_active(self) -> ActiveOverlay | None:
        """Installs the packaged snapshot when there is no pointer, and replaces a bundle-origin pointer when the package changed.

        A pointer written by collect or rollback is left as-is, including across `leis-mcp update`.
        """
        bundled = self._packaged_manifest()
        active = self._read_pointer()
        if active is None:
            return self.activate_tree(package_bundle(), origin="bundle") if bundled else None
        if bundled and active.get("origin") == "bundle" and active.get("version") != bundled["version"]:
            return self.activate_tree(package_bundle(), origin="bundle")
        return self._open_pointer(active)

    def activate_tree(self, source: Path, *, origin: str) -> ActiveOverlay:
        manifest = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
        version = manifest["version"]
        target = self.versions / version
        if not target.exists():
            self._stage_copy(source, target)
            self._build_index(target, manifest)
        current = self._read_pointer()
        if current and current.get("version") == version:
            return self._open(target, origin=current.get("origin", origin))
        self._write_pointer(
            {"version": version, "origin": origin, "previous": current.get("version") if current else None}
        )
        return self._open(target, origin=origin)

    def rollback(self) -> ActiveOverlay:
        current = self._read_pointer()
        previous = current.get("previous") if current else None
        if not current or not previous:
            raise OverlayError("Não há versão anterior do acervo parlamentar para restaurar.")
        target = self.versions / previous
        if not (target / "manifest.json").exists():
            raise OverlayError(f"A versão anterior não está mais no disco: {previous}")
        self._write_pointer({"version": previous, "origin": "rollback", "previous": current["version"]})
        return self._open_pointer(self._read_pointer())

    def status(self) -> dict[str, Any]:
        pointer = self._read_pointer()
        if not pointer:
            return {"ativo": False}
        try:
            active = self._open_pointer(pointer)
        except OverlayError as exc:
            return {"ativo": False, "erro": str(exc), "ponteiro": pointer}
        return {
            "ativo": True,
            "versao": active.version,
            "origem": active.origin,
            "anterior": pointer.get("previous"),
            "normas": len(active.manifest.get("norms", [])),
            "nao_exaustivo": active.manifest.get("not_exhaustive", True),
            "diretorio": str(active.root),
        }

    def _packaged_manifest(self) -> dict[str, Any] | None:
        path = package_bundle() / "manifest.json"
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def _stage_copy(self, source: Path, target: Path) -> None:
        staging = self.root / "staging" / target.name
        if staging.exists():
            shutil.rmtree(staging)
        staging.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source, staging)
        target.parent.mkdir(parents=True, exist_ok=True)
        staging.rename(target)

    def _build_index(self, directory: Path, manifest: dict[str, Any]) -> None:
        database = directory / "index.sqlite"
        with closing(sqlite3.connect(database)) as db:
            db.executescript(_SCHEMA)
            for item in manifest["norms"]:
                file_path = directory / "norms" / item["path"]
                data = file_path.read_bytes()
                if git_blob(data.decode()) != item["blob"]:
                    raise OverlayError(f"Hash divergente ao ativar {item['path']}.")
                db.execute(
                    "INSERT INTO norms (path, blob, sphere, kind, year, number, date, state, city, agency)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?)",
                    (
                        item["path"],
                        bytes.fromhex(item["blob"]),
                        item["sphere"],
                        item["kind"],
                        item.get("year"),
                        str(item["number"]),
                        item.get("date"),
                        item["organ"],
                    ),
                )
            db.executemany(
                "INSERT INTO meta VALUES (?, ?)",
                [
                    ("commit", manifest["version"]),
                    ("total", str(len(manifest["norms"]))),
                    ("kind", "parliamentary-overlay"),
                ],
            )
            db.commit()

    def _open_pointer(self, pointer: dict[str, Any]) -> ActiveOverlay:
        directory = self.versions / pointer["version"]
        if not (directory / "manifest.json").exists():
            raise OverlayError(f"Versão ativa do acervo parlamentar ausente: {pointer['version']}")
        return self._open(directory, origin=pointer.get("origin", "unknown"))

    def _open(self, directory: Path, *, origin: str) -> ActiveOverlay:
        manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
        if not (directory / "index.sqlite").exists():
            self._build_index(directory, manifest)
        return ActiveOverlay(directory, manifest["version"], origin, manifest)

    def _read_pointer(self) -> dict[str, Any] | None:
        if not self.pointer.exists():
            return None
        return json.loads(self.pointer.read_text(encoding="utf-8"))

    def _write_pointer(self, payload: dict[str, Any]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        temporary = self.pointer.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, self.pointer)


def check_document(markdown: str, expect: dict[str, Any] | None) -> int:
    """Returns how many article keys the existing parser found. Raises OverlayError when `expect` fails."""
    source = FrontMatterParser().parse(markdown)
    document = DocumentParser().parse(source.body)
    count = sum(len(block.articles) for block in document.blocks)
    if not expect:
        return count
    minimum = int(expect.get("min_articles", 1))
    if count < minimum:
        raise OverlayError(f"Parser encontrou {count} artigos; o mínimo desta fonte é {minimum}.")
    for snippet in expect.get("contains", []):
        if snippet not in source.body:
            raise OverlayError(f"Texto obrigatório ausente após a normalização: {snippet[:80]}")
    return count


def write_bundle(directory: Path, norms: list[dict[str, Any]], gaps: list[dict[str, Any]]) -> dict[str, Any]:
    """Writes norms, provenance and manifest. Does not write the SQLite index (activation does)."""
    if directory.exists():
        shutil.rmtree(directory)
    entries = [{"canonical_id": item["canonical_id"], "body_sha256": item["body_sha256"]} for item in norms]
    manifest = {
        "version": version_id(entries),
        "not_exhaustive": True,
        "norms": norms,
        "gaps": gaps,
    }
    for item in norms:
        target = directory / "norms" / item["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(item.pop("_markdown"), encoding="utf-8")
        provenance = directory / "provenance" / f"{item['canonical_id'].replace(':', '_')}.json"
        provenance.parent.mkdir(parents=True, exist_ok=True)
        provenance.write_text(json.dumps(item["provenance"], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (directory / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return manifest


def built_norm(
    item: dict[str, Any],
    body: str,
    *,
    raw: bytes,
    fetched_at: str,
    final_url: str,
) -> dict[str, Any]:
    digest = body_sha256(body)
    markdown = render_document(item, body, collected_at=fetched_at, content_hash=digest)
    check_document(markdown, item.get("expect"))
    path = repository_path(item)
    provenance = {
        "canonical_id": item["canonical_id"],
        "source_url": item["source_url"],
        "fetched_url": final_url,
        "fetched_at": fetched_at,
        "raw_sha256": hashlib.sha256(raw).hexdigest(),
        "body_sha256": digest,
        "text_nature": item["text_nature"],
        "vigencia": "nao_verificada",
        "historical_url": item.get("historical_url"),
        "notes": item.get("notes"),
    }
    return {
        "canonical_id": item["canonical_id"],
        "path": path,
        "blob": git_blob(markdown),
        "organ": item["organ"],
        "sphere": item["sphere"],
        "kind": item["kind"],
        "number": str(item["number"]),
        "year": item["year"],
        "date": item["date"],
        "source_url": item["source_url"],
        "fetched_at": fetched_at,
        "body_sha256": digest,
        "text_nature": item["text_nature"],
        "vigencia": "nao_verificada",
        "aliases": list(item.get("aliases", [])),
        "provenance": provenance,
        "_markdown": markdown,
    }
