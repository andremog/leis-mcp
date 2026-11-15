import json
from pathlib import Path

import httpx
import pytest

from leis_mcp.config import Settings
from leis_mcp.content import FrontMatterParser
from leis_mcp.legal_text import DocumentParser, ProvisionExtractor
from leis_mcp.models import LoadedNorm, Norm, ValidityState
from leis_mcp.online import OnlineValidity
from leis_mcp.online.addresses import Address, LexmlAddress, Roman, SenadoAddress

FIXTURES = Path(__file__).parent / "fixtures" / "online"
CIVIL_CODE_URN = "urn:lex:br:federal:lei:2002-01-10;10406"
PROCUREMENT_URN = "urn:lex:br:federal:lei:1993-06-21;8666"
LABOUR_CODE_URN = "urn:lex:br:federal:decreto.lei:1943-05-01;5452"

CIVIL_CODE = f"""---
titulo: "Lei nº 10.406 de 10/01/2002"
tipo: "Lei Ordinária"
numero: "10406"
revogada: false
versao_texto: "compilada (texto vigente)"
urn: "{CIVIL_CODE_URN}"
---
# Lei nº 10.406 de 10/01/2002

**Art. 1º** Toda pessoa é capaz de direitos e deveres na ordem civil.

**Art. 3º** São absolutamente incapazes de exercer pessoalmente os atos da vida civil os menores de 16 (dezesseis) anos.

I - *(Revogado pela Lei nº 13.146, de 6/7/2015)*

**Art. 4º** São incapazes, relativamente a certos atos ou à maneira de os exercer:

Parágrafo único. A capacidade dos indígenas será regulada por legislação especial.
"""

PROCUREMENT = f"""---
titulo: "Lei nº 8.666 de 21/06/1993"
tipo: "Lei Ordinária"
numero: "8666"
revogada: null
versao_texto: "compilada (texto vigente)"
urn: "{PROCUREMENT_URN}"
---
# Lei nº 8.666 de 21/06/1993

**Art. 1º** Esta Lei estabelece normas gerais sobre licitações.
"""

LABOUR_CODE = f"""---
titulo: "Decreto-Lei nº 5.452 de 01/05/1943"
tipo: "Decreto-Lei"
numero: "5452"
revogada: false
versao_texto: "compilada (texto vigente)"
urn: "{LABOUR_CODE_URN}"
---
# Decreto-Lei nº 5.452 de 01/05/1943

**Art. 8º** As autoridades administrativas e a Justiça do Trabalho decidirão.

Parágrafo único. O direito comum será fonte subsidiária do direito do trabalho.

**Art. 17.** *(Revogado pela Lei nº 13.874, de 20/9/2019)*
"""


class FakeApis:
    """normas.leg.br and Senado answers, served from recorded (trimmed) responses."""

    def __init__(
        self, normas: dict[str, object], senado: dict[str, object] | None = None, failures: dict[str, int] | None = None
    ):
        self.normas = normas
        self.senado = senado or {}
        self.failures = failures or {}  # leading 503 answers, per host
        self.calls: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        host = request.url.host
        self.calls.append(host)
        if self.failures.get(host):
            self.failures[host] -= 1
            return httpx.Response(503)
        if request.url.host == "normas.leg.br":
            body = self.normas.get(request.url.params["urn"])
        else:
            body = self.senado.get(request.url.path)
        return httpx.Response(200, json=body) if body is not None else httpx.Response(404)


def fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text("utf-8"))


def norm(text: str, number: str, year: int, sphere: str = "federal") -> LoadedNorm:
    source = FrontMatterParser().parse(text)
    item = Norm(path=f"federal/x/{number}.md", sphere=sphere, number=number, year=year, blob="0" * 40)
    return LoadedNorm(item, source.metadata, DocumentParser().parse(source.body), source.body_offset)


def lookup(tmp_path, apis: FakeApis, **settings) -> OnlineValidity:
    validity = OnlineValidity(Settings(home=tmp_path, **settings), httpx.Client(transport=httpx.MockTransport(apis)))
    validity._RETRY_DELAY = 0
    return validity


def read(validity: OnlineValidity, loaded: LoadedNorm, article: str, **units):
    return validity.for_provision(loaded, ProvisionExtractor(loaded.document).extract(article, **units))


@pytest.mark.parametrize(
    ("fragment", "address"),
    [
        ("art3", Address("3")),
        ("art3_cpt", Address("3", "caput")),
        ("art3_cpt_inc1", Address("3", "caput", "1")),
        ("art4_par1u", Address("4", "unico")),
        ("art206_par1_inc2_ali1", Address("206", "1", "2", "a")),
        ("art44-1", Address("44-A")),
        ("art44_cpt_inc4,lei-10825-2003-12-22", Address("44", "caput", "4")),
        ("prt1_liv1_tit1", None),
    ],
)
def test_lexml_addresses(fragment, address):
    assert LexmlAddress.parse(fragment) == address


@pytest.mark.parametrize(
    ("name", "address"),
    [
        ("Art. 17 [Decreto-Lei nº 5.452 de 01/05/1943]", Address("17")),
        ("Art. 15, caput, Item 3 [Lei nº 8.666 de 21/06/1993]", Address("15", "caput", "3")),
        ("Art. 24, § 2, Inciso 1 [x]", Address("24", "2", "1")),
        ("Art. 8, Parágrafo Único [x]", Address("8", "unico")),
        ("Art. 482, caput, Alínea m [x]", Address("482", "caput", "", "m")),
        ("Anexo I [x]", None),
    ],
)
def test_senado_addresses(name, address):
    assert SenadoAddress.parse(name) == address


def test_requested_address_uses_lexml_numbers():
    assert Address.requested("3", ((2, "IV"),)) == Address("3", "caput", "4")
    assert Address.requested("3", ((1, "unico"), (2, "I-A"))) == Address("3", "unico", "1-1")
    assert Roman.value("XLIX") == "49"
    assert Address("3", "caput", "1").ancestors() == [Address("3"), Address("3", "caput")]
    assert Address("44", "caput", "1-1").describe() == "art. 44, I-A"
    assert SenadoAddress.parse("Art. 15, Item 3 [x]") == Address("15", "caput", "3")
    assert SenadoAddress.parse("Art. 15, caput, Inciso IV [x]") == Address("15", "caput", "4")


def test_repealed_clause_with_its_wordings(tmp_path):
    apis = FakeApis({CIVIL_CODE_URN: fixture("normas-leg-codigo-civil.json")})
    result = read(lookup(tmp_path, apis), norm(CIVIL_CODE, "10406", 2002), "3", clause="I")
    provision = result.payload["dispositivo"]
    assert provision["situacao"] == "revogado"
    assert [(item["desde"], item["ate"], item["atual"]) for item in provision["versoes"]] == [
        ("2002-01-10", "2015-07-05", False),
        ("2015-07-06", None, True),
    ]
    assert provision["versoes"][1]["alterado_por"] == "Lei nº 13.146 de 06/07/2015"
    assert any("registra este dispositivo como revogado pela Lei nº 13.146" in notice for notice in result.notices)
    assert result.state is None
    assert apis.calls == ["normas.leg.br"]  # multi-version compilation: no need for the Senado


def test_article_lists_changes_inside_it(tmp_path):
    apis = FakeApis({CIVIL_CODE_URN: fixture("normas-leg-codigo-civil.json")})
    provision = read(lookup(tmp_path, apis), norm(CIVIL_CODE, "10406", 2002), "3").payload["dispositivo"]
    assert provision["situacao"] == "alterado"
    assert provision["versoes"][-1]["texto"].endswith("os menores de 16 (dezesseis) anos.")
    assert sorted(item["dispositivo"] for item in provision["alteracoes_internas"]) == [
        "art. 3º, I",
        "art. 3º, II",
        "art. 3º, III",
    ]


def test_sole_paragraph_and_unchanged_article(tmp_path):
    apis = FakeApis({CIVIL_CODE_URN: fixture("normas-leg-codigo-civil.json")})
    validity, loaded = lookup(tmp_path, apis), norm(CIVIL_CODE, "10406", 2002)
    assert read(validity, loaded, "4", paragraph="único").payload["dispositivo"]["situacao"] == "alterado"
    unchanged = read(validity, loaded, "1").payload["dispositivo"]
    assert unchanged["situacao"] == "redação original, sem alterações registradas"


def test_norm_not_in_force(tmp_path):
    apis = FakeApis({PROCUREMENT_URN: fixture("normas-leg-lei-8666.json")})
    result = read(lookup(tmp_path, apis), norm(PROCUREMENT, "8666", 1993), "1")
    assert result.state == ValidityState.NOT_IN_FORCE
    assert result.payload["norma"]["forca_legal"] == "NotInForce"
    assert result.payload["norma"]["revogada_por"][0]["link"].endswith("lei:2021-04-01;14133")
    assert any("não está em vigor" in notice for notice in result.notices)
    assert apis.calls == ["normas.leg.br", "legis.senado.leg.br"]  # no provision history: the Senado is asked


def test_senado_events_when_there_is_no_history(tmp_path):
    apis = FakeApis(
        {LABOUR_CODE_URN: {"legislationIdentifier": LABOUR_CODE_URN}},
        {"/dadosabertos/legislacao/DEL/5452/1943.json": fixture("senado-clt.json")},
    )
    validity, loaded = lookup(tmp_path, apis), norm(LABOUR_CODE, "5452", 1943)
    repealed = read(validity, loaded, "17")
    assert repealed.payload["dispositivo"]["situacao"] == "revogado"
    assert repealed.payload["dispositivo"]["eventos"][-1]["norma"] == "Lei nº 13.874 de 20/09/2019"
    assert any("Senado" in notice and "revogado pela Lei nº 13.874" in notice for notice in repealed.notices)
    suppressed = read(validity, loaded, "8", paragraph="único").payload["dispositivo"]
    assert suppressed["situacao"] == "revogado"
    assert suppressed["eventos"][0]["tipo"].startswith("Supressão")


def test_senado_503_is_retried(tmp_path):
    apis = FakeApis(
        {LABOUR_CODE_URN: {"legislationIdentifier": LABOUR_CODE_URN}},
        {"/dadosabertos/legislacao/DEL/5452/1943.json": fixture("senado-clt.json")},
        failures={"legis.senado.leg.br": 1},
    )
    result = read(lookup(tmp_path, apis), norm(LABOUR_CODE, "5452", 1943), "17")
    assert result.payload["dispositivo"]["situacao"] == "revogado"
    assert apis.calls == ["normas.leg.br", "legis.senado.leg.br", "legis.senado.leg.br"]


@pytest.mark.parametrize(
    "body",
    [
        [],
        {"legislationIdentifier": "x", "@reverse": None, "hasPart": "?"},
        {"legislationIdentifier": "x", "encoding": 7},
    ],
)
def test_unexpected_payloads_never_raise(tmp_path, body):
    apis = FakeApis({CIVIL_CODE_URN: body})
    payload = read(lookup(tmp_path, apis), norm(CIVIL_CODE, "10406", 2002), "1").payload
    assert isinstance(payload["disponivel"], bool)


def test_unexpected_senado_payload_is_a_failure(tmp_path):
    apis = FakeApis(
        {LABOUR_CODE_URN: {"legislationIdentifier": LABOUR_CODE_URN}},
        {"/dadosabertos/legislacao/DEL/5452/1943.json": {"DetalheDocumento": ["?"]}},
    )
    payload = read(lookup(tmp_path, apis), norm(LABOUR_CODE, "5452", 1943), "17").payload
    assert payload["disponivel"] is True
    assert payload["dispositivo"]["situacao"] == "sem dados"


def test_stale_record_is_flagged_when_the_sources_fail(tmp_path):
    apis = FakeApis({CIVIL_CODE_URN: fixture("normas-leg-codigo-civil.json")})
    loaded = norm(CIVIL_CODE, "10406", 2002)
    read(lookup(tmp_path, apis), loaded, "3")
    apis.failures = {"normas.leg.br": 2}
    result = read(lookup(tmp_path, apis, online_ttl_days=0), loaded, "3")
    assert result.payload["desatualizado"] is True
    assert result.payload["dispositivo"]["situacao"] == "alterado"
    assert any("podem estar desatualizados" in notice for notice in result.notices)


def test_not_found_is_cached_and_damaged_cache_is_ignored(tmp_path):
    apis = FakeApis({})
    loaded = norm(CIVIL_CODE, "10406", 2002)
    read(lookup(tmp_path, apis), loaded, "1")
    read(lookup(tmp_path, apis), loaded, "1")
    assert apis.calls == ["normas.leg.br", "legis.senado.leg.br"]
    for record in (tmp_path / "cache" / "online").iterdir():
        record.write_text("[]")
    read(lookup(tmp_path, apis), loaded, "1")
    assert apis.calls == ["normas.leg.br", "legis.senado.leg.br"] * 2


def test_failure_is_reported_and_backs_off(tmp_path):
    def offline(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline", request=request)

    validity = OnlineValidity(Settings(home=tmp_path), httpx.Client(transport=httpx.MockTransport(offline)))
    loaded = norm(CIVIL_CODE, "10406", 2002)
    first = read(validity, loaded, "1").payload
    assert first["disponivel"] is False
    assert "indisponível" in first["motivo"] and "ConnectError" in first["motivo"]
    assert "falhou há pouco" in read(validity, loaded, "1").payload["motivo"]


def test_cache_is_reused_within_the_ttl(tmp_path):
    apis = FakeApis({CIVIL_CODE_URN: fixture("normas-leg-codigo-civil.json")})
    loaded = norm(CIVIL_CODE, "10406", 2002)
    read(lookup(tmp_path, apis), loaded, "3")
    read(lookup(tmp_path, apis), loaded, "3", clause="I")  # new instance, same cache directory
    assert apis.calls == ["normas.leg.br"]
    read(lookup(tmp_path, apis, online_ttl_days=0), loaded, "3")
    assert apis.calls == ["normas.leg.br", "normas.leg.br"]


def test_unknown_norm(tmp_path):
    apis = FakeApis({})
    payload = read(lookup(tmp_path, apis), norm(CIVIL_CODE, "10406", 2002), "1").payload
    assert payload == {
        "disponivel": False,
        "motivo": "A norma não consta nas fontes online consultadas (normas.leg.br, Senado Federal).",
    }


def test_offline_and_non_federal(tmp_path):
    apis = FakeApis({CIVIL_CODE_URN: fixture("normas-leg-codigo-civil.json")})
    offline = read(lookup(tmp_path, apis, online=False), norm(CIVIL_CODE, "10406", 2002), "1").payload
    state = read(lookup(tmp_path, apis), norm(CIVIL_CODE, "10406", 2002, sphere="estaduais"), "1").payload
    assert offline["disponivel"] is False and "LEIS_MCP_OFFLINE" in offline["motivo"]
    assert state["disponivel"] is False and "federais" in state["motivo"]
    assert apis.calls == []
