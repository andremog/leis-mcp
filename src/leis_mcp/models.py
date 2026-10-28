"""Data models shared across the package. No behavior beyond trivial accessors."""

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from .document import Article, LegalDocument


class SourceField(StrEnum):
    """Keys of the norms' YAML front matter (the source schema is pt-BR)."""

    TITLE = "titulo"
    KIND = "tipo"
    NUMBER = "numero"
    DATE = "data"
    SUMMARY = "ementa"
    REVOKED = "revogada"
    STATUS = "situacao"
    TEXT_VERSION = "versao_texto"
    SOURCE_URL = "fonte"
    PUBLICATION = "publicacao"
    URN = "urn"
    STATE = "uf"
    DOWNLOADED_AT = "baixado_em"


@dataclass(frozen=True, slots=True, kw_only=True)
class PathInfo:
    """Metadata derived from a norm's path in the source repository."""

    path: str
    sphere: str
    kind: str | None = None
    year: int | None = None
    number: str | None = None
    date: str | None = None
    state: str | None = None
    city: str | None = None
    agency: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class Norm(PathInfo):
    """An indexed norm: path metadata plus the git blob holding its content."""

    blob: str


@dataclass(frozen=True, slots=True)
class FileNameInfo:
    number: str | None = None
    year: int | None = None
    date: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class NormQuery:
    """Filters over the norm index. Empty fields are ignored."""

    number: str | None = None
    years: tuple[int, ...] = ()
    kind: str | None = None
    sphere: str | None = None
    state: str | None = None
    city: str | None = None
    agency: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class Reference:
    """A parsed user reference to a norm ('Lei 8.245/91', 'CDC', a repository path)."""

    number: str | None = None
    years: tuple[int, ...] = ()
    kind: str | None = None
    sphere: str | None = None
    path: str | None = None
    block: str | None = None
    alias: str | None = None


@dataclass(frozen=True, slots=True)
class IndexStats:
    commit: str
    norms: int
    seconds: float


@dataclass(frozen=True, slots=True)
class SourceText:
    """A norm's Markdown split into front matter and body."""

    metadata: dict[str, Any]
    body: str
    body_offset: int  # lines before the body, to map file lines to body lines


@dataclass(frozen=True, slots=True)
class Excerpt:
    citation: str
    text: str
    head: str | None  # caput, only when a paragraph/clause/subclause was requested
    block_title: str
    notices: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class LoadedNorm:
    norm: Norm
    metadata: dict[str, Any]
    document: LegalDocument
    body_offset: int

    @property
    def title(self) -> str:
        return self.metadata.get(SourceField.TITLE, self.norm.path)


@dataclass(frozen=True, slots=True)
class Occurrence:
    file: Path
    line: int  # 1-based
    text: str


@dataclass(frozen=True, slots=True)
class SearchMatch:
    loaded: LoadedNorm
    occurrence: Occurrence
    article: Article | None
