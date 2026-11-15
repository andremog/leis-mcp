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
    LEGAL_FORCE = "forca_legal_fonte"  # normas.leg.br `legislationLegalForce`, as a JSON list
    IN_FORCE = "vigente_fonte"  # regulators
    REPEAL_EVIDENCE = "evidencia_revogacao"  # regulators
    # Parliamentary overlay. Absent on upstream norms.
    IDENTIFIER = "identificador"
    ORGAN = "orgao"
    VIGENCIA = "vigencia"
    TEXT_NATURE = "natureza_texto"
    COLLECTED_AT = "coletado_em"
    CONTENT_HASH = "hash_conteudo"


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
    organ: str | None = None


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


class ValidityState(StrEnum):
    """What the sources say about a norm being in force. Never inferred from missing data."""

    REVOKED = "revogada"
    NOT_IN_FORCE = "sem_vigencia_na_fonte"
    IN_FORCE = "vigente_segundo_a_fonte"
    NO_REPEAL_RECORDED = "sem_revogacao_registrada"
    OTHER = "outra"
    UNKNOWN = "nao_informada"
    CONFLICTING = "conflitante"


@dataclass(frozen=True, slots=True)
class Validity:
    state: ValidityState
    fields: dict[str, Any]  # raw source fields behind the state
    repealed_by: str | None = None
    origin: str = "acervo"


@dataclass(frozen=True, slots=True)
class Wording:
    version: str | None
    consolidated: bool | None
    captured_at: str | None
    commit: str


@dataclass(frozen=True, slots=True)
class ProvisionStatus:
    """A provision whose whole text is a repeal or veto annotation, e.g. `I - *(Revogado pela Lei ...)*`."""

    revoked: bool
    vetoed: bool
    note: str


@dataclass(frozen=True, slots=True)
class Excerpt:
    citation: str
    text: str
    head: str | None  # caput, only when a paragraph/clause/subclause was requested
    block_title: str
    notices: tuple[str, ...]
    article: str = ""
    units: tuple[tuple[int, str], ...] = ()  # requested (Level, key) pairs, paragraph -> subclause
    status: ProvisionStatus | None = None


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
