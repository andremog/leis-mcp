"""Sobe o servidor real por stdio e chama as ferramentas com o cliente MCP oficial."""

import asyncio
import json
import os
import sys

import pytest
from mcp import Client, StdioServerParameters

pytestmark = pytest.mark.integration


async def _sessao(settings):
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "leis_mcp", "serve"],
        env={**os.environ, "LEIS_MCP_HOME": str(settings.home)},
    )
    async with Client(params) as client:
        ferramentas = {t.name for t in (await client.list_tools()).tools}
        ler = await client.call_tool("ler_artigo", {"norma": "8245/1991", "artigo": "23", "inciso": "III"})
        busca = await client.call_tool("buscar_norma", {"referencia": "CLT"})
        texto = await client.call_tool(
            "buscar_texto", {"consulta": "probidade e boa-fé", "norma": "código civil", "limite": 3}
        )
        lista = await client.call_tool(
            "listar_normas", {"esfera": "federal", "tipo": "leis-complementares", "ano": 2006}
        )
        erro = await client.call_tool("ler_artigo", {"norma": "8245/1991", "artigo": "999"})
        apelidos = await client.read_resource("leis://apelidos")
    return ferramentas, ler, busca, texto, lista, erro, apelidos


def test_servidor_stdio_ponta_a_ponta(settings):
    ferramentas, ler, busca, texto, lista, erro, apelidos = asyncio.run(_sessao(settings))
    assert {"buscar_norma", "ler_artigo", "buscar_texto", "listar_normas"} <= ferramentas

    assert not ler.is_error
    dados = ler.structured_content
    assert dados["dispositivo"]["texto"] == (
        "III - restituir o imóvel, finda a locação, no estado em que o recebeu, "
        "salvo as deteriorações decorrentes do seu uso normal;"
    )
    assert dados["fonte"] and dados["aviso_legal"]

    assert busca.structured_content["resultados"][0]["caminho"] == "federal/decretos-lei/1943/del-5452-1943-05-01.md"
    assert any(o.get("artigo") == "422" for g in texto.structured_content["resultados"] for o in g["ocorrencias"])
    assert lista.structured_content["nivel"] == "normas"

    assert erro.is_error
    assert "Art. 999 não encontrado" in erro.content[0].text

    assert "cdc" in json.loads(apelidos.contents[0].text)
