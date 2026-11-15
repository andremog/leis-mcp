"""Builds a `LegalDocument` (blocks of articles and their units) from a norm's Markdown body."""

import bisect
import re
from collections.abc import Callable
from dataclasses import replace

from .. import messages
from ..document import Article, Block, Heading, LegalDocument, Unit
from .labels import UNIT_LABELS, ArticleLabel, Label


class DocumentParser:
    _HEADING = re.compile(r"^(?P<marks>#{1,6})\s+(?P<title>.+?)\s*$")
    # Signature lines that close the normative text.
    _END_MARKER = re.compile(
        r"^\s*\**\s*(?:(?:Bras[íi]lia|Rio de Janeiro|Capital Federal)\s*,|Este texto n[ãa]o substitui)",
        re.IGNORECASE,
    )
    # Both phrases: "eu promulgo" and "parte(s) vetada(s)". Either one alone is an ordinary enactment or a veto.
    _VETO_PROMULGATION = re.compile(r"eu promulgo", re.IGNORECASE)
    _VETO_PARTS = re.compile(r"partes? vetadas?", re.IGNORECASE)
    # An errata quotes the wrong wording and the correction. Reading either one as the article is worse
    # than reporting that the format was not recognized.
    _ERRATA = re.compile(r"onde se (?:l[eêi]|diz)|leia\s*[-–—]?\s*se", re.IGNORECASE)
    _ARTIGO_UNICO = re.compile(r"(?m)^[\s*_\"'“”«»]*Artigo\s+único\b", re.IGNORECASE)

    def parse(self, body: str) -> LegalDocument:
        # Only "\n": line numbers must match the search engines' (str.splitlines also breaks on \f, \x85...).
        lines = body.removesuffix("\n").split("\n")
        headings = [
            Heading(index, len(match["marks"]), match["title"])
            for index, line in enumerate(lines)
            if (match := self._HEADING.match(line))
        ]
        stops = {heading.line for heading in headings}
        stops |= {index for index, line in enumerate(lines) if self._END_MARKER.match(line)}
        starts = self._starts(lines, stops, ArticleLabel.from_line)
        # Quoted headings are an article only when the strict pass found none. Otherwise a quoted
        # "Art." inside an amending article would be split out of that article.
        quoted = False
        if not starts and self._is_veto_promulgation(body):
            starts = self._quoted_starts(lines, stops)
            quoted = bool(starts)
        boundaries = sorted(stops | {index for index, _ in starts} | {len(lines)})
        articles = [self._article(lines, start, key, boundaries, quoted) for start, key in starts]
        return LegalDocument(lines, self._group_into_blocks(articles, headings), quoted_fallback=quoted)

    def _is_veto_promulgation(self, body: str) -> bool:
        return bool(
            self._VETO_PROMULGATION.search(body)
            and self._VETO_PARTS.search(body)
            and not self._ERRATA.search(body)
            and not self._ARTIGO_UNICO.search(body)
        )

    @staticmethod
    def _starts(lines: list[str], stops: set[int], recognize: Callable[[str], str | None]) -> list[tuple[int, str]]:
        return [
            (index, key)
            for index, line in enumerate(lines)
            if index not in stops and (key := recognize(line)) is not None
        ]

    def _quoted_starts(self, lines: list[str], stops: set[int]) -> list[tuple[int, str]]:
        """Quoted headings of a veto promulgation, without splitting an article quoted inside another.

        A line introduced by a colon (`acrescida do seguinte art. 12-C:`) is the inserted text of the
        article above it. A second heading with the same number is the wording of that article, not a
        new block.
        """
        starts: list[tuple[int, str]] = []
        previous = ""
        for index, line in enumerate(lines):
            stripped = line.strip()
            key = None if index in stops else ArticleLabel.from_quoted_line(line)
            # The preamble itself ends with a colon ("as seguintes partes vetadas:"). That must not
            # swallow the first article. A colon only keeps a later heading inside the article above it.
            introduced = bool(starts) and previous.rstrip("\"'”»").endswith(":")
            repeated = bool(starts) and key == starts[-1][1]
            if key and not introduced and not repeated:
                starts.append((index, key))
            if stripped:
                previous = stripped
        return starts

    def _article(self, lines: list[str], start: int, key: str, boundaries: list[int], quoted: bool = False) -> Article:
        end = boundaries[bisect.bisect_right(boundaries, start)]
        units = tuple(unit for index in range(start + 1, end) if (unit := self._unit(lines[index], index, quoted)))
        return Article(key, start, end, units)

    @staticmethod
    def _unit(line: str, index: int, quoted: bool = False) -> Unit | None:
        texts = (line, Label.unwrap_quotes(line)) if quoted else (line,)
        for text in texts:
            for label in UNIT_LABELS:
                if (key := label.from_line(text)) is not None:
                    return Unit(label.level, key, index)
        return None

    def _group_into_blocks(self, articles: list[Article], headings: list[Heading]) -> list[Block]:
        """Starts a new block whenever an article number repeats (numbering restarted)."""
        blocks: list[Block] = []
        previous_block_start = -1
        for article in articles:
            if not blocks or article.key in blocks[-1].articles:
                blocks.append(Block(len(blocks), self._block_title(headings, article.start, previous_block_start)))
                previous_block_start = article.start
            blocks[-1].articles[article.key] = replace(article, block=blocks[-1].index)
        return blocks

    @staticmethod
    def _block_title(headings: list[Heading], line: int, previous_block_start: int) -> str:
        """A top-level heading introduced since the previous block, else the nearest heading."""
        before = [heading for heading in headings if heading.line < line]
        new_top_level = [heading for heading in before if heading.level == 1 and heading.line > previous_block_start]
        chosen = new_top_level or before
        return chosen[-1].title if chosen else messages.MAIN_TEXT_BLOCK
