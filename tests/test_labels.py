import pytest

from leis_mcp.document import Level
from leis_mcp.errors import QueryError
from leis_mcp.legal_text import ArticleLabel, Citation, ClauseLabel, Label, ParagraphLabel, SubclauseLabel


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("23", "23"), ("Art. 23", "23"), ("art 9º", "9"), ("9o", "9"), ("1.º", "1"), ("10.", "10"),
        ("1.000", "1000"), ("4º-A", "4-A"), ("4-a", "4-A"), ("359-M-A", "359-M-A"), (422, "422"), ("l97", "197"),
    ],
)  # fmt: skip
def test_article_parse(value, expected):
    assert ArticleLabel.parse(value) == expected


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("**Art. 1º** Caput", "1"),
        ("Art. 2o Caput", "2"),
        ("**Art. 3.º** Caput", "3"),
        ("**Art. 4º-A.** Caput", "4-A"),
        ("**Art. 1.000.** Caput", "1000"),
        ("Art. l97. Caput", "197"),
        ("Arts. 1º e 2º revogados", None),
        ("Texto qualquer", None),
    ],
)
def test_article_from_line(line, expected):
    assert ArticleLabel.from_line(line) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [("2", "2"), ("2º", "2"), ("§ 2°", "2"), ("único", "unico"), ("Parágrafo único", "unico"), ("3º-A", "3-A")],
)
def test_paragraph_parse(value, expected):
    assert ParagraphLabel.parse(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"), [("III", "III"), ("iv", "IV"), (3, "III"), ("14", "XIV"), ("I-A", "I-A"), ("1-a", "I-A")]
)
def test_clause_parse(value, expected):
    assert ClauseLabel.parse(value) == expected


@pytest.mark.parametrize(
    ("label", "line", "expected"),
    [
        (ParagraphLabel, "**§ 2°** Texto", "2"),
        (ParagraphLabel, "**Parágrafo único.** Texto", "unico"),
        (ClauseLabel, "II – texto", "II"),
        (ClauseLabel, "IV- texto", "IV"),
        (ClauseLabel, "CLT - texto", None),
        (SubclauseLabel, "b) texto", "b"),
    ],
)
def test_unit_from_line(label, line, expected):
    assert label.from_line(line) == expected


@pytest.mark.parametrize(
    ("label", "value"),
    [
        (ArticleLabel, "abc"),
        (ParagraphLabel, "x"),
        (ClauseLabel, "abc"),
        (ClauseLabel, 0),
        (ClauseLabel, 4000),
        (SubclauseLabel, "ab"),
    ],
)
def test_invalid_values(label, value):
    with pytest.raises(QueryError):
        label.parse(value)


def test_ordinal():
    assert Label.ordinal("9") == "9º"
    assert Label.ordinal("4-A") == "4º-A"
    assert Label.ordinal("10") == "10"


def test_citation():
    units = {Level.PARAGRAPH: "1", Level.CLAUSE: "II", Level.SUBCLAUSE: "a"}
    assert Citation.format("10", units) == "art. 10, § 1º, II, alínea a"
    assert Citation.format("3", {Level.PARAGRAPH: "unico"}) == "art. 3º, parágrafo único"
