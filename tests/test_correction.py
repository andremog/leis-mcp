"""Query-time corrections: (NR) stays with the amended law, and Planalto fills an evident gap."""

import json
import logging
import re
import shutil
from pathlib import Path

import httpx
import pytest

from leis_mcp.config import Settings
from leis_mcp.content import FrontMatterParser
from leis_mcp.errors import QueryError
from leis_mcp.legal_text import DocumentParser
from leis_mcp.models import LoadedNorm, Norm
from leis_mcp.online import OnlineResult
from leis_mcp.online.planalto import PlanaltoText, compiled_url, decode_page, html_to_body, names_law
from leis_mcp.online.transport import refuse_unofficial
from leis_mcp.services.correction import CorpusCorrector
from leis_mcp.services.presenter import NormPresenter

ART_5 = (
    "Art. 5º Na aplicação desta Lei, serão observados os princípios da legalidade, "
    "da impessoalidade, da moralidade, da publicidade, da eficiência, do interesse público, "
    "da probidade administrativa, da igualdade, do planejamento, da transparência, da eficácia, "
    "da segregação de funções, da motivação, da vinculação ao edital, do julgamento objetivo, "
    "da segurança jurídica, da razoabilidade, da competitividade, da proporcionalidade, da celeridade, "
    "da economicidade e do desenvolvimento nacional sustentável, assim como as disposições do "
    "Decreto-Lei nº 4.657, de 4 de setembro de 1942 (Lei de Introdução às Normas do Direito Brasileiro)."
)

PLANALT_14133 = f"""<html><head><title>L14133</title></head><body>
<p>LEI Nº 14.133, DE 1º DE ABRIL DE 2021</p>
<p>{ART_5}</p>
<p>Art. 6º Para os fins desta Lei, consideram-se:</p>
</body></html>
"""

VETO_14133 = """---
titulo: "Lei nº 14.133 de 01/04/2021"
numero: "14133"
versao_texto: "republicação/retificação"
fonte: "https://normas.leg.br/?urn=urn:lex:br:federal:lei:2021-04-01;14133"
---
Faço saber que eu promulgo as seguintes partes vetadas da Lei nº 14.133, de 1º de abril de 2021:

"**Art. 37** Texto promulgado do artigo 37.

**§ 2º** Parágrafo promulgado.
"""

ALDIR = """---
titulo: "Lei nº 14.017 de 29/06/2020"
numero: "14017"
versao_texto: "compilada (texto vigente)"
fonte: "https://normas.leg.br/?urn=urn:lex:br:federal:lei:2020-06-29;14017"
---
**Art. 12.** Texto do art. 12 da Lei Aldir Blanc no acervo.
"""

LEI_14150 = """---
titulo: "Lei nº 14.150 de 12/05/2021"
numero: "14150"
versao_texto: "republicação/retificação"
fonte: "https://normas.leg.br/?urn=urn:lex:br:federal:lei:2021-05-12;14150"
---
**Altera a Lei nº 14.017, de 29 de junho de 2020 (Lei Aldir Blanc).**

Faço saber que eu promulgo as seguintes partes vetadas da Lei nº 14.150, de 12 de maio de 2021:

**"Art**. 1º ..................................................................................................................

'Art. 12. Ficam prorrogados automaticamente por 2 anos os prazos. (NR)
"""

CURRENT = """<html><body>
<p>LEI Nº 14.100, DE 1º DE JANEIRO DE 2020</p>
<p>Art. 1º Texto vigente obtido no Planalto.</p>
</body></html>
"""


def _norm(number: str, year: int, kind: str = "leis-ordinarias") -> Norm:
    return Norm(
        path=f"federal/{kind}/{year}/lei-{number}-{year}.md",
        sphere="federal",
        kind=kind,
        year=year,
        number=number,
        blob="ab" * 20,
    )


def _load(markdown: str, norm: Norm) -> LoadedNorm:
    source = FrontMatterParser().parse(markdown)
    return LoadedNorm(norm, source.metadata, DocumentParser().parse(source.body), source.body_offset)


class _Online:
    def __init__(self, payload: dict | None = None):
        self.payload = payload or {"disponivel": False, "motivo": "teste"}

    def for_provision(self, loaded, excerpt):
        return OnlineResult(self.payload)


class _Service:
    def __init__(self, mapping: dict[str, LoadedNorm], online: _Online | None = None):
        self._mapping = mapping
        self._by_norm = {item.norm: item for item in mapping.values()}
        self._presenter = NormPresenter("https://example.test", "abc")
        self._online = online or _Online()
        self._resolver = self
        self._loader = self

    def resolve(self, reference: str):
        if reference not in self._mapping:
            raise QueryError(reference)
        return self._mapping[reference].norm, None

    def load(self, norm: Norm):
        return self._by_norm[norm]


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def _corrector(tmp_path, handler, *, online: bool = True) -> CorpusCorrector:
    settings = Settings(home=tmp_path, online=online)
    return CorpusCorrector(PlanaltoText(settings, _client(handler)))


def test_compiled_url_is_deterministic_and_refuses_a_guess():
    assert compiled_url("leis-ordinarias", "14133", 2021) == (
        "https://www.planalto.gov.br/ccivil_03/_ato2019-2022/2021/lei/l14133.htm"
    )
    assert compiled_url("leis-complementares", "214", 2025) == (
        "https://www.planalto.gov.br/ccivil_03/_ato2023-2026/2025/lei/lcp/lcp214.htm"
    )
    assert compiled_url("decretos", "11462", 2023) is None
    assert compiled_url("leis-ordinarias", "14133", 1808) is None
    with pytest.raises(httpx.InvalidURL):
        refuse_unofficial("https://gov.br/planalto")
    with pytest.raises(httpx.InvalidURL):
        refuse_unofficial("https://evil.planalto.gov.br/lei.htm")
    refuse_unofficial("https://www.planalto.gov.br/ccivil_03/_ato2019-2022/2021/lei/l14133.htm")


def test_art_5_comes_from_the_recorded_planalto_page_and_is_cached_apart(tmp_path, caplog):
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        assert request.headers.get("authorization") is None
        return httpx.Response(200, text=PLANALT_14133)

    loaded = _load(VETO_14133, _norm("14133", 2021))
    service = _Service({})
    with caplog.at_level(logging.INFO):
        result = _corrector(tmp_path, handler).read(service, loaded, "5", None, None, None, None)
    assert calls == ["https://www.planalto.gov.br/ccivil_03/_ato2019-2022/2021/lei/l14133.htm"]
    assert result["dispositivo"]["texto"] == ART_5
    assert (
        result["avisos"][0] == f"texto obtido do Planalto em {result['origem']['obtido_em']}, acervo local incompleto"
    )
    assert result["fonte"].endswith("/l14133.htm")
    assert "autocorrection law=14133/2021 article=5 problem=fragmento source=" in caplog.text
    cached = list((tmp_path / "cache" / "planalto").glob("*"))
    assert {path.suffix for path in cached} == {".html", ".json"}
    assert not (tmp_path / "cache" / "objects").exists()
    assert not (tmp_path / "overlay").exists()
    meta = json.loads(next(path for path in cached if path.suffix == ".json").read_text())
    assert meta["url"].endswith("/l14133.htm")


def test_offline_fragment_fails_closed_without_a_request(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError(request.url)

    loaded = _load(VETO_14133, _norm("14133", 2021))
    with pytest.raises(QueryError, match="LEIS_MCP_OFFLINE"):
        _corrector(tmp_path, handler, online=False).read(_Service({}), loaded, "5", None, None, None, None)


def test_redirect_off_the_allowlist_is_not_followed(tmp_path):
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.host)
        return httpx.Response(302, headers={"location": "https://gov.br/planalto"})

    loaded = _load(VETO_14133, _norm("14133", 2021))
    with pytest.raises(QueryError, match="partes vetadas"):
        _corrector(tmp_path, handler).read(_Service({}), loaded, "5", None, None, None, None)
    assert calls == ["www.planalto.gov.br"]


def test_page_for_another_law_is_not_returned(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<p>LEI Nº 9.999, DE 1º DE JANEIRO DE 2021</p><p>Art. 5º Texto alheio.</p>")

    loaded = _load(VETO_14133, _norm("14133", 2021))
    with pytest.raises(QueryError, match="partes vetadas"):
        result = _corrector(tmp_path, handler).read(_Service({}), loaded, "5", None, None, None, None)
        assert "Texto alheio" not in str(result)


def test_preamble_that_names_another_law_fails_closed(tmp_path):
    body = VETO_14133.replace("Lei nº 14.133", "Lei nº 9.999")
    loaded = _load(body, _norm("14133", 2021))

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("planalto must not be asked")

    with pytest.raises(QueryError, match="não atribui"):
        _corrector(tmp_path, handler).read(_Service({}), loaded, "37", None, None, None, None)


def test_lei_14150_art_12_is_aldir_blanc_and_art_1_stays_whole(tmp_path):
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(404)

    amending = _load(LEI_14150, _norm("14150", 2021))
    amended = _load(ALDIR, _norm("14017", 2020))
    service = _Service({"14017/2020": amended})
    corrector = _corrector(tmp_path, handler)
    article = corrector.read(service, amending, "1", None, None, None, None)
    assert "Ficam prorrogados" in article["dispositivo"]["texto"]
    assert article["dispositivo"]["citacao"] == "art. 1º"
    before = len(calls)
    missing = corrector.read(service, amending, "12", None, None, None, None)
    assert len(calls) == before
    assert "não é dispositivo da Lei nº 14.150" in missing["mensagem"]
    assert "14.017" in missing["mensagem"]
    assert "art. 1º" in missing["mensagem"]
    assert missing["dispositivo_da_lei_alterada"]["dispositivo"]["texto"].startswith("**Art. 12.** Texto do art. 12")
    assert "Ficam prorrogados" not in missing["dispositivo_da_lei_alterada"]["dispositivo"]["texto"]


def test_newer_wording_returns_both_only_when_the_date_is_unambiguous(tmp_path, caplog):
    law = """---
titulo: "Lei nº 14.100 de 01/01/2020"
numero: "14100"
versao_texto: "compilada (texto vigente)"
baixado_em: 2025-01-01
fonte: "https://normas.leg.br/?urn=urn:lex:br:federal:lei:2020-01-01;14100"
---
**Art. 1º** Texto antigo do acervo.
"""
    loaded = _load(law, _norm("14100", 2020))
    online = _Online(
        {
            "disponivel": True,
            "dispositivo": {"versoes": [{"desde": "2026-02-01", "atual": True, "texto": "vigente"}]},
        }
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=CURRENT)

    service = _Service({}, online)
    with caplog.at_level(logging.INFO):
        result = _corrector(tmp_path, handler).read(service, loaded, "1", None, None, None, None)
    assert result["dispositivo"]["texto"] == "Art. 1º Texto vigente obtido no Planalto."
    assert result["dispositivo"]["vigente"] is True
    shutil.rmtree(tmp_path / "cache" / "planalto")
    narrowed = law.replace(
        "**Art. 1º** Texto antigo do acervo.", "**Art. 1º** Caput.\n\n**§ 2º** Parágrafo antigo do acervo."
    )
    page = CURRENT.replace(
        "Art. 1º Texto vigente obtido no Planalto.",
        "Art. 1º Caput vigente.</p><p>§ 2º Parágrafo vigente obtido no Planalto.",
    )

    def paragraph(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=page)

    detail = _corrector(tmp_path, paragraph).read(
        _Service({}, online), _load(narrowed, _norm("14100", 2020)), "1", "2", None, None, None
    )
    assert detail["dispositivo"]["citacao"] == "art. 1º, § 2º"
    assert detail["dispositivo"]["texto"].startswith("§ 2º Parágrafo vigente")
    assert detail["redacao_acervo"]["texto"].startswith("**§ 2º** Parágrafo antigo")
    assert result["redacao_acervo"]["vigente"] is False
    assert result["redacao_acervo"]["texto"] == "**Art. 1º** Texto antigo do acervo."
    assert "redacao_posterior" in caplog.text

    ambiguous = _Online(
        {
            "disponivel": True,
            "dispositivo": {
                "versoes": [{"desde": "2026-02-01", "atual": True}, {"desde": "2026-03-01", "atual": True}]
            },
        }
    )

    def explode(request: httpx.Request) -> httpx.Response:
        raise AssertionError(request.url)

    kept = _corrector(tmp_path, explode).read(_Service({}, ambiguous), loaded, "1", None, None, None, None)
    assert kept["dispositivo"]["texto"] == "**Art. 1º** Texto antigo do acervo."


def test_latin1_page_with_a_split_label_yields_article_5():
    html = (
        "<html><body><p>LEI N\xba 14.133, DE 1\xba DE ABRIL DE 2021</p>"
        "<p>Art.</p><p>5\xba Na aplica\xe7\xe3o desta Lei, ser\xe3o observados os princ\xedpios.</p>"
        "<p>Art.</p><p>6\xba Para os fins desta Lei, consideram-se:</p></body></html>"
    )
    body = html_to_body(decode_page(html.encode("cp1252")))
    assert names_law(body, "14133")
    text = DocumentParser().parse(body)
    from leis_mcp.legal_text import ProvisionExtractor

    assert ProvisionExtractor(text).extract("5").text.startswith("Art. 5º Na aplicação desta Lei")


def test_a_citation_of_the_expected_law_does_not_make_the_page_that_law():
    body = html_to_body(
        "<p>LEI Nº 9.999, DE 1º DE JANEIRO DE 2021</p>"
        "<p>Altera a Lei nº 14.133, de 1º de abril de 2021.</p>"
        "<p>Art. 5º Texto alheio.</p>"
    )
    assert names_law(body, "9999")
    assert not names_law(body, "14133")


FIXTURE = Path(__file__).parent / "fixtures" / "planalto-lei-14133-vetos.html"
IN_FORCE = {
    ("37", "2"): "Ressalvados os casos de inexigibilidade",
    ("54", "1"): "Sem prejuízo do disposto no",
    ("115", "4"): "Nas contratações de obras e serviços de engenharia",
    ("175", "2"): "Até 31 de dezembro de 2023, os Municípios",
}


def test_recorded_planalto_paragraphs_drop_the_struck_veto(tmp_path):
    page = FIXTURE.read_text(encoding="utf-8")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=page)

    loaded = _load(VETO_14133, _norm("14133", 2021))
    corrector = _corrector(tmp_path, handler)
    for (article, paragraph), needle in IN_FORCE.items():
        result = corrector.read(_Service({}), loaded, article, paragraph, None, None, None)
        flat = re.sub(r"\s+", " ", result["dispositivo"]["texto"])
        assert needle in flat
        assert "(VETADO)" not in flat
        assert result["avisos"][0].startswith("texto obtido do Planalto em ")


def test_two_in_force_labels_fail_closed(tmp_path):
    page = """<html><body>
    <p>LEI Nº 14.133, DE 1º DE ABRIL DE 2021</p>
    <p>Art. 37. Caput.</p>
    <p>§ 2º (VETADO).</p>
    <p>§ 2º Ressalvados os casos de inexigibilidade.</p>
    </body></html>"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=page)

    loaded = _load(VETO_14133, _norm("14133", 2021))
    with pytest.raises(QueryError, match="dois rótulos vigentes") as error:
        _corrector(tmp_path, handler).read(_Service({}), loaded, "37", "2", None, None, None)
    assert "(VETADO)" not in str(error.value)
    assert "Ressalvados" not in str(error.value)


def test_nested_strike_and_windows_1252_keep_only_the_promulgated_paragraph():
    snippet = (
        "<html><body><p>LEI Nº 14.133, DE 1º DE ABRIL DE 2021</p><p>Art. 37. Caput.</p>"
        '<span style="text-decoration: line-through"><span><del>§ 2º (VETADO).</del></span></span>'
        "<p>§ 2º Ressalvados os casos de inexigibilidade.</p></body></html>"
    )
    body = html_to_body(decode_page(snippet.encode("cp1252")))
    text = DocumentParser().parse(body)
    from leis_mcp.legal_text import ProvisionExtractor

    found = re.sub(r"\s+", " ", ProvisionExtractor(text).extract("37", paragraph="2").text)
    assert found.startswith("§ 2º Ressalvados os casos de inexigibilidade.")
    assert "(VETADO)" not in found


def test_whole_article_allows_inciso_numbering_to_restart(tmp_path):
    page = FIXTURE.read_text(encoding="utf-8").replace(
        "<p>Art. 37. O julgamento por melhor técnica ou por técnica e preço deverá ser realizado por:</p>",
        "<p>Art. 37. O julgamento por melhor técnica ou por técnica e preço deverá ser realizado por:</p>"
        "<p>I - verificação da capacitação e da experiência do licitante;</p>"
        "<p>§ 1º A banca referida no inciso II do caput deste artigo terá no mínimo 3 (três) membros.</p>"
        "<p>I - integrante indicado pelo órgão;</p>",
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=page)

    loaded = _load(VETO_14133, _norm("14133", 2021))
    result = _corrector(tmp_path, handler).read(_Service({}), loaded, "37", None, None, None, None)
    text = re.sub(r"\s+", " ", result["dispositivo"]["texto"])
    assert "verificação da capacitação" in text
    assert "Ressalvados os casos de inexigibilidade" in text
    assert "(VETADO)" not in text
    caput = _corrector(tmp_path, handler).read(_Service({}), loaded, "37", None, "I", None, None)
    assert "verificação da capacitação" in caput["dispositivo"]["texto"]
    inner = _corrector(tmp_path, handler).read(_Service({}), loaded, "37", "2", "I", None, None)
    assert "melhor técnica" in inner["dispositivo"]["texto"]
    assert "verificação da capacitação" not in inner["dispositivo"]["texto"]


def test_sibling_alineas_stay_with_their_own_inciso_and_a_duplicate_fails_closed(tmp_path):
    page = """<html><body>
    <p>LEI Nº 14.133, DE 1º DE ABRIL DE 2021</p>
    <p>Art. 37. Caput com incisos.</p>
    <p>I - primeiro inciso do caput.</p>
    <p>a) alínea do inciso I.</p>
    <p>II - segundo inciso do caput.</p>
    <p>a) alínea do inciso II.</p>
    <p>§ 2º Parágrafo com dois a no mesmo inciso.</p>
    <p>I - inciso do parágrafo:</p>
    <p>a) primeira ocorrência.</p>
    <p>a) segunda ocorrência no mesmo inciso.</p>
    </body></html>"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=page)

    loaded = _load(VETO_14133, _norm("14133", 2021))
    corrector = _corrector(tmp_path, handler)
    service = _Service({})
    inciso_i = corrector.read(service, loaded, "37", None, "I", None, None)
    assert "primeiro inciso do caput" in inciso_i["dispositivo"]["texto"]
    assert "alínea do inciso I" in inciso_i["dispositivo"]["texto"]
    assert "inciso II" not in inciso_i["dispositivo"]["texto"]
    alinea_i = corrector.read(service, loaded, "37", None, "I", "a", None)
    assert alinea_i["dispositivo"]["texto"].startswith("a) alínea do inciso I.")
    alinea_ii = corrector.read(service, loaded, "37", None, "II", "a", None)
    assert alinea_ii["dispositivo"]["texto"].startswith("a) alínea do inciso II.")
    with pytest.raises(QueryError, match="dois rótulos vigentes") as error:
        corrector.read(service, loaded, "37", "2", "I", "a", None)
    assert "primeira ocorrência" not in str(error.value)
    assert "segunda ocorrência" not in str(error.value)


def test_many_empty_strikes_are_removed_without_dropping_the_text():
    page = "<div><p>LEI Nº 14.133, DE 1º DE ABRIL DE 2021</p><p>Art. 1º " + "<s></s>x" * 4000 + "</p></div>"
    body = html_to_body(page)
    assert "x" * 4000 in re.sub(r"\s+", "", body)


def test_repeated_inciso_inside_one_paragraph_fails_closed(tmp_path):
    page = """<html><body>
    <p>LEI Nº 14.133, DE 1º DE ABRIL DE 2021</p>
    <p>Art. 37. Caput.</p>
    <p>I - inciso único do caput.</p>
    <p>§ 2º Texto do parágrafo.</p>
    <p>I - texto vetado do inciso.</p>
    <p>I - texto vigente do inciso.</p>
    <p>a) alinea vetada.</p>
    <p>a) alinea vigente.</p>
    </body></html>"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=page)

    loaded = _load(VETO_14133, _norm("14133", 2021))
    corrector = _corrector(tmp_path, handler)
    for args in (("37", "2", None, None), ("37", "2", "I", None), ("37", "2", "I", "a")):
        with pytest.raises(QueryError, match="dois rótulos vigentes") as error:
            corrector.read(_Service({}), loaded, *args, None)
        assert "texto vetado do inciso" not in str(error.value)
        assert "texto vigente do inciso" not in str(error.value)
        assert "alinea vetada" not in str(error.value)


def test_struck_br_and_stray_end_tag_drop_only_the_revoked_tail():
    page = """<html><body>
    <p>LEI Nº 14.133, DE 1º DE ABRIL DE 2021</p>
    <p>Art. 37. Caput.</p>
    <span style="text-decoration: line-through">§ 1º (VETADO). trecho revogado</font> continua revogado.</span>
    <p>§ 1º Texto vigente do parágrafo.</p>
    <strike>§ 2º (VETADO).<br>mais revogado</strike>
    <p>§ 2º Ressalvados os casos de inexigibilidade.</p>
    </body></html>"""
    body = html_to_body(page)
    assert "(VETADO)" not in body
    assert "continua revogado" not in body
    assert "mais revogado" not in body
    assert "Texto vigente do parágrafo" in body
    assert "Ressalvados os casos de inexigibilidade" in body


def test_parser_refuses_an_oversized_page_and_ignores_external_entities():
    with pytest.raises(ValueError, match="size cap"):
        html_to_body("x" * (4 * 1024 * 1024 + 1))
    page = (
        '<!DOCTYPE foo [<!ENTITY xxe SYSTEM "http://127.0.0.1/secret-from-network">]>'
        "<p>LEI Nº 14.133, DE 1º DE ABRIL DE 2021</p><p>Art. 1º &xxe; fim.</p>"
    )
    body = html_to_body(page)
    assert "secret-from-network" not in body
    assert "Art. 1º" in body


def test_inline_strike_keeps_the_following_words_and_loose_alinea_fails_closed(tmp_path):
    page = """<html><body>
    <p>LEI Nº 14.133, DE 1º DE ABRIL DE 2021</p>
    <p>Art. 37. O prazo é de <span style="text-decoration: line-through">30</span> 15 dias.</p>
    <p style="text-decoration: line-through">§ 1º (VETADO).</p>
    § 1º Texto vigente solto.
    <p>§ 2º Texto do parágrafo.</p>
    <p>a) texto vetado da alinea.</p>
    <p>a) texto vigente da alinea.</p>
    </body></html>"""
    body = html_to_body(page)
    assert "O prazo é de 15 dias." in re.sub(r"\s+", " ", body)
    assert "(VETADO)" not in body
    assert "Texto vigente solto." in body
    loaded = _load(VETO_14133, _norm("14133", 2021))

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=page)

    with pytest.raises(QueryError, match="dois rótulos vigentes") as error:
        _corrector(tmp_path, handler).read(_Service({}), loaded, "37", "2", None, "a", None)
    assert "texto vetado da alinea" not in str(error.value)


def test_literal_less_than_entity_survives_tag_removal():
    page = "<p>LEI Nº 14.133, DE 1º DE ABRIL DE 2021</p><p>Art. 9º O valor &lt; 10 e &gt; 5 permanece.</p>"
    body = html_to_body(page)
    assert "O valor < 10 e > 5 permanece." in body


def test_html_fixture_keeps_the_official_article_sentence():
    body = html_to_body(PLANALT_14133)
    assert "LEI Nº 14.133" in body
    assert ART_5 in body
