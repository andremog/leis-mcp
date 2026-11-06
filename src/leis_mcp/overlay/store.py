"""Versioned overlay directory: validate in staging, then publish, then move the pointer.

The upstream index is a different file (`index.sqlite`). Rebuilding it leaves this directory in place.
"""

import hashlib
import json
import os
import re
import secrets
import shutil
import sqlite3
import stat
from contextlib import closing, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..config import Settings
from ..content.front_matter import FrontMatterParser
from ..legal_text.document_parser import DocumentParser
from ..models import Reference
from .identity import (
    norm_identity,
    provenance_basename,
    require_canonical,
    require_norm_path,
    require_version,
    version_id,
)
from .render import git_blob, render_document, repository_path
from .store_errors import OverlayError

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
_MAX_FILES = 500
_MAX_FILE_BYTES = 8 * 1024 * 1024
_MAX_TREE_BYTES = 32 * 1024 * 1024
_BUNDLE_KIND = "leis-mcp-parliamentary-bundle"
_PROTECTED_ROOTS = {Path("/"), Path("/data"), Path("/usr"), Path("/etc"), Path("/var"), Path("/home"), Path("/bin")}


def package_bundle() -> Path:
    return Path(__file__).resolve().parent.parent / "data" / "parliamentary" / "bundle"


def body_sha256(body: str) -> str:
    return hashlib.sha256(body.encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class ActiveOverlay:
    root: Path
    version: str
    origin: str
    manifest: dict[str, Any]
    indexed_rows: int

    @property
    def db_path(self) -> Path:
        return self.root / "index.sqlite"

    def files_by_blob(self) -> dict[str, Path]:
        return {item["blob"]: _norm_file(self.root, item["path"]) for item in self.manifest["norms"]}

    def files_by_path(self) -> dict[str, Path]:
        return {item["path"]: _norm_file(self.root, item["path"]) for item in self.manifest["norms"]}

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
        self._protected = collection_protected(settings)

    @property
    def versions(self) -> Path:
        return self.root / "versions"

    @property
    def pointer(self) -> Path:
        return self.root / "active.json"

    def ensure_active(self) -> ActiveOverlay | None:
        """Installs the packaged snapshot when there is no pointer, and replaces a bundle-origin pointer when the package changed.

        A pointer written by collect, manual activation or rollback is left as-is, including across `leis-mcp update`.
        """
        with self._locked():
            return self._ensure_active()

    def activate_tree(self, source: Path, *, origin: str) -> ActiveOverlay:
        with self._locked():
            return self._activate_tree(source, origin=origin)

    def rollback(self) -> ActiveOverlay:
        with self._locked():
            current = self._read_pointer()
            previous = current.get("previous") if current else None
            if not current or not previous:
                raise OverlayError("Não há versão anterior do acervo parlamentar para restaurar.")
            require_version(previous)
            require_version(current["version"])
            # Validate before any pointer byte changes.
            verified = self._verify_published(self._version_dir(previous), origin="rollback", expected_version=previous)
            before = self.pointer.read_bytes()
            try:
                self._write_pointer({"version": previous, "origin": "rollback", "previous": current["version"]})
                return verified
            except Exception:
                if self.pointer.exists() or True:
                    self.pointer.write_bytes(before)
                raise

    def status(self) -> dict[str, Any]:
        try:
            pointer = self._read_pointer()
        except (OverlayError, json.JSONDecodeError, UnicodeError, OSError) as exc:
            return {"ativo": False, "erro": str(exc)}
        if not pointer:
            return {"ativo": False}
        try:
            active = self._verify_published(
                self._version_dir(pointer["version"]),
                origin=str(pointer.get("origin")),
                expected_version=pointer["version"],
            )
        except (OverlayError, json.JSONDecodeError, UnicodeError, OSError) as exc:
            return {"ativo": False, "erro": str(exc), "ponteiro": pointer.get("version")}
        return {
            "ativo": True,
            "versao": active.version,
            "origem": pointer.get("origin"),
            "anterior": pointer.get("previous"),
            "normas": active.indexed_rows,
            "nao_exaustivo": active.manifest.get("not_exhaustive", True),
            "diretorio": str(active.root),
        }

    def _ensure_active(self) -> ActiveOverlay | None:
        bundled = self._packaged_manifest()
        active = self._read_pointer()
        if active is None:
            return self._activate_tree(package_bundle(), origin="bundle") if bundled else None
        if bundled and active.get("origin") == "bundle" and active.get("version") != bundled["version"]:
            return self._activate_tree(package_bundle(), origin="bundle")
        return self._verify_published(
            self._version_dir(active["version"]), origin=str(active.get("origin")), expected_version=active["version"]
        )

    def _activate_tree(self, source: Path, *, origin: str) -> ActiveOverlay:
        manifest = _load_manifest(source / "manifest.json")
        version = require_version(manifest["version"])
        if version != version_id(manifest["norms"]):
            raise OverlayError("A versão declarada não confere com o conteúdo e os metadados.")
        _reject_special(source)
        current = self._read_pointer()
        target = self._publish(source, manifest, current)
        if current and current.get("version") == version and current.get("origin") == origin:
            return self._verify_published(target, origin=origin, expected_version=version)
        previous = None if not current or current.get("version") == version else current.get("version")
        if current and current.get("version") == version:
            previous = current.get("previous")
        before = self.pointer.read_bytes() if self.pointer.exists() else None
        try:
            self._write_pointer({"version": version, "origin": origin, "previous": previous})
            return self._verify_published(target, origin=origin, expected_version=version)
        except Exception:
            if before is None:
                self.pointer.unlink(missing_ok=True)
            else:
                self.pointer.write_bytes(before)
            raise

    def _publish(self, source: Path, manifest: dict[str, Any], pointer: dict[str, Any] | None) -> Path:
        _refuse_symlink_chain(self.root)
        self._purge_staging()
        version = manifest["version"]
        target = self._version_dir(version)
        if target.exists() or target.is_symlink():
            try:
                if target.is_symlink():
                    raise OverlayError("Versão publicada não pode ser um link.")
                self._verify_published(target, origin="verified", expected_version=version)
                return target
            except OverlayError:
                referenced = pointer and version in (pointer.get("version"), pointer.get("previous"))
                staging = self._build_staging(source, manifest)
                try:
                    self._verify_tree(staging, manifest)
                except OverlayError:
                    shutil.rmtree(staging, ignore_errors=True)
                    raise
                if referenced:
                    # The pointer still names this id. Swap only after the replacement verifies.
                    self._swap_in(staging, target)
                else:
                    self._retire(target)
                    self._swap_in(staging, target)
                return target
        staging = self._build_staging(source, manifest)
        try:
            self._verify_tree(staging, manifest)
            _fsync_tree(staging)
            target.parent.mkdir(parents=True, exist_ok=True)
            staging.rename(target)
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise
        self._verify_published(target, origin="verified", expected_version=version)
        return target

    def _build_staging(self, source: Path, manifest: dict[str, Any]) -> Path:
        staging_root = self.root / "staging"
        if staging_root.is_symlink():
            raise OverlayError("Diretório de staging não pode ser um link.")
        staging_root.mkdir(parents=True, exist_ok=True)
        _require_space(staging_root)
        staging = staging_root / f".{manifest['version']}.{os.getpid()}.{secrets.token_hex(4)}"
        try:
            if staging.exists():
                shutil.rmtree(staging)
            _copy_regular_tree(source, staging)
            self._build_index(staging, manifest)
            (staging / "READY").write_text("ok\n", encoding="utf-8")
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise
        return staging

    def _swap_in(self, staging: Path, target: Path) -> None:
        retired = target.with_name(f".retired-{target.name}-{secrets.token_hex(4)}")
        moved = False
        try:
            if target.exists() or target.is_symlink():
                target.rename(retired)
                moved = True
            staging.rename(target)
        except Exception:
            if moved and not target.exists() and retired.exists():
                retired.rename(target)
            shutil.rmtree(staging, ignore_errors=True)
            raise
        else:
            if moved:
                shutil.rmtree(retired, ignore_errors=True)
        self._verify_published(target, origin="verified", expected_version=target.name)

    def _purge_staging(self) -> None:
        """Drops unpublished staging leftovers. Active and previous versions live under versions/."""
        staging = self.root / "staging"
        if staging.is_symlink():
            raise OverlayError("Diretório de staging não pode ser um link.")
        if staging.exists():
            shutil.rmtree(staging)

    def _retire(self, target: Path) -> None:
        if target.is_symlink():
            target.unlink()
            return
        retired = target.with_name(f".rejected-{target.name}-{secrets.token_hex(4)}")
        target.rename(retired)

    def _build_index(self, directory: Path, manifest: dict[str, Any]) -> None:
        database = directory / "index.sqlite"
        if database.exists() or database.is_symlink():
            database.unlink()
        with closing(sqlite3.connect(database)) as db:
            db.executescript(_SCHEMA)
            for item in manifest["norms"]:
                file_path = _norm_file(directory, item["path"])
                data = _read_bounded(file_path)
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
                    ("ready", "1"),
                ],
            )
            db.commit()

    def _verify_published(self, directory: Path, *, origin: str, expected_version: str) -> ActiveOverlay:
        try:
            if directory.is_symlink() or not directory.is_dir():
                raise OverlayError("Versão do overlay ausente ou inválida.")
            if directory.name != expected_version:
                raise OverlayError("Diretório da versão não confere com o ponteiro.")
            if not (directory / "READY").is_file() or (directory / "READY").is_symlink():
                raise OverlayError("Versão do overlay incompleta.")
            manifest = _load_manifest(directory / "manifest.json")
            if manifest["version"] != expected_version:
                raise OverlayError("Manifesto não confere com a versão esperada.")
            self._verify_tree(directory, manifest)
            rows = _index_rows(directory / "index.sqlite", manifest)
        except OverlayError:
            raise
        except (KeyError, ValueError, TypeError, json.JSONDecodeError, UnicodeError, sqlite3.Error) as exc:
            raise OverlayError("Versão do overlay ilegível.") from exc
        return ActiveOverlay(directory, manifest["version"], origin, manifest, rows)

    def _verify_tree(self, directory: Path, manifest: dict[str, Any]) -> None:
        if manifest.get("version") != version_id(manifest["norms"]):
            raise OverlayError("A versão declarada não confere com o conteúdo e os metadados.")
        paths: set[str] = set()
        total = 0
        for item in manifest["norms"]:
            path = require_norm_path(item["path"])
            if path in paths:
                raise OverlayError("Caminho de norma repetido.")
            paths.add(path)
            file_path = _norm_file(directory, path)
            data = _read_bounded(file_path)
            total += len(data)
            if total > _MAX_TREE_BYTES:
                raise OverlayError("Pacote parlamentar acima do limite.")
            if git_blob(data.decode()) != item["blob"]:
                raise OverlayError(f"Hash divergente em {path}.")
            text = data.decode()
            parsed = FrontMatterParser().parse(text)
            body = parsed.body[1:] if parsed.body.startswith("\n") else parsed.body
            if not body.endswith("\n"):
                body += "\n"
            if body_sha256(body) != item["body_sha256"]:
                raise OverlayError(f"Hash do corpo divergente em {path}.")
            _metadata_matches(parsed.metadata, item)
            provenance = _provenance_file(directory, item["canonical_id"])
            try:
                stored = json.loads(_read_bounded(provenance).decode())
            except json.JSONDecodeError as exc:
                raise OverlayError(f"Proveniência ilegível em {path}.") from exc
            if not isinstance(stored, dict) or stored != item["provenance"]:
                raise OverlayError(f"Proveniência divergente em {path}.")
        if len(paths) != len(manifest["norms"]):
            raise OverlayError("Manifesto parlamentar inválido.")

    def _version_dir(self, version: str) -> Path:
        require_version(version)
        _refuse_symlink_chain(self.root)
        if self.versions.is_symlink():
            raise OverlayError("Diretório de versões não pode ser um link.")
        directory = self.versions / version
        if directory.is_symlink():
            raise OverlayError("Versão do overlay não pode ser um link.")
        root = self.versions.resolve()
        resolved = directory.resolve() if directory.exists() else directory.resolve()
        try:
            resolved.relative_to(root)
        except ValueError as exc:
            raise OverlayError("Versão fora do diretório do overlay.") from exc
        return directory

    def _packaged_manifest(self) -> dict[str, Any] | None:
        path = package_bundle() / "manifest.json"
        if not path.exists():
            return None
        manifest = _load_manifest(path)
        require_version(manifest["version"])
        return manifest

    def _read_pointer(self) -> dict[str, Any] | None:
        if not self.pointer.exists():
            return None
        if self.pointer.is_symlink():
            raise OverlayError("Ponteiro do overlay não pode ser um link.")
        try:
            payload = json.loads(self.pointer.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise OverlayError("Ponteiro do overlay ilegível.") from exc
        if not isinstance(payload, dict):
            raise OverlayError("Ponteiro do overlay ilegível.")
        require_version(payload.get("version"))
        if payload.get("previous") not in (None,):
            require_version(payload.get("previous"))
        if payload.get("origin") not in {"bundle", "manual", "collect", "rollback"}:
            raise OverlayError("Origem do overlay desconhecida.")
        return payload

    def _write_pointer(self, payload: dict[str, Any]) -> None:
        require_version(payload["version"])
        if payload.get("previous") is not None:
            require_version(payload["previous"])
        self.root.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile_pointer(self.root)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.pointer)
            _fsync_dir(self.root)
        except Exception:
            Path(temporary).unlink(missing_ok=True)
            raise

    @contextmanager
    def _locked(self):
        self.root.mkdir(parents=True, exist_ok=True)
        lock_path = self.root / ".lock"
        with lock_path.open("a+", encoding="utf-8") as handle:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def tempfile_pointer(root: Path) -> tuple[int, str]:
    return tempfile_mkstemp(root)


def tempfile_mkstemp(root: Path) -> tuple[int, str]:
    import tempfile

    return tempfile.mkstemp(prefix=".active-", suffix=".tmp", dir=root)


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


def write_bundle(
    directory: Path,
    norms: list[dict[str, Any]],
    gaps: list[dict[str, Any]],
    *,
    replace: bool = False,
    protected: tuple[Path, ...] = (),
) -> dict[str, Any]:
    """Writes a bundle beside `directory` and swaps it in only when that is allowed.

    A nonempty destination is refused unless it is already a parliamentary bundle and `replace` is set.
    Application home, the upstream checkout, cache, overlay versions and filesystem roots are never deleted.
    """
    destination = Path(directory)
    _refuse_protected(destination, protected)
    if destination.exists() and not replace:
        raise OverlayError("Destino já existe. A coleta não apaga um diretório sem --replace.")
    if destination.exists() and not _recognised_bundle(destination):
        raise OverlayError("Destino existente não é um pacote parlamentar reconhecido.")
    parent = destination.parent
    parent.mkdir(parents=True, exist_ok=True)
    staging = parent / f".{destination.name}.staging-{os.getpid()}-{secrets.token_hex(4)}"
    manifest = _manifest_for(norms, gaps)
    try:
        _write_bundle_files(staging, norms, manifest)
        _fsync_tree(staging)
        if destination.exists():
            displaced = parent / f".{destination.name}.replaced-{secrets.token_hex(4)}"
            destination.rename(displaced)
            try:
                staging.rename(destination)
            except Exception:
                if not destination.exists():
                    displaced.rename(destination)
                raise
            shutil.rmtree(displaced, ignore_errors=True)
        else:
            staging.rename(destination)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
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
    require_norm_path(path)
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
        "title": item["title"],
        "summary": item["summary"],
        "publication": item.get("publication") or "",
        "source_url": item["source_url"],
        "fetched_at": fetched_at,
        "body_sha256": digest,
        "text_nature": item["text_nature"],
        "text_version": item["text_version"],
        "vigencia": "nao_verificada",
        "aliases": list(item.get("aliases", [])),
        "provenance": provenance,
        "_markdown": markdown,
    }


def _manifest_for(norms: list[dict[str, Any]], gaps: list[dict[str, Any]]) -> dict[str, Any]:
    public = [{key: value for key, value in item.items() if key != "_markdown"} for item in norms]
    return {
        "kind": _BUNDLE_KIND,
        "version": version_id(public),
        "not_exhaustive": True,
        "norms": public,
        "gaps": gaps,
    }


def _write_bundle_files(directory: Path, norms: list[dict[str, Any]], manifest: dict[str, Any]) -> None:
    directory.mkdir(parents=True)
    for item in manifest["norms"]:
        source = next(candidate for candidate in norms if candidate["canonical_id"] == item["canonical_id"])
        target = _norm_path(directory, item["path"])
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source["_markdown"], encoding="utf-8")
        provenance = _provenance_path(directory, item["canonical_id"])
        provenance.parent.mkdir(parents=True, exist_ok=True)
        provenance.write_text(json.dumps(item["provenance"], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (directory / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def _load_manifest(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise OverlayError("Manifesto parlamentar ausente.")
    try:
        payload = json.loads(_read_bounded(path).decode())
    except (json.JSONDecodeError, UnicodeError) as exc:
        raise OverlayError("Manifesto parlamentar ilegível.") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("norms"), list) or not payload["norms"]:
        raise OverlayError("Manifesto parlamentar inválido.")
    if payload.get("kind") not in (None, _BUNDLE_KIND):
        raise OverlayError("Manifesto parlamentar inválido.")
    try:
        require_version(payload.get("version"))
        for item in payload["norms"]:
            if not isinstance(item, dict):
                raise OverlayError("Manifesto parlamentar inválido.")
            _require_manifest_norm(item)
    except (KeyError, TypeError, ValueError) as exc:
        raise OverlayError("Manifesto parlamentar inválido.") from exc
    return payload


def _require_manifest_norm(item: dict[str, Any]) -> None:
    """Every field later read from a persisted norm, not only the identity hash."""
    norm_identity(item)
    require_canonical(item.get("canonical_id"))
    require_norm_path(item.get("path"))
    blob = item.get("blob")
    if not isinstance(blob, str) or not re.fullmatch(r"[0-9a-fA-F]{40}", blob):
        raise OverlayError("Hash do manifesto recusado.")
    provenance = item.get("provenance")
    if not isinstance(provenance, dict) or provenance.get("canonical_id") != item["canonical_id"]:
        raise OverlayError("Proveniência do manifesto recusada.")
    if not isinstance(item.get("vigencia"), str) or not item["vigencia"]:
        raise OverlayError("Vigência do manifesto recusada.")
    year = item.get("year")
    if isinstance(year, bool) or not isinstance(year, int):
        raise OverlayError("Ano do manifesto recusado.")


def _metadata_matches(metadata: dict[str, Any], item: dict[str, Any]) -> None:
    expected = {
        "titulo": item.get("title") or "",
        "ementa": item.get("summary") or "",
        "publicacao": item.get("publication") or "",
        "versao_texto": item["text_version"],
        "natureza_texto": item["text_nature"],
        "vigencia": item["vigencia"],
        "fonte": item["source_url"],
        "identificador": item["canonical_id"],
        "orgao": item["organ"],
    }
    for key, value in expected.items():
        if str(metadata.get(key) or "") != str(value):
            raise OverlayError(f"Metadado {key} divergente em {item['path']}.")


def _index_rows(database: Path, manifest: dict[str, Any]) -> int:
    if database.is_symlink() or not database.is_file():
        raise OverlayError("Índice do overlay ausente.")
    try:
        with closing(sqlite3.connect(f"{database.resolve().as_uri()}?mode=ro", uri=True)) as db:
            ready = db.execute("SELECT value FROM meta WHERE key = 'ready'").fetchone()
            commit = db.execute("SELECT value FROM meta WHERE key = 'commit'").fetchone()
            total = db.execute("SELECT value FROM meta WHERE key = 'total'").fetchone()
            rows = db.execute(
                "SELECT path, hex(blob), sphere, kind, year, number, date, agency FROM norms ORDER BY path"
            ).fetchall()
            index_sql = [
                row[0] or ""
                for row in db.execute("SELECT sql FROM sqlite_master WHERE type = 'index' AND tbl_name = 'norms'")
            ]
    except sqlite3.DatabaseError as exc:
        raise OverlayError("Índice do overlay ilegível.") from exc
    if not ready or ready[0] != "1" or not commit or commit[0] != manifest["version"]:
        raise OverlayError("Índice do overlay incompleto.")
    if not any("unique" in sql.casefold() and "path" in sql.casefold() for sql in index_sql):
        raise OverlayError("Índice do overlay sem unicidade de caminho.")
    try:
        declared = int(total[0]) if total and total[0] is not None else None
    except (TypeError, ValueError) as exc:
        raise OverlayError("Total do índice do overlay ilegível.") from exc
    expected = {item["path"]: item for item in manifest["norms"]}
    if declared != len(manifest["norms"]) or len(rows) != len(expected):
        raise OverlayError("Índice do overlay não confere com o manifesto.")
    seen: set[str] = set()
    for path, blob, sphere, kind, year, number, date, agency in rows:
        if path in seen:
            raise OverlayError("Caminho repetido no índice do overlay.")
        seen.add(path)
        item = expected.get(path)
        if item is None or blob.lower() != str(item.get("blob", "")).lower() or agency != item.get("organ"):
            raise OverlayError("Linha do índice do overlay divergente.")
        if sphere != item.get("sphere") or kind != item.get("kind") or str(number) != str(item.get("number")):
            raise OverlayError("Linha do índice do overlay divergente.")
        if year != item.get("year") or (date or "") != (item.get("date") or ""):
            raise OverlayError("Linha do índice do overlay divergente.")
    if seen != set(expected):
        raise OverlayError("Índice do overlay não cobre o manifesto.")
    return len(rows)


def _norm_path(root: Path, relative: str) -> Path:
    require_norm_path(relative)
    return _contained_path(root, f"norms/{relative}")


def _norm_file(root: Path, relative: str) -> Path:
    candidate = _norm_path(root, relative)
    if candidate.is_symlink():
        raise OverlayError("Link simbólico recusado no pacote.")
    if not candidate.is_file():
        raise OverlayError(f"Arquivo da norma ausente: {relative}")
    resolved = candidate.resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError as exc:
        raise OverlayError("Caminho de norma escapa do pacote.") from exc
    return candidate


def _provenance_path(root: Path, canonical_id: str) -> Path:
    return _contained_path(root, f"provenance/{provenance_basename(canonical_id)}")


def _provenance_file(root: Path, canonical_id: str) -> Path:
    candidate = _provenance_path(root, canonical_id)
    if candidate.is_symlink() or not candidate.is_file():
        raise OverlayError("Proveniência ausente.")
    resolved = candidate.resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError as exc:
        raise OverlayError("Proveniência fora do pacote.") from exc
    return candidate


def _contained_path(root: Path, relative: str) -> Path:
    if relative.startswith("/") or ".." in relative.split("/"):
        raise OverlayError("Caminho de norma escapa do pacote.")
    root_resolved = Path(root).resolve()
    candidate = root_resolved.joinpath(*relative.split("/"))
    lexical = Path(os.path.normpath(str(candidate)))
    try:
        lexical.relative_to(root_resolved)
    except ValueError as exc:
        raise OverlayError("Caminho de norma escapa do pacote.") from exc
    return Path(root).joinpath(*relative.split("/"))


def _contained_file(root: Path, relative: str) -> Path:
    candidate = _contained_path(root, relative)
    if candidate.is_symlink():
        raise OverlayError("Link simbólico recusado no pacote.")
    if not candidate.is_file():
        raise OverlayError(f"Arquivo da norma ausente: {relative}")
    resolved = candidate.resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError as exc:
        raise OverlayError("Caminho de norma escapa do pacote.") from exc
    return candidate


def _read_bounded(path: Path) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise OverlayError("Arquivo do overlay recusado.")
    size = path.stat().st_size
    if size > _MAX_FILE_BYTES:
        raise OverlayError("Arquivo do overlay acima do limite.")
    return path.read_bytes()


def _copy_regular_tree(source: Path, dest: Path) -> None:
    if source.is_symlink() or not source.is_dir():
        raise OverlayError("Origem do pacote recusada.")
    files = 0
    total = 0
    for dirpath, dirnames, filenames in os.walk(source, followlinks=False):
        current = Path(dirpath)
        if current.is_symlink():
            raise OverlayError("Link simbólico recusado no pacote.")
        for name in [*dirnames, *filenames]:
            path = current / name
            mode = path.lstat().st_mode
            if stat.S_ISLNK(mode) or not (stat.S_ISDIR(mode) or stat.S_ISREG(mode)):
                raise OverlayError("Arquivo especial recusado no pacote.")
            if stat.S_ISREG(mode):
                files += 1
                total += path.stat().st_size
                if files > _MAX_FILES or total > _MAX_TREE_BYTES or path.stat().st_size > _MAX_FILE_BYTES:
                    raise OverlayError("Pacote parlamentar acima do limite.")
    shutil.copytree(source, dest, symlinks=False, copy_function=_copy_file)
    _reject_copied_links(dest)


def _copy_file(source: str, dest: str) -> None:
    src = Path(source)
    if src.is_symlink() or not stat.S_ISREG(src.lstat().st_mode):
        raise OverlayError("Arquivo especial recusado no pacote.")
    shutil.copy2(source, dest)


def _reject_copied_links(root: Path) -> None:
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        for name in [*dirnames, *filenames]:
            if (Path(dirpath) / name).is_symlink():
                raise OverlayError("Link simbólico recusado no pacote.")


def _reject_special(path: Path) -> None:
    if path.is_symlink():
        raise OverlayError("Origem do pacote não pode ser um link.")


def _recognised_bundle(directory: Path) -> bool:
    manifest = directory / "manifest.json"
    if directory.is_symlink() or not manifest.is_file() or manifest.is_symlink():
        return False
    try:
        payload = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError):
        return False
    return isinstance(payload, dict) and payload.get("kind") == _BUNDLE_KIND


def collection_protected(settings: Settings) -> tuple[Path, ...]:
    """Paths the CLI refuses to replace, including descendants and ancestors."""
    return (
        settings.home,
        settings.repo_dir,
        settings.db_path,
        settings.cache_dir,
        settings.overlay_dir,
        settings.overlay_dir / "versions",
        settings.overlay_dir / "staging",
    )


def _refuse_protected(destination: Path, extra: tuple[Path, ...]) -> None:
    _refuse_symlink_chain(destination if destination.exists() else destination.parent)
    if destination.exists() and destination.is_symlink():
        raise OverlayError("Destino da coleta não pode ser um link.")
    resolved = destination.resolve()
    if resolved in _PROTECTED_ROOTS:
        raise OverlayError("Destino da coleta recusado.")
    for root in extra:
        if not root.exists() and not root.is_absolute():
            continue
        root_resolved = root.resolve()
        # Inside a protected tree, or a parent whose replacement would delete it.
        if _is_within(resolved, root_resolved) or _is_within(root_resolved, resolved):
            raise OverlayError("Destino da coleta recusado.")


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _refuse_symlink_chain(path: Path) -> None:
    """Rejects a symlink at path or any existing ancestor before a write follows it."""
    current = path
    for _ in range(64):
        if current.is_symlink():
            raise OverlayError("Link simbólico recusado no caminho do overlay.")
        parent = current.parent
        if parent == current:
            return
        current = parent


def _fsync_tree(root: Path) -> None:
    for dirpath, _, filenames in os.walk(root):
        for name in filenames:
            descriptor = os.open(Path(dirpath) / name, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        _fsync_dir(Path(dirpath))


def _require_space(path: Path) -> None:
    usage = shutil.disk_usage(path if path.exists() else path.parent)
    if usage.free < _MAX_TREE_BYTES:
        raise OverlayError("Espaço livre insuficiente para o overlay.")


def _fsync_dir(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
