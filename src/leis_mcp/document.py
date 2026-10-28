"""Structure of a parsed norm: blocks of articles, each with paragraphs, clauses and subclauses."""

import bisect
from dataclasses import dataclass, field
from enum import IntEnum
from itertools import chain
from typing import Self

from .text import TextNormalizer


class Level(IntEnum):
    """Provision levels below an article: paragraph (§), clause (inciso), subclause (alínea)."""

    PARAGRAPH = 1
    CLAUSE = 2
    SUBCLAUSE = 3


@dataclass(frozen=True, slots=True)
class Unit:
    level: Level
    key: str
    line: int


@dataclass(frozen=True, slots=True)
class Heading:
    line: int
    level: int
    title: str


@dataclass(frozen=True, slots=True)
class Span:
    """A line range [start, end) of a document and the units inside it."""

    start: int
    end: int
    units: tuple[Unit, ...]

    def keys(self, level: Level) -> list[str]:
        return [unit.key for unit in self.units if unit.level == level]

    def narrow(self, level: Level, key: str) -> Self | None:
        """Range of the first unit `level`/`key`, ending at the next unit of the same or higher level."""
        index = next((i for i, unit in enumerate(self.units) if (unit.level, unit.key) == (level, key)), None)
        if index is None:
            return None
        following = self.units[index + 1 :]
        end = next((unit.line for unit in following if unit.level <= level), self.end)
        return type(self)(self.units[index].line, end, tuple(unit for unit in following if unit.line < end))

    def before_first(self, level: Level) -> Self:
        end = next((unit.line for unit in self.units if unit.level == level), self.end)
        return type(self)(self.start, end, tuple(unit for unit in self.units if unit.line < end))


@dataclass(frozen=True, slots=True)
class Article:
    key: str
    start: int
    end: int
    units: tuple[Unit, ...]
    block: int = 0

    @property
    def head_end(self) -> int:
        """End of the caput: first paragraph/clause/subclause line."""
        return next((unit.line for unit in self.units), self.end)

    @property
    def span(self) -> Span:
        return Span(self.start, self.end, self.units)


@dataclass(eq=False, slots=True)
class Block:
    """Articles with continuous numbering. A norm has several when numbering restarts (CF + ADCT)."""

    index: int
    title: str
    articles: dict[str, Article] = field(default_factory=dict)

    def matches(self, selector: str | int) -> bool:
        if isinstance(selector, int) or selector.isdigit():
            return self.index == int(selector)
        return TextNormalizer.fold(selector) in TextNormalizer.fold(self.title)


class LegalDocument:
    def __init__(self, lines: list[str], blocks: list[Block]):
        self.lines = lines
        self.blocks = blocks
        articles = chain.from_iterable(block.articles.values() for block in blocks)
        self._ordered = sorted(articles, key=lambda article: article.start)
        self._starts = [article.start for article in self._ordered]

    @property
    def main_block(self) -> Block | None:
        return max(self.blocks, key=lambda block: len(block.articles), default=None)

    @property
    def has_several_blocks(self) -> bool:
        return len(self.blocks) > 1

    def block_title(self, article: Article) -> str:
        return self.blocks[article.block].title

    def article_at(self, line: int) -> Article | None:
        """Article containing body line `line` (0-based)."""
        position = bisect.bisect_right(self._starts, line) - 1
        if position < 0:
            return None
        article = self._ordered[position]
        return article if line < article.end else None

    def text(self, start: int, end: int) -> str:
        return "\n".join(self.lines[start:end]).strip()
