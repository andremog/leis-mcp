"""Builds a versioned bundle from the inventory. Not invoked on a schedule and not part of `leis-mcp update`."""

import json
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

from .allowlist import MAX_BYTES
from .collector import Collector
from .normalizer import html_to_markdown
from .store import OverlayError, built_norm, write_bundle

_INVENTORY = Path(__file__).resolve().parent.parent / "data" / "parliamentary" / "inventory.json"


def inventory_path() -> Path:
    return _INVENTORY


def load_inventory() -> dict[str, Any]:
    return json.loads(_INVENTORY.read_text(encoding="utf-8"))


def collect_inventory(
    *,
    html_dir: Path | None,
    destination: Path,
    replace: bool = False,
    protected: tuple[Path, ...] = (),
) -> dict[str, Any]:
    inventory = load_inventory()
    collector = None if html_dir else Collector()
    norms: list[dict[str, Any]] = []
    gaps = list(inventory.get("gaps", []))
    try:
        for item in inventory["items"]:
            try:
                raw, fetched_at, final_url = _load(item, html_dir, collector)
                body = html_to_markdown(_decode(raw))
                norms.append(built_norm(item, body, raw=raw, fetched_at=fetched_at, final_url=final_url))
            except Exception as exc:
                if item.get("required", True):
                    raise OverlayError(f"{item['canonical_id']}: {exc}") from exc
                gaps.append({"canonical_id": item["canonical_id"], "motivo": str(exc), "coletado": False})
    finally:
        if collector is not None:
            collector.close()
    if not norms:
        raise OverlayError("Nenhuma norma parlamentar foi normalizada.")
    return write_bundle(destination, norms, gaps, replace=replace, protected=protected)


def _load(item: dict[str, Any], html_dir: Path | None, collector: Collector | None) -> tuple[bytes, str, str]:
    if html_dir is not None:
        name = str(item["local_name"])
        if "/" in name or name.startswith(".") or not name.replace("-", "").isalnum():
            raise OverlayError("Nome local da fonte recusado.")
        path = html_dir / f"{name}.html"
        if path.is_symlink() or not path.is_file():
            raise OverlayError("Arquivo local da fonte recusado.")
        if path.stat().st_size > MAX_BYTES:
            raise OverlayError("Arquivo local da fonte acima do limite.")
        raw = path.read_bytes()
        header = path.with_suffix(".headers")
        fetched_at = _header_date(header) if header.exists() else datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        return raw, fetched_at, item["source_url"]
    assert collector is not None
    fetched = collector.fetch(item["source_url"])
    return fetched.data, fetched.fetched_at, fetched.url


def _decode(raw: bytes) -> str:
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("cp1252")


def _header_date(path: Path) -> str:
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.lower().startswith("date:"):
            raw = line.split(":", 1)[1].strip()
            try:
                parsed = parsedate_to_datetime(raw).astimezone(UTC)
            except (TypeError, ValueError, IndexError):
                return raw
            return parsed.strftime("%Y-%m-%dT%H:%M:%SZ")
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
