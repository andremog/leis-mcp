"""Quoted article headings used by promulgations of vetoed parts (Lei 14.133/2021 in the corpus)."""

import pytest

from leis_mcp.errors import ProvisionNotFoundError
from leis_mcp.legal_text import ArticleLabel, DocumentParser, ProvisionExtractor
from leis_mcp.messages import VETO_PROMULGATION_ONLY

# Shapes taken from federal/leis-ordinarias/2021/lei-14133-2021-04-01.md. The corpus file is the
# promulgation of the vetoed parts, not the consolidated statute.
PROMULGATION = """
O PRESIDENTE DA REPÚBLICA

Faço saber que o Congresso Nacional decreta e eu promulgo as seguintes partes vetadas da Lei:

"**Art. 37** ..................................................................................................................

**§ 2º** Texto do parágrafo promulgado:

I - melhor técnica; ou

II - técnica e preço.

**"Art.** 54 .................................................................................................................

**§ 1º** Extrato do edital.

**"Art**. 115 ...............................................................................................................

**§ 4º** Licença prévia.

**"Art.** 175 ...............................................................................................................

**§ 2º** Divulgação complementar.

Brasília, 10 de junho de 2021; 200º da Independência e 133º da República.
"""

AMENDING = """
Faço saber que eu promulgo a seguinte Lei:

**Art. 1º** A Lei passa a vigorar com as seguintes alterações:

"**Art. 37** Citação que permanece dentro do art. 1º.

“**Art.** 9º Texto novo do artigo nove.

**Art. 2º** Esta Lei entra em vigor na data de sua publicação.
"""

ERRATA = """
**RETIFICAÇÃO**

No art. 5º, onde se lê:

"Art. 5º Texto errado que não pode ser devolvido como se fosse o artigo.

Leia-se:

"Art. 5º Texto da correção, que também não é o texto integral.
"""

ARTIGO_UNICO = """
Faço saber que o Congresso Nacional decreta e eu sanciono a seguinte Lei:

Artigo único. O art. 9º passa a vigorar acrescido dos seguintes parágrafos:

“**Art.** 9º Texto citado do artigo alterado.

**§ 4º** Parágrafo incluído.
"""


def _keys(body: str) -> list[str]:
    document = DocumentParser().parse(body)
    return [key for block in document.blocks for key in block.articles]


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ('"**Art. 37** resto', "37"),
        ('**"Art.** 54 resto', "54"),
        ('**"Art**. 115 resto', "115"),
        ('**"Art.** 175 resto', "175"),
        ('"Art. 4º Ficam', "4"),
        ("“**Art. 5º** A implantação", "5"),
        ("“**Art.** 15.....", "15"),
        ("“**Art.**18.....", "18"),
        ("'Art. 12-C. Texto", "12-C"),
        ('**"Art**. 1º texto', "1"),
        ('"Art. 20-A texto', "20-A"),
        ("**Art. 1º** Caput", "1"),
        ("Arts. 1º e 2º revogados", None),
        ("Artigo único. O art. 9º", None),
        ("Texto qualquer", None),
        ("veja o Art. 5º no meio", None),
    ],
)
def test_quoted_label(line, expected):
    assert ArticleLabel.from_quoted_line(line) == expected


@pytest.mark.parametrize(
    "line",
    [
        '"**Art. 37** resto',
        '**"Art.** 54 resto',
        '**"Art**. 115 resto',
        "“**Art.** 9º Texto novo",
        '"Art. 5º Texto errado',
    ],
)
def test_strict_label_ignores_quoted_headings(line):
    assert ArticleLabel.from_line(line) is None


def test_promulgation_reads_quoted_articles_and_says_art_5_is_absent():
    document = DocumentParser().parse(PROMULGATION)
    extractor = ProvisionExtractor(document)
    assert document.quoted_fallback
    assert _keys(PROMULGATION) == ["37", "54", "115", "175"]

    paragraph = extractor.extract("37", paragraph="2")
    assert paragraph.text.startswith("**§ 2º** Texto do parágrafo promulgado:")
    assert "II - técnica e preço." in paragraph.text
    assert extractor.extract("37", paragraph="2", clause="I").text == "I - melhor técnica; ou"
    assert extractor.extract("54", paragraph="1").text == "**§ 1º** Extrato do edital."
    assert "Brasília" not in extractor.extract("175").text
    assert VETO_PROMULGATION_ONLY in extractor.extract("37").notices

    with pytest.raises(ProvisionNotFoundError, match="partes vetadas") as error:
        extractor.extract("5")
    assert "Art. 37" in error.value.suggestions
    assert "Art. 5 não encontrado." in str(error.value)


def test_quoted_amendment_stays_inside_the_amending_article():
    document = DocumentParser().parse(AMENDING)
    assert not document.quoted_fallback
    assert _keys(AMENDING) == ["1", "2"]
    text = ProvisionExtractor(document).extract("1").text
    assert '"**Art. 37**' in text
    assert "Texto novo do artigo nove." in text
    assert VETO_PROMULGATION_ONLY not in ProvisionExtractor(document).extract("1").notices


def test_errata_and_artigo_unico_are_not_treated_as_promulgated_articles():
    assert DocumentParser().parse(ERRATA).blocks == []
    assert DocumentParser().parse(ARTIGO_UNICO).blocks == []
    mixed = "Faço saber que eu promulgo as seguintes partes vetadas:\n\n" + ERRATA
    assert DocumentParser().parse(mixed).blocks == []
    onde_se_diz = mixed.replace("onde se lê", "onde se diz").replace("Leia-se", "Leia–se")
    assert DocumentParser().parse(onde_se_diz).blocks == []
    onde_se_lia = mixed.replace("onde se lê", "onde se lia")
    assert DocumentParser().parse(onde_se_lia).blocks == []


def test_singular_parte_vetada_is_still_a_promulgation():
    body = """
Faço saber que eu promulgo a seguinte parte vetada da Lei nº 14.162:

"Art. 5º A Lei nº 9.264 passa a vigorar acrescida do seguinte art. 12-C:

'Art. 12-C. Sem prejuízo dos direitos dos servidores.
"""
    document = DocumentParser().parse(body)
    assert _keys(body) == ["5"]
    assert "Sem prejuízo dos direitos dos servidores." in ProvisionExtractor(document).extract("5").text


def test_ordinary_promulgation_formula_does_not_open_the_fallback():
    body = """
Faço saber que eu promulgo a seguinte Lei:

"**Art. 5º** Texto de uma lei comum, sem relação com vetos.
"""
    assert DocumentParser().parse(body).blocks == []


def test_artigo_unico_is_not_replaced_by_a_quoted_article():
    body = """
Faço saber que eu promulgo as seguintes partes vetadas:

Artigo único. O art. 5º passa a vigorar assim:

"**Art. 5º-A** Texto inserido em outra lei.
"""
    assert DocumentParser().parse(body).blocks == []


def test_inserted_article_stays_inside_the_promulgated_article():
    body = """
Faço saber que eu promulgo as seguintes partes vetadas:

"Art. 5º A Lei nº 9.264 passa a vigorar acrescida do seguinte art. 12-C:

'Art. 12-C. Sem prejuízo dos direitos dos servidores.

"Art. 19. A Lei nº 13.756 passa a vigorar acrescida do seguinte art. 20-A:

"Art. 20-A. Destinação de 3% da arrecadação das loterias.

"Art. 21. Os prazos de validade das certidões serão prorrogados.
"""
    document = DocumentParser().parse(body)
    extractor = ProvisionExtractor(document)
    assert _keys(body) == ["5", "19", "21"]
    article = extractor.extract("5")
    assert "Sem prejuízo dos direitos dos servidores." in article.text
    assert "12-C" in article.text
    assert "Destinação de 3%" in extractor.extract("19").text
    assert extractor.extract("21").text.startswith('"Art. 21.')
    with pytest.raises(ProvisionNotFoundError, match="partes vetadas"):
        extractor.extract("12-C")
    with pytest.raises(ProvisionNotFoundError, match="partes vetadas"):
        extractor.extract("20-A")


def test_repeated_heading_is_the_wording_of_the_same_article():
    body = """
Faço saber que eu promulgo as seguintes partes vetadas:

**"Art**. 1º ..................................................................................................................

'Art. 1º Esta Lei dispõe sobre o texto promulgado.' (NR)

**'Art.** 2º ...................................................................................................................

**§ 3º** Parágrafo do artigo 2.
"""
    document = DocumentParser().parse(body)
    extractor = ProvisionExtractor(document)
    assert _keys(body) == ["1", "2"]
    assert len(document.blocks) == 1
    text = extractor.extract("1").text
    assert text.startswith('**"Art**. 1º')
    assert "Esta Lei dispõe sobre o texto promulgado." in text
    assert extractor.extract("2", paragraph="3").text.startswith("**§ 3º**")


def test_quoted_inciso_is_addressable_and_a_missing_unit_keeps_the_warning():
    body = """
Faço saber que eu promulgo as seguintes partes vetadas:

"**Art. 12.** Caput do artigo.

"XXVII - Fundo Especial de assistência.

"XXX - despesas com o reajuste.
"""
    extractor = ProvisionExtractor(DocumentParser().parse(body))
    assert extractor.extract("12", clause="XXVII").text == '"XXVII - Fundo Especial de assistência.'
    with pytest.raises(ProvisionNotFoundError, match="partes vetadas") as missing_paragraph:
        extractor.extract("12", paragraph="1")
    assert "§ 1º" in str(missing_paragraph.value)
    with pytest.raises(ProvisionNotFoundError, match="partes vetadas") as missing_clause:
        extractor.extract("12", clause="I")
    assert "XXVII" in missing_clause.value.suggestions
