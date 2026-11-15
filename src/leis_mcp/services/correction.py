"""Query-time corrections from an official source, or a clear refusal.

An article quoted with `(NR)`/`(AC)` is never returned as an article of the law that quotes it.
A veto promulgation is read only when its preamble names that same law. A file that is plainly
incomplete can be replaced by the compiled Planalto page. A newer wording is added only when
normas.leg.br names exactly one current version dated after the corpus capture. Anything else
stays on the corpus text or fails closed.
"""

import logging
from datetime import UTC, date, datetime
from typing import Any

from .. import messages
from ..errors import ProvisionNotFoundError, QueryError
from ..legal_text import DocumentParser, ProvisionExtractor
from ..legal_text.insertions import Insertion, digits
from ..legal_text.insertions import display_number as show_number
from ..legal_text.labels import ArticleLabel, Label
from ..models import LoadedNorm, SourceField
from ..online.planalto import OfficialPage, PlanaltoText, PlanaltoUnavailable

log = logging.getLogger(__name__)


def _log(law: str, article: str, problem: str, source: str) -> None:
    log.info(
        "autocorrection law=%s article=%s problem=%s source=%s at=%s",
        law,
        article,
        problem,
        source,
        datetime.now(UTC).isoformat(timespec="seconds"),
    )


class CorpusCorrector:
    def __init__(self, planalto: PlanaltoText):
        self._planalto = planalto

    def read(
        self,
        service: Any,
        loaded: LoadedNorm,
        article: str,
        paragraph: str | None,
        clause: str | None,
        subclause: str | None,
        block: str | None,
    ) -> dict[str, Any]:
        key = ArticleLabel.parse(article)
        law = _law_label(loaded)
        if self._preamble_rejects(loaded):
            raise QueryError(messages.PREAMBLE_REJECTED.format(number=show_number(loaded.norm.number or "")))
        quoted = self._insertions(loaded, key)
        if quoted and not _has_article(loaded, key):
            return self._amended(service, loaded, key, quoted, law)
        local = self._local(service, loaded, article, paragraph, clause, subclause, block)
        if not _fragment(loaded):
            if local is None:
                source = loaded.metadata.get(SourceField.SOURCE_URL)
                raise QueryError(messages.NO_ARTICLES.format(path=loaded.norm.path, source=source))
            if isinstance(local, ProvisionNotFoundError):
                raise local.in_norm(loaded.title)
            refreshed = self._refresh(service, loaded, local, article, paragraph, clause, subclause, law)
            return refreshed or local
        official = self._official(loaded, article, paragraph, clause, subclause, law, "fragmento")
        if official is not None:
            return official
        if isinstance(local, dict):
            return local
        if not self._planalto.online:
            raise QueryError(messages.PLANALT_OFFLINE.format(article=Label.ordinal(key)))
        if not loaded.document.blocks:
            source = loaded.metadata.get(SourceField.SOURCE_URL)
            raise QueryError(messages.NO_ARTICLES.format(path=loaded.norm.path, source=source))
        raise local.in_norm(loaded.title)

    def _amended(self, service: Any, loaded: LoadedNorm, key: str, found: list[Insertion], law: str) -> dict[str, Any]:
        hosts = list(dict.fromkeys(item.host for item in found))
        host = ", ".join(Label.ordinal(item) for item in hosts)
        laws = {item.law for item in found}
        year = next((item.year for item in found if item.year), None)
        number = laws.pop() if len(laws) == 1 else None
        if number is None or not number:
            message = messages.NOT_THIS_LAW_UNKNOWN.format(
                article=Label.ordinal(key), number=show_number(loaded.norm.number or ""), host=host
            )
        else:
            message = messages.NOT_THIS_LAW.format(
                article=Label.ordinal(key),
                number=show_number(loaded.norm.number or ""),
                host=host,
                other=show_number(number),
            )
        text = loaded.document.text(found[0].start, found[0].end)
        payload: dict[str, Any] = {
            "norma": service._presenter.norm(loaded.norm, loaded.metadata),
            "mensagem": message,
            "redacao_dada": {"artigo_desta_lei": host, "artigo_alterado": Label.ordinal(key), "texto": text},
            "avisos": [messages.VETO_PROMULGATION_ONLY] if loaded.document.quoted_fallback else [],
            "fonte": loaded.metadata.get(SourceField.SOURCE_URL),
            "aviso_legal": messages.LEGAL_NOTICE,
        }
        source = ""
        if number:
            other = self._other_corpus(service, loaded, number, year, key)
            if other is not None:
                payload["dispositivo_da_lei_alterada"] = other
                source = str(other.get("fonte") or "")
        _log(law, key, "redacao_nr", source)
        return payload

    def _other_corpus(
        self, service: Any, loaded: LoadedNorm, number: str, year: int | None, article: str
    ) -> dict | None:
        reference = f"{number}/{year}" if year else number
        try:
            target, _ = service._resolver.resolve(reference)
        except QueryError:
            return {
                "mensagem": messages.OTHER_LAW_MISSING.format(
                    article=Label.ordinal(article), number=show_number(number)
                )
            }
        if digits(target.number or "") != number or (year is not None and target.year != year):
            return None
        other = service._loader.load(target)
        try:
            excerpt = ProvisionExtractor(other.document).extract(article)
        except ProvisionNotFoundError:
            return {
                "mensagem": messages.OTHER_LAW_MISSING.format(
                    article=Label.ordinal(article), number=show_number(number)
                )
            }
        online = service._online.for_provision(other, excerpt)
        presented = service._presenter.provision(other, excerpt, online)
        return {"norma": presented["norma"], "dispositivo": presented["dispositivo"], "fonte": presented["fonte"]}

    def _official(
        self,
        loaded: LoadedNorm,
        article: str,
        paragraph: str | None,
        clause: str | None,
        subclause: str | None,
        law: str,
        problem: str,
    ) -> dict[str, Any] | None:
        key = ArticleLabel.parse(article)
        try:
            page = self._planalto.fetch(loaded.norm)
        except PlanaltoUnavailable:
            return None
        if not self._planalto.accepted(loaded.norm, page):
            return None
        document = DocumentParser().parse(page.body)
        try:
            excerpt = ProvisionExtractor(document).extract(article, paragraph, clause, subclause)
        except ProvisionNotFoundError:
            return None
        _log(law, key, problem, page.url)
        notice = messages.PLANALT_OBTAINED.format(date=_date(page))
        return {
            "norma": _bare_norm(loaded),
            "dispositivo": {"citacao": excerpt.citation, "texto": excerpt.text},
            "avisos": [notice, *excerpt.notices],
            "fonte": page.url,
            "origem": {"tipo": "planalto", "url": page.url, "obtido_em": page.fetched_on, "acervo_local": "incompleto"},
            "aviso_legal": messages.LEGAL_NOTICE,
        }

    def _refresh(
        self,
        service: Any,
        loaded: LoadedNorm,
        local: dict[str, Any],
        article: str,
        paragraph: str | None,
        clause: str | None,
        subclause: str | None,
        law: str,
    ) -> dict[str, Any] | None:
        key = ArticleLabel.parse(article)
        since = _current_wording_date(local.get("vigencia_online"))
        captured = _parse_date(str(loaded.metadata.get(SourceField.DOWNLOADED_AT) or ""))
        if since is None or captured is None or since <= captured:
            return None
        try:
            page = self._planalto.fetch(loaded.norm)
        except PlanaltoUnavailable:
            return None
        if not self._planalto.accepted(loaded.norm, page):
            return None
        try:
            excerpt = ProvisionExtractor(DocumentParser().parse(page.body)).extract(
                article, paragraph, clause, subclause
            )
        except ProvisionNotFoundError:
            return None
        if excerpt.text.strip() == str(local["dispositivo"]["texto"]).strip():
            return None
        _log(law, key, "redacao_posterior", page.url)
        notice = messages.WORDING_IN_FORCE.format(
            date=_date(page), captured=captured.isoformat(), since=since.isoformat()
        )
        device = {**local["dispositivo"], "citacao": excerpt.citation, "texto": excerpt.text, "vigente": True}
        if excerpt.head:
            device["caput"] = excerpt.head
        return local | {
            "dispositivo": device,
            "redacao_acervo": {
                "texto": local["dispositivo"]["texto"],
                "vigente": False,
                "capturada_em": captured.isoformat(),
            },
            "avisos": [notice, *local.get("avisos", [])],
            "fonte_vigente": page.url,
        }

    def _preamble_rejects(self, loaded: LoadedNorm) -> bool:
        if not loaded.document.quoted_fallback:
            return False
        numbers = loaded.document.promulgated_numbers
        return len(numbers) != 1 or numbers[0] != digits(loaded.norm.number or "")

    def _insertions(self, loaded: LoadedNorm, key: str) -> list[Insertion]:
        return [item for item in loaded.document.insertions if item.target == key]

    @staticmethod
    def _local(service, loaded, article, paragraph, clause, subclause, block):
        if not loaded.document.blocks:
            return None
        try:
            excerpt = ProvisionExtractor(loaded.document).extract(article, paragraph, clause, subclause, block)
        except ProvisionNotFoundError as exc:
            return exc
        online = service._online.for_provision(loaded, excerpt)
        return service._presenter.provision(loaded, excerpt, online)


def _fragment(loaded: LoadedNorm) -> bool:
    document = loaded.document
    if document.quoted_fallback and len(document.promulgated_numbers) == 1:
        return document.promulgated_numbers[0] == digits(loaded.norm.number or "")
    version = str(loaded.metadata.get(SourceField.TEXT_VERSION) or "").casefold()
    incomplete = "republica" in version or "retifica" in version
    return incomplete and not _has_article(loaded, "1")


def _has_article(loaded: LoadedNorm, key: str) -> bool:
    return any(key in block.articles for block in loaded.document.blocks)


def _law_label(loaded: LoadedNorm) -> str:
    return f"{digits(loaded.norm.number or '')}/{loaded.norm.year or ''}"


def _bare_norm(loaded: LoadedNorm) -> dict[str, Any]:
    return {
        "titulo": loaded.metadata.get(SourceField.TITLE),
        "numero": loaded.norm.number,
        "ano": loaded.norm.year,
        "caminho": loaded.norm.path,
    }


def _parse_date(value: str) -> date | None:
    head = value.strip()[:10]
    try:
        return date.fromisoformat(head)
    except ValueError:
        return None


def _current_wording_date(online: dict[str, Any] | None) -> date | None:
    """The date of the single current wording, when the online record is unambiguous."""
    if not isinstance(online, dict) or not online.get("disponivel"):
        return None
    provision = online.get("dispositivo")
    versions = provision.get("versoes") if isinstance(provision, dict) else None
    if not isinstance(versions, list):
        return None
    current = [item for item in versions if isinstance(item, dict) and item.get("atual") is True and item.get("desde")]
    if len(current) != 1:
        return None
    return _parse_date(str(current[0]["desde"]))


def _date(page: OfficialPage) -> str:
    return page.fetched_on
