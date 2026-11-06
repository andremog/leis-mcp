"""Fetches official HTML. Does not follow a redirect until the next URL passes the allowlist."""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import urljoin

import httpx

from .allowlist import CONNECT_TIMEOUT, MAX_BYTES, MAX_REDIRECTS, READ_TIMEOUT, USER_AGENT, FetchRejected, validate_url

log = logging.getLogger(__name__)

_REDIRECTS = {
    httpx.codes.MOVED_PERMANENTLY,
    httpx.codes.FOUND,
    httpx.codes.SEE_OTHER,
    httpx.codes.TEMPORARY_REDIRECT,
    httpx.codes.PERMANENT_REDIRECT,
}
_HTML_TYPES = ("text/html", "application/xhtml+xml", "text/plain")


@dataclass(frozen=True, slots=True)
class Fetched:
    url: str
    data: bytes
    content_type: str
    fetched_at: str

    def text(self) -> str:
        """UTF-8 when the bytes are valid UTF-8, even if a legacy meta tag says otherwise."""
        try:
            return self.data.decode("utf-8")
        except UnicodeDecodeError:
            return self.data.decode("cp1252")


class Collector:
    def __init__(
        self,
        *,
        client: httpx.Client | None = None,
        resolve: Callable | None = None,
        max_bytes: int = MAX_BYTES,
        max_redirects: int = MAX_REDIRECTS,
    ):
        self._client = client or httpx.Client(
            timeout=httpx.Timeout(CONNECT_TIMEOUT, read=READ_TIMEOUT),
            follow_redirects=False,
            headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml;q=0.9"},
        )
        self._owns_client = client is None
        self._resolve = resolve
        self._max_bytes = max_bytes
        self._max_redirects = max_redirects

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def fetch(self, url: str) -> Fetched:
        current = self._check(url)
        for _hop in range(self._max_redirects + 1):
            try:
                response = self._client.send(self._client.build_request("GET", current), stream=True)
            except httpx.HTTPError as exc:
                raise FetchRejected(f"Falha de rede ao buscar a fonte oficial ({type(exc).__name__}).") from exc
            try:
                if response.status_code in _REDIRECTS:
                    location = response.headers.get("location")
                    if not location:
                        raise FetchRejected("Redirecionamento sem destino.")
                    current = self._check(urljoin(current, location))
                    log.info("Redirect accepted for host %s", _host(current))
                    continue
                if response.status_code != httpx.codes.OK:
                    raise FetchRejected(f"HTTP {response.status_code} na fonte oficial.")
                content_type = response.headers.get("content-type", "")
                if not any(content_type.lower().startswith(kind) for kind in _HTML_TYPES):
                    raise FetchRejected(f"Tipo de conteúdo recusado: {content_type or '(vazio)'}")
                data = self._read_capped(response)
            finally:
                response.close()
            log.info("Fetched host %s (%s bytes)", _host(current), len(data))
            return Fetched(
                url=current,
                data=data,
                content_type=content_type,
                fetched_at=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            )
        raise FetchRejected("Redirecionamentos demais.")

    def _check(self, url: str) -> str:
        kwargs = {"resolve": self._resolve} if self._resolve is not None else {}
        return validate_url(url, **kwargs)

    def _read_capped(self, response: httpx.Response) -> bytes:
        chunks: list[bytes] = []
        total = 0
        for chunk in response.iter_bytes():
            total += len(chunk)
            if total > self._max_bytes:
                raise FetchRejected(f"Fonte maior que o limite de {self._max_bytes} bytes.")
            chunks.append(chunk)
        return b"".join(chunks)


def _host(url: str) -> str:
    return url.split("/")[2] if "://" in url else url
