"""Warnings attached to responses: repealed norms, non-consolidated text, repealed or vetoed provisions."""

import re
from collections.abc import Callable
from typing import Any

from .. import messages
from ..models import SourceField


class ProvisionNotices:
    _RULES = (
        (re.compile(r"~~|<\s*(?:del|s|strike)\s*>", re.IGNORECASE), messages.STRUCK_TEXT),
        (re.compile(r"\brevogad[oa]s?\b", re.IGNORECASE), messages.MENTIONS_REPEAL),
        (re.compile(r"\bvetad[oa]s?\b", re.IGNORECASE), messages.MENTIONS_VETO),
    )

    @classmethod
    def for_text(cls, text: str) -> list[str]:
        return [message for pattern, message in cls._RULES if pattern.search(text)]


class NormNotices:
    _INACTIVE_STATUS = re.compile(r"revog|n[ãa]o vigente|sem efeito", re.IGNORECASE)
    _CONSOLIDATED_MARKER = "compilad"

    @classmethod
    def for_metadata(cls, metadata: dict[str, Any]) -> list[str]:
        return [notice for check in cls._CHECKS if (notice := check(metadata))]

    @staticmethod
    def _revoked(metadata: dict[str, Any]) -> str | None:
        return messages.NORM_REVOKED if str(metadata.get(SourceField.REVOKED)).lower() == "true" else None

    @staticmethod
    def _status(metadata: dict[str, Any]) -> str | None:
        status = str(metadata.get(SourceField.STATUS) or "")
        return messages.SOURCE_STATUS.format(status=status) if NormNotices._INACTIVE_STATUS.search(status) else None

    @staticmethod
    def _version(metadata: dict[str, Any]) -> str | None:
        version = str(metadata.get(SourceField.TEXT_VERSION) or "")
        if not version:
            return messages.UNKNOWN_VERSION
        if NormNotices._CONSOLIDATED_MARKER in version.lower():
            return None
        return messages.NOT_CONSOLIDATED.format(version=version)

    @staticmethod
    def _vigencia(metadata: dict[str, Any]) -> str | None:
        vigencia = str(metadata.get(SourceField.VIGENCIA) or "")
        return messages.VIGENCIA_UNKNOWN if vigencia == "nao_verificada" else None

    @staticmethod
    def _cutoff(metadata: dict[str, Any]) -> str | None:
        version = str(metadata.get(SourceField.TEXT_VERSION) or "")
        folded = version.casefold()
        if any(
            marker in folded
            for marker in (
                "consolidado até",
                "não foi conferid",
                "nao foi conferid",
                "notas de alteração",
                "notas de alteracao",
            )
        ):
            return messages.CONSOLIDATION_CUTOFF.format(version=version)
        return None

    _CHECKS: tuple[Callable[[dict[str, Any]], str | None], ...] = (_revoked, _status, _version, _vigencia, _cutoff)
