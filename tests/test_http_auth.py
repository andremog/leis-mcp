"""Optional bearer key and open health check of the streamable-http transport."""

from unittest.mock import Mock

import pytest
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route, WebSocketRoute
from starlette.testclient import TestClient
from starlette.websockets import WebSocket, WebSocketDisconnect

from leis_mcp.config import Settings, default_host, default_port
from leis_mcp.server import LeisServer
from leis_mcp.server.http import ApiKeyMiddleware

KEY = "test-key-123"
INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}},
}
MCP_HEADERS = {"accept": "application/json, text/event-stream", "content-type": "application/json"}


async def _ok(_request):
    return PlainTextResponse("inner")


def client(api_key: str | None) -> TestClient:
    inner = Starlette(routes=[Route("/mcp", _ok, methods=["GET", "POST"])])
    return TestClient(ApiKeyMiddleware(inner, api_key))


def test_without_key_everything_passes():
    assert client(None).post("/mcp").text == "inner"


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"authorization": "Bearer wrong"},
        {"authorization": KEY},  # scheme missing
        {"authorization": f"Basic {KEY}"},
        {"authorization": f"Bearer {KEY}x"},
        {"x-api-key": KEY},
    ],
)
def test_with_key_rejects_missing_or_wrong_header(headers):
    response = client(KEY).post("/mcp", headers=headers)
    assert response.status_code == 401
    assert response.json() == {"error": "unauthorized"}
    assert response.headers["www-authenticate"] == "Bearer"


def test_with_key_query_param_is_not_accepted():
    assert client(KEY).post(f"/mcp?key={KEY}").status_code == 401


def test_with_key_accepts_correct_bearer():
    response = client(KEY).post("/mcp", headers={"authorization": f"Bearer {KEY}"})
    assert response.status_code == 200
    assert response.text == "inner"


@pytest.mark.parametrize("api_key", [None, KEY])
def test_health_is_open(api_key):
    response = client(api_key).get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_health_only_open_for_get():
    assert client(KEY).post("/health").status_code == 401


def test_real_mcp_app_requires_key():
    app = LeisServer(Mock()).http_app(api_key=KEY)
    with TestClient(app, base_url="http://127.0.0.1:8000") as http:
        assert http.post("/mcp", json=INITIALIZE, headers=MCP_HEADERS).status_code == 401
        ok = http.post("/mcp", json=INITIALIZE, headers={**MCP_HEADERS, "authorization": f"Bearer {KEY}"})
        assert ok.status_code == 200
        assert "leis-brasil" in ok.text
        assert http.get("/health").status_code == 200


def test_settings_reads_key_and_hides_it(monkeypatch):
    monkeypatch.setenv("LEIS_MCP_API_KEY", KEY)
    monkeypatch.setenv("LEIS_MCP_WARM_ON_START", "1")
    settings = Settings.from_env()
    assert settings.api_key == KEY
    assert settings.warm_on_start is True
    assert KEY not in repr(settings)


def test_settings_empty_key_means_no_auth(monkeypatch):
    monkeypatch.setenv("LEIS_MCP_API_KEY", "")
    assert Settings.from_env().api_key is None


def test_host_and_port_defaults(monkeypatch):
    for name in ("LEIS_MCP_HOST", "LEIS_MCP_PORT", "PORT"):
        monkeypatch.delenv(name, raising=False)
    assert (default_host(), default_port()) == ("127.0.0.1", 8000)
    monkeypatch.setenv("PORT", "4321")
    monkeypatch.setenv("LEIS_MCP_HOST", "0.0.0.0")
    assert (default_host(), default_port()) == ("0.0.0.0", 4321)
    monkeypatch.setenv("LEIS_MCP_PORT", "9000")
    assert default_port() == 9000


async def _ws(websocket: WebSocket):
    await websocket.accept()
    await websocket.send_text("inner")
    await websocket.close()


def ws_client(api_key: str | None) -> TestClient:
    inner = Starlette(routes=[WebSocketRoute("/mcp", _ws)])
    return TestClient(ApiKeyMiddleware(inner, api_key))


def test_with_key_websocket_without_header_is_closed():
    with pytest.raises(WebSocketDisconnect) as excinfo, ws_client(KEY).websocket_connect("/mcp"):
        pass
    assert excinfo.value.code == 1008


def test_with_key_websocket_with_header_passes():
    with ws_client(KEY).websocket_connect("/mcp", headers={"authorization": f"Bearer {KEY}"}) as ws:
        assert ws.receive_text() == "inner"
