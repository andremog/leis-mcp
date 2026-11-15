"""Senado Federal open data: `situacao: R` for repealed federal norms and the events recorded on each
provision (amendment, repeal, veto, suspension, constitutionality rulings).

Used when normas.leg.br has no multi-version compilation of the norm (CLT, Penal Code, CDC...).
"""

from typing import Any

import httpx

from .addresses import SenadoAddress
from .transport import acceptable_senate, read_json


class SenadoSource:
    NAME = "senado"
    URL = "https://legis.senado.leg.br/dadosabertos/legislacao/{kind}/{number}/{year}.json"
    # Front matter `tipo` -> Senado code
    KINDS = {
        "Lei Ordinária": "LEI",
        "Lei": "LEI",
        "Lei Complementar": "LCP",
        "Decreto-Lei": "DEL",
        "Decreto": "DEC",
        "Medida Provisória": "MPV",
        "Emenda Constitucional": "EMC",
    }
    _IGNORED_EVENTS = ("Correlat", "Regulamenta")  # cross references, not changes

    def __init__(self, client: httpx.Client):
        self._client = client

    @classmethod
    def supports(cls, kind: str | None) -> bool:
        return isinstance(kind, str) and kind in cls.KINDS

    def fetch(self, kind: str, number: str, year: int) -> dict[str, Any] | None:
        if not self.supports(kind) or not acceptable_senate(number, year):
            raise httpx.InvalidURL("refused")
        url = self.URL.format(kind=self.KINDS[kind], number=number, year=year)
        data = read_json(self._client, url)
        if not isinstance(data, dict):
            return None
        listing = self._child(self._child(data, "DetalheDocumento"), "documentos")
        documents = self._as_list(listing.get("documento"))
        return self.summarize(documents[0]) if documents else None

    @classmethod
    def summarize(cls, document: dict[str, Any]) -> dict[str, Any]:
        events: dict[str, list[dict[str, str]]] = {}
        for provision in cls._as_list(cls._child(document, "disps").get("disp")):
            address = SenadoAddress.parse(str(provision.get("nomeDispositivo") or ""))
            if address is None:
                continue
            for ref in cls._as_list(cls._child(provision, "refs").get("ref")):
                kind = str(ref.get("comentario") or "")
                if kind and not any(ignored in kind for ignored in cls._IGNORED_EVENTS):
                    events.setdefault("|".join(address), []).append({"tipo": kind, "norma": ref.get("dispositivo")})
        return {"situacao": cls._child(document, "identificacao").get("situacao"), "eventos": events}

    @staticmethod
    def _child(value: Any, key: str) -> dict[str, Any]:
        child = value.get(key) if isinstance(value, dict) else None
        return child if isinstance(child, dict) else {}

    @staticmethod
    def _as_list(value: Any) -> list[dict[str, Any]]:
        items = value if isinstance(value, list) else [value] if value else []
        return [item for item in items if isinstance(item, dict)]
