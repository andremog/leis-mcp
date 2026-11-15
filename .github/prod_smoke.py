"""Production tool smoke. Runs inside the Fly machine. Never prints the API key."""

import json
import os
import sys
import urllib.error
import urllib.request

URL = "http://127.0.0.1:8000/mcp"


def load_key() -> str | None:
    key = os.environ.get("LEIS_MCP_API_KEY")
    if key:
        return key
    try:
        raw = open("/proc/1/environ", "rb").read()
    except OSError:
        return None
    for item in raw.split(b"\0"):
        if item.startswith(b"LEIS_MCP_API_KEY="):
            return item.split(b"=", 1)[1].decode()
    return None


KEY = load_key()
if not KEY:
    print("NO_KEY")
    sys.exit(0)


def post(payload: dict, session: str | None) -> tuple[int, str | None, str]:
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "Authorization": f"Bearer {KEY}",
    }
    if session:
        headers["mcp-session-id"] = session
    request = urllib.request.Request(URL, data=json.dumps(payload).encode(), headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            body = response.read().decode("utf-8", "replace")
            return response.status, response.headers.get("mcp-session-id"), body
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        return exc.code, exc.headers.get("mcp-session-id"), body


def show(body: str) -> str:
    return body.replace(KEY, "[redacted]")


def call(session: str | None, name: str, arguments: dict, call_id: int) -> str:
    status, _, body = post(
        {"jsonrpc": "2.0", "id": call_id, "method": "tools/call", "params": {"name": name, "arguments": arguments}},
        session,
    )
    text = show(body)
    print(f"CALL {name} {json.dumps(arguments, ensure_ascii=False)} http={status} chars={len(text)}")
    print(text[:4000])
    print("---END---")
    return text


def failed(text: str) -> bool:
    return '"isError": true' in text or '"isError":true' in text or "Não encontrei" in text


essential_fail = False
status, session, body = post(
    {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "prod-smoke", "version": "0"},
        },
    },
    None,
)
print("INITIALIZE", status, "session", bool(session), show(body)[:300])
if status != 200:
    print("ESSENTIAL_FAIL initialize")
    sys.exit(1)
try:
    post({"jsonrpc": "2.0", "method": "notifications/initialized"}, session)
except Exception as exc:  # noqa: BLE001
    print("INIT_NOTE", type(exc).__name__)

checks = [
    ("listar_normas", {}, ("federal",), ()),
    ("buscar_norma", {"referencia": "Lei 8.245/91"}, ("8.245", "8245"), ()),
    ("buscar_texto", {"consulta": "locação", "norma": "Lei 8.245/91", "limite": 1}, ("total_ocorrencias",), ()),
    ("ler_artigo", {"norma": "Lei 8.245/91", "artigo": "1"}, ("Art.",), ("Não encontrei",)),
    (
        "ler_artigo",
        {"norma": "resolucao 1/2002-cn", "artigo": "7"},
        ("quadragésimo segundo", "§ 7"),
        ("Art. 8", "Não encontrei"),
    ),
]
for index, (name, arguments, required, forbidden) in enumerate(checks, start=2):
    text = call(session, name, arguments, index)
    ok = not failed(text) and all(piece in text for piece in required) and all(piece not in text for piece in forbidden)
    label = "PASS" if ok else "ESSENTIAL_FAIL"
    print(label, name, json.dumps(arguments, ensure_ascii=False))
    essential_fail = essential_fail or not ok

reports = [
    ("art37", {"norma": "Lei 14.133/2021", "artigo": "37", "paragrafo": "2"}),
    ("art12150", {"norma": "Lei 14.150/2021", "artigo": "12"}),
    ("art5", {"norma": "Lei 14.133/2021", "artigo": "5"}),
]
for index, (label, arguments) in enumerate(reports, start=20):
    text = call(session, "ler_artigo", arguments, index)
    print(
        "REPORT",
        label,
        "vetado" if "(VETADO)" in text else "sem_vetado",
        "ressalvados" if "Ressalvados" in text else "sem_ressalvados",
        "planalto" if "Planalto" in text else "sem_planalto",
        "14017" if "14.017" in text else "sem_14017",
        "nao_encontrei" if "Não encontrei" in text else "texto",
    )

try:
    import httpx

    direct = httpx.get(
        "https://www.planalto.gov.br/ccivil_03/_ato2019-2022/2021/lei/l14133.htm",
        follow_redirects=False,
        timeout=20,
    )
    print("PLANALTO_DIRECT", direct.status_code, (direct.headers.get("location") or "")[:180])
except Exception as exc:  # noqa: BLE001
    print("PLANALTO_DIRECT", type(exc).__name__, str(exc)[:180])

print("SMOKE_DONE")
sys.exit(1 if essential_fail else 0)
