"""Warnings attached to responses: repealed or not-in-force norms, non-consolidated text, repealed or vetoed
provisions."""

import re
from typing import Any

from .. import messages
from ..models import ProvisionStatus, SourceField, Validity, ValidityState, Wording


class ProvisionNotices:
    _STRUCK = re.compile(r"~~|<\s*(?:del|s|strike)\s*>", re.IGNORECASE)
    # Source annotations: `*(Revogado pela Lei nº 13.874, de 20/9/2019)*`, `(VETADO)`, `(Revogados)`.
    _ANNOTATION = re.compile(
        r"\(\s*(?P<note>(?P<kind>Revogad[oa]s?|Vetad[oa]s?)\b[^()]*(?:\([^()]*\)[^()]*)*)\)", re.IGNORECASE
    )
    _WHOLE_NOTE = re.compile(r"(?:Revogad[oa]s?|Vetad[oa]s?)(?:\s+(?:pel[oa]s?|em)\b.*)?", re.IGNORECASE | re.DOTALL)
    # The provision label before an annotation that is its whole text: `**Art. 17.**`, `I -`, `§ 2º`,
    # `Arts. 89 a 99.`, `Parágrafo único.`
    _LABEL = re.compile(
        r"^[*_\s]*(?:Arts?\.?\s*[\d.ºo°A-Z-]+(?:\s*(?:a|e)\s*[\d.ºo°A-Z-]+)?|§+\s*[\d.ºo°A-Z-]+|"
        r"Par[áa]grafo\s+[úu]nico|[IVXLCDM]+(?:-[A-Z])?|[a-z]\))[*_\s.:\-–—]*",
        re.IGNORECASE,
    )

    @classmethod
    def status(cls, text: str) -> ProvisionStatus | None:
        """Status of a provision whose first line is its label plus a repeal or veto annotation, or ends with one
        (compiled texts may keep the old wording before the note)."""
        lines = [line for line in text.strip().splitlines() if line.strip()]
        rest = cls._LABEL.sub("", lines[0], count=1).strip(" *_.;") if lines else ""
        match = cls._ANNOTATION.fullmatch(rest) or cls._ending_annotation(rest)
        if not match and not rest and len(lines) > 1:  # label alone on its line, only a note on the next
            match = cls._ANNOTATION.fullmatch(lines[1].strip(" *_.;"))
        if not match:
            return None
        revoked = match["kind"].lower().startswith("revogad")
        return ProvisionStatus(revoked=revoked, vetoed=not revoked, note=match["note"].strip())

    @classmethod
    def _ending_annotation(cls, line: str) -> re.Match[str] | None:
        """A closing note about the whole provision; `(vetado o § 2º)` or `(revogados os incisos I a III)` are not."""
        matches = list(cls._ANNOTATION.finditer(line))
        last = matches[-1] if matches else None
        if not last or line[last.end() :].strip(" *_.;") or not cls._WHOLE_NOTE.fullmatch(last["note"].strip()):
            return None
        return last

    @classmethod
    def for_text(cls, text: str, status: ProvisionStatus | None) -> list[str]:
        notices = [messages.STRUCK_TEXT] if cls._STRUCK.search(text) else []
        if status:
            template = messages.PROVISION_REVOKED if status.revoked else messages.PROVISION_VETOED
            notices.append(template.format(note=status.note))
        nested = sum(1 for match in cls._ANNOTATION.finditer(text) if match["kind"].lower().startswith("revogad"))
        nested -= 1 if status and status.revoked else 0
        if nested > 0:
            notices.append(messages.PARTS_REVOKED.format(count=nested))
        return notices


class NormNotices:
    _CUTOFF_MARKERS = (
        "consolidado até",
        "não foi conferid",
        "nao foi conferid",
        "notas de alteração",
        "notas de alteracao",
    )

    @classmethod
    def for_norm(cls, validity: Validity, wording: Wording, metadata: dict[str, Any] | None = None) -> list[str]:
        notices = [notice for notice in (cls._validity(validity), cls._wording(wording)) if notice]
        if metadata:
            notices.extend(cls._overlay(metadata))
        return notices

    @classmethod
    def _overlay(cls, metadata: dict[str, Any]) -> list[str]:
        """Parliamentary corpus: unverified validity and a source consolidation cutoff stay visible."""
        notices = []
        if str(metadata.get(SourceField.VIGENCIA) or "") == "nao_verificada":
            notices.append(messages.VIGENCIA_UNKNOWN)
        version = str(metadata.get(SourceField.TEXT_VERSION) or "")
        if any(marker in version.casefold() for marker in cls._CUTOFF_MARKERS):
            notices.append(messages.CONSOLIDATION_CUTOFF.format(version=version))
        return notices

    @staticmethod
    def _validity(validity: Validity) -> str | None:
        fields = ", ".join(f"{key}: {value}" for key, value in validity.fields.items())
        match validity.state:
            case ValidityState.REVOKED if validity.repealed_by:
                return messages.NORM_REVOKED_BY.format(repealed_by=validity.repealed_by)
            case ValidityState.REVOKED:
                return messages.NORM_REVOKED
            case ValidityState.NOT_IN_FORCE:
                return messages.NORM_NOT_IN_FORCE.format(fields=fields)
            case ValidityState.CONFLICTING:
                return messages.VALIDITY_CONFLICT.format(fields=fields)
            case ValidityState.OTHER:
                return messages.SOURCE_STATUS.format(status=validity.fields.get("situacao"))
            case ValidityState.UNKNOWN:
                return messages.VALIDITY_UNKNOWN
        return None

    @staticmethod
    def _wording(wording: Wording) -> str | None:
        if not wording.version:
            return messages.UNKNOWN_VERSION
        if wording.consolidated:
            return None
        return messages.NOT_CONSOLIDATED.format(version=wording.version)
