"""Facade used by the MCP tools and the CLI."""

from collections import defaultdict
from itertools import chain
from pathlib import Path
from typing import Any, Self

from .. import messages
from ..catalog.git_repository import GitRepository
from ..catalog.index import NormIndex
from ..catalog.path_parser import PathParser
from ..config import Settings
from ..content.store import ContentStore
from ..errors import ProvisionNotFoundError, QueryError
from ..legal_text.extractor import ProvisionExtractor
from ..models import LoadedNorm, Norm, NormQuery, Occurrence, SearchMatch, SourceField
from ..overlay.catalog import CompositeIndex, attach
from ..references.parser import ReferenceParser
from ..search.text_search import TextSearch
from .loader import NormLoader
from .presenter import NormPresenter
from .resolver import NormResolver


class LegislationService:
    MAX_FOUND = 50
    MAX_OCCURRENCES = 100
    MAX_PAGE_SIZE = 200
    MIN_QUERY_LENGTH = 3
    _DETAILED_RESULTS = 5  # results whose content is fetched to show their front matter
    # Browse drill-down, per sphere: the first unset column is the one counted.
    _DRILLDOWN = {
        "estaduais": ("state", "kind"),
        "municipais": ("state", "city", "kind"),
        "reguladores": ("agency", "kind"),
        "jurisprudencia": ("agency", "kind"),
    }
    _DEFAULT_DRILLDOWN = ("kind",)
    _QUERY_FIELDS = {"year": "years"}  # browse column -> NormQuery field, when they differ

    def __init__(
        self,
        index: NormIndex | CompositeIndex,
        references: ReferenceParser,
        store: ContentStore,
        presenter: NormPresenter,
        search: TextSearch,
        data_dir: Path,
    ):
        self._index = index
        self._references = references
        self._resolver = NormResolver(index, references)
        self._store = store
        self._loader = NormLoader(store)
        self._presenter = presenter
        self._search = search
        self._data_dir = data_dir

    @classmethod
    def from_settings(cls, settings: Settings, repository: GitRepository | None = None) -> Self:
        repository = repository or GitRepository(settings)
        index, store, aliases = attach(settings, repository)
        presenter = NormPresenter(settings.blob_base_url, index.commit)
        return cls(index, ReferenceParser(extra_aliases=aliases), store, presenter, TextSearch(), settings.home)

    def find_norms(
        self,
        reference: str | None = None,
        number: str | int | None = None,
        year: int | None = None,
        kind: str | None = None,
        sphere: str | None = None,
        state: str | None = None,
        limit: int = 10,
        agency: str | None = None,
    ) -> dict[str, Any]:
        norms, parsed = self._resolver.find(
            reference,
            number,
            year=year,
            kind=kind,
            sphere=sphere,
            state=state,
            agency=agency,
            limit=self._clamp(limit, self.MAX_FOUND),
        )
        if not norms:
            return self._presenter.no_matches()
        detailed = norms[: self._DETAILED_RESULTS]
        self._store.prefetch(detailed)  # failures just leave those results without front matter
        available = [norm for norm in detailed if self._store.is_cached(norm)]
        return self._presenter.norm_matches(norms, self._load_all(available), parsed)

    def read_provision(
        self,
        norm: str,
        article: str,
        paragraph: str | None = None,
        clause: str | None = None,
        subclause: str | None = None,
        block: str | None = None,
    ) -> dict[str, Any]:
        target, reference = self._resolver.resolve(norm)
        loaded = self._loader.load(target)
        if not loaded.document.blocks:
            source = loaded.metadata.get(SourceField.SOURCE_URL)
            raise QueryError(messages.NO_ARTICLES.format(path=target.path, source=source))
        extractor = ProvisionExtractor(loaded.document)
        try:
            excerpt = extractor.extract(article, paragraph, clause, subclause, block or reference.block)
        except ProvisionNotFoundError as exc:
            raise exc.in_norm(loaded.title) from exc
        return self._presenter.provision(loaded, excerpt)

    def search_text(
        self,
        query: str,
        norm: str | None = None,
        sphere: str | None = None,
        kind: str | None = None,
        year: int | None = None,
        state: str | None = None,
        limit: int = 20,
        agency: str | None = None,
    ) -> dict[str, Any]:
        if len((query or "").strip()) < self.MIN_QUERY_LENGTH:
            raise QueryError(messages.QUERY_TOO_SHORT.format(minimum=self.MIN_QUERY_LENGTH))
        filters = self._resolver.query_for(year=year, kind=kind, sphere=sphere, state=state, agency=agency)
        norms, scope = self._search_scope(norm, filters)
        by_file: dict[Path, list[Norm]] = defaultdict(list)  # identical texts share one cached blob
        for item in norms:
            by_file[self._store.path_for(item.blob)].append(item)
        occurrences = self._search.search(query, list(by_file), limit=self._clamp(limit, self.MAX_OCCURRENCES))
        ordered = sorted(occurrences, key=lambda occurrence: (str(occurrence.file), occurrence.line))
        matches = chain.from_iterable(self._matches(by_file[occurrence.file], occurrence) for occurrence in ordered)
        return self._presenter.search_results(query, scope, self._search.engine_name, list(matches))

    def browse(
        self,
        sphere: str | None = None,
        kind: str | None = None,
        year: int | None = None,
        state: str | None = None,
        city: str | None = None,
        agency: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        if sphere and sphere not in PathParser.SPHERES:
            raise QueryError(messages.INVALID_SPHERE.format(sphere=sphere, options=", ".join(PathParser.SPHERES)))
        query = self._resolver.query_for(year=year, kind=kind, sphere=sphere, state=state, city=city, agency=agency)
        total = self._index.count(query)
        column = self._drilldown_column(query)
        if column:
            return self._presenter.counts(column, total, self._index.count_by(column, query))
        norms = self._index.browse(query, self._clamp(limit, self.MAX_PAGE_SIZE), int(offset))
        cached = [norm for norm in norms if self._store.is_cached(norm)]
        return self._presenter.page(norms, total, int(offset), self._load_all(cached))

    def aliases(self) -> dict[str, str]:
        return self._references.alias_table()

    def warm_up(
        self,
        include_aliases: bool = True,
        sphere: str | None = None,
        kind: str | None = None,
        year: int | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        """Downloads the aliased laws and/or every norm matching a filter into the cache."""
        norms, errors = self._aliased_norms() if include_aliases else ([], [])
        if sphere or kind or year:
            query = self._resolver.query_for(year=year, kind=kind, sphere=sphere)
            norms += self._index.browse(query, limit=limit or self._index.count(query))
        unique = list({norm.blob: norm for norm in norms}.values())
        return {"norms": len(unique), "errors": errors + self._store.prefetch(unique)}

    def status(self) -> dict[str, Any]:
        return {
            **self._index.metadata,
            "cached": len(self._store.cached_blobs()),
            "search_engine": self._search.engine_name,
            "data_dir": str(self._data_dir),
        }

    def _search_scope(self, norm: str | None, filters: NormQuery) -> tuple[list[Norm], str]:
        if norm:
            target, _ = self._resolver.resolve(norm)
            self._store.read(target)
            return [target], messages.NORM_SCOPE.format(path=target.path)
        cached = self._index.by_blobs(self._store.cached_blobs(), filters)
        return cached, messages.CACHE_SCOPE.format(count=len(cached))

    def _matches(self, norms: list[Norm], occurrence: Occurrence) -> list[SearchMatch]:
        return [self._match(norm, occurrence) for norm in norms]

    def _match(self, norm: Norm, occurrence: Occurrence) -> SearchMatch:
        loaded = self._loader.load(norm)
        article = loaded.document.article_at(occurrence.line - 1 - loaded.body_offset)
        return SearchMatch(loaded, occurrence, article)

    def _drilldown_column(self, query: NormQuery) -> str | None:
        columns = ("sphere", *self._DRILLDOWN.get(query.sphere or "", self._DEFAULT_DRILLDOWN), "year")
        return next((column for column in columns if not getattr(query, self._QUERY_FIELDS.get(column, column))), None)

    def _aliased_norms(self) -> tuple[list[Norm], list[str]]:
        """One norm per distinct alias target, plus an error per target that could not be resolved."""
        representatives = {target: alias for alias, target in self._references.alias_table().items()}
        norms, errors = [], []
        for alias in representatives.values():
            try:
                norms.append(self._resolver.resolve(alias)[0])
            except QueryError as exc:
                errors.append(f"{alias}: {exc}")
        return norms, errors

    def _load_all(self, norms: list[Norm]) -> dict[Norm, LoadedNorm]:
        return {norm: self._loader.load(norm) for norm in norms}

    @staticmethod
    def _clamp(value: int, maximum: int) -> int:
        return max(1, min(int(value), maximum))
