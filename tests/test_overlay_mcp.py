"""Wire-level MCP calls against the parliamentary overlay and a tiny upstream index."""

import asyncio
import json
import os
import sqlite3
import sys

from mcp import Client, StdioServerParameters

from leis_mcp.catalog.index import IndexBuilder
from leis_mcp.config import Settings

ART_7 = (
    "Art. 7º Aprovada na Câmara dos Deputados, a matéria será encaminhada ao Senado Federal, "
    "que, para apreciá-la, terá até o 42º (quadragésimo segundo) dia de vigência da Medida Provisória, "
    "contado da sua publicação no Diário Oficial da União."
)


def _home(tmp_path):
    settings = Settings(home=tmp_path)
    with sqlite3.connect(settings.db_path) as db:
        db.executescript(IndexBuilder._SCHEMA)
        db.executescript(IndexBuilder._INDEXES)
        db.execute(
            "INSERT INTO norms VALUES (?, ?, 'estaduais', 'resolucoes', 2002, '1', '2002-03-01', 'DF', NULL, NULL)",
            ("estaduais/DF/resolucoes/2002/resolucao-1-2002.md", bytes.fromhex("cc" * 20)),
        )
        db.executemany(
            "INSERT INTO meta VALUES (?, ?)",
            [("commit", "mcp-test"), ("built_at", "2026-01-01T00:00:00Z"), ("total", "1")],
        )
        db.commit()
    blob = settings.cache_dir / "objects" / "cc" / ("cc" * 19)
    blob.parent.mkdir(parents=True)
    blob.write_text("---\ntitulo: DF\n---\n\nArt. 1º Homônimo distrital.\n", encoding="utf-8")
    return settings


async def _call(home, name, arguments):
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "leis_mcp", "serve"],
        env={**os.environ, "LEIS_MCP_HOME": str(home)},
    )
    async with Client(params) as client:
        result = await client.call_tool(name, arguments)
    return result


def test_mcp_tools_return_cn_identity_and_reject_contradictory_organ(tmp_path):
    home = _home(tmp_path).home
    found = asyncio.run(_call(home, "buscar_norma", {"referencia": "Resolução nº 1/2002 do Congresso Nacional"}))
    assert not found.is_error
    top = found.structured_content["resultados"][0]
    assert top["identificador"] == "br:federal:congresso-nacional:resolucao:1:2002"
    assert top["orgao"] == "congresso-nacional"
    assert top["vigencia"] == "nao_verificada"
    assert "/DF/" not in top["caminho"]

    article = asyncio.run(_call(home, "ler_artigo", {"norma": "resolucao 1/2002-cn", "artigo": "7"}))
    assert article.structured_content["dispositivo"]["texto"].split("\n", 1)[0] == ART_7
    assert any("Vigência não verificada" in notice for notice in article.structured_content["avisos"])

    regimento = asyncio.run(_call(home, "ler_artigo", {"norma": "regimento comum", "artigo": "1"}))
    assert any("Corte de consolidação" in notice for notice in regimento.structured_content["avisos"])
    assert any("janeiro de 2023" in notice for notice in regimento.structured_content["avisos"])

    ricd = asyncio.run(_call(home, "ler_artigo", {"norma": "ricd", "artigo": "1"}))
    assert any("Resolução nº 33" in notice or "33, de 2026" in notice for notice in ricd.structured_content["avisos"])

    conflict = asyncio.run(_call(home, "buscar_norma", {"referencia": "ricd", "orgao": "senado-federal"}))
    assert conflict.structured_content["total"] == 0

    listed = asyncio.run(
        _call(
            home,
            "listar_normas",
            {"esfera": "federal", "tipo": "resolucoes", "ano": 2002, "orgao": "congresso-nacional"},
        )
    )
    assert listed.structured_content["total"] == 1
    assert listed.structured_content["normas"][0]["caminho"].endswith("resolucao-1-2002.md")

    text = asyncio.run(
        _call(home, "buscar_texto", {"consulta": "quadragésimo segundo", "norma": "resolucao 1/2002-cn"})
    )
    assert text.structured_content["total_ocorrencias"] >= 1
    assert text.structured_content["resultados"][0]["ocorrencias"][0]["artigo"] == "7º"

    aliases = asyncio.run(_aliases(home))
    assert "resolucao 1/2002-cn" in aliases
    assert "cdc" in aliases


async def _aliases(home):
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "leis_mcp", "serve"],
        env={**os.environ, "LEIS_MCP_HOME": str(home)},
    )
    async with Client(params) as client:
        resource = await client.read_resource("leis://apelidos")
    return json.loads(resource.contents[0].text)
