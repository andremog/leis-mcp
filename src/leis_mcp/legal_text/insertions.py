"""Quoted new wording that belongs to the law being amended, not to the law that quotes it.

A block in quotes that ends with `(NR)` or `(AC)`, and a heading introduced by a colon
(`acrescida do seguinte art. 12-C:`), is the amended law's text. After one `(NR)`/`(AC)`
inside an article, later quoted headings in that same article stay with it.
"""

import re
from dataclasses import dataclass

from ..document import LegalDocument
from .labels import ArticleLabel

_QUOTE = re.compile(r"""^[\s*_]*['"“”«»]""")
_MARKER = re.compile(r"\((?:NR|AC)\)", re.IGNORECASE)
_PREAMBLE = re.compile(
    r"partes?\s+vetadas?\s+da\s+Lei(?:\s+Complementar)?\s+n[ºo°.]?\s*(?P<number>[\d.]+)",
    re.IGNORECASE,
)
_ALTERS = re.compile(
    r"Altera(?:m)?\s+a\s+Lei(?:\s+Complementar)?\s+n[ºo°.]?\s*(?P<number>[\d.]+)"
    r"(?P<tail>[^\n]{0,80})",
    re.IGNORECASE,
)
_AMENDS = re.compile(
    r"Lei(?:\s+Complementar)?\s+n[ºo°.]?\s*(?P<number>[\d.]+)(?P<tail>[^\n]{0,160}?)\s+passa a vigorar",
    re.IGNORECASE,
)
_YEAR = re.compile(r"\b(?:19|20)\d{2}\b")


@dataclass(frozen=True, slots=True)
class Insertion:
    """New wording of `target` (another law) found inside `host` (this law's article)."""

    host: str
    target: str
    law: str | None  # digits of the amended law, when exactly one law is named
    year: int | None
    start: int
    end: int


def digits(number: str) -> str:
    return "".join(char for char in number if char.isdigit())


def display_number(number: str) -> str:
    """'14133' or '14.133' -> '14.133'."""
    raw = digits(number)
    return f"{raw[:-3]}.{raw[-3:]}" if len(raw) > 3 else raw


def preamble_numbers(body: str) -> tuple[str, ...]:
    """Law numbers in `parte(s) vetada(s) da Lei nº N`, in order, digits only."""
    found = [digits(match["number"]) for match in _PREAMBLE.finditer(body)]
    return tuple(dict.fromkeys(number for number in found if number))


def _quoted_key(line: str) -> str | None:
    if not _QUOTE.match(line) or ArticleLabel.from_line(line):
        return None
    return ArticleLabel.from_quoted_line(line)


def _one_law(text: str) -> tuple[str, int | None] | None:
    """The single law a sentence amends, or None when there isn't exactly one."""
    numbers = []
    for pattern in (_AMENDS, _ALTERS):
        for match in pattern.finditer(text):
            number = digits(match["number"])
            if not number:
                continue
            year = _YEAR.search(match["tail"])
            numbers.append((number, int(year.group()) if year else None))
        if numbers:
            break
    unique = {number for number, _ in numbers}
    if len(unique) != 1:
        return None
    number = unique.pop()
    years = {year for item, year in numbers if item == number and year}
    return number, years.pop() if len(years) == 1 else None


def find_insertions(document: LegalDocument) -> tuple[Insertion, ...]:
    """Quoted headings that are not themselves an article of this document."""
    lines = document.lines
    heads = [(index, key) for index, line in enumerate(lines) if (key := _quoted_key(line))]
    if not heads:
        return ()
    own = {article.start for block in document.blocks for article in block.articles.values()}
    default = _one_law("\n".join(lines))
    insertions: list[Insertion] = []
    for position, (index, key) in enumerate(heads):
        if index in own:
            continue  # this heading is an article of the file's own law
        host = document.article_at(index)
        if host is None:
            continue
        end = heads[position + 1][0] if position + 1 < len(heads) else host.end
        end = min(end, host.end)
        context = document.text(host.start, index)
        named = _one_law(context) or default
        insertions.append(
            Insertion(
                host.key,
                key,
                named[0] if named else None,
                named[1] if named else None,
                index,
                end,
            )
        )
    return tuple(insertions)
