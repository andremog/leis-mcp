"""Builds the pt-BR response payloads returned by the MCP tools."""

import re
from typing import Any
from urllib.parse import quote

from .. import messages
from ..legal_text.labels import Label
from ..legal_text.notices import NormNotices
from ..legal_text.validity import NormValidity, TextWording
from ..models import Excerpt, LoadedNorm, Norm, Reference, SearchMatch, SourceField, Validity, ValidityState, Wording
from ..online import OnlineResult
from ..search.pattern import AccentInsensitivePattern


class NormPresenter:
    _METADATA_FIELDS = tuple(SourceField)
    _PATH_FIELDS = {
        "year": "ano", "number": "numero", "date": "data", "state": "uf", "city": "municipio", "agency": "orgao",
    }  # fmt: skip
    _LISTED_FIELDS = {"path": "caminho", **_PATH_FIELDS}
    _SEARCH_NORM_FIELDS = ("titulo", "caminho", "fonte", "ementa", "revogada")
    _SUMMARY_FIELDS = (SourceField.TITLE, SourceField.SUMMARY, SourceField.REVOKED)
    _GROUP_LABELS = {
        "sphere": "esfera", "kind": "tipo", "year": "ano", "state": "uf", "city": "municipio", "agency": "orgao",
    }  # fmt: skip
    _SNIPPET_WIDTH = 320

    def __init__(self, blob_base_url: str, commit: str):
        self._links_base = f"{blob_base_url}/{commit}"
        self._commit = commit

    def norm(self, norm: Norm, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        metadata = metadata or {}
        info = {str(field): metadata[field] for field in self._METADATA_FIELDS if metadata.get(field) not in (None, "")}
        info |= {"caminho": norm.path, "esfera": norm.sphere}
        if norm.path.startswith("parlamentar/"):
            info["acervo"] = "parlamentar"
        else:
            info["github"] = f"{self._links_base}/{quote(norm.path)}"
        if norm.kind:
            info["tipo_diretorio"] = norm.kind
        from_path = self._fields(norm, self._PATH_FIELDS)
        return info | {label: value for label, value in from_path.items() if label not in info}

    def norm_matches(self, norms: list[Norm], loaded: dict[Norm, LoadedNorm], reference: Reference | None) -> dict:
        response: dict[str, Any] = {
            "total": len(norms),
            "resultados": [self._norm_match(norm, loaded.get(norm)) for norm in norms],
            "aviso_legal": messages.LEGAL_NOTICE,
        }
        if reference and reference.alias:
            response["apelido"] = reference.alias
        return response

    @staticmethod
    def no_matches() -> dict[str, Any]:
        return {
            "total": 0,
            "resultados": [],
            "mensagem": messages.NO_MATCHING_NORMS,
            "aviso_legal": messages.LEGAL_NOTICE,
        }

    def provision(self, loaded: LoadedNorm, excerpt: Excerpt, online: OnlineResult) -> dict[str, Any]:
        provision: dict[str, Any] = {"citacao": excerpt.citation, "texto": excerpt.text}
        if excerpt.head:
            provision["caput"] = excerpt.head
        if loaded.document.has_several_blocks:
            provision["bloco"] = excerpt.block_title
        if excerpt.status:
            status = excerpt.status
            provision["situacao"] = {"revogado": status.revoked, "vetado": status.vetoed, "nota": status.note}
        validity, wording = self._validity(loaded, online.state), self._wording(loaded)
        return {
            "norma": self.norm(loaded.norm, loaded.metadata),
            "dispositivo": provision,
            "vigencia": self._validity_payload(validity),
            "redacao": self._wording_payload(wording),
            "vigencia_online": online.payload,
            "avisos": [*NormNotices.for_norm(validity, wording, loaded.metadata), *excerpt.notices, *online.notices],
            "fonte": loaded.metadata.get(SourceField.SOURCE_URL),
            "aviso_legal": messages.LEGAL_NOTICE,
        }

    def search_results(self, query: str, scope: str, engine: str, matches: list[SearchMatch]) -> dict[str, Any]:
        pattern = AccentInsensitivePattern(query).compiled
        groups: dict[str, dict[str, Any]] = {}
        for match in matches:
            group = groups.setdefault(
                match.loaded.norm.path, {"norma": self._search_norm(match.loaded), "ocorrencias": []}
            )
            group["ocorrencias"].append(self._occurrence(match, pattern))
        return {
            "consulta": query,
            "escopo": scope,
            "motor": engine,
            "total_ocorrencias": len(matches),
            "resultados": list(groups.values()),
            "aviso_legal": messages.LEGAL_NOTICE,
        }

    def counts(self, column: str, total: int, counts: dict[str, int]) -> dict[str, Any]:
        return {"nivel": self._GROUP_LABELS[column], "total": total, "contagens": counts}

    def page(self, norms: list[Norm], total: int, offset: int, loaded: dict[Norm, LoadedNorm]) -> dict[str, Any]:
        return {
            "nivel": "normas",
            "total": total,
            "offset": offset,
            "normas": [self._listed(norm, loaded.get(norm)) for norm in norms],
            "dica": messages.BROWSE_HINT,
        }

    def _norm_match(self, norm: Norm, loaded: LoadedNorm | None) -> dict[str, Any]:
        entry = self.norm(norm, loaded.metadata if loaded else None)
        if not loaded:
            return entry
        validity, wording = self._validity(loaded), self._wording(loaded)
        entry |= {"vigencia": self._validity_payload(validity), "redacao": self._wording_payload(wording)}
        notices = NormNotices.for_norm(validity, wording, loaded.metadata)
        return entry | {"avisos": notices} if notices else entry

    def _search_norm(self, loaded: LoadedNorm) -> dict[str, Any]:
        info = self.norm(loaded.norm, loaded.metadata)
        validity = self._validity(loaded)
        summary = {"estado": str(validity.state), "resumo": messages.VALIDITY_SUMMARIES[validity.state]}
        return {field: info[field] for field in self._SEARCH_NORM_FIELDS if field in info} | {"vigencia": summary}

    def _listed(self, norm: Norm, loaded: LoadedNorm | None) -> dict[str, Any]:
        item = self._fields(norm, self._LISTED_FIELDS)
        if not loaded:
            return item
        metadata = loaded.metadata
        item |= {str(field): metadata[field] for field in self._SUMMARY_FIELDS if field in metadata}
        return item | {"vigencia": str(self._validity(loaded).state)}

    @staticmethod
    def _validity(loaded: LoadedNorm, online: ValidityState | None = None) -> Validity:
        validity = NormValidity.read(loaded.metadata, loaded.document.lines)
        origin = "parlamentar" if loaded.norm.path.startswith("parlamentar/") else validity.origin
        if origin != validity.origin:
            validity = Validity(validity.state, validity.fields, validity.repealed_by, origin)
        if online is None:
            return validity
        state = NormValidity.combine({validity.state, online})
        return Validity(state, validity.fields, validity.repealed_by, f"{validity.origin}+online")

    def _wording(self, loaded: LoadedNorm) -> Wording:
        return TextWording.read(loaded.metadata, self._commit)

    @staticmethod
    def _validity_payload(validity: Validity) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "estado": str(validity.state),
            "resumo": messages.VALIDITY_SUMMARIES[validity.state],
            "origem": messages.VALIDITY_ORIGINS[validity.origin],
            "campos": validity.fields,
        }
        if validity.repealed_by:
            payload["revogada_por"] = validity.repealed_by
        return payload

    @staticmethod
    def _wording_payload(wording: Wording) -> dict[str, Any]:
        summaries = {True: messages.WORDING_CONSOLIDATED, False: messages.WORDING_ORIGINAL}
        return {
            "versao_texto": wording.version,
            "compilada": wording.consolidated,
            "capturada_em": wording.captured_at,
            "commit_acervo": wording.commit,
            "resumo": summaries.get(wording.consolidated, messages.WORDING_UNKNOWN),
        }

    def _occurrence(self, match: SearchMatch, pattern: re.Pattern[str]) -> dict[str, Any]:
        item: dict[str, Any] = {
            "linha": match.occurrence.line,
            "trecho": self._snippet(match.occurrence.text, pattern),
        }
        document = match.loaded.document
        if match.article:
            item["artigo"] = Label.ordinal(match.article.key)
            if document.has_several_blocks:
                item["bloco"] = document.block_title(match.article)
        return item

    @staticmethod
    def _fields(norm: Norm, labels: dict[str, str]) -> dict[str, Any]:
        return {label: value for attribute, label in labels.items() if (value := getattr(norm, attribute)) is not None}

    @classmethod
    def _snippet(cls, line: str, pattern: re.Pattern[str]) -> str:
        """The line, or a window of it around the first match when it is long."""
        line = line.strip()
        width = cls._SNIPPET_WIDTH
        if len(line) <= width:
            return line
        match = pattern.search(line)
        start = max(0, (match.start() if match else 0) - width // 3)
        end = start + width
        return ("…" if start else "") + line[start:end] + ("…" if end < len(line) else "")
