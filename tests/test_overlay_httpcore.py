"""Real httpcore response lifecycle with a fake network, plus one bounded official fetch."""

import re
import socket
import ssl
import time

import httpcore
import pytest

from leis_mcp.overlay.allowlist import FetchRejected
from leis_mcp.overlay.collector import Collector, Fetched

OFFICIAL = "https://www2.camara.leg.br/legin/fed/rescon/2002/resolucao-1-8-maio-2002-497942-normaatualizada-pl.html"


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
    return _response(200, body)


def _response(status: int, body: bytes, content_type: str = "text/html; charset=utf-8") -> list[bytes]:
    header = (
        f"HTTP/1.1 {status} X\r\nContent-Type: {content_type}\r\nContent-Length: {len(body)}\r\n"
        "Connection: close\r\n\r\n"
    ).encode()
    return [header, body]


_HTTP_STATUS = re.compile(r"HTTP (\d{3}) na fonte oficial\.")
_OUTAGE_STATUS = frozenset({502, 503, 504})
_CONTENT_REJECTION = (
    "Conteúdo comprimido",
    "Tipo de conteúdo",
    "Fonte maior que o limite",
    "Redirecionamento",
    "Falha ao resolver ",
    "Nenhum endereço",
    "Endereço não público",
    "URL recusada",
    "Host fora da lista",
    "Conexão para host não validado",
    "Socket Unix",
    "Porta recusada",
    "URL com IP literal",
)
_AFTER_CONNECT = (
    httpcore.ReadError,
    httpcore.WriteError,
    httpcore.ProtocolError,
    httpcore.ConnectionNotAvailable,
)


def _chain(exc: BaseException) -> list[BaseException]:
    """Parent before nested errors. The pool re-raises with ``from None``, so the original TLS error survives only as an argument of ``ConnectError``."""
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
    """Reason to skip when the official page could not be reached. None means the failure stands."""
    message = str(exc)
    status = _HTTP_STATUS.fullmatch(message)
    if status:
        code = int(status.group(1))
        if code in _OUTAGE_STATUS:
            return f"fonte oficial indisponível: HTTP {code} ({type(exc).__name__}: {exc})"
        return None
    if message.startswith(_CONTENT_REJECTION):
        return None
    if message == "Prazo da coleta esgotado." or message.startswith("Prazo esgotado ao resolver "):
        return f"fonte oficial indisponível: timeout ({type(exc).__name__}: {exc})"
    chain = _chain(exc)
    if any(isinstance(item, ssl.SSLCertVerificationError) for item in chain):
        return None
    for item in chain:
        if isinstance(item, (httpcore.TimeoutException, TimeoutError)):
            return f"fonte oficial indisponível: timeout ({type(item).__name__}: {item})"
        if isinstance(item, httpcore.ConnectError):
            tls = next((cause for cause in chain if isinstance(cause, ssl.SSLError)), None)
            if tls is not None:
                return f"fonte oficial indisponível: TLS ({type(tls).__name__}: {tls})"
            return f"fonte oficial indisponível: conexão ({type(item).__name__}: {item})"
        if isinstance(item, ssl.SSLError):
            return f"fonte oficial indisponível: TLS ({type(item).__name__}: {item})"
        if isinstance(item, ConnectionRefusedError):
            return f"fonte oficial indisponível: conexão ({type(item).__name__}: {item})"
        if isinstance(item, _AFTER_CONNECT):
            return None
    return None


def fetch_official_or_skip(fetch):
    """Fetch the official page. Skip only when `official_source_outage` recognizes an outage."""
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


class _RaisingBackend(httpcore.NetworkBackend):
    def __init__(self, error: BaseException, *, on_tls: bool = False):
        self._error = error
        self._on_tls = on_tls

    def connect_tcp(self, host: str, port: int, timeout: float | None = None, local_address=None, socket_options=None):
        if not self._on_tls:
            raise self._error
        error = self._error

        class _Stream(httpcore.NetworkStream):
            def read(self, max_bytes: int, timeout: float | None = None) -> bytes:
                return b""

            def write(self, buffer: bytes, timeout: float | None = None) -> None:
                return None

            def close(self) -> None:
                return None

            def start_tls(self, ssl_context, server_hostname: str | None = None, timeout: float | None = None):
                raise error

        return _Stream()


def _mapped_connect(cause: BaseException) -> httpcore.ConnectError:
    """httpcore ``map_exceptions`` does ``raise ConnectError(original) from original``."""
    return httpcore.ConnectError(cause)


def _fetch_scripted(chunks: list[bytes | BaseException], **kwargs):
    return Collector(resolve=_public_dns, network=ScriptedBackend([chunks], []), total_timeout=2, **kwargs).fetch(
        OFFICIAL
    )


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


@pytest.mark.integration  # live HTTPS to www2.camara.leg.br
def test_bounded_official_resolution_page():
    fetched = fetch_official_or_skip(lambda: Collector(max_bytes=400_000, total_timeout=40).fetch(OFFICIAL))
    _assert_bounded_official_page(fetched)


@pytest.mark.parametrize("status", [502, 503, 504])
def test_gateway_status_skips_even_when_the_body_contains_the_phrase(status):
    body = "norma atualizada quadragésimo segundo".encode()
    with pytest.raises(pytest.skip.Exception, match=rf"HTTP {status}") as skipped:
        fetch_official_or_skip(lambda: _fetch_scripted(_response(status, body), max_bytes=400_000))
    assert "FetchRejected" in str(skipped.value)


@pytest.mark.parametrize("status", [400, 401, 403, 404, 429, 500, 501])
def test_other_http_status_fails(status):
    with pytest.raises(FetchRejected, match=rf"HTTP {status} na fonte oficial\."):
        fetch_official_or_skip(lambda: _fetch_scripted(_response(status, b"no"), max_bytes=400_000))


def test_http_200_without_the_phrase_fails_the_page_assertions():
    fetched = fetch_official_or_skip(
        lambda: _fetch_scripted(_html(b"<html>upstream request timeout</html>"), max_bytes=400_000)
    )
    with pytest.raises(AssertionError):
        _assert_bounded_official_page(fetched)


def test_size_content_type_and_read_error_are_not_outages():
    with pytest.raises(FetchRejected, match="limite"):
        fetch_official_or_skip(lambda: _fetch_scripted(_html(b"x" * 80), max_bytes=40))
    with pytest.raises(FetchRejected, match="Tipo de conteúdo"):
        fetch_official_or_skip(
            lambda: _fetch_scripted(_response(200, b"x", content_type="application/octet-stream"), max_bytes=400_000)
        )
    with pytest.raises(FetchRejected, match="ReadError"):
        fetch_official_or_skip(lambda: _fetch_scripted([httpcore.ReadError("reset")]))
    with pytest.raises(FetchRejected, match="RemoteProtocolError"):
        fetch_official_or_skip(lambda: _fetch_scripted([httpcore.RemoteProtocolError("truncated")]))


def test_structure_and_size_assertions_still_fail():
    phrase = "quadragésimo segundo".encode()
    too_big = Fetched(url=OFFICIAL, data=phrase + b"x" * 400_000, content_type="text/html", fetched_at="t")
    other_host = Fetched(url="https://example.test/legin", data=phrase, content_type="text/html", fetched_at="t")
    with pytest.raises(AssertionError):
        _assert_bounded_official_page(too_big)
    with pytest.raises(AssertionError):
        _assert_bounded_official_page(other_host)


@pytest.mark.parametrize(
    "error",
    [
        httpcore.ConnectTimeout("handshake timed out"),
        httpcore.ConnectError("connection refused"),
        httpcore.PoolTimeout("no connection"),
        TimeoutError("timed out"),
        ConnectionRefusedError(111, "connection refused"),
    ],
)
def test_connect_and_timeout_errors_skip(error):
    backend = _RaisingBackend(error)
    with pytest.raises(pytest.skip.Exception, match="fonte oficial indisponível") as skipped:
        fetch_official_or_skip(lambda: Collector(resolve=_public_dns, network=backend, total_timeout=2).fetch(OFFICIAL))
    assert type(error).__name__ in str(skipped.value)


@pytest.mark.parametrize(
    "error",
    [
        httpcore.ReadTimeout("slow headers"),
        httpcore.WriteTimeout("slow request"),
    ],
)
def test_read_and_write_timeouts_skip(error):
    with pytest.raises(pytest.skip.Exception, match="timeout") as skipped:
        fetch_official_or_skip(lambda: _fetch_scripted([error]))
    assert type(error).__name__ in str(skipped.value)


def test_tls_handshake_failure_skips_and_certificate_failure_does_not():
    handshake = _mapped_connect(ssl.SSLError("unexpected eof"))
    with pytest.raises(pytest.skip.Exception, match="TLS") as skipped:
        fetch_official_or_skip(
            lambda: Collector(
                resolve=_public_dns, network=_RaisingBackend(handshake, on_tls=True), total_timeout=2
            ).fetch(OFFICIAL)
        )
    assert "SSLError" in str(skipped.value)

    certificate = _mapped_connect(ssl.SSLCertVerificationError("certificate verify failed"))
    with pytest.raises(FetchRejected, match="ConnectError"):
        fetch_official_or_skip(
            lambda: Collector(
                resolve=_public_dns, network=_RaisingBackend(certificate, on_tls=True), total_timeout=2
            ).fetch(OFFICIAL)
        )


def test_raw_tls_error_and_collector_deadline_skip():
    with pytest.raises(pytest.skip.Exception, match="TLS") as skipped:
        fetch_official_or_skip(
            lambda: Collector(
                resolve=_public_dns,
                network=_RaisingBackend(ssl.SSLEOFError("eof during handshake"), on_tls=True),
                total_timeout=2,
            ).fetch(OFFICIAL)
        )
    assert "SSLEOFError" in str(skipped.value)

    with pytest.raises(pytest.skip.Exception, match="timeout") as deadline:
        fetch_official_or_skip(lambda: Collector(resolve=_public_dns, total_timeout=0).fetch(OFFICIAL))
    assert "FetchRejected" in str(deadline.value)


def test_dns_failure_reset_while_reading_and_non_public_address_fail():
    def unresolvable(host, port, type):  # noqa: A002
        raise socket.gaierror("no name")

    with pytest.raises(FetchRejected, match="Falha ao resolver"):
        fetch_official_or_skip(lambda: Collector(resolve=unresolvable, total_timeout=2).fetch(OFFICIAL))

    with pytest.raises(ConnectionResetError):
        fetch_official_or_skip(lambda: _fetch_scripted([ConnectionResetError("reset while reading")]))

    def proxy(host, port, type):  # noqa: A002
        return [(2, 1, 6, "", ("198.18.0.1", port))]

    with pytest.raises(FetchRejected, match="não público"):
        fetch_official_or_skip(lambda: Collector(resolve=proxy, total_timeout=2).fetch(OFFICIAL))
