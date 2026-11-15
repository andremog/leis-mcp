"""normas.leg.br: legal force of a federal norm, the norms that repealed it and, for multi-version
compilations (Civil Code, CPC, LGPD...), every wording of each provision with its dates.

The API never states "in force": it only marks `legislationLegalForce: NotInForce`.
"""

from typing import Any

import httpx

from .addresses import Address, LexmlAddress
from .transport import acceptable_urn, read_json


class NormasLegSource:
    NAME = "normas_leg"
    URL = "https://normas.leg.br/api/public/normas"
    _TEXT_LIMIT = 600  # characters kept per wording

    def __init__(self, client: httpx.Client):
        self._client = client

    def fetch(self, urn: str) -> dict[str, Any] | None:
        """Summary of the norm, or None when the API does not know the URN."""
        if not acceptable_urn(urn):
            raise httpx.InvalidURL("urn")
        data = read_json(self._client, self.URL, {"urn": urn, "tipo_documento": "maior-detalhe"})
        if not isinstance(data, dict) or "legislationIdentifier" not in data:
            return None
        return self.summarize(data)

    @classmethod
    def summarize(cls, data: dict[str, Any]) -> dict[str, Any]:
        provisions = cls._provisions(data["hasPart"]) if "hasPart" in data else None
        reverse = data.get("@reverse") if isinstance(data.get("@reverse"), dict) else {}
        force = data.get("legislationLegalForce")
        return {
            "forca_legal": force if isinstance(force, str) else None,
            "revogada_por": [cls._law(item) for item in cls._as_list(reverse.get("legislationRepeals"))],
            "versoes_texto": [
                item.get("version") for item in cls._as_list(data.get("encoding")) if item.get("version")
            ],
            "dispositivos": provisions,
        }

    @classmethod
    def _provisions(cls, parts: Any) -> dict[str, Any]:
        """Wordings of the provisions that changed, plus the addresses of those that did not."""
        versions: dict[Address, list[dict[str, Any]]] = {}
        for node in cls._nodes(parts):
            identifier = node["legislationIdentifier"]
            work, _, fragment = identifier.partition("!")
            if "@" not in work or not (address := LexmlAddress.parse(fragment)):
                continue
            versions.setdefault(address, []).append(cls._version(node))
        changed = {}
        unchanged = []
        for address, items in versions.items():
            items.sort(key=lambda item: str(item["desde"] or ""))
            if len(items) > 1 or any(not item["atual"] for item in items):
                changed["|".join(address)] = items
            else:
                unchanged.append("|".join(address))
        return {"alterados": changed, "sem_alteracao": unchanged}

    @classmethod
    def _version(cls, node: dict[str, Any]) -> dict[str, Any]:
        coverage = str(node.get("temporalCoverage") or "")
        consolidates = node.get("legislationConsolidates")
        text = str(node.get("text") or "")
        return {
            "desde": node.get("legislationDate"),
            "ate": coverage.partition("/")[2] or None,
            "tipo": node.get("legislationType"),
            "por": cls._law(consolidates)["nome"] if isinstance(consolidates, dict) else None,
            "atual": node.get("legislationLegalForce") != "NotInForce",
            "texto": text[: cls._TEXT_LIMIT] + ("…" if len(text) > cls._TEXT_LIMIT else ""),
        }

    @classmethod
    def _nodes(cls, value: Any):
        stack = [value]
        while stack:
            item = stack.pop()
            if isinstance(item, list):
                stack.extend(item)
            elif isinstance(item, dict):
                if isinstance(item.get("legislationIdentifier"), str):
                    yield item
                stack.extend(child for child in item.values() if isinstance(child, (dict, list)))

    @staticmethod
    def _law(item: dict[str, Any]) -> dict[str, Any]:
        return {"nome": item.get("name"), "link": item.get("@id")}

    @staticmethod
    def _as_list(value: Any) -> list[dict[str, Any]]:
        items = value if isinstance(value, list) else [value] if value else []
        return [item for item in items if isinstance(item, dict)]
