"""Turns user references into indexed norms."""

from dataclasses import replace

from .. import messages
from ..catalog.index import NormIndex
from ..errors import QueryError
from ..models import Norm, NormQuery, Reference
from ..references.parser import ReferenceParser, organ_slug


class NormResolver:
    def __init__(self, index: NormIndex, references: ReferenceParser):
        self._index = index
        self._references = references

    def _parse(self, text: str) -> Reference:
        reference = self._references.parse(text)
        if reference is None:
            raise QueryError(messages.UNKNOWN_REFERENCE.format(reference=text))
        return reference

    def query_for(
        self,
        reference: Reference | None = None,
        *,
        year: int | None = None,
        kind: str | None = None,
        sphere: str | None = None,
        state: str | None = None,
        city: str | None = None,
        agency: str | None = None,
    ) -> NormQuery:
        """Index query from a reference plus explicit filters, which take precedence."""
        base = reference or Reference()
        query = NormQuery(number=base.number, years=base.years, kind=base.kind, sphere=base.sphere, agency=base.organ)
        overrides = {
            "years": (int(year),) if year else None,
            "kind": self._references.kind_directory(kind),
            "state": state.upper() if state else None,
            "sphere": sphere or ("estaduais" if state else None),
            "city": city,
            "agency": organ_slug(agency) if agency else None,
        }
        return replace(query, **{field: value for field, value in overrides.items() if value})

    def find(
        self,
        text: str | None = None,
        number: str | int | None = None,
        *,
        year: int | None = None,
        kind: str | None = None,
        sphere: str | None = None,
        state: str | None = None,
        agency: str | None = None,
        limit: int = 10,
    ) -> tuple[list[Norm], Reference | None]:
        raw = text or (str(number) if number is not None else None)
        reference = self._parse(raw) if raw else None
        if reference and reference.path:
            norm = self._by_path(reference.path)
            query = self.query_for(reference, year=year, kind=kind, sphere=sphere, state=state, agency=agency)
            if not _matches_filters(norm, query):
                return [], reference
            return [norm], reference
        query = self.query_for(reference, year=year, kind=kind, sphere=sphere, state=state, agency=agency)
        if not query.number:
            raise QueryError(messages.NUMBER_REQUIRED)
        norms = self._index.find(query, limit)
        if not norms and query.sphere == "federal" and not (sphere or state):
            norms = self._index.find(replace(query, sphere=None), limit)  # alias sphere is only a hint
        return norms, reference

    def resolve(self, text: str) -> tuple[Norm, Reference]:
        """The single norm a reference points to; fails when it is unknown or ambiguous."""
        if not text or not str(text).strip():
            raise QueryError(messages.NORM_REQUIRED)
        norms, reference = self.find(str(text))
        if not norms:
            raise QueryError(messages.NORM_NOT_FOUND.format(reference=text))
        if self._is_ambiguous(norms, reference):
            raise QueryError(
                messages.AMBIGUOUS_REFERENCE.format(
                    reference=text,
                    example="/".join(str(part) for part in (norms[0].number, norms[0].year) if part),
                    options="; ".join(norm.path for norm in norms[:6]),
                )
            )
        return norms[0], reference

    def _by_path(self, path: str) -> Norm:
        norm = self._index.by_path(path)
        if norm is None:
            raise QueryError(messages.PATH_NOT_INDEXED.format(path=path))
        return norm

    @staticmethod
    def _is_ambiguous(norms: list[Norm], reference: Reference) -> bool:
        """Several norms of the same sphere/kind/state and no year to tell them apart."""
        if len(norms) < 2 or reference.path or reference.years:
            return False
        first, second = norms[:2]
        return (first.sphere, first.kind, first.state) == (second.sphere, second.kind, second.state)


def _matches_filters(norm: Norm, query: NormQuery) -> bool:
    """Explicit filters win over an alias path. A contradictory organ does not return that alias."""
    checks = (
        (query.agency, norm.agency),
        (query.sphere, norm.sphere),
        (query.kind, norm.kind),
        (query.number, norm.number),
        (query.state, norm.state),
        (query.city, norm.city),
    )
    if any(expected is not None and actual != expected for expected, actual in checks):
        return False
    return not query.years or norm.year in query.years
