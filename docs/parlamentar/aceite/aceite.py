"""Teste de aceite do plano do acervo parlamentar (docs/parlamentar/plano-e-status.md).

Roda no checkout publicado, com o índice real, por processos novos: CLI e servidor MCP via stdio.
Uso: python aceite.py <home> <dir-de-trabalho>
"""

import asyncio
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

HOME = Path(sys.argv[1])
WORK = Path(sys.argv[2])
ENV = {**os.environ, "LEIS_MCP_HOME": str(HOME)}
RESULTS: list[tuple[str, str, bool, str]] = []

NORMS = [  # (órgão, referência, número, ano, caminho)
    (
        "congresso-nacional",
        "Resolução 1/1970",
        "1",
        1970,
        "parlamentar/federal/congresso-nacional/resolucoes/1970/resolucao-1-1970.md",
    ),
    (
        "congresso-nacional",
        "Resolução 1/2002",
        "1",
        2002,
        "parlamentar/federal/congresso-nacional/resolucoes/2002/resolucao-1-2002.md",
    ),
    (
        "congresso-nacional",
        "Resolução 1/2006",
        "1",
        2006,
        "parlamentar/federal/congresso-nacional/resolucoes/2006/resolucao-1-2006.md",
    ),
    (
        "camara-dos-deputados",
        "Resolução 17/1989",
        "17",
        1989,
        "parlamentar/federal/camara-dos-deputados/resolucoes/1989/resolucao-17-1989.md",
    ),
    (
        "camara-dos-deputados",
        "Resolução 25/2001",
        "25",
        2001,
        "parlamentar/federal/camara-dos-deputados/resolucoes/2001/resolucao-25-2001.md",
    ),
    (
        "senado-federal",
        "Resolução 20/1993",
        "20",
        1993,
        "parlamentar/federal/senado-federal/resolucoes/1993/resolucao-20-1993.md",
    ),
]
ORGAN_NAME = {
    "congresso-nacional": "Congresso Nacional",
    "camara-dos-deputados": "Câmara dos Deputados",
    "senado-federal": "Senado Federal",
}


def check(group: str, name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((group, name, bool(ok), detail))


def cli(*args: str) -> dict:
    out = subprocess.run(["leis-mcp", *args], env=ENV, capture_output=True, text=True, timeout=900)
    if out.returncode != 0:
        raise RuntimeError(f"leis-mcp {' '.join(args)}: {out.stderr[-400:]}")
    return json.loads(out.stdout)


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def tree_hash(root: Path) -> str:
    digest = hashlib.sha256()
    for item in sorted(p for p in root.rglob("*") if p.is_file() and "staging" not in p.parts):
        digest.update(str(item.relative_to(root)).encode() + item.read_bytes())
    return digest.hexdigest()


async def with_server(body):
    params = StdioServerParameters(command="leis-mcp", args=["serve"], env=ENV)
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()

        async def call(tool: str, arguments: dict):
            result = await session.call_tool(tool, arguments)
            text = result.content[0].text
            return json.loads(text) if text.startswith("{") else text

        return await body(session, call)


def paths(found) -> list[str]:
    return [item["caminho"] for item in found.get("resultados", [])] if isinstance(found, dict) else []


async def tools(session, call):
    names = sorted(tool.name for tool in (await session.list_tools()).tools)
    check(
        "Objetivo",
        "as 4 ferramentas existem",
        names == ["buscar_norma", "buscar_texto", "ler_artigo", "listar_normas"],
        str(names),
    )

    # Objetivo: cada uma das seis, pedida com o órgão, é achada e lida, nunca substituída por homônimo.
    for organ, reference, _number, _year, path in NORMS:
        label = f"{reference} ({ORGAN_NAME[organ]})"
        found = await call("buscar_norma", {"referencia": reference, "orgao": organ, "limite": 10})
        got = paths(found)
        check("Objetivo", f"buscar_norma acha {label}", got[:1] == [path], str(got[:3]))
        check(
            "Objetivo",
            f"buscar_norma com órgão não traz homônimo ({label})",
            all(p.startswith(f"parlamentar/federal/{organ}/") for p in got),
            str(got),
        )
        read = await call(
            "ler_artigo",
            {
                "norma": f"{reference} do {ORGAN_NAME[organ]}"
                if organ != "camara-dos-deputados"
                else f"{reference} da Câmara dos Deputados",
                "artigo": "1",
            },
        )
        ok = isinstance(read, dict) and read["norma"]["caminho"] == path
        check(
            "Objetivo",
            f"ler_artigo lê {label} pelo nome do órgão",
            ok,
            read if not ok else read["norma"]["identificador"],
        )

    # Sem o órgão, o homônimo pode aparecer; com o órgão, nunca.
    plain = paths(await call("buscar_norma", {"referencia": "Resolução 1/2002", "limite": 20}))
    check(
        "Objetivo",
        "sem órgão, a lista pode trazer o homônimo do DF",
        any(p.startswith("estaduais/DF/") for p in plain) or plain,
        f"{len(plain)} resultados; DF presente: {any(p.startswith('estaduais/DF/') for p in plain)}",
    )
    check("Objetivo", "sem órgão, a do Congresso também aparece", NORMS[1][4] in plain, str(plain[:5]))
    text_cn = await call("buscar_texto", {"consulta": "Medida Provisória", "orgao": "congresso-nacional", "limite": 50})
    hits = [r["norma"]["caminho"] for r in text_cn.get("resultados", [])] if isinstance(text_cn, dict) else []
    check(
        "Objetivo",
        "buscar_texto com órgão CN só devolve normas do CN",
        hits and all(h.startswith("parlamentar/federal/congresso-nacional/") for h in hits),
        f"{len(hits)} ocorrências em {sorted(set(hits))}",
    )
    listed = await call("listar_normas", {"esfera": "federal", "tipo": "resolucoes", "orgao": "congresso-nacional"})
    check(
        "Objetivo",
        "listar_normas por órgão CN: 1970, 2002, 2006",
        isinstance(listed, dict) and set(listed.get("contagens", {})) == {"1970", "2002", "2006"},
        str(listed),
    )

    # Aceite 1: filtros de órgão, esfera, tipo, número e ano nas quatro ferramentas.
    exact = paths(
        await call(
            "buscar_norma",
            {"numero": "1", "ano": 2002, "tipo": "resolucoes", "esfera": "federal", "orgao": "congresso-nacional"},
        )
    )
    check("Aceite 1 (filtros)", "buscar_norma número+ano+tipo+esfera+órgão", exact == [NORMS[1][4]], str(exact))
    wrong_year = await call("buscar_norma", {"numero": "1", "ano": 2003, "orgao": "congresso-nacional"})
    check(
        "Aceite 1 (filtros)",
        "ano sem norma do CN não devolve o DF nem outra",
        not paths(wrong_year),
        str(wrong_year)[:160],
    )
    df = paths(
        await call(
            "buscar_norma",
            {"numero": "1", "ano": 2002, "tipo": "resolucoes", "esfera": "estaduais", "uf": "DF", "limite": 5},
        )
    )
    check(
        "Aceite 1 (filtros)",
        "esfera estaduais/UF DF devolve só o DF",
        df and all(p.startswith("estaduais/DF/") for p in df),
        str(df),
    )
    senate = await call(
        "listar_normas", {"esfera": "federal", "tipo": "resolucoes", "orgao": "senado-federal", "ano": 1993}
    )
    senate_paths = (
        [n.get("caminho") for n in senate.get("normas", senate.get("resultados", []))]
        if isinstance(senate, dict)
        else []
    )
    check(
        "Aceite 1 (filtros)",
        "listar_normas órgão+ano lista a Res. 20/1993 do Senado",
        NORMS[5][4] in senate_paths,
        str(senate)[:200],
    )
    by_year = await call(
        "buscar_texto", {"consulta": "decoro", "orgao": "camara-dos-deputados", "ano": 2001, "limite": 20}
    )
    yhits = {r["norma"]["caminho"] for r in by_year.get("resultados", [])} if isinstance(by_year, dict) else set()
    check("Aceite 1 (filtros)", "buscar_texto órgão+ano (Câmara, 2001)", yhits == {NORMS[4][4]}, str(sorted(yhits)))
    kind = await call(
        "buscar_texto",
        {"consulta": "decoro", "orgao": "senado-federal", "tipo": "resolucoes", "esfera": "federal", "limite": 20},
    )
    khits = {r["norma"]["caminho"] for r in kind.get("resultados", [])} if isinstance(kind, dict) else set()
    check("Aceite 1 (filtros)", "buscar_texto órgão+tipo+esfera (Senado)", khits == {NORMS[5][4]}, str(sorted(khits)))

    # Aceite 2: art. 7º da Res. 1/2002-CN, caput e sete parágrafos.
    art7 = await call("ler_artigo", {"norma": "Resolução 1/2002 do Congresso Nacional", "artigo": "7"})
    body = art7["dispositivo"]["texto"]
    paragraphs = [f"§ {i}º" for i in range(1, 8)]
    check(
        "Aceite 2 (art. 7º)", "caput presente", body.startswith("Art. 7º Aprovada na Câmara dos Deputados"), body[:80]
    )
    check(
        "Aceite 2 (art. 7º)",
        "§§ 1º a 7º presentes e em ordem",
        all(p in body for p in paragraphs)
        and [body.index(p) for p in paragraphs] == sorted(body.index(p) for p in paragraphs),
        "",
    )
    import re

    opened = re.findall(r"(?m)^§ (\d+)º", body)  # paragraphs that open a line; "(CF, art. 62, § 8º)" is a citation
    check(
        "Aceite 2 (art. 7º)",
        "exatamente os §§ 1º a 7º abrem linha (nenhum § 8º)",
        opened == [str(i) for i in range(1, 8)],
        str(opened),
    )
    p7 = await call("ler_artigo", {"norma": "Resolução 1/2002 do Congresso Nacional", "artigo": "7", "paragrafo": "7"})
    check(
        "Aceite 2 (art. 7º)",
        "§ 7º isolado, com o caput como contexto",
        p7["dispositivo"]["texto"].startswith("§ 7º Aplicam-se")
        and "Art. 7º" in (p7["dispositivo"].get("caput") or ""),
        p7["dispositivo"]["texto"][:60],
    )

    # Aceite 3: 104-A do Regimento Comum e 4º-A onde a norma o tiver.
    a104 = await call("ler_artigo", {"norma": "Regimento Comum", "artigo": "104-A"})
    check(
        "Aceite 3 (artigos com letra)",
        "art. 104-A do Regimento Comum",
        a104["dispositivo"]["citacao"] == "art. 104-A" and a104["dispositivo"]["texto"].startswith("Art. 104-A"),
        a104["dispositivo"]["texto"][:90],
    )
    a104b = await call("ler_artigo", {"norma": "Regimento Comum", "artigo": "104"})
    check(
        "Aceite 3 (artigos com letra)",
        "o 104-A não vaza para o art. 104",
        "104-A" not in a104b["dispositivo"]["texto"].split("\n")[0],
        a104b["dispositivo"]["texto"][:60],
    )
    p4a = await call(
        "ler_artigo", {"norma": "Resolução 1/2006 do Congresso Nacional", "artigo": "47", "paragrafo": "4-A"}
    )
    check(
        "Aceite 3 (artigos com letra)",
        "§ 4º-A do art. 47 da Res. 1/2006-CN",
        isinstance(p4a, dict) and p4a["dispositivo"]["texto"].startswith("§ 4º-A"),
        (p4a["dispositivo"]["texto"][:90] if isinstance(p4a, dict) else str(p4a)[:160]),
    )
    a13a = await call("ler_artigo", {"norma": "Resolução 17/1989 da Câmara dos Deputados", "artigo": "13-A"})
    check(
        "Aceite 3 (artigos com letra)",
        "art. 13-A do RICD",
        isinstance(a13a, dict) and a13a["dispositivo"]["texto"].startswith("Art. 13-A"),
        (a13a["dispositivo"]["texto"][:90] if isinstance(a13a, dict) else str(a13a)[:160]),
    )

    # Aceite 4: avisos.
    for organ, reference, *_ in NORMS:
        name = (
            f"{reference} da Câmara dos Deputados"
            if organ == "camara-dos-deputados"
            else f"{reference} do {ORGAN_NAME[organ]}"
        )
        notices = " ".join((await call("ler_artigo", {"norma": name, "artigo": "1"}))["avisos"])
        check(
            "Aceite 4 (avisos)",
            f"vigência desconhecida: {reference} ({ORGAN_NAME[organ]})",
            "Vigência não verificada" in notices,
            notices[:120],
        )
    rc = await call("ler_artigo", {"norma": "Regimento Comum", "artigo": "1"})
    blob = json.dumps(rc, ensure_ascii=False)
    check(
        "Aceite 4 (avisos)", "corte de consolidação de janeiro de 2023 (Regimento Comum)", "janeiro de 2023" in blob, ""
    )
    ricd = await call("ler_artigo", {"norma": "Resolução 17/1989 da Câmara dos Deputados", "artigo": "1"})
    blob = json.dumps(ricd, ensure_ascii=False)
    check("Aceite 4 (avisos)", "notas do RICD até a Res. 33/2026", "33" in blob and "2026" in blob, "")
    senate1 = await call("ler_artigo", {"norma": "Resolução 20/1993 do Senado Federal", "artigo": "1"})
    blob = json.dumps(senate1, ensure_ascii=False).lower()
    check(
        "Aceite 4 (avisos)",
        "Senado marcado como histórico, não compilado vigente",
        "histórica" in blob or "historico" in blob or "histórico" in blob,
        senate1["norma"].get("versao_texto", "")[:120],
    )

    # Upstream continua consultável.
    cdc = await call("ler_artigo", {"norma": "CDC", "artigo": "6", "inciso": "III"})
    check(
        "Upstream",
        "CDC art. 6º, III do acervo upstream",
        cdc["norma"]["caminho"].startswith("federal/"),
        cdc["dispositivo"]["texto"][:60],
    )


def tiny_bundle(directory: Path) -> Path:
    sys.path.insert(0, str(Path.cwd() / "src"))
    from leis_mcp.legal_text.labels import __name__ as _  # noqa: F401  (import path sanity)
    from leis_mcp.overlay.normalizer import html_to_markdown
    from leis_mcp.overlay.store import built_norm, write_bundle

    item = {
        "canonical_id": "br:federal:congresso-nacional:resolucao:9:1999",
        "organ": "congresso-nacional", "sphere": "federal", "kind": "resolucoes", "type_label": "Resolução",
        "number": "9", "year": 1999, "date": "1999-01-01", "title": "Norma piloto", "summary": "Versão de teste.",
        "source_url": "https://www2.camara.leg.br/legin/teste.html", "text_nature": "consolidado_pela_fonte",
        "text_version": "compilada pela fonte oficial; vigência não verificada de forma independente",
        "aliases": ["norma piloto"], "expect": {"min_articles": 1},
    }  # fmt: skip
    html = "<p>Art. 1º Texto da segunda versão, só para o teste de rollback.</p>"
    norm = built_norm(
        item, html_to_markdown(html), raw=html.encode(), fetched_at="2026-01-01T00:00:00Z", final_url=item["source_url"]
    )
    write_bundle(directory, [norm], [])
    return directory


def lifecycle(publisher: str) -> None:
    overlay = HOME / "overlay"
    db = HOME / "index.sqlite"

    # Aceite 5: update do upstream não apaga o overlay.
    before_overlay, before_db = tree_hash(overlay), file_hash(db)
    env_pub = {**ENV, "LEIS_MCP_INDEX_URL": publisher}
    out = subprocess.run(["leis-mcp", "update"], env=env_pub, capture_output=True, text=True, timeout=900)
    updated = json.loads(out.stdout) if out.returncode == 0 else {}
    check(
        "Aceite 5 (update)",
        "update instala um upstream de commit novo",
        updated.get("commit") == "d" * 40,
        out.stderr[-200:] if out.returncode else str(updated),
    )
    check("Aceite 5 (update)", "índice upstream foi trocado", file_hash(db) != before_db, "")
    check("Aceite 5 (update)", "overlay byte a byte igual depois do update", tree_hash(overlay) == before_overlay, "")
    status = cli("status")
    check(
        "Aceite 5 (update)",
        "status: upstream novo + overlay p-985cdac4a67032e34cf0",
        status["commit"] == "d" * 40 and status.get("overlay_version") == "p-985cdac4a67032e34cf0",
        f"{status['commit'][:8]} / {status.get('overlay_version')}",
    )

    # Aceite 6: restart relê o disco; rollback volta a versão anterior sem mexer no upstream.
    upstream_hash = file_hash(db)
    second = tiny_bundle(WORK / "segunda-versao")
    activated = cli("overlay", "activate", "--from", str(second))
    check(
        "Aceite 6 (restart/rollback)",
        "segunda versão do overlay ativada",
        activated["versao"] != "p-985cdac4a67032e34cf0",
        str(activated),
    )
    read_new = subprocess.run(["leis-mcp", "read", "norma piloto", "1"], env=ENV, capture_output=True, text=True)
    check(
        "Aceite 6 (restart/rollback)",
        "processo novo lê a segunda versão do disco",
        "segunda versão" in read_new.stdout,
        read_new.stdout[-160:],
    )
    back = cli("overlay", "rollback")
    check(
        "Aceite 6 (restart/rollback)",
        "rollback volta a p-985cdac4a67032e34cf0",
        back["versao"] == "p-985cdac4a67032e34cf0",
        str(back),
    )
    check("Aceite 6 (restart/rollback)", "rollback não mexe no índice upstream", file_hash(db) == upstream_hash, "")
    art7 = subprocess.run(
        ["leis-mcp", "read", "Resolução 1/2002 do Congresso Nacional", "7", "-p", "7"],
        env=ENV,
        capture_output=True,
        text=True,
    )
    check(
        "Aceite 6 (restart/rollback)",
        "processo novo depois do rollback lê o art. 7º, § 7º",
        "§ 7º Aplicam-se" in art7.stdout,
        art7.stdout[-160:],
    )
    gone = subprocess.run(["leis-mcp", "read", "norma piloto", "1"], env=ENV, capture_output=True, text=True)
    check(
        "Aceite 6 (restart/rollback)",
        "a segunda versão sumiu depois do rollback",
        "segunda versão" not in gone.stdout,
        "",
    )

    # Aceite 7: lacunas do inventário continuam registradas como pendentes.
    sys.path.insert(0, str(Path.cwd() / "src"))
    from leis_mcp.overlay.store import package_bundle

    manifest = json.loads((package_bundle() / "manifest.json").read_text(encoding="utf-8"))
    titles = " | ".join(json.dumps(g, ensure_ascii=False) for g in manifest["gaps"])
    expected = ["93", "40", "43", "48", "Parlasul", "Atos da Mesa", "Vetos"]
    check(
        "Aceite 7 (lacunas)",
        f"{len(manifest['gaps'])} lacunas no manifesto do pacote",
        all(word in titles for word in expected),
        "",
    )
    plan = Path("docs/parlamentar/plano-e-status.md").read_text(encoding="utf-8")
    check(
        "Aceite 7 (lacunas)",
        "plano lista as mesmas lacunas como pendentes",
        "O que ainda não está neste pacote" in plan and "RISF" in plan and "Parlasul" in plan,
        "",
    )


def main() -> None:
    asyncio.run(with_server(tools))
    lifecycle(sys.argv[3])
    width = max(len(name) for _, name, _, _ in RESULTS)
    group = None
    for g, name, ok, detail in RESULTS:
        if g != group:
            print(f"\n## {g}")
            group = g
        print(f"{'PASSOU' if ok else 'FALHOU'}  {name.ljust(width)}  {'' if ok else detail}")
    failed = [r for r in RESULTS if not r[2]]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} verificações passaram")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
