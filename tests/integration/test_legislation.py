"""Real cases, with the text checked against the ctresb/leis repository (network + local index)."""

import pytest

from leis_mcp import messages
from leis_mcp.errors import QueryError

pytestmark = pytest.mark.integration

TENANCY_LAW = "federal/leis-ordinarias/1991/lei-8245-1991-10-18.md"


def test_tenancy_law_art_23_clause_iii(service):
    response = service.read_provision("8245/1991", "23", clause="III")
    assert response["dispositivo"]["texto"] == (
        "III - restituir o imóvel, finda a locação, no estado em que o recebeu, "
        "salvo as deteriorações decorrentes do seu uso normal;"
    )
    assert response["dispositivo"]["caput"] == "**Art. 23.** O locatário é obrigado a:"
    assert response["norma"]["caminho"] == TENANCY_LAW
    assert response["fonte"].startswith("https://normas.leg.br/")
    assert response["aviso_legal"] == messages.LEGAL_NOTICE
    assert response["avisos"] == []


@pytest.mark.parametrize(
    "reference", ["Lei 8.245/91", "8.245", "lei do inquilinato", "Lei nº 8.245, de 18 de outubro de 1991"]
)
def test_reference_forms_reach_the_same_law(service, reference):
    assert service.read_provision(reference, "23", clause="3")["norma"]["caminho"] == TENANCY_LAW


def test_tenancy_law_art_38_paragraph_2(service):
    text = service.read_provision("inquilinato", "38", paragraph="2º")["dispositivo"]["texto"]
    assert text.startswith("**§ 2º** A caução em dinheiro")
    assert "caderneta de poupança" in text


def test_consumer_code_art_20_clause_i(service):
    response = service.read_provision("CDC", "20", clause="I")
    assert response["dispositivo"]["texto"] == "I - a reexecução dos serviços, sem custo adicional e quando cabível;"
    assert response["norma"]["caminho"] == "federal/leis-ordinarias/1990/lei-8078-1990-09-11.md"


def test_civil_code_art_422(service):
    text = service.read_provision("código civil", "422")["dispositivo"]["texto"]
    assert text.startswith("**Art. 422.**")
    assert "probidade e boa-fé" in text


def test_civil_code_thousands_separator(service):
    assert service.read_provision("10406/2002", "1.000")["dispositivo"]["texto"].startswith("**Art. 1.000.**")


def test_small_claims_law_art_9(service):
    text = service.read_provision("9099/1995", "9º")["dispositivo"]["texto"]
    assert "Nas causas de valor até vinte salários mínimos" in text
    assert "assistidas por advogado" in text


def test_labour_code_uses_the_consolidation_body(service):
    response = service.read_provision("CLT", "2")
    assert response["norma"]["caminho"] == "federal/decretos-lei/1943/del-5452-1943-05-01.md"
    assert "Considera-se empregador" in response["dispositivo"]["texto"]
    assert any("Há outro Art. 2º" in notice for notice in response["avisos"])


def test_constitution_and_transitional_provisions(service):
    response = service.read_provision("constituição", "5", clause="XI")
    assert response["dispositivo"]["texto"].startswith("XI - a casa é asilo inviolável do indivíduo")
    assert response["fonte"].startswith("https://www.planalto.gov.br/")
    adct = service.read_provision("ADCT", "10")
    assert adct["dispositivo"]["bloco"] == "ATO DAS DISPOSIÇÕES CONSTITUCIONAIS TRANSITÓRIAS"


def test_repealed_provision_has_notice(service):
    response = service.read_provision("código civil", "374")
    assert response["dispositivo"]["situacao"] == {
        "revogado": True,
        "vetado": False,
        "nota": "Revogado pela Lei nº 10.677, de 22/5/2003",
    }
    assert any("REVOGADO" in notice for notice in response["avisos"])


def test_find_labour_code(service):
    top = service.find_norms("Decreto-Lei 5.452/1943")["resultados"][0]
    assert top["caminho"] == "federal/decretos-lei/1943/del-5452-1943-05-01.md"
    assert top["titulo"] == "Decreto-Lei nº 5.452 de 01/05/1943"
    assert top["fonte"].startswith("https://")
    assert top["revogada"] is False


def test_find_by_number_and_year(service):
    results = service.find_norms(number="8.245", year=1991, sphere="federal")["resultados"]
    assert [result["caminho"] for result in results] == [TENANCY_LAW]
    assert results[0]["ementa"].startswith("Dispõe sobre as locações dos imóveis urbanos")


def test_every_alias_resolves_to_a_federal_norm(service):
    # The service resolver sees the parliamentary overlay; a bare upstream index does not.
    assert all(service._resolver.resolve(alias)[0].sphere == "federal" for alias in service.aliases())


def test_clear_errors(service):
    with pytest.raises(QueryError, match="não encontrado"):
        service.read_provision("8245/1991", "999")
    with pytest.raises(QueryError, match="Não entendi"):
        service.read_provision("blablabla", "1")
    with pytest.raises(QueryError, match="ambígua"):
        service.read_provision("decreto 5452", "1")


def test_search_within_a_norm(service):
    response = service.search_text("caderneta de poupanca", norm="inquilinato")
    occurrence = response["resultados"][0]["ocorrencias"][0]
    assert occurrence["artigo"] == "38"
    assert "caderneta de poupança" in occurrence["trecho"]


def test_search_in_cache(service):
    service.read_provision("CDC", "1")
    response = service.search_text("reexecução dos serviços", kind="lei")
    paths = [group["norma"]["caminho"] for group in response["resultados"]]
    assert "federal/leis-ordinarias/1990/lei-8078-1990-09-11.md" in paths


def test_browse(service):
    assert service.browse()["contagens"]["federal"] > 100_000
    assert service.browse("federal")["contagens"]["leis-complementares"] > 200
    assert service.browse("federal", "leis-complementares")["contagens"]["2006"] >= 1
    assert service.browse("estaduais")["nivel"] == "uf"
    page = service.browse("federal", "leis-complementares", 2006)["normas"]
    assert "federal/leis-complementares/2006/lcp-123-2006-12-14.md" in [norm["caminho"] for norm in page]


def test_repealed_article_names_the_repealing_law(service):
    response = service.read_provision("CLT", "17")
    assert response["dispositivo"]["situacao"]["nota"] == "Revogado pela Lei nº 13.874, de 20/9/2019"
    assert response["vigencia"]["estado"] == "sem_revogacao_registrada"
    assert response["vigencia_online"]["disponivel"] is False


def test_word_revoked_in_the_text_is_not_a_repeal(service):
    response = service.read_provision("código penal", "88")
    assert "situacao" not in response["dispositivo"]
    assert response["avisos"] == []


def test_repealed_law_with_its_repeal_note(service):
    response = service.read_provision("8666/1993", "1")
    assert response["vigencia"]["estado"] == "revogada"
    assert response["vigencia"]["revogada_por"].startswith("Revogada em 30/12/2023 pela Lei nº 14.133")
    assert response["redacao"]["compilada"] is True
    assert response["redacao"]["capturada_em"]


def test_decree_not_in_force_is_flagged_in_search(service):
    top = service.find_norms("Decreto 69.582/1971")["resultados"][0]
    assert top["vigencia"]["estado"] == "sem_vigencia_na_fonte"
    assert top["vigencia"]["campos"] == {"forca_legal_fonte": '["NotInForce"]'}
    assert any("NÃO está em vigor" in notice for notice in top["avisos"])


def test_state_status_from_the_source(service):
    path = "estaduais/DF/decretos-legislativos/2001/dlg-718-2001-07-10.md"
    response = service.read_provision(path, "1")
    assert response["vigencia"]["estado"] == "sem_revogacao_registrada"
    assert response["vigencia"]["campos"]["situacao"] == "Sem Revogação Expressa"
    assert response["vigencia_online"]["disponivel"] is False


def test_text_search_and_browse_show_validity(service):
    group = service.search_text("caderneta de poupanca", norm="inquilinato")["resultados"][0]
    assert group["norma"]["vigencia"]["estado"] == "sem_revogacao_registrada"
    service.read_provision("LC 123/2006", "1")
    page = service.browse("federal", "leis-complementares", 2006)["normas"]
    listed = next(norm for norm in page if norm["caminho"] == "federal/leis-complementares/2006/lcp-123-2006-12-14.md")
    assert listed["vigencia"] == "sem_revogacao_registrada"


def test_live_online_validity(online_service):
    clause = online_service.read_provision("código civil", "3", clause="I")["vigencia_online"]
    assert clause["disponivel"] is True
    assert clause["dispositivo"]["situacao"] == "revogado"
    assert clause["dispositivo"]["versoes"][-1]["alterado_por"].startswith("Lei nº 13.146")
    repealed_law = online_service.read_provision("8666/1993", "1")
    assert repealed_law["vigencia_online"]["norma"]["forca_legal"] == "NotInForce"
    assert repealed_law["vigencia"]["origem"].startswith("acervo ctresb/leis e consulta online")
    labour = online_service.read_provision("CLT", "17")["vigencia_online"]["dispositivo"]
    assert labour["situacao"] == "revogado"
