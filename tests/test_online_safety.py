"""Conflict-area checks: online calls stay on official hosts, fail closed, and do not leak the API key."""

import logging
import socket
import time

import httpcore
import httpx
import pytest

from leis_mcp.config import Settings
from leis_mcp.content import FrontMatterParser
from leis_mcp.legal_text import DocumentParser, ProvisionExtractor
from leis_mcp.models import LoadedNorm, Norm
from leis_mcp.online import OnlineValidity
from leis_mcp.online import transport as net
from leis_mcp.server.http import ApiKeyMiddleware

KEY = "super-secret-leis-key-99"
URN = "urn:lex:br:federal:lei:2002-01-10;10406"
TEXT = f"""---
titulo: Lei
tipo: "Lei Ordinária"
numero: "10406"
urn: "{URN}"
---
**Art. 1º** Texto.
"""


def _loaded(text: str, number: str = "10406", year: int = 2002, sphere: str = "federal") -> LoadedNorm:
    source = FrontMatterParser().parse(text)
    item = Norm(path=f"{sphere}/x/{number}.md", sphere=sphere, number=number, year=year, blob="0" * 40)
    return LoadedNorm(item, source.metadata, DocumentParser().parse(source.body), source.body_offset)


def _read(validity: OnlineValidity, loaded: LoadedNorm):
    return validity.for_provision(loaded, ProvisionExtractor(loaded.document).extract("1"))


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler), headers={"Authorization": f"Bearer {KEY}"})


class _Inner:
    def __init__(self):
        self.host = None

    def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        self.host = host
        return "stream"

    def connect_unix_socket(self, path, timeout=None, socket_options=None):
        raise AssertionError(path)

    def sleep(self, seconds: float) -> None:
        return None


def test_default_client_is_bounded_and_ignores_proxy_env(tmp_path, monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("LEIS_MCP_API_KEY", KEY)
    validity = OnlineValidity(Settings(home=tmp_path, api_key=KEY))
    client = validity._client
    try:
        assert client.follow_redirects is False
        assert client.trust_env is False
        assert client.timeout.connect == net.CONNECT_TIMEOUT
        assert client.timeout.read == net.READ_TIMEOUT
        assert client.timeout.read <= net.TOTAL_TIMEOUT
        blob = " ".join(f"{name}={value}" for name, value in client.headers.items())
        assert KEY not in blob
        assert "authorization" not in {name.lower() for name in client.headers}
    finally:
        client.close()


def test_redirect_is_not_followed(tmp_path, caplog):
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.host)
        return httpx.Response(302, headers={"location": "http://169.254.169.254/latest/meta-data"})

    validity = OnlineValidity(Settings(home=tmp_path, api_key=KEY), _client(handler))
    started = time.monotonic()
    with caplog.at_level(logging.WARNING):
        payload = _read(validity, _loaded(TEXT)).payload
    assert time.monotonic() - started < 2
    assert payload["disponivel"] is False
    assert calls  # the official hosts were asked; the Location host was not
    assert set(calls) <= net.ALLOWED_HOSTS
    assert "169.254.169.254" not in "".join(calls)
    assert "RemoteProtocolError" in caplog.text
    assert KEY not in caplog.text


def test_hostile_urn_and_senate_number_never_leave_the_process(tmp_path):
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(f"{request.url.host}{request.url.path}")
        return httpx.Response(200, json={"legislationIdentifier": URN})

    validity = OnlineValidity(Settings(home=tmp_path), _client(handler))
    hostile = TEXT.replace(URN, "http://127.0.0.1/latest/meta-data")
    assert _read(validity, _loaded(hostile)).payload["disponivel"] is False
    assert calls == []

    sneaky = TEXT.replace('numero: "10406"', 'numero: "../169.254.169.254"')
    loaded = _loaded(sneaky, number="../169.254.169.254")
    payload = _read(validity, loaded).payload
    assert payload["disponivel"] is True  # normas.leg.br answered; the Senado number was refused
    assert calls == ["normas.leg.br/api/public/normas"]
    assert all(host.split("/", 1)[0] in net.ALLOWED_HOSTS for host in calls)


def test_timeout_and_oversized_body_do_not_hang_or_raise(tmp_path, monkeypatch):
    def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    validity = OnlineValidity(Settings(home=tmp_path), _client(timeout))
    started = time.monotonic()
    first = _read(validity, _loaded(TEXT)).payload
    assert time.monotonic() - started < 2
    assert first["disponivel"] is False
    assert "ReadTimeout" in first["motivo"]

    monkeypatch.setattr(net.time, "monotonic", lambda: 10_000.0)

    def body(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"{" + b"x" * (net.MAX_BODY + 8))

    huge = OnlineValidity(Settings(home=tmp_path / "huge"), _client(body))
    assert _read(huge, _loaded(TEXT)).payload["disponivel"] is False


def test_api_key_is_absent_from_logs_and_outbound_requests(tmp_path, caplog):
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        raise httpx.ConnectError(f"down {KEY}", request=request)

    client = httpx.Client(
        transport=httpx.MockTransport(handler),
        headers={"Authorization": f"Bearer {KEY}", "User-Agent": f"agent {KEY}", "Cookie": KEY},
    )
    validity = OnlineValidity(Settings(home=tmp_path, api_key=KEY), client)
    with caplog.at_level(logging.DEBUG):
        payload = _read(validity, _loaded(TEXT)).payload
    request = seen[0]
    outbound = str(request.url) + " " + " ".join(f"{name}:{value}" for name, value in request.headers.items())
    assert KEY not in outbound
    assert KEY not in caplog.text
    assert KEY not in payload["motivo"]
    assert "authorization" not in {name.lower() for name in request.headers}
    assert "cookie" not in {name.lower() for name in request.headers}


def test_backend_connects_only_to_a_public_address_of_an_official_host():
    def public(host, port, type):  # noqa: A002
        assert host == "normas.leg.br"
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", port))]

    inner = _Inner()
    stream = net.OfficialBackend(inner, public).connect_tcp("normas.leg.br", 443)
    assert stream == "stream"
    assert inner.host == "8.8.8.8"

    def link_local(host, port, type):  # noqa: A002
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.169.254", port))]

    blocked = _Inner()
    with pytest.raises(httpcore.ConnectError):
        net.OfficialBackend(blocked, link_local).connect_tcp("legis.senado.leg.br", 443)
    assert blocked.host is None
    with pytest.raises(httpcore.ConnectError):
        net.OfficialBackend(_Inner(), public).connect_tcp("169.254.169.254", 80)
    with pytest.raises(httpcore.ConnectError):
        net.OfficialBackend(_Inner(), public).connect_tcp("evil.example", 443)
    with pytest.raises(httpx.InvalidURL):
        net.refuse_unofficial("http://normas.leg.br/api")
    with pytest.raises(httpx.InvalidURL):
        net.refuse_unofficial("https://user:secret@normas.leg.br/api")
    with pytest.raises(httpx.InvalidURL):
        net.read_json(httpx.Client(), "https://127.0.0.1/latest/meta-data")


def test_dns_timeout_does_not_wait_for_the_resolver(monkeypatch):
    monkeypatch.setattr(net, "DNS_TIMEOUT", 0.2)

    def hang(host, port, type):  # noqa: A002
        time.sleep(5)
        return []

    started = time.monotonic()
    with pytest.raises(httpcore.ConnectError):
        net.OfficialBackend(_Inner(), hang).connect_tcp("normas.leg.br", 443)
    assert time.monotonic() - started < 2


def test_auth_middleware_still_rejects_mcp_without_bearer():
    """The HTTP key check is unchanged by the vigência client: /health stays open, /mcp does not."""
    from starlette.applications import Starlette
    from starlette.responses import PlainTextResponse
    from starlette.routing import Route
    from starlette.testclient import TestClient

    app = Starlette(routes=[Route("/mcp", lambda _request: PlainTextResponse("inner"), methods=["POST"])])
    client = TestClient(ApiKeyMiddleware(app, KEY))
    assert client.get("/health").status_code == 200
    assert client.post("/mcp").status_code == 401
    assert KEY not in client.post("/mcp").text
    assert client.post("/mcp", headers={"authorization": f"Bearer {KEY}"}).status_code == 200


def test_online_ttl_env_cannot_disable_the_bound(monkeypatch):
    monkeypatch.setenv("LEIS_MCP_ONLINE_TTL_DAYS", "100000")
    assert Settings.from_env().online_ttl_days == 7
    monkeypatch.setenv("LEIS_MCP_ONLINE_TTL_DAYS", "nan")
    assert Settings.from_env().online_ttl_days == 7
    monkeypatch.setenv("LEIS_MCP_OFFLINE", "yes")
    assert Settings.from_env().online is False
