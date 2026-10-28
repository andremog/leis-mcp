import pytest

from leis_mcp.errors import ProvisionNotFoundError
from leis_mcp.legal_text import DocumentParser, ProvisionExtractor


@pytest.mark.parametrize(
    ("article", "start"),
    [
        ("1", "**Art. 1º** Caput do artigo primeiro."),
        ("2", "**Art. 2o** Caput do artigo segundo"),
        ("3", "**Art. 3.º** Caput com ponto"),
        ("4-A", "**Art. 4º-A.** Artigo com letra."),
        ("10", "**Art. 10.** Artigo dez"),
        ("11", "Art. 11. Artigo sem negrito."),
        ("1.000", "**Art. 1.000.** Artigo mil"),
        ("197", "Art. l97. Artigo com OCR"),
        ("359-M-A", "**Art. 359-M-A.** Artigo com duas letras."),
    ],
)
def test_label_variations(extractor, article, start):
    assert extractor.extract(article).text.startswith(start)


def test_article_1_is_not_10_or_1000(extractor):
    assert extractor.extract("1").text == "**Art. 1º** Caput do artigo primeiro."


def test_clauses_with_different_dashes(extractor):
    assert extractor.extract("2", clause="I").text == "I - primeiro inciso;"
    assert extractor.extract("2", clause="II").text == "II – segundo inciso com meia-risca;"
    assert extractor.extract("2", clause="III").text == "III— terceiro inciso com travessão colado;"


def test_clause_includes_subclauses_and_head(extractor):
    excerpt = extractor.extract("2", clause="IV")
    assert excerpt.text.splitlines()[0] == "IV- quarto inciso sem espaço antes do hífen:"
    assert "b) alínea b do inciso IV." in excerpt.text
    assert excerpt.head.startswith("**Art. 2o**")
    assert excerpt.citation == "art. 2º, IV"


def test_subclause(extractor):
    assert extractor.extract("2", clause="IV", subclause="b").text == "b) alínea b do inciso IV."


def test_paragraphs(extractor):
    assert extractor.extract("3", paragraph="único").text == "**Parágrafo único.** Parágrafo único do artigo 3º."
    assert extractor.extract("10", paragraph="2").text == "**§ 2°** Segundo parágrafo, com sinal de grau."
    assert extractor.extract("10", paragraph="3-A").text == "**§ 3º-A** Parágrafo com letra."
    first = extractor.extract("10", paragraph="1")
    assert first.text.startswith("**§ 1º**")
    assert "II - inciso II do § 1º." in first.text
    assert "§ 2°" not in first.text


def test_clause_inside_paragraph(extractor):
    excerpt = extractor.extract("10", paragraph="1º", clause="II")
    assert excerpt.text == "II - inciso II do § 1º."
    assert excerpt.citation == "art. 10, § 1º, II"


def test_clause_only_in_paragraph_points_to_it(extractor):
    with pytest.raises(ProvisionNotFoundError, match="§ 1º"):
        extractor.extract("10", clause="I")


def test_notices(extractor):
    assert any("revogação" in notice for notice in extractor.extract("11").notices)
    assert any("tachado" in notice for notice in extractor.extract("12").notices)
    assert extractor.extract("1").notices == ()


def test_last_article_stops_at_signature(extractor):
    assert "Brasília" not in extractor.extract("359-M-A").text


def test_missing_article_suggests_neighbours(extractor):
    with pytest.raises(ProvisionNotFoundError) as error:
        extractor.extract("13")
    assert "Art. 12" in error.value.suggestions


def test_blocks_main_and_transitional(blocks):
    extractor = ProvisionExtractor(blocks)
    assert [block.title for block in blocks.blocks] == [
        "Constituição de Teste",
        "ATO DAS DISPOSIÇÕES CONSTITUCIONAIS TRANSITÓRIAS",
    ]
    main = extractor.extract("1")
    assert main.text.endswith("corpo principal.")
    assert any("Há outro Art. 1º" in notice for notice in main.notices)
    assert extractor.extract("1", block="transitorias").text.endswith("transitório.")
    assert extractor.extract("2", block=1).text.endswith("transitório.")
    with pytest.raises(ProvisionNotFoundError):
        extractor.extract("3", block="transitorias")


def test_line_numbers_ignore_form_feed():
    document = DocumentParser().parse("**Art. 1º** Um.\n\x0c\n**Art. 2º** Dois.\n")
    assert document.article_at(2).key == "2"  # \x0c must not count as a line break


def test_article_at(variations):
    line = next(index for index, text in enumerate(variations.lines) if text.startswith("II – segundo"))
    assert variations.article_at(line).key == "2"
    assert variations.article_at(0) is None
