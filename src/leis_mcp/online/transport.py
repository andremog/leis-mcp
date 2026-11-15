"""Outbound limits for the vigência lookup.

Requests go only to normas.leg.br and legis.senado.leg.br, over HTTPS, to a public address resolved
for that name. Redirects are not followed. The body and the clock are capped. The API key is never
attached, and a URN or number that is not a plain official identifier is not sent.
"""

import ipaddress
import json
import re
import socket
import threading
import time
from typing import Any
from urllib.parse import urlsplit

import httpcore
import httpx

ALLOWED_HOSTS = frozenset({"normas.leg.br", "legis.senado.leg.br"})
MAX_BODY = 4 * 1024 * 1024
CONNECT_TIMEOUT = 5.0
READ_TIMEOUT = 10.0
TOTAL_TIMEOUT = 20.0
DNS_TIMEOUT = 5.0

_URN = re.compile(r"^urn:lex:[A-Za-z0-9:._;-]{1,200}$")
_NUMBER = re.compile(r"^\d{1,7}$")
_CREDENTIAL_HEADERS = frozenset({"authorization", "proxy-authorization", "cookie"})


def acceptable_urn(urn: str) -> bool:
    return bool(_URN.fullmatch(urn))


def acceptable_senate(number: str, year: object) -> bool:
    return (
        bool(_NUMBER.fullmatch(number))
        and isinstance(year, int)
        and not isinstance(year, bool)
        and 1822 <= year <= 2100
    )


def refuse_unofficial(url: httpx.URL | str) -> None:
    """Raises when `url` is not HTTPS on an allowlisted host and port, with no user info."""
    parsed = url if isinstance(url, httpx.URL) else httpx.URL(url)
    host = (parsed.host or "").lower().rstrip(".")
    if (
        parsed.scheme != "https"
        or parsed.username
        or parsed.password
        or host not in ALLOWED_HOSTS
        or parsed.port not in (None, 443)
        or urlsplit(str(parsed)).hostname != host
    ):
        raise httpx.InvalidURL("unofficial url")


def read_json(client: httpx.Client, url: str, params: dict[str, str] | None = None) -> Any:
    """GET JSON from an official URL. `None` on 404. Redirects and oversized bodies are errors."""
    built = httpx.URL(url)
    if params:
        built = built.copy_merge_params(params)
    refuse_unofficial(built)
    deadline = time.monotonic() + TOTAL_TIMEOUT
    with client.stream("GET", built, follow_redirects=False) as response:
        if response.is_redirect:
            raise httpx.RemoteProtocolError("redirect")
        if response.status_code == httpx.codes.NOT_FOUND:
            return None
        response.raise_for_status()
        buffer = bytearray()
        for chunk in response.iter_bytes():
            if time.monotonic() > deadline:
                raise httpx.ReadTimeout("deadline")
            buffer.extend(chunk)
            if len(buffer) > MAX_BODY:
                raise httpx.RemoteProtocolError("response too large")
    return json.loads(bytes(buffer))


def official_client(user_agent: str) -> httpx.Client:
    """Client that does not follow redirects, ignores proxy env, and connects only to a vetted address."""
    ssl_context = httpx.create_ssl_context(trust_env=True)
    transport = httpx.HTTPTransport(verify=ssl_context, retries=0, trust_env=False)
    pool = transport._pool  # same hook the snapshot downloader uses to install its network backend
    inner = getattr(pool, "_network_backend", None)
    if not isinstance(inner, httpcore.NetworkBackend):
        raise RuntimeError("online transport has no network backend")
    pool._network_backend = OfficialBackend(inner)
    return httpx.Client(
        transport=transport,
        timeout=httpx.Timeout(READ_TIMEOUT, connect=CONNECT_TIMEOUT, write=CONNECT_TIMEOUT, pool=CONNECT_TIMEOUT),
        follow_redirects=False,
        trust_env=False,
        headers={"User-Agent": user_agent, "Accept": "application/json"},
    )


class OfficialBackend:
    """Resolves an allowlisted host and connects to that public address, not to a later lookup."""

    def __init__(self, inner: httpcore.NetworkBackend, resolve=socket.getaddrinfo):
        self._inner = inner
        self._resolve = resolve

    def connect_tcp(self, host: str, port: int, timeout=None, local_address=None, socket_options=None):
        hostname = host.decode("ascii", "replace") if isinstance(host, bytes) else str(host)
        hostname = hostname.lower().rstrip(".")
        if hostname not in ALLOWED_HOSTS or port != 443:
            raise httpcore.ConnectError("host refused")
        infos = _bounded_resolve(self._resolve, hostname, DNS_TIMEOUT)
        chosen: str | None = None
        if not infos:
            raise httpcore.ConnectError("dns")
        for info in infos:
            ip = ipaddress.ip_address(info[4][0])
            site_local = isinstance(ip, ipaddress.IPv6Address) and ip.is_site_local
            if not ip.is_global or ip.is_multicast or site_local:
                raise httpcore.ConnectError("non-public address")
            if chosen is None:
                chosen = str(ip)
        assert chosen is not None
        return self._inner.connect_tcp(
            chosen, port, timeout=timeout, local_address=local_address, socket_options=socket_options
        )

    def connect_unix_socket(self, path: str, timeout=None, socket_options=None):
        raise httpcore.ConnectError("unix socket refused")

    def sleep(self, seconds: float) -> None:
        self._inner.sleep(seconds)


def _bounded_resolve(resolve, host: str, timeout: float):
    outcome: dict[str, object] = {}

    def run() -> None:
        try:
            outcome["value"] = resolve(host, 443, type=socket.SOCK_STREAM)
        except BaseException as exc:  # noqa: BLE001 - surfaced to the caller thread
            outcome["error"] = exc

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    worker.join(timeout)
    if worker.is_alive():
        raise httpcore.ConnectError("dns timeout")
    if "error" in outcome:
        raise httpcore.ConnectError("dns") from outcome["error"]  # type: ignore[misc]
    return outcome.get("value") or []
