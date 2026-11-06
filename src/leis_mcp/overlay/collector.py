"""Fetches official HTML. The TCP connection uses the vetted public address, not a second DNS lookup.

Environment proxies are ignored (`trust_env` is off and no proxy is configured). A transparent
on-path redirect after the socket opens is outside this process.
"""

import logging
import socket
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import urlsplit, urlunsplit

import httpcore

from .allowlist import (
    DNS_TIMEOUT,
    MAX_BYTES,
    MAX_REDIRECTS,
    TOTAL_TIMEOUT,
    USER_AGENT,
    FetchRejected,
    validate_url,
    vetted_address,
)

log = logging.getLogger(__name__)

_REDIRECTS = {301, 302, 303, 307, 308}
_HTML_TYPES = ("text/html", "application/xhtml+xml", "text/plain")
Exchange = Callable[[str, str, float], tuple[int, dict[str, str], bytes]]


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


class _PinnedBackend(httpcore.NetworkBackend):
    """Connects to one already-checked address. The URL host is only used as SNI by httpcore."""

    def __init__(self, hostname: str, ip: str, inner: httpcore.NetworkBackend):
        self._hostname = hostname
        self._ip = ip
        self._inner = inner

    def connect_tcp(self, host: str, port: int, timeout=None, local_address=None, socket_options=None):
        if host not in {self._hostname, self._ip}:
            raise FetchRejected("Conexão para host não validado.")
        return self._inner.connect_tcp(
            self._ip, port, timeout=timeout, local_address=local_address, socket_options=socket_options
        )

    def connect_unix_socket(self, path: str, timeout=None, socket_options=None):
        raise FetchRejected("Socket Unix recusado.")

    def sleep(self, seconds: float) -> None:
        self._inner.sleep(seconds)


class Collector:
    def __init__(
        self,
        *,
        resolve: Callable | None = None,
        exchange: Exchange | None = None,
        network: httpcore.NetworkBackend | None = None,
        max_bytes: int = MAX_BYTES,
        max_redirects: int = MAX_REDIRECTS,
        total_timeout: float = TOTAL_TIMEOUT,
        dns_timeout: float = DNS_TIMEOUT,
    ):
        self._resolve = resolve
        self._exchange = exchange
        self._network = network
        self._max_bytes = max_bytes
        self._max_redirects = max_redirects
        self._total_timeout = total_timeout
        self._dns_timeout = dns_timeout

    def close(self) -> None:
        return None

    def fetch(self, url: str) -> Fetched:
        deadline = time.monotonic() + self._total_timeout
        current = url
        for _hop in range(self._max_redirects + 1):
            _check_deadline(deadline)
            hostname, ip = vetted_address(current, resolve=self._resolver())
            if self._exchange is not None:
                status, headers, body = self._exchange(ip, current, deadline)
            else:
                status, headers, body = self._pinned_http(hostname, ip, current, deadline)
            _check_deadline(deadline)
            if status in _REDIRECTS:
                location = headers.get("location") or headers.get("Location")
                if not location:
                    raise FetchRejected("Redirecionamento sem destino.")
                current = _join(current, location)
                log.info("Redirect accepted for host %s", urlsplit(current).hostname)
                continue
            if status != 200:
                raise FetchRejected(f"HTTP {status} na fonte oficial.")
            content_type = headers.get("content-type") or headers.get("Content-Type") or ""
            encoding = (headers.get("content-encoding") or headers.get("Content-Encoding") or "").lower()
            if encoding not in ("", "identity"):
                raise FetchRejected("Conteúdo comprimido recusado.")
            if not any(content_type.lower().startswith(kind) for kind in _HTML_TYPES):
                raise FetchRejected(f"Tipo de conteúdo recusado: {content_type or '(vazio)'}")
            if len(body) > self._max_bytes:
                raise FetchRejected(f"Fonte maior que o limite de {self._max_bytes} bytes.")
            log.info("Fetched host %s (%s bytes)", hostname, len(body))
            return Fetched(
                url=current,
                data=body,
                content_type=content_type,
                fetched_at=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            )
        raise FetchRejected("Redirecionamentos demais.")

    def _resolver(self) -> Callable:
        raw = self._resolve or socket.getaddrinfo

        def bounded(host: str, port: int, type):  # noqa: A002
            return _bounded(lambda: raw(host, port, type=type), self._dns_timeout, host)

        return bounded

    def _pinned_http(self, hostname: str, ip: str, url: str, deadline: float) -> tuple[int, dict[str, str], bytes]:
        parts = urlsplit(url)
        inner = self._network or httpcore.SyncBackend()
        backend = _PinnedBackend(hostname, ip, inner)
        pool = httpcore.ConnectionPool(network_backend=backend, retries=0)
        _check_deadline(deadline)
        request = httpcore.Request(
            b"GET",
            httpcore.URL(
                scheme=b"https",
                host=hostname.encode(),
                port=parts.port or 443,
                target=(parts.path or "/").encode() + (b"?" + parts.query.encode() if parts.query else b""),
            ),
            headers=[
                (b"host", hostname.encode()),
                (b"user-agent", USER_AGENT.encode()),
                (b"accept", b"text/html"),
                (b"accept-encoding", b"identity"),
            ],
        )
        try:
            response = pool.handle_request(request)
        except httpcore.ConnectError as exc:
            raise FetchRejected(f"Falha de rede ao buscar a fonte oficial ({type(exc).__name__}).") from exc
        except FetchRejected:
            raise
        finally:
            pool.close()
        status = response.status
        headers = {key.decode().lower(): value.decode(errors="replace") for key, value in response.headers}
        chunks: list[bytes] = []
        total = 0
        for chunk in response.stream:
            _check_deadline(deadline)
            total += len(chunk)
            if total > self._max_bytes:
                response.stream.close()
                raise FetchRejected(f"Fonte maior que o limite de {self._max_bytes} bytes.")
            chunks.append(chunk)
        return status, headers, b"".join(chunks)


def _bounded(call: Callable[[], object], timeout: float, host: str) -> object:
    outcome: dict[str, object] = {}

    def run() -> None:
        try:
            outcome["value"] = call()
        except BaseException as exc:  # noqa: BLE001 - surfaced to the caller thread
            outcome["error"] = exc

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    worker.join(timeout)
    if worker.is_alive():
        raise FetchRejected(f"Prazo esgotado ao resolver {host}.")
    if "error" in outcome:
        raise outcome["error"]  # type: ignore[misc]
    return outcome.get("value") or []


def _join(base: str, location: str) -> str:
    parts = urlsplit(location)
    if parts.scheme or parts.netloc:
        return location
    base_parts = urlsplit(base)
    path = location if location.startswith("/") else base_parts.path.rsplit("/", 1)[0] + "/" + location
    return urlunsplit((base_parts.scheme, base_parts.netloc, path, "", ""))


def _check_deadline(deadline: float) -> None:
    if time.monotonic() > deadline:
        raise FetchRejected("Prazo da coleta esgotado.")


# Re-export for tests that still import validate_url from the collector's dependency.
__all__ = ["Collector", "Fetched", "validate_url"]
