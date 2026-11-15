"""Compiled federal law text from www.planalto.gov.br.

The URL is a deterministic path for leis ordinárias and complementares from 1995 on. The page is
accepted only when its opening text names the same law. Redirects are not followed. Nothing is
written outside `cache/planalto`.
"""

import html
import json
import logging
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import httpx

from ..config import Settings
from ..legal_text import DocumentParser
from ..legal_text.insertions import digits
from ..models import Norm
from .transport import official_client, read_body

log = logging.getLogger(__name__)

_SCRIPT = re.compile(r"(?is)<(script|style)\b[^>]*>.*?</\1>")
_BREAK = re.compile(r"(?i)<br\s*/?>|</(?:p|div|h[1-6]|li|tr)>")
_TAG = re.compile(r"(?s)<[^>]+>")
_HEADING = re.compile(r"[ \t]{2,}(?=Art\.\s+\d)")
_LAW = re.compile(r"Lei\s+(?:Complementar\s+)?n[ºo°.]?\s*(?P<number>[\d.]+)", re.IGNORECASE)


class PlanaltoUnavailable(Exception):
    """The official page was not used. `reason` is a stable token, not the response body."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True, slots=True)
class OfficialPage:
    url: str
    fetched_on: str
    body: str

    def document(self):
        return DocumentParser().parse(self.body)


def compiled_url(kind: str | None, number: str | None, year: int | None) -> str | None:
    """The compiled Planalto page, or None when the path would be a guess."""
    if kind not in ("leis-ordinarias", "leis-complementares") or year is None or not 1995 <= year <= 2100:
        return None
    raw = digits(number or "")
    if not raw.isdigit() or not 1 <= len(raw) <= 7 or raw != str(int(raw)):
        return None
    start = 1995 + ((year - 1995) // 4) * 4
    leaf = f"lei/l{raw}.htm" if kind == "leis-ordinarias" else f"lei/lcp/lcp{raw}.htm"
    return f"https://www.planalto.gov.br/ccivil_03/_ato{start}-{start + 3}/{year}/{leaf}"


def decode_page(payload: bytes) -> str:
    """Planalto compiled pages are Windows-1252 unless the bytes are actually UTF-8."""
    head = payload[:800].lower()
    if b"charset=utf-8" in head:
        try:
            return payload.decode("utf-8")
        except UnicodeDecodeError:
            pass
    try:
        return payload.decode("utf-8")
    except UnicodeDecodeError:
        return payload.decode("cp1252")


def html_to_body(page: str) -> str:
    text = _SCRIPT.sub(" ", page)
    text = _BREAK.sub("\n", text)
    text = html.unescape(_TAG.sub("", text))
    text = _HEADING.sub("\n", text)
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.split("\n")]
    return _join_split_labels("\n".join(line for line in lines if line))


def _join_split_labels(text: str) -> str:
    """The compiled HTML often breaks `Art.` and the number across two tags."""
    lines = text.split("\n")
    joined: list[str] = []
    index = 0
    while index < len(lines):
        if re.fullmatch(r"Art\.?", lines[index]) and index + 1 < len(lines) and re.match(r"\d", lines[index + 1]):
            joined.append(f"Art. {lines[index + 1]}")
            index += 2
            continue
        joined.append(lines[index])
        index += 1
    return "\n".join(joined)


def names_law(body: str, number: str) -> bool:
    """The first law number in the opening is this law. A later citation does not count."""
    match = _LAW.search(body[:4000])
    return bool(match) and digits(match["number"]) == digits(number)


class PlanaltoText:
    def __init__(self, settings: Settings, client: httpx.Client | None = None):
        self._settings = settings
        self._client = client
        self._dir = settings.cache_dir / "planalto"

    @property
    def online(self) -> bool:
        return self._settings.online

    def fetch(self, norm: Norm) -> OfficialPage:
        if not self._settings.online:
            raise PlanaltoUnavailable("offline")
        url = compiled_url(norm.kind, norm.number, norm.year)
        if url is None:
            raise PlanaltoUnavailable("no_url")
        cached = self._read(norm, url)
        if cached:
            return cached
        try:
            body = read_body(self._client_for(), url)
        except httpx.HTTPStatusError as exc:
            raise PlanaltoUnavailable("unavailable") from exc
        except httpx.HTTPError as exc:
            raise PlanaltoUnavailable("unavailable") from exc
        if body is None:
            raise PlanaltoUnavailable("unavailable")
        page = self._page(url, body)
        if page is None or not self.accepted(norm, page):
            raise PlanaltoUnavailable("mismatch")
        self._write(norm, url, body, page.fetched_on)
        return page

    def _client_for(self) -> httpx.Client:
        if self._client is None:
            self._client = official_client(self._user_agent(), accept="text/html")
        return self._client

    def _page(self, url: str, payload: bytes) -> OfficialPage | None:
        body = html_to_body(decode_page(payload))
        # The caller checks the law number. A homepage or another act fails here only on emptiness.
        if "Art." not in body[:8000] and "LEI" not in body[:1500].upper():
            return None
        fetched_on = datetime.now(UTC).date().isoformat()
        return OfficialPage(url, fetched_on, body)

    def accepted(self, norm: Norm, page: OfficialPage) -> bool:
        return bool(norm.number) and names_law(page.body, norm.number)

    def _read(self, norm: Norm, url: str) -> OfficialPage | None:
        html_path, meta_path = self._paths(norm)
        try:
            meta = json.loads(meta_path.read_text("utf-8"))
            payload = html_path.read_bytes()
        except (OSError, ValueError):
            return None
        if not isinstance(meta, dict) or meta.get("url") != url or not isinstance(meta.get("fetched_on"), str):
            return None
        page = OfficialPage(url, meta["fetched_on"], html_to_body(decode_page(payload)))
        return page if self.accepted(norm, page) else None

    def _write(self, norm: Norm, url: str, payload: bytes, fetched_on: str) -> None:
        html_path, meta_path = self._paths(norm)
        self._store(html_path, payload, binary=True)
        self._store(meta_path, json.dumps({"url": url, "fetched_on": fetched_on}).encode(), binary=True)

    def _paths(self, norm: Norm) -> tuple[Path, Path]:
        name = f"{norm.kind}-{digits(norm.number or '')}-{norm.year}"
        return self._dir / f"{name}.html", self._dir / f"{name}.json"

    @staticmethod
    def _store(path: Path, payload: bytes, *, binary: bool) -> None:
        del binary
        temporary = None
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            descriptor, name = tempfile.mkstemp(dir=path.parent)
            temporary = Path(name)
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(payload)
            os.replace(temporary, path)
        except OSError:
            log.warning("Could not cache the Planalto page: %s", path.name)
            if temporary:
                temporary.unlink(missing_ok=True)

    @staticmethod
    def _user_agent() -> str:
        return "leis-mcp"
