"""Builds a `LegalDocument` (blocks of articles and their units) from a norm's Markdown body."""

import bisect
import re
from dataclasses import replace

from .. import messages
from ..document import Article, Block, Heading, LegalDocument, Unit
from .labels import UNIT_LABELS, ArticleLabel


class DocumentParser:
    _HEADING = re.compile(r"^(?P<marks>#{1,6})\s+(?P<title>.+?)\s*$")
    # Signature lines that close the normative text.
    _END_MARKER = re.compile(
        r"^\s*\**\s*(?:(?:Bras[íi]lia|Rio de Janeiro|Capital Federal)\s*,|Este texto n[ãa]o substitui)",
        re.IGNORECASE,
    )

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
        starts = [
            (index, key)
            for index, line in enumerate(lines)
            if index not in stops and (key := ArticleLabel.from_line(line)) is not None
        ]
        boundaries = sorted(stops | {index for index, _ in starts} | {len(lines)})
        articles = [self._article(lines, start, key, boundaries) for start, key in starts]
        return LegalDocument(lines, self._group_into_blocks(articles, headings))

    def _article(self, lines: list[str], start: int, key: str, boundaries: list[int]) -> Article:
        end = boundaries[bisect.bisect_right(boundaries, start)]
        units = tuple(unit for index in range(start + 1, end) if (unit := self._unit(lines[index], index)))
        return Article(key, start, end, units)

    @staticmethod
    def _unit(line: str, index: int) -> Unit | None:
        return next(
            (Unit(label.level, key, index) for label in UNIT_LABELS if (key := label.from_line(line)) is not None),
            None,
        )

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
