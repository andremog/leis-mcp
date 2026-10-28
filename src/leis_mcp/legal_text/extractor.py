"""Extracts the exact text of an article, paragraph, clause or subclause from a `LegalDocument`."""

from .. import messages
from ..document import Article, Block, LegalDocument, Level, Span
from ..errors import ProvisionNotFoundError
from ..models import Excerpt
from .labels import UNIT_LABELS, ArticleLabel, Citation, Label, ParagraphLabel
from .notices import ProvisionNotices


class ProvisionExtractor:
    _NEIGHBOURS = 5

    def __init__(self, document: LegalDocument):
        self._document = document

    def extract(
        self,
        article: str | int,
        paragraph: str | int | None = None,
        clause: str | int | None = None,
        subclause: str | None = None,
        block: str | int | None = None,
    ) -> Excerpt:
        key = ArticleLabel.parse(article)
        selected, notices = self._select(key, block)
        requested = self._requested_units(paragraph, clause, subclause)
        span = self._narrow(selected, requested)
        text = self._document.text(span.start, span.end)
        head = self._document.text(selected.start, selected.head_end) if requested else None
        return Excerpt(
            citation=Citation.format(key, requested),
            text=text,
            head=head if head != text else None,
            block_title=self._document.block_title(selected),
            notices=(*notices, *ProvisionNotices.for_text(text)),
        )

    @staticmethod
    def _requested_units(*values: str | int | None) -> dict[Level, str]:
        """Parsed keys of the requested units, ordered paragraph -> clause -> subclause."""
        return {
            label.level: label.parse(value)
            for label, value in zip(UNIT_LABELS, values, strict=True)
            if value not in (None, "")
        }

    def _narrow(self, article: Article, requested: dict[Level, str]) -> Span:
        span = article.span
        if requested and Level.PARAGRAPH not in requested:
            span = span.before_first(Level.PARAGRAPH)  # clauses without a paragraph belong to the caput
        for level, key in requested.items():
            narrowed = span.narrow(level, key)
            if narrowed is None:
                raise self._unit_not_found(article, level, key, span, requested.get(Level.PARAGRAPH))
            span = narrowed
        return span

    def _select(self, key: str, selector: str | int | None) -> tuple[Article, list[str]]:
        candidates = [block for block in self._document.blocks if key in block.articles]
        if not candidates:
            raise ProvisionNotFoundError(messages.ARTICLE_NOT_FOUND.format(article=key), self._neighbours(key))
        chosen = self._choose_block(key, candidates, selector)
        others = [block for block in candidates if block is not chosen]
        notices = [self._duplicate_notice(key, chosen, others)] if others else []
        return chosen.articles[key], notices

    def _choose_block(self, key: str, candidates: list[Block], selector: str | int | None) -> Block:
        if selector in (None, ""):
            main = self._document.main_block
            return next((block for block in candidates if block is main), candidates[0])
        matching = [block for block in candidates if block.matches(selector)]
        if not matching:
            raise ProvisionNotFoundError(
                messages.ARTICLE_NOT_IN_BLOCK.format(article=key, block=selector),
                [messages.BLOCK_OPTION.format(index=block.index, title=block.title) for block in candidates],
            )
        return matching[0]

    @staticmethod
    def _duplicate_notice(key: str, chosen: Block, others: list[Block]) -> str:
        listed = "; ".join(messages.BLOCK_REFERENCE.format(index=block.index, title=block.title) for block in others)
        return messages.DUPLICATE_ARTICLE.format(article=Label.ordinal(key), others=listed, shown=chosen.title)

    def _neighbours(self, key: str) -> list[str]:
        main = self._document.main_block
        if main is None:
            return []
        target = ArticleLabel.number_of(key)
        keys = list(main.articles)
        closest = set(sorted(keys, key=lambda k: abs(ArticleLabel.number_of(k) - target))[: self._NEIGHBOURS])
        return [messages.ARTICLE_SUGGESTION.format(article=k) for k in keys if k in closest]

    def _unit_not_found(
        self, article: Article, level: Level, key: str, span: Span, paragraph: str | None
    ) -> ProvisionNotFoundError:
        builders = {
            Level.PARAGRAPH: lambda: self._paragraph_not_found(article, key, span),
            Level.CLAUSE: lambda: self._clause_not_found(article, key, span, paragraph),
            Level.SUBCLAUSE: lambda: self._subclause_not_found(article, key, span),
        }
        return builders[level]()

    @staticmethod
    def _paragraph_not_found(article: Article, key: str, span: Span) -> ProvisionNotFoundError:
        return ProvisionNotFoundError(
            messages.PARAGRAPH_NOT_FOUND.format(paragraph=ParagraphLabel.display(key), article=article.key),
            [ParagraphLabel.display(existing) for existing in span.keys(Level.PARAGRAPH)],
        )

    def _clause_not_found(
        self, article: Article, key: str, span: Span, paragraph: str | None
    ) -> ProvisionNotFoundError:
        scope = f", {ParagraphLabel.display(paragraph)}" if paragraph else ""
        message = messages.CLAUSE_NOT_FOUND.format(clause=key, article=article.key, scope=scope)
        holders = [] if paragraph else self._paragraphs_with_clause(article, key)
        if holders:
            message += messages.CLAUSE_FOUND_IN_PARAGRAPHS.format(paragraphs=", ".join(holders))
        return ProvisionNotFoundError(message, span.keys(Level.CLAUSE))

    @staticmethod
    def _paragraphs_with_clause(article: Article, key: str) -> list[str]:
        whole = article.span
        return [
            ParagraphLabel.display(candidate)
            for candidate in whole.keys(Level.PARAGRAPH)
            if (inside := whole.narrow(Level.PARAGRAPH, candidate)) and inside.narrow(Level.CLAUSE, key)
        ]

    @staticmethod
    def _subclause_not_found(article: Article, key: str, span: Span) -> ProvisionNotFoundError:
        return ProvisionNotFoundError(
            messages.SUBCLAUSE_NOT_FOUND.format(subclause=key, article=article.key),
            [f"{existing})" for existing in span.keys(Level.SUBCLAUSE)],
        )
