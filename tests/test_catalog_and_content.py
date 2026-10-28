import pytest

from leis_mcp.catalog import PathParser
from leis_mcp.content import ContentStore, FrontMatterParser


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        (
            "federal/leis-ordinarias/1991/lei-8245-1991-10-18.md",
            {"sphere": "federal", "kind": "leis-ordinarias", "year": 1991, "number": "8245", "date": "1991-10-18"},
        ),
        (
            "federal/medidas-provisorias/1998/mpv-1697-59-1998-10-26.md",
            {"kind": "medidas-provisorias", "number": "1697", "date": "1998-10-26"},
        ),
        (
            "estaduais/SP/leis-ordinarias/1991/lei-8245-1991-10-18-id123.md",
            {"sphere": "estaduais", "state": "SP", "kind": "leis-ordinarias", "number": "8245", "year": 1991},
        ),
        (
            "municipais/AC/rio-branco/leis-complementares/2020/leis-complementares-81-2020-sapl-561.md",
            {"state": "AC", "city": "rio-branco", "kind": "leis-complementares", "number": "81", "year": 2020},
        ),
        (
            "reguladores/anac/portarias/2026/portaria-n-18.602-de-13-de-janeiro-de-2026-681177890.md",
            {"agency": "anac", "kind": "portarias", "number": "18602", "year": 2026, "date": "2026-01-13"},
        ),
        ("jurisprudencia/stj/sumulas/sumula-221.md", {"agency": "stj", "kind": "sumulas", "number": "221"}),
        ("federal/decretos-nao-numerados/1997/dec-sn-1997-06-16-s3.md", {"number": None, "year": 1997}),
        ("constituicao.md", {"sphere": "federal", "kind": "constituicao", "year": 1988}),
    ],
)
def test_path_parser(path, expected):
    info = PathParser().parse(path)
    assert {field: getattr(info, field) for field in expected} == expected


@pytest.mark.parametrize(
    ("path", "indexable"),
    [
        ("federal/leis-ordinarias/1991/lei-8245-1991-10-18.md", True),
        ("constituicao.md", True),
        ("README.md", False),
        ("estaduais/AC/_falhas.jsonl", False),
        ("jurisprudencia/_lotes/x/lote-0001.json", False),
        ("estaduais/AC/_fonte/auditoria.md", False),
        ("scripts/coleta.md", False),
    ],
)
def test_is_indexable(path, indexable):
    assert PathParser().is_indexable(path) is indexable


def test_front_matter_keeps_dates_as_strings():
    source = FrontMatterParser().parse('---\ntitulo: "X"\ndata: 2003-02-01\nrevogada: false\n---\n# X\n')
    assert source.metadata == {"titulo": "X", "data": "2003-02-01", "revogada": False}
    assert source.body == "# X\n"
    assert source.body_offset == 5


@pytest.mark.parametrize(
    "text",
    [
        "----\ntitulo: X\n---\ncorpo\n",  # not a delimiter line
        "---\ntitulo: X\n---x\ncorpo\n",
        "sem cabeçalho\n",
    ],
)
def test_front_matter_requires_delimiter_lines(text):
    assert FrontMatterParser().parse(text).metadata == {}


def test_front_matter_accepts_crlf():
    source = FrontMatterParser().parse("---\r\ntitulo: X\r\n---\r\ncorpo\r\n")
    assert source.metadata == {"titulo": "X"}
    assert source.body == "corpo\r\n"


def test_constitution_metadata_comes_from_body():
    text = (
        "# Constituição da República\n\n> Fonte: [texto compilado](https://planalto.gov.br/cf.htm), obtido em 2026.\n"
    )
    metadata = FrontMatterParser().parse(text, "constituicao.md").metadata
    assert metadata["titulo"] == "Constituição da República"
    assert metadata["fonte"] == "https://planalto.gov.br/cf.htm"
    assert metadata["revogada"] is False
    assert "compilad" in metadata["versao_texto"]


def test_blob_hash_matches_git():
    # printf 'hello\n' | git hash-object --stdin
    assert ContentStore.blob_hash(b"hello\n") == "ce013625030ba8dba906f756967f9e9ca394464a"
