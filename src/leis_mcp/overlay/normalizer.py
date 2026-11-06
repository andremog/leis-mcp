"""Turns official HTML into Markdown the existing article parser can read.

Struck, revoked and parenthetical amendment notes stay in the text. The HTML is data:
nothing in it is executed or treated as an instruction.
"""

import re
from html.parser import HTMLParser

_PI = re.compile(r"<\?[^>]*>", re.I)
_OFFICE = re.compile(r"</?o:[a-z0-9:-]+[^>]*>", re.I)
_XML_NS = re.compile(r"<\?xml:namespace[^>]*\?>", re.I)
_SIGNATURE = re.compile(
    r"^(?:Congresso Nacional|C[âa]mara dos Deputados|Senado Federal),\s+(?:em\s+)?\d",
    re.IGNORECASE,
)
_RULE = re.compile(r"^_{5,}$")
_NORMA = re.compile(r'<div[^>]*class="[^"]*\btextoNorma\b[^"]*"', re.IGNORECASE)
_AFTER_NORMA = re.compile(r'<div[^>]*class="[^"]*\bpublicacoesTI\b', re.IGNORECASE)
_HEADING = re.compile(
    r"^(?:T[ÍI]TULO|CAP[ÍI]TULO|SE[ÇC][ÃA]O|SUBSE[ÇC][ÃA]O|LIVRO|PARTE|ANEXO)\b",
    re.IGNORECASE,
)
# A second article is a new provision only after a sentence boundary. "no Art. 7º" stays a citation.
_PACKED_ARTICLE = re.compile(r"(?<=[.!?])\s+(?=Art\.\s*\d)")
_AFTER_SENTENCE = re.compile(r"(?<=[.!?])\s+(?=Par[áa]grafo\s+[úu]nico\b|§\s*\d)")
_CHROME = ("navegador defasado", "veja também:")
_STOP = "Este texto não substitui a publicação oficial."
_START = "\ue000"
_END = "\ue001"


class _HtmlToLines(HTMLParser):
    _SKIP = frozenset({"script", "style", "head", "noscript", "header", "footer", "nav", "aside"})
    _VOID = frozenset({"br", "img", "hr", "meta", "link", "input", "source", "wbr"})
    _BREAK = frozenset({"p", "div", "h1", "h2", "h3", "h4", "h5", "h6", "li", "tr", "blockquote"})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.lines: list[str] = []
        self._buf: list[str] = []
        self._open: list[bool] = []
        self._skip = 0
        self._sup = 0
        self._strike = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in self._SKIP:
            self._skip += 1
            return
        if self._skip:
            return
        if tag == "br":
            if self._sup:
                self._buf.append(" ")
            else:
                self._flush()
            return
        if tag in self._VOID:
            return
        if tag in self._BREAK:
            self._flush()
        style = (dict(attrs).get("style") or "").replace(" ", "").lower()
        struck = tag in {"s", "strike", "del"} or "line-through" in style
        self._open.append(struck)
        if struck:
            self._strike += 1
            self._buf.append(_START)
        if tag == "sup":
            self._sup += 1
            self._buf.append("[")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in self._SKIP and self._skip:
            self._skip -= 1
            return
        if self._skip or tag in self._VOID:
            return
        if tag == "sup" and self._sup:
            self._sup -= 1
            self._buf.append("]")
        struck = self._open.pop() if self._open else False
        if struck and self._strike:
            self._strike -= 1
            self._buf.append(_END)
        if tag in self._BREAK:
            self._flush()

    def handle_data(self, data: str) -> None:
        if not self._skip and data:
            self._buf.append(data)

    def close(self) -> None:
        super().close()
        self._flush()

    def _flush(self) -> None:
        raw = "".join(self._buf)
        self._buf = []
        text = _clean(raw)
        if self._strike and text:
            core = text.strip().strip("~").strip()
            text = f"~~{core}~~" if core else ""
        if text and text.strip("~").strip():
            self.lines.append(text)


def html_to_markdown(html: str) -> str:
    """Body only (no front matter). Idempotent for the same HTML string."""
    cleaned = _OFFICE.sub("", _XML_NS.sub("", _PI.sub("", _focus(html))))
    parser = _HtmlToLines()
    parser.feed(cleaned)
    parser.close()
    lines = _split_packed_provisions(parser.lines)
    lines = [line for line in lines if not _is_chrome(line)]
    lines = _stop_before_signature(lines)
    lines = [_as_heading(line) for line in lines]
    return "\n\n".join(lines).strip() + "\n"


def _focus(html: str) -> str:
    """Portal pages wrap the norm; the navigation around `textoNorma` is not part of the text."""
    match = _NORMA.search(html)
    if not match:
        return html
    chunk = html[match.start() :]
    end = _AFTER_NORMA.search(chunk)
    return chunk[: end.start()] if end else chunk


def _clean(text: str) -> str:
    text = text.replace("\u00a0", " ").replace("\u00ad", "").replace(_START, "~~").replace(_END, "~~")
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"(?:~~\s*){2,}", "", text)
    return text.strip()


def _split_packed_provisions(lines: list[str]) -> list[str]:
    """Splits real packed provisions. An inline citation such as 'no Art. 7º' stays on the same line."""
    split: list[str] = []
    for line in lines:
        pieces = [part.strip() for part in _PACKED_ARTICLE.split(line) if part.strip()] or [line]
        for piece in pieces:
            split.extend(part.strip() for part in _AFTER_SENTENCE.split(piece) if part.strip())
    return split


def _is_chrome(line: str) -> bool:
    folded = line.casefold()
    return any(snippet in folded for snippet in _CHROME)


def _stop_before_signature(lines: list[str]) -> list[str]:
    """Keeps the promulgation block and the footnote rule out of the last article."""
    for index, line in enumerate(lines):
        if _SIGNATURE.match(line) or _RULE.match(line):
            return [*lines[:index], _STOP, *lines[index:]]
    return lines


def _as_heading(line: str) -> str:
    if line.startswith("#") or line.startswith("Art.") or line.startswith("§"):
        return line
    if _HEADING.match(line):
        return f"## {line}"
    return line
