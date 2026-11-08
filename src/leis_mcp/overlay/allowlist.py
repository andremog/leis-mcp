"""HTTPS allowlist for the parliamentary collector.

Source pages are data. Hosts, redirects, timeouts and the byte cap are enforced here so a
collected URL cannot be turned into a request to a link-local or private address.
"""

import ipaddress
import socket
from urllib.parse import urlsplit

from ..errors import LeisMcpError

ALLOWED_HOSTS = frozenset(
    {
        "www2.camara.leg.br",
        "www.camara.leg.br",
        "camara.leg.br",
        "legis.senado.leg.br",
        "www.senado.leg.br",
        "www2.senado.leg.br",
        "www12.senado.leg.br",
        "www25.senado.leg.br",
        "www.congressonacional.leg.br",
        "congressonacional.leg.br",
        "normas.leg.br",
        "www.lexml.gov.br",
    }
)
MAX_BYTES = 8 * 1024 * 1024
MAX_REDIRECTS = 3
CONNECT_TIMEOUT = 10.0
READ_TIMEOUT = 30.0
TOTAL_TIMEOUT = 45.0
DNS_TIMEOUT = 5.0
USER_AGENT = "leis-mcp-overlay/0.1 (+https://github.com/andremog/leis-mcp)"


class FetchRejected(LeisMcpError):
    """The collector refused a URL before using its body. The message is safe to show an operator."""


def validate_url(url: str, *, resolve=socket.getaddrinfo) -> str:
    """Accepts only HTTPS on an allowlisted host whose addresses are public. Returns `url` unchanged."""
    _split_public(url, resolve=resolve)
    return url


def vetted_address(url: str, *, resolve=socket.getaddrinfo) -> tuple[str, str]:
    """Returns `(hostname, ip)` after every resolved address has been checked. The caller must connect to `ip`."""
    return _split_public(url, resolve=resolve)


def _split_public(url: str, *, resolve) -> tuple[str, str]:
    parts = urlsplit(url.strip())
    if parts.scheme != "https":
        raise FetchRejected(f"URL recusada (só https): {parts.scheme or '(vazia)'}")
    if parts.username or parts.password:
        raise FetchRejected("URL recusada: usuário ou senha no endereço.")
    host = (parts.hostname or "").lower().rstrip(".")
    if not host or host not in ALLOWED_HOSTS:
        raise FetchRejected(f"Host fora da lista permitida: {host or '(vazio)'}")
    if parts.port not in (None, 443):
        raise FetchRejected(f"Porta recusada: {parts.port}")
    try:
        if ipaddress.ip_address(host):
            raise FetchRejected("URL com IP literal é recusada; use o nome oficial do host.")
    except ValueError:
        pass
    try:
        infos = resolve(host, 443, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise FetchRejected(f"Falha ao resolver {host}.") from exc
    if not infos:
        raise FetchRejected(f"Nenhum endereço para {host}.")
    chosen: str | None = None
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        site_local = isinstance(ip, ipaddress.IPv6Address) and ip.is_site_local
        if not ip.is_global or ip.is_multicast or site_local:
            raise FetchRejected(f"Endereço não público para {host}: recusado.")
        chosen = chosen or str(ip)
    if chosen is None:
        raise FetchRejected(f"Nenhum endereço para {host}.")
    return host, chosen
