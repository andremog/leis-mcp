"""Real httpcore response lifecycle with a fake network, plus one bounded official fetch."""

import re
import ssl
import time

import httpcore
import pytest

from leis_mcp.overlay.allowlist import FetchRejected
from leis_mcp.overlay.collector import Collector, Fetched

OFFICIAL = "https://www2.camara.leg.br/legin/fed/rescon/2002/resolucao-1-8-maio-2002-497942-normaatualizada-pl.html"
_HTTP_STATUS = re.compile(r"HTTP (\d{3}) na fonte oficial\.")
# 404 is the HTML page missing on the same envoy that also answers 502/503/504.
# The collector in production still rejects every one of these.
_OUTAGE_STATUS = frozenset({404, 502, 503, 504})


class ScriptedStream(httpcore.NetworkStream):
    def __init__(self, chunks: list[bytes | BaseException], events: list):
        self._chunks = list(chunks)
        self._events = events
        self.closed = False

    def read(self, max_bytes: int, timeout: float | None = None) -> bytes:
        self._events.append(("read", timeout, self.closed))
        if self.closed:
            raise httpcore.ReadError("stream read after pool closed")
        if not self._chunks:
            return b""
        item = self._chunks.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item[:max_bytes]

    def write(self, buffer: bytes, timeout: float | None = None) -> None:
        self._events.append(("write", timeout))

    def close(self) -> None:
        self.closed = True
        self._events.append(("close",))

    def start_tls(self, ssl_context, server_hostname: str | None = None, timeout: float | None = None):
        self._events.append(("tls", timeout, server_hostname))
        return self


class ScriptedBackend(httpcore.NetworkBackend):
    def __init__(self, responses: list[list[bytes | BaseException]], events: list, *, block: float | None = None):
        self._responses = list(responses)
        self.events = events
        self.block = block
        self.streams: list[ScriptedStream] = []

    def connect_tcp(self, host: str, port: int, timeout: float | None = None, local_address=None, socket_options=None):
        self.events.append(("connect", host, port, timeout))
        if self.block:
            time.sleep(min(self.block, timeout or self.block))
            raise httpcore.ConnectTimeout("blocked connect honored the socket timeout")
        if timeout is None:
            raise AssertionError("connect without a deadline")
        chunks = self._responses.pop(0)
        stream = ScriptedStream(chunks, self.events)
        self.streams.append(stream)
        return stream


def _public_dns(host, port, type):  # noqa: A002
    return [(2, 1, 6, "", ("8.8.8.8", port))]


def _html(body: bytes) -> list[bytes]:
    header = (
        b"HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\nContent-Length: "
        + str(len(body)).encode()
        + b"\r\nConnection: close\r\n\r\n"
    )
    return [header, body]


def test_split_header_and_body_are_read_before_close():
    events: list = []
    backend = ScriptedBackend([_html(b"<p>hello</p>")], events)
    fetched = Collector(resolve=_public_dns, network=backend, total_timeout=2).fetch(
        "https://www2.camara.leg.br/legin/norma.html"
    )
    assert fetched.data == b"<p>hello</p>"
    assert any(event[0] == "read" and event[2] is False for event in events)
    assert events[-1] == ("close",)
    assert (events[0][0], events[0][1], events[0][2]) == ("connect", "8.8.8.8", 443)
    assert events[0][3] is not None and events[0][3] <= 2
    assert any(event[0] == "tls" and event[2] == "www2.camara.leg.br" and event[1] is not None for event in events)


def test_body_in_several_reads_oversized_stream_and_read_error():
    events: list = []
    body = b"abcdefghi"
    header = b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\nContent-Length: 9\r\n\r\n"
    backend = ScriptedBackend([[header, body[:3], body[3:6], body[6:]]], events)
    fetched = Collector(resolve=_public_dns, network=backend, total_timeout=2).fetch(
        "https://www2.camara.leg.br/legin/norma.html"
    )
    assert fetched.data == body
    reads = [event for event in events if event[0] == "read"]
    assert len(reads) >= 3
    assert all(event[1] is not None and event[2] is False for event in reads)

    huge = b"x" * 80
    huge_header = b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\nContent-Length: 80\r\n\r\n"
    huge_events: list = []
    huge_backend = ScriptedBackend([[huge_header, huge[:40], huge[40:]]], huge_events)
    with pytest.raises(FetchRejected, match="limite"):
        Collector(resolve=_public_dns, network=huge_backend, total_timeout=2, max_bytes=40).fetch(
            "https://www2.camara.leg.br/legin/norma.html"
        )
    assert huge_backend.streams[0].closed is True

    error_events: list = []
    error_backend = ScriptedBackend([[httpcore.ReadError("boom")]], error_events)
    with pytest.raises(FetchRejected):
        Collector(resolve=_public_dns, network=error_backend, total_timeout=2).fetch(
            "https://www2.camara.leg.br/legin/norma.html"
        )
    assert error_backend.streams[0].closed is True


def test_redirect_and_blocked_connect_use_the_remaining_deadline():
    events: list = []
    redirect = (
        b"HTTP/1.1 302 Found\r\nLocation: https://www2.camara.leg.br/legin/next.html\r\nContent-Length: 0\r\n\r\n"
    )
    backend = ScriptedBackend([[redirect], _html(b"<p>ok</p>")], events)
    fetched = Collector(resolve=_public_dns, network=backend, total_timeout=2).fetch(
        "https://www2.camara.leg.br/legin/start.html"
    )
    assert fetched.data == b"<p>ok</p>"
    assert fetched.url.endswith("/next.html")
    connects = [event for event in events if event[0] == "connect"]
    assert len(connects) == 2
    assert all(event[3] is not None and event[3] <= 2 for event in connects)

    started = time.monotonic()
    with pytest.raises(FetchRejected):
        Collector(resolve=_public_dns, network=ScriptedBackend([], [], block=5), total_timeout=0.05).fetch(
            "https://www2.camara.leg.br/legin/slow.html"
        )
    assert time.monotonic() - started < 0.5


def _chain(exc: BaseException) -> list[BaseException]:
    found: list[BaseException] = []
    seen: set[int] = set()
    pending: list[BaseException] = [exc]
    while pending:
        current = pending.pop(0)
        if id(current) in seen:
            continue
        seen.add(id(current))
        found.append(current)
        nested = current.__cause__
        if nested is None and not current.__suppress_context__:
            nested = current.__context__
        if isinstance(nested, BaseException):
            pending.append(nested)
        pending.extend(arg for arg in current.args if isinstance(arg, BaseException))
    return found


def official_source_outage(exc: BaseException) -> str | None:
    """Skip reason when the official HTML page is not being served. None means the failure stands."""
    if any(isinstance(item, ssl.SSLCertVerificationError) for item in _chain(exc)):
        return None
    message = str(exc)
    status = _HTTP_STATUS.fullmatch(message)
    if status:
        code = int(status.group(1))
        if code in _OUTAGE_STATUS:
            return f"fonte oficial indisponível: HTTP {code} ({type(exc).__name__}: {exc})"
        return None
    if message.startswith("Falha de rede ao buscar a fonte oficial"):
        return f"fonte oficial indisponível: {type(exc).__name__}: {exc}"
    if message == "Prazo da coleta esgotado." or message.startswith("Prazo esgotado ao resolver "):
        return f"fonte oficial indisponível: timeout ({type(exc).__name__}: {exc})"
    return None


def fetch_official_or_skip(fetch):
    try:
        return fetch()
    except Exception as exc:
        reason = official_source_outage(exc)
        if reason is None:
            raise
        pytest.skip(reason)


def _assert_bounded_official_page(fetched: Fetched) -> None:
    assert len(fetched.data) < 400_000
    assert "quadragésimo segundo" in fetched.text()
    assert fetched.url.startswith("https://www2.camara.leg.br/")


def _response(status: int, body: bytes) -> list[bytes]:
    header = (
        f"HTTP/1.1 {status} X\r\nContent-Type: text/html; charset=utf-8\r\nContent-Length: {len(body)}\r\n"
        "Connection: close\r\n\r\n"
    ).encode()
    return [header, body]


def _fetch_scripted(chunks: list[bytes | BaseException], **kwargs):
    return Collector(resolve=_public_dns, network=ScriptedBackend([chunks], []), total_timeout=2, **kwargs).fetch(
        OFFICIAL
    )


@pytest.mark.integration  # live HTTPS to www2.camara.leg.br
def test_bounded_official_resolution_page():
    fetched = fetch_official_or_skip(lambda: Collector(max_bytes=400_000, total_timeout=40).fetch(OFFICIAL))
    _assert_bounded_official_page(fetched)


@pytest.mark.parametrize("status", [404, 502, 503, 504])
def test_missing_or_gateway_page_skips(status):
    body = "norma atualizada quadragésimo segundo".encode()
    with pytest.raises(pytest.skip.Exception, match=rf"HTTP {status}") as skipped:
        fetch_official_or_skip(lambda: _fetch_scripted(_response(status, body), max_bytes=400_000))
    assert "FetchRejected" in str(skipped.value)


@pytest.mark.parametrize("status", [400, 403, 500])
def test_other_http_status_fails(status):
    with pytest.raises(FetchRejected, match=rf"HTTP {status} na fonte oficial\."):
        fetch_official_or_skip(lambda: _fetch_scripted(_response(status, b"no"), max_bytes=400_000))


def test_http_200_without_the_phrase_fails_the_page_assertions():
    fetched = fetch_official_or_skip(lambda: _fetch_scripted(_html(b"<html>sem a frase</html>"), max_bytes=400_000))
    with pytest.raises(AssertionError):
        _assert_bounded_official_page(fetched)


def test_size_limit_is_not_an_outage():
    with pytest.raises(FetchRejected, match="limite"):
        fetch_official_or_skip(lambda: _fetch_scripted(_html(b"x" * 80), max_bytes=40))


def test_network_failure_skips_and_certificate_failure_does_not():
    try:
        raise FetchRejected("Falha de rede ao buscar a fonte oficial (ConnectTimeout).") from httpcore.ConnectTimeout(
            "t"
        )
    except FetchRejected as exc:
        assert official_source_outage(exc) is not None
    try:
        raise FetchRejected(
            "Falha de rede ao buscar a fonte oficial (ConnectError)."
        ) from ssl.SSLCertVerificationError("certificate verify failed")
    except FetchRejected as exc:
        assert official_source_outage(exc) is None
