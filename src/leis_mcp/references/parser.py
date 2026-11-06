"""Parses free-form norm references: 'Lei 8.245/91', '8245/1991', 'LC 123/2006', 'CDC'."""

import re
from dataclasses import replace
from datetime import date
from itertools import chain, repeat

from ..models import Reference
from ..text import TextNormalizer
from .aliases import CONSTITUTION_ALIASES, FEDERAL_LAW_ALIASES, KIND_ALIASES


def organ_slug(value: str | None) -> str | None:
    """'Congresso Nacional' or 'cn' -> 'congresso-nacional'. Unknown text is hyphenated and lowercased."""
    if not value or not str(value).strip():
        return None
    _remainder, organ = ReferenceParser._extract_organ(TextNormalizer.normalize(str(value)))
    if organ:
        return organ
    return TextNormalizer.normalize(str(value)).replace(" ", "-") or None


class ReferenceParser:
    # One organ per reference. Two different organs leave the text unchanged and the organ unset.
    _ORGANS = (
        ("congresso-nacional", re.compile(r"\b(?:do\s+)?congresso\s+nacional\b|\bcn\b|-cn\b")),
        ("camara-dos-deputados", re.compile(r"\b(?:da\s+)?camara\s+dos\s+deputados\b|\bcamara\b|-cd\b")),
        ("senado-federal", re.compile(r"\b(?:do\s+)?senado\s+federal\b|\bsenado\b|-sf\b")),
    )
    _NUMBER = r"\d{1,3}(?:\.\d{3})+|\d+"
    _KINDS = "|".join(re.escape(kind) for kind in sorted(KIND_ALIASES, key=len, reverse=True))
    # "lei n 8.245/91", "8245 de 1991", "dl 5452", "decreto 9.000, de 08/05/2017"
    _COMPACT = re.compile(
        rf"^(?:(?P<kind>{_KINDS})\.?\s*)?(?:n[or]?\.?\s*)?(?P<number>{_NUMBER})"
        r"(?:\s*[/,\s-]\s*(?:de\s+)?(?:\d{1,2}[./-]\d{1,2}[./-])?(?P<year>\d{4}|\d{2}))?$"
    )
    # "lei n 8.245, de 18 de outubro de 1991"
    _LONG_FORM = re.compile(rf"^(?P<kind>{_KINDS})\b.*?(?P<number>{_NUMBER})")
    _LONG_FORM_YEAR = re.compile(r"\b(1[89]\d\d|20\d\d)\b")
    _NOISE = re.compile(r"\s*\b(?:federal|ordinaria)\b")

    def __init__(self, current_year: int | None = None, extra_aliases: dict[str, Reference] | None = None):
        self._current_year = current_year or date.today().year
        self._extra_aliases = dict(extra_aliases or {})
        self._aliases = self._alias_index()

    def parse(self, text: str) -> Reference | None:
        raw = text.strip()
        if raw.endswith(".md"):
            return Reference(path=raw.lstrip("/"))
        key = TextNormalizer.normalize(raw)
        if alias := self._aliases.get(key):
            return alias
        key, organ = self._extract_organ(key)
        key = self._NOISE.sub("", key)
        key = re.sub(r"\s+", " ", key).strip(" -")
        reference = self._parse_compact(key) or self._parse_long_form(key)
        if reference is not None and organ:
            return replace(reference, organ=organ)
        return reference

    def kind_directory(self, kind: str | None) -> str | None:
        """'lei' / 'Lei Complementar' / 'leis-ordinarias' -> repository directory name."""
        if not kind:
            return None
        key = TextNormalizer.normalize(kind)
        return KIND_ALIASES.get(key, key.replace(" ", "-"))

    def expand_year(self, year: str) -> tuple[int, ...]:
        """'1991' -> (1991,); '91' -> (1991,); '25' -> (2025, 1925) while 2025 is not in the future."""
        if len(year) == 4:
            return (int(year),)
        short = int(year)
        if short <= self._current_year % 100:
            return (2000 + short, 1900 + short)
        return (1900 + short,)

    def alias_table(self) -> dict[str, str]:
        return {alias: self._describe(reference) for alias, reference in sorted(self._aliases.items())}

    @staticmethod
    def normalize_number(number: str) -> str:
        return str(int(number.replace(".", "")))

    def _parse_compact(self, key: str) -> Reference | None:
        match = self._COMPACT.match(key)
        if not match:
            return None
        return Reference(
            number=self.normalize_number(match["number"]),
            years=self.expand_year(match["year"]) if match["year"] else (),
            kind=KIND_ALIASES.get(match["kind"] or ""),
        )

    def _parse_long_form(self, key: str) -> Reference | None:
        match = self._LONG_FORM.match(key)
        if not match:
            return None
        year = self._LONG_FORM_YEAR.search(key, match.end())
        return Reference(
            number=self.normalize_number(match["number"]),
            years=(int(year.group(1)),) if year else (),
            kind=KIND_ALIASES[match["kind"]],
        )

    def _alias_index(self) -> dict[str, Reference]:
        laws = {
            Reference(kind=kind, number=number, years=(year,), sphere="federal"): aliases
            for (kind, number, year), aliases in FEDERAL_LAW_ALIASES.items()
        }
        constitution = {
            Reference(path=path, block=block, sphere="federal"): aliases
            for (path, block), aliases in CONSTITUTION_ALIASES.items()
        }
        pairs = chain.from_iterable(zip(aliases, repeat(target)) for target, aliases in (laws | constitution).items())
        index = {TextNormalizer.normalize(alias): replace(target, alias=alias) for alias, target in pairs}
        for alias, reference in self._extra_aliases.items():
            key = TextNormalizer.normalize(alias)
            if key not in index:
                index[key] = replace(reference, alias=alias)
        return index

    @classmethod
    def _extract_organ(cls, key: str) -> tuple[str, str | None]:
        found = [slug for slug, pattern in cls._ORGANS if pattern.search(key)]
        unique = list(dict.fromkeys(found))
        if len(unique) != 1:
            return key, None
        slug = unique[0]
        pattern = next(item for item in cls._ORGANS if item[0] == slug)[1]
        return pattern.sub(" ", key), slug

    @staticmethod
    def _describe(reference: Reference) -> str:
        if reference.path:
            return f"{reference.path}#{reference.block}" if reference.block else reference.path
        return f"{reference.kind} {reference.number}/{reference.years[0]}"
