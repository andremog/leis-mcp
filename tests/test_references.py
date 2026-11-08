import pytest

from leis_mcp.models import Reference
from leis_mcp.references import ReferenceParser


@pytest.fixture(scope="module")
def parser() -> ReferenceParser:
    return ReferenceParser(current_year=2026)


@pytest.mark.parametrize(
    ("text", "number", "years", "kind"),
    [
        ("8.245", "8245", (), None),
        ("8245", "8245", (), None),
        ("8245/1991", "8245", (1991,), None),
        ("Lei 8.245/91", "8245", (1991,), "leis-ordinarias"),
        ("Lei nº 8.245/1991", "8245", (1991,), "leis-ordinarias"),
        ("lei federal 8245 de 1991", "8245", (1991,), "leis-ordinarias"),
        ("Lei nº 8.245, de 18 de outubro de 1991", "8245", (1991,), "leis-ordinarias"),
        ("LC 123/2006", "123", (2006,), "leis-complementares"),
        ("Lei Complementar 123", "123", (), "leis-complementares"),
        ("Decreto-Lei nº 5.452/1943", "5452", (1943,), "decretos-lei"),
        ("DL 5452", "5452", (), "decretos-lei"),
        ("Decreto 9.000/2017", "9000", (2017,), "decretos"),
        ("MP 1.154/2023", "1154", (2023,), "medidas-provisorias"),
    ],
)
def test_parse(parser, text, number, years, kind):
    reference = parser.parse(text)
    assert (reference.number, reference.years, reference.kind) == (number, years, kind)


@pytest.mark.parametrize(
    ("alias", "number", "year", "kind"),
    [
        ("Lei do Inquilinato", "8245", 1991, "leis-ordinarias"),
        ("CDC", "8078", 1990, "leis-ordinarias"),
        ("CLT", "5452", 1943, "decretos-lei"),
        ("Código Civil", "10406", 2002, "leis-ordinarias"),
        ("cpc", "13105", 2015, "leis-ordinarias"),
        ("código penal", "2848", 1940, "decretos-lei"),
    ],
)
def test_aliases(parser, alias, number, year, kind):
    reference = parser.parse(alias)
    assert (reference.number, reference.years, reference.kind, reference.sphere) == (number, (year,), kind, "federal")


def test_constitution_aliases(parser):
    assert parser.parse("Constituição").path == "constituicao.md"
    adct = parser.parse("ADCT")
    assert (adct.path, adct.block) == ("constituicao.md", "transitorias")


def test_path_and_unknown(parser):
    assert parser.parse("federal/leis-ordinarias/1991/lei-8245-1991-10-18.md").path.startswith("federal/")
    assert parser.parse("xyz") is None


def test_expand_year(parser):
    assert parser.expand_year("91") == (1991,)
    assert parser.expand_year("02") == (2002, 1902)
    assert parser.expand_year("1943") == (1943,)


@pytest.mark.parametrize(
    ("kind", "directory"),
    [("lei", "leis-ordinarias"), ("Lei Complementar", "leis-complementares"), ("leis-ordinarias", "leis-ordinarias")],
)
def test_kind_directory(parser, kind, directory):
    assert parser.kind_directory(kind) == directory


def test_alias_table(parser):
    table = parser.alias_table()
    assert table["cdc"] == "leis-ordinarias 8078/1990"
    assert table["adct"] == "constituicao.md#transitorias"


def test_congress_resolution_carries_the_organ(parser):
    reference = parser.parse("Resolução nº 1/2002 do Congresso Nacional")
    assert (reference.number, reference.years, reference.kind, reference.organ) == (
        "1",
        (2002,),
        "resolucoes",
        "congresso-nacional",
    )
    assert parser.parse("lei federal 8245 de 1991").organ is None


def test_extra_alias_does_not_override_a_builtin():
    parser = ReferenceParser(current_year=2026, extra_aliases={"cdc": Reference(path="parlamentar/nao.md")})
    assert parser.parse("CDC").number == "8078"
