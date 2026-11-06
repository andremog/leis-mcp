"""Snapshot identity and path confinement for the parliamentary overlay.

The version id covers the legal body and the metadata that changes what the server says
(nature, version text, source, aliases, title). Collection timestamps stay out of it.
"""

import hashlib
import json
import re
from typing import Any

from .store_errors import OverlayError  # noqa: F401 - re-exported for callers that validate paths

_VERSION = re.compile(r"^p-[0-9a-f]{20}$")
_SEGMENT = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def norm_identity(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "aliases": sorted(str(alias) for alias in item.get("aliases") or []),
        "body_sha256": str(item["body_sha256"]),
        "canonical_id": str(item["canonical_id"]),
        "date": str(item.get("date") or ""),
        "kind": str(item["kind"]),
        "number": str(item["number"]),
        "organ": str(item["organ"]),
        "path": str(item["path"]),
        "publication": str(item.get("publication") or ""),
        "source_url": str(item["source_url"]),
        "sphere": str(item["sphere"]),
        "summary": str(item.get("summary") or ""),
        "text_nature": str(item["text_nature"]),
        "text_version": str(item["text_version"]),
        "title": str(item.get("title") or ""),
        "vigencia": str(item.get("vigencia") or ""),
        "year": int(item["year"]) if item.get("year") not in (None, "") else None,
    }


def version_id(norms: list[dict[str, Any]]) -> str:
    payload = json.dumps(
        [norm_identity(item) for item in sorted(norms, key=lambda item: str(item["canonical_id"]))],
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    return "p-" + hashlib.sha256(payload.encode()).hexdigest()[:20]


def require_version(value: object) -> str:
    if not isinstance(value, str) or not _VERSION.fullmatch(value):
        raise OverlayError("Identificador de versão do overlay recusado.")
    return value


def require_norm_path(value: object) -> str:
    """A canonical relative path under `parlamentar/`. Rejects absolute paths and `..`."""
    if not isinstance(value, str) or "\\" in value or value.startswith("/") or value.startswith("~"):
        raise OverlayError("Caminho de norma recusado.")
    parts = value.split("/")
    if len(parts) < 3 or parts[0] != "parlamentar" or not value.endswith(".md"):
        raise OverlayError("Caminho de norma recusado.")
    if any(part in ("", ".", "..") or not _SEGMENT.fullmatch(part.removesuffix(".md")) for part in parts):
        raise OverlayError("Caminho de norma recusado.")
    return value
