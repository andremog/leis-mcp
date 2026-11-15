"""Online validity of federal norms and provisions, from normas.leg.br with the Senado as fallback.

Results are cached on disk per URN for `Settings.online_ttl_days`. Network failures never fail a tool
call: the response says the lookup is unavailable and the source data stays in place.
"""

import functools
import hashlib
import json
import logging
import os
import tempfile
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

import httpx

from .. import messages
from ..config import Settings
from ..models import Excerpt, LoadedNorm, SourceField, ValidityState
from .addresses import Address
from .normas_leg import NormasLegSource
from .senado import SenadoSource
from .transport import acceptable_senate, acceptable_urn, official_client, refuse_unofficial

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class OnlineResult:
    payload: dict[str, Any]
    notices: list[str] = field(default_factory=list)
    state: ValidityState | None = None  # set only when an online source marks the norm as not in force


class OnlineValidity:
    RETRY_AFTER = 60.0  # seconds without calling a source that just failed
    _RETRY_DELAY = 1.5  # the Senado answers 503 under load
    _PARTIAL_TTL = 3600.0  # seconds; a record missing a source that failed is refreshed sooner
    _INTERNAL_LIMIT = 20
    _REVOKED_KINDS = ("Repeal_Version", "Suppression_Version")
    _REVOKED_EVENTS = ("Revogação (", "Supressão (")

    def __init__(self, settings: Settings, client: httpx.Client | None = None):
        self._settings = settings
        self._client = client or official_client(self._user_agent())
        self._client.event_hooks["request"].append(self._on_request)
        self._normas_leg = NormasLegSource(self._client)
        self._senado = SenadoSource(self._client)
        self._cache_dir = settings.cache_dir / "online"
        self._failed_at: dict[str, float] = {}
        self._lock = threading.Lock()

    def for_provision(self, loaded: LoadedNorm, excerpt: Excerpt) -> OnlineResult:
        if not self._settings.online:
            return OnlineResult({"disponivel": False, "motivo": messages.ONLINE_DISABLED})
        urn = loaded.metadata.get(SourceField.URN)
        if loaded.norm.sphere != "federal" or not urn:
            return OnlineResult({"disponivel": False, "motivo": messages.ONLINE_FEDERAL_ONLY})
        urn_text = str(urn)
        secret = self._settings.api_key
        if not acceptable_urn(urn_text) or (secret is not None and secret in urn_text):
            return OnlineResult({"disponivel": False, "motivo": messages.ONLINE_UNAVAILABLE.format(details="URN")})
        record, errors = self._record(loaded, urn_text)
        if record is None:
            reason = messages.ONLINE_UNAVAILABLE.format(details="; ".join(errors))
            return OnlineResult({"disponivel": False, "motivo": reason})
        sources = [name for name in (NormasLegSource.NAME, SenadoSource.NAME) if record.get(name)]
        if not sources:
            return OnlineResult({"disponivel": False, "motivo": messages.ONLINE_NOT_FOUND})
        try:
            norm, norm_notices, state = self._norm(record)
            provision, provision_notices = self._provision(record, Address.requested(excerpt.article, excerpt.units))
        except Exception as exc:  # noqa: BLE001 - a damaged cache record must not fail the tool call
            log.warning("Online lookup record is unusable: %s", type(exc).__name__)
            return OnlineResult({"disponivel": False, "motivo": messages.ONLINE_UNAVAILABLE.format(details=exc)})
        payload = {
            "disponivel": True,
            "consultado_em": record["consultado_em"],
            "fontes": [messages.ONLINE_SOURCES[name] for name in sources],
            "norma": norm,
            "dispositivo": provision,
        }
        notices = [*norm_notices, *provision_notices]
        if record.get("_stale"):
            payload["desatualizado"] = True
            notices.append(messages.ONLINE_STALE.format(date=record["consultado_em"]))
        if errors:
            payload["falhas"] = errors
        return OnlineResult(payload, notices, state)

    def _norm(self, record: dict[str, Any]) -> tuple[dict[str, Any], list[str], ValidityState | None]:
        normas, senado = record.get(NormasLegSource.NAME) or {}, record.get(SenadoSource.NAME) or {}
        repealed_by = [law for law in normas.get("revogada_por", []) if law.get("nome") or law.get("link")]
        names = ", ".join(str(law.get("nome") or law.get("link")) for law in repealed_by)
        notices, state, summary = [], None, messages.ONLINE_SUMMARY_NO_MARK
        if normas.get("forca_legal") == "NotInForce":
            by = messages.ONLINE_REPEALED_BY.format(laws=names) if names else ""
            notices.append(messages.ONLINE_NOT_IN_FORCE.format(repealed_by=by))
            state, summary = ValidityState.NOT_IN_FORCE, messages.ONLINE_SUMMARY_NOT_IN_FORCE
        if senado.get("situacao") == "R":
            notices.append(messages.ONLINE_REVOKED_SENADO)
            state = state or ValidityState.REVOKED
            summary = summary if state == ValidityState.NOT_IN_FORCE else messages.ONLINE_SUMMARY_REVOKED
        norm = {"resumo": summary, "forca_legal": normas.get("forca_legal"), "situacao_senado": senado.get("situacao")}
        if repealed_by:
            norm["revogada_por"] = repealed_by
        return norm, notices, state

    def _provision(self, record: dict[str, Any], address: Address) -> tuple[dict[str, Any], list[str]]:
        provisions = (record.get(NormasLegSource.NAME) or {}).get("dispositivos")
        if provisions:
            return self._lexml_provision(provisions, address)
        events = (record.get(SenadoSource.NAME) or {}).get("eventos")
        if events is not None:
            return self._senado_provision(events, address)
        return {"situacao": messages.PROVISION_STATES["unknown"], "mensagem": messages.ONLINE_NO_PROVISION_DATA}, []

    def _lexml_provision(self, provisions: dict[str, Any], address: Address) -> tuple[dict[str, Any], list[str]]:
        changed: dict[str, list[dict[str, Any]]] = provisions["alterados"]
        unchanged = set(provisions["sem_alteracao"])
        candidates = [address, Address(address.article, "caput")] if address.is_article else [address]
        keys = ["|".join(candidate) for candidate in candidates]
        versions = next((changed[key] for key in keys if key in changed), None)
        repealed = next(
            (
                changed[key][-1]
                for key in ["|".join(item) for item in address.ancestors()]
                if self._repealed(changed.get(key))
            ),
            None,
        )
        source = messages.ONLINE_SOURCES[NormasLegSource.NAME]
        if repealed or self._repealed(versions):
            last = repealed or versions[-1]
            state, notice = "revoked", messages.ONLINE_PROVISION_REVOKED.format(source=source, by=self._by(last))
        elif versions:
            state = "changed"
            notice = messages.ONLINE_PROVISION_CHANGED.format(source=source, by=self._by(versions[-1]))
        else:
            state, notice = ("original" if any(key in unchanged for key in keys) else "unknown"), None
        provision: dict[str, Any] = {"fonte": source, "situacao": messages.PROVISION_STATES[state]}
        if versions:
            provision["versoes"] = [self._wording(item) for item in versions]
        internal = self._internal(changed, address)
        if internal:
            provision["alteracoes_internas"] = internal
        if state == "unknown":
            provision["mensagem"] = messages.ONLINE_NO_PROVISION_DATA
        return provision, [notice] if notice else []

    def _senado_provision(
        self, events: dict[str, list[dict[str, str]]], address: Address
    ) -> tuple[dict[str, Any], list[str]]:
        own = [address, Address(address.article, "caput")] if address.is_article else [address]
        found = [event for item in [*address.ancestors(), *own] for event in events.get("|".join(item), [])]
        source = messages.ONLINE_SOURCES[SenadoSource.NAME]
        notices = []
        if found and found[-1]["tipo"].startswith(self._REVOKED_EVENTS):
            state = "revoked"
            notices.append(messages.ONLINE_PROVISION_REVOKED.format(source=source, by=self._by(found[-1], "norma")))
        elif found:
            state = "changed"
            notices.append(messages.ONLINE_PROVISION_CHANGED.format(source=source, by=self._by(found[-1], "norma")))
        else:
            state = "no_events"
        if any("nconstitucional" in event["tipo"] for event in found):
            notices.append(messages.ONLINE_UNCONSTITUTIONAL.format(source=source))
        provision: dict[str, Any] = {"fonte": source, "situacao": messages.PROVISION_STATES[state]}
        if found:
            provision["eventos"] = found
        nested = sum(
            len(items)
            for key, items in events.items()
            if (inner := Address(*key.split("|"))) != address and address.contains(inner) and inner not in own
        )
        if nested:
            provision["eventos_em_subdispositivos"] = nested
        return provision, notices

    def _internal(self, changed: dict[str, list[dict[str, Any]]], address: Address) -> list[dict[str, Any]]:
        own = {address, Address(address.article, "caput")} if address.is_article else {address}
        internal = []
        for key, versions in changed.items():
            inner = Address(*key.split("|"))
            if inner in own or not address.contains(inner):
                continue
            last = versions[-1]
            state = "revoked" if self._repealed(versions) else "changed"
            internal.append(
                {
                    "dispositivo": inner.describe(),
                    "situacao": messages.PROVISION_STATES[state],
                    "por": last["por"],
                    "desde": last["desde"],
                }
            )
        return internal[: self._INTERNAL_LIMIT]

    @classmethod
    def _repealed(cls, versions: list[dict[str, Any]] | None) -> bool:
        return bool(versions) and versions[-1]["tipo"] in cls._REVOKED_KINDS

    @staticmethod
    def _wording(item: dict[str, Any]) -> dict[str, Any]:
        wording = {
            "desde": item["desde"],
            "ate": item["ate"],
            "tipo": messages.VERSION_KINDS.get(item["tipo"], item["tipo"]),
            "atual": item["atual"],
            "texto": item["texto"],
        }
        if item["por"]:
            wording["alterado_por"] = item["por"]
        return wording

    @staticmethod
    def _by(item: dict[str, Any], key: str = "por") -> str:
        return messages.ONLINE_BY.format(law=item[key]) if item.get(key) else ""

    def _record(self, loaded: LoadedNorm, urn: str) -> tuple[dict[str, Any] | None, list[str]]:
        """Cached or fetched summary. A record where one source failed is kept for `_PARTIAL_TTL` only;
        when every source fails, an expired record comes back marked `_stale`."""
        path = self._cache_dir / f"{hashlib.sha1(urn.encode(), usedforsecurity=False).hexdigest()}.json"
        cached = self._read(path)
        if cached:
            ttl = self._PARTIAL_TTL if cached.get("_partial") else self._settings.online_ttl_days * 86400
            if time.time() - cached["_fetched"] < ttl:
                return cached, []
        errors: list[str] = []
        record: dict[str, Any] = {}
        fetches = {NormasLegSource.NAME: lambda: self._normas_leg.fetch(urn)}
        norm, kind = loaded.norm, loaded.metadata.get(SourceField.KIND)
        if SenadoSource.supports(kind) and norm.number and acceptable_senate(norm.number, norm.year):
            fetches[SenadoSource.NAME] = functools.partial(self._senado.fetch, kind, norm.number, norm.year)
        for name, fetch in fetches.items():
            if name == SenadoSource.NAME and (record.get(NormasLegSource.NAME) or {}).get("dispositivos"):
                break  # the provision history is already there
            answered, summary = self._call(name, fetch, errors)
            if summary:
                record[name] = summary
            elif not answered and cached and cached.get(name):
                record[name] = cached[name]  # keep what the failed source said last time
                record |= {"_stale": True, "consultado_em": cached["consultado_em"]}
        if errors and not any(record.get(name) for name in fetches):
            return (cached | {"_stale": True}, errors) if cached else (None, errors)
        record.setdefault("consultado_em", datetime.now(UTC).isoformat(timespec="seconds"))
        record |= {"_fetched": time.time(), "_partial": bool(errors)}
        self._write(path, record)
        return record, errors

    def _call(
        self, name: str, fetch: Callable[[], dict[str, Any] | None], errors: list[str]
    ) -> tuple[bool, dict[str, Any] | None]:
        """(whether the source answered, its summary). Never raises: this is the network boundary."""
        with self._lock:
            if time.monotonic() - self._failed_at.get(name, -self.RETRY_AFTER) < self.RETRY_AFTER:
                errors.append(f"{messages.ONLINE_SOURCES[name]}: falhou há pouco")
                return False, None
        for attempt in range(2):
            try:
                return True, fetch()
            except httpx.HTTPStatusError as exc:
                server_error = exc.response.status_code >= 500
                if not server_error or attempt:
                    return False, self._failed(name, exc, errors, back_off=server_error)
                time.sleep(self._RETRY_DELAY)
            except httpx.TransportError as exc:
                return False, self._failed(name, exc, errors, back_off=True)
            except Exception as exc:  # noqa: BLE001 - unexpected payloads must not fail the tool call
                return False, self._failed(name, exc, errors, back_off=False)
        return False, None

    def _failed(self, name: str, exc: Exception, errors: list[str], back_off: bool) -> None:
        log.warning("Online lookup failed (%s): %s", name, type(exc).__name__)
        if back_off:  # outages and overload; a bad answer for one norm says nothing about the others
            with self._lock:
                self._failed_at[name] = time.monotonic()
        errors.append(f"{messages.ONLINE_SOURCES[name]}: {type(exc).__name__}")

    @staticmethod
    def _read(path: Path) -> dict[str, Any] | None:
        try:
            record = json.loads(path.read_text("utf-8"))
        except (OSError, ValueError):
            return None
        valid = (
            isinstance(record, dict)
            and isinstance(record.get("_fetched"), int | float)
            and isinstance(record.get("consultado_em"), str)
        )
        return record if valid else None

    @staticmethod
    def _write(path: Path, record: dict[str, Any]) -> None:
        temporary = None
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile("w", dir=path.parent, delete=False, encoding="utf-8") as handle:
                temporary = Path(handle.name)
                json.dump(record, handle, ensure_ascii=False)
            os.replace(temporary, path)
        except OSError as exc:
            log.warning("Could not cache the online lookup: %s", type(exc).__name__)
            if temporary:
                temporary.unlink(missing_ok=True)

    def _on_request(self, request: httpx.Request) -> None:
        """Drops credential headers and refuses any URL that is not one of the two official hosts."""
        refuse_unofficial(request.url)
        for name in ("authorization", "proxy-authorization", "cookie"):
            request.headers.pop(name, None)
        secret = self._settings.api_key
        if not secret:
            return
        if secret in str(request.url):
            raise httpx.InvalidURL("refused")
        for key, value in list(request.headers.items()):
            if secret in value:
                request.headers[key] = value.replace(secret, "redacted")

    @staticmethod
    def _user_agent() -> str:
        try:
            return f"leis-mcp/{version('leis-mcp')}"
        except PackageNotFoundError:
            return "leis-mcp"
