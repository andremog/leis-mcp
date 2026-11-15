"""Compiled federal law text from www.planalto.gov.br.

The URL is a deterministic path for leis ordinárias and complementares from 1995 on. The page is
accepted only when its opening text names the same law. Redirects are not followed. Nothing is
written outside `cache/planalto`.
"""

import json
import logging
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import httpx
from lxml.etree import ParserError
from lxml.html import HtmlElement, HTMLParser, document_fromstring

from ..config import Settings
from ..document import LegalDocument, Level
from ..legal_text import DocumentParser
from ..legal_text.insertions import digits
from ..legal_text.labels import ArticleLabel, ClauseLabel, ParagraphLabel, SubclauseLabel
from ..models import Norm
from .transport import MAX_BODY, official_client, read_body

log = logging.getLogger(__name__)

# libxml2 HTML recovery. no_network blocks external entities; huge_tree stays off.
# lxml is the parser itself: BeautifulSoup would still need a builder, and a second
# unescape-after-regex pass is what used to delete a literal "&lt;".
_PARSER_KWARGS = {
    "recover": True,
    "no_network": True,
    "huge_tree": False,
    "remove_comments": True,
    "remove_pis": True,
}
_DROP_TAGS = frozenset({"script", "style", "noscript", "head"})
_STRUCK_TAGS = frozenset({"s", "strike", "del"})
_BLOCK_TAGS = frozenset({"p", "div", "li", "tr", "blockquote", "h1", "h2", "h3", "h4", "h5", "h6"})
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


def _struck(element: HtmlElement) -> bool:
    tag = element.tag.lower() if isinstance(element.tag, str) else ""
    if tag in _STRUCK_TAGS or tag in _DROP_TAGS:
        return True
    style = (element.get("style") or "").replace(" ", "").lower()
    return "text-decoration:line-through" in style or "text-decoration-line:line-through" in style


def _detach_keeping_tail(element: HtmlElement) -> None:
    """Drop a struck element but keep the text that follows it in the parent."""
    parent = element.getparent()
    if parent is None:
        return
    tail = element.tail
    if tail:
        previous = element.getprevious()
        if previous is not None:
            previous.tail = (previous.tail or "") + tail
        else:
            parent.text = (parent.text or "") + tail
        element.tail = None
    parent.remove(element)


def _render(element: HtmlElement) -> str:
    chunks: list[str] = []
    if element.text:
        chunks.append(element.text)
    for child in element:
        tag = child.tag.lower() if isinstance(child.tag, str) else ""
        if tag == "br":
            chunks.append("\n")
        elif tag in _BLOCK_TAGS:
            chunks.append(f"\n{_render(child)}\n")
        else:
            chunks.append(_render(child))
        if child.tail:
            chunks.append(child.tail)
    return "".join(chunks)


def html_to_body(page: str) -> str:
    """Text of a Planalto page. Struck subtrees are removed on the DOM, then text nodes are read.

    `&lt;` is already a character in the text node, so it is not eaten as a tag. `<br>` and a
    stray end tag inside a strike stay inside that subtree because libxml2 recovers the tree.
    """
    if len(page) > MAX_BODY:
        raise ValueError("html document exceeds the size cap")
    try:
        root = document_fromstring(page, parser=HTMLParser(**_PARSER_KWARGS))
    except ParserError:
        return ""
    for element in list(root.iter()):
        if isinstance(element.tag, str) and _struck(element):
            _detach_keeping_tail(element)
    body = root.find("body")
    text = _render(body if body is not None else root)
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


def conflicting_label(
    document: LegalDocument,
    article: str,
    paragraph: str | None = None,
    clause: str | None = None,
    subclause: str | None = None,
) -> bool:
    """True when the requested device still has two in-force labels.

    Inciso ``I`` of the caput and inciso ``I`` of a paragraph are different devices. A repeated
    inciso is a conflict only inside the paragraph (or the caput) that contains it.
    """
    key = ArticleLabel.parse(article)
    found = [item for block in document.blocks for item in block.articles.values() if item.key == key]
    if len(found) != 1:
        return len(found) > 1
    chosen = found[0]
    paragraph_key = ParagraphLabel.parse(paragraph) if paragraph not in (None, "") else None
    repeated_paragraph = _repeated(chosen, Level.PARAGRAPH, chosen.start, chosen.end)
    if repeated_paragraph and (
        paragraph_key is None or _count(chosen, Level.PARAGRAPH, paragraph_key, chosen.start, chosen.end) > 1
    ):
        return True
    if paragraph_key is not None:
        start, end = _paragraph_span(chosen, paragraph_key)
        if start is None:
            return False
        return _scope_conflicts(chosen, start, end, clause, subclause)
    caput_end = next((unit.line for unit in chosen.units if unit.level == Level.PARAGRAPH), chosen.end)
    if clause not in (None, "") or subclause not in (None, ""):
        return _scope_conflicts(chosen, chosen.start, caput_end, clause, subclause)
    if _scope_conflicts(chosen, chosen.start, caput_end, None, None):
        return True
    for unit in chosen.units:
        if unit.level != Level.PARAGRAPH:
            continue
        start, end = _paragraph_span(chosen, unit.key, unit.line)
        if start is not None and _scope_conflicts(chosen, start, end, None, None):
            return True
    return False


def _paragraph_span(article, key: str, line: int | None = None) -> tuple[int | None, int]:
    matches = [unit for unit in article.units if unit.level == Level.PARAGRAPH and unit.key == key]
    if line is not None:
        matches = [unit for unit in matches if unit.line == line] or matches[:1]
    if not matches:
        return None, article.end
    start = matches[0].line
    later = [unit.line for unit in article.units if unit.level == Level.PARAGRAPH and unit.line > start]
    return start, later[0] if later else article.end


def _scope_conflicts(article, start: int, end: int, clause: str | None, subclause: str | None) -> bool:
    clauses = _keys(article, Level.CLAUSE, start, end)
    loose = _loose_subclauses(article, start, end)
    if subclause not in (None, "") and clause in (None, "") and loose.count(SubclauseLabel.parse(subclause)) > 1:
        return True
    if clause in (None, "") and subclause in (None, "") and len(loose) != len(set(loose)):
        return True
    if clause not in (None, ""):
        wanted = ClauseLabel.parse(clause)
        if clauses.count(wanted) > 1:
            return True
        for clause_start, clause_end in _child_spans(article, Level.CLAUSE, start, end, wanted):
            if _subclause_conflict(article, clause_start, clause_end, subclause):
                return True
        return False
    if len(clauses) != len(set(clauses)):
        return True
    for clause_start, clause_end in _child_spans(article, Level.CLAUSE, start, end, None):
        if _subclause_conflict(article, clause_start, clause_end, subclause):
            return True
    return False


def _loose_subclauses(article, start: int, end: int) -> list[str]:
    """Alíneas in this span that do not sit under an inciso."""
    clause_lines = [line for line in _child_spans(article, Level.CLAUSE, start, end, None) for line in line[:1]]
    keys = []
    for unit in article.units:
        if unit.level != Level.SUBCLAUSE or not start <= unit.line < end:
            continue
        if any(line <= unit.line for line in clause_lines):
            continue
        keys.append(unit.key)
    return keys


def _subclause_conflict(article, start: int, end: int, subclause: str | None) -> bool:
    keys = _keys(article, Level.SUBCLAUSE, start, end)
    if subclause not in (None, ""):
        return keys.count(SubclauseLabel.parse(subclause)) > 1
    return len(keys) != len(set(keys))


def _child_spans(article, level: Level, start: int, end: int, key: str | None) -> list[tuple[int, int]]:
    lines = [
        unit.line
        for unit in article.units
        if unit.level == level and start <= unit.line < end and (key is None or unit.key == key)
    ]
    bounds = [*lines, end]
    return list(zip(lines, bounds[1:], strict=False))


def _keys(article, level: Level, start: int, end: int) -> list[str]:
    return [unit.key for unit in article.units if unit.level == level and start <= unit.line < end]


def _repeated(article, level: Level, start: int, end: int) -> bool:
    keys = _keys(article, level, start, end)
    return len(keys) != len(set(keys))


def _count(article, level: Level, key: str, start: int, end: int) -> int:
    return _keys(article, level, start, end).count(key)


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
        try:
            body = html_to_body(decode_page(payload))
        except ValueError:
            return None
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
        try:
            body = html_to_body(decode_page(payload))
        except ValueError:
            return None
        page = OfficialPage(url, meta["fetched_on"], body)
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
