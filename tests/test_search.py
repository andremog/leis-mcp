import pytest

from leis_mcp.search import AccentInsensitivePattern, PythonEngine, RipgrepEngine, TextSearch

from .conftest import FIXTURES

ENGINES = [
    PythonEngine(),
    pytest.param(RipgrepEngine(), marks=pytest.mark.skipif(not RipgrepEngine.available(), reason="rg missing")),
]
FILE = FIXTURES / "variacoes.md"


@pytest.mark.parametrize("engine", ENGINES)
def test_ignores_accents_and_case(engine):
    occurrences = TextSearch(engine).search("TRAVESSAO colado", [FILE])
    assert [occurrence.line for occurrence in occurrences] == [26]
    assert occurrences[0].text == "III— terceiro inciso com travessão colado;"


@pytest.mark.parametrize("engine", ENGINES)
def test_limits(engine):
    search = TextSearch(engine)
    assert len(search.search("artigo", [FILE], limit=3, per_file=10)) == 3
    assert len(search.search("artigo", [FILE], limit=50, per_file=2)) == 2
    assert search.search("inexistente-xyz", [FILE]) == []
    assert search.search("   ", [FILE]) == []


def test_pattern_escapes_regex():
    assert AccentInsensitivePattern("art.  5º (a)").regex == r"[aáàâãä]rt\.\s+5º\s+\([aáàâãä]\)"
