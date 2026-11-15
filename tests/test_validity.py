import pytest

from leis_mcp.legal_text import NormValidity, ProvisionNotices, TextWording
from leis_mcp.legal_text.notices import NormNotices
from leis_mcp.models import ValidityState

State = ValidityState


# Values found in the source (federal, DF, MG, SP, SC, regulators).
@pytest.mark.parametrize(
    ("metadata", "state"),
    [
        ({}, State.UNKNOWN),
        ({"revogada": None}, State.UNKNOWN),
        ({"revogada": True}, State.REVOKED),
        ({"revogada": False}, State.NO_REPEAL_RECORDED),
        ({"revogada": "false"}, State.NO_REPEAL_RECORDED),
        ({"revogada": None, "forca_legal_fonte": '["NotInForce"]'}, State.NOT_IN_FORCE),
        ({"revogada": None, "forca_legal_fonte": "[]"}, State.UNKNOWN),
        ({"revogada": False, "situacao": "Sem Revogação Expressa"}, State.NO_REPEAL_RECORDED),
        ({"situacao": "Não consta revogação expressa"}, State.NO_REPEAL_RECORDED),
        ({"revogada": True, "situacao": "Revogada"}, State.REVOKED),
        ({"revogada": False, "situacao": "Vigente"}, State.IN_FORCE),
        ({"situacao": "Em Vigor"}, State.IN_FORCE),
        (
            {"revogada": False, "situacao": "Em vigor por força do art. 2º da Emenda Constitucional nº 32"},
            State.IN_FORCE,
        ),
        ({"revogada": False, "situacao": "Sem Eficácia"}, State.NOT_IN_FORCE),
        ({"revogada": False, "situacao": "Efeitos cessados"}, State.NOT_IN_FORCE),
        ({"revogada": False, "situacao": "Anulado"}, State.NOT_IN_FORCE),
        ({"revogada": False, "situacao": "Inconstitucional"}, State.NOT_IN_FORCE),
        ({"revogada": False, "situacao": "Reeditada"}, State.OTHER),
        ({"revogada": False, "situacao": "Alterado"}, State.OTHER),
        ({"situacao": "Não revogada"}, State.NO_REPEAL_RECORDED),
        ({"situacao": "Não está em vigor"}, State.NOT_IN_FORCE),
        ({"situacao": "Não vigente"}, State.NOT_IN_FORCE),
        ({"forca_legal_fonte": '[["NotInForce"]]'}, State.UNKNOWN),
        ({"forca_legal_fonte": "NotInForce"}, State.NOT_IN_FORCE),
        ({"revogada": False, "vigente_fonte": True}, State.IN_FORCE),
        ({"revogada": True, "vigente_fonte": "false"}, State.REVOKED),
        ({"revogada": False, "situacao": "Vigente", "forca_legal_fonte": '["NotInForce"]'}, State.CONFLICTING),
    ],
)
def test_state_from_front_matter(metadata, state):
    assert NormValidity.read(metadata, []).state == state


def test_raw_fields_are_kept():
    validity = NormValidity.read({"revogada": False, "situacao": "Vigente", "titulo": "x"}, [])
    assert validity.fields == {"revogada": False, "situacao": "Vigente"}


def test_repeal_note_before_the_articles():
    lines = [
        "# Lei nº 8.666 de 21/06/1993",
        "",
        "LEI Nº 8.666, DE 21 DE JUNHO DE 1993\\*",
        "",
        "*(Revogada em 30/12/2023 pela Lei nº 14.133, de 1º/4/2021, com redação dada pela Lei Complementar nº 198)*",
        "",
        "**Art. 1º** Esta Lei estabelece normas gerais.",
    ]
    validity = NormValidity.read({"revogada": True}, lines)
    assert validity.state == State.REVOKED
    assert validity.repealed_by.startswith("Revogada em 30/12/2023 pela Lei nº 14.133")


def test_repeal_note_inside_an_article_is_not_the_norm():
    lines = ["# Lei", "", "**Art. 1º** Texto.", "", "*(Revogada pela Lei nº 1)*"]
    assert NormValidity.read({}, lines).repealed_by is None


def test_combine_keeps_conflicts():
    assert NormValidity.combine({State.CONFLICTING, State.NOT_IN_FORCE}) == State.CONFLICTING
    assert NormValidity.combine({State.NO_REPEAL_RECORDED, State.NOT_IN_FORCE}) == State.NOT_IN_FORCE
    assert NormValidity.combine({State.UNKNOWN, State.REVOKED}) == State.REVOKED


@pytest.mark.parametrize(
    ("version", "consolidated"),
    [
        ("compilada (texto vigente)", True),
        ("redação atual (PDF gerado pela ALMT)", True),
        ("atualizada", True),
        ("original", False),
        ("publicação original", False),
        ("publicação original (texto histórico, não consolidado)", False),
        ("texto anotado da ALESC", None),
        (None, None),
    ],
)
def test_wording(version, consolidated):
    wording = TextWording.read({"versao_texto": version, "baixado_em": "2026-10-13"}, "abc")
    assert (wording.consolidated, wording.captured_at, wording.commit) == (consolidated, "2026-10-13", "abc")


@pytest.mark.parametrize(
    ("text", "revoked", "note"),
    [
        (
            "**Art. 17.** *(Revogado pela Lei nº 13.874, de 20/9/2019)*",
            True,
            "Revogado pela Lei nº 13.874, de 20/9/2019",
        ),
        ("Art. 374*. (Revogado pela Lei nº 10.677, de 22/5/2003)*", True, "Revogado pela Lei nº 10.677, de 22/5/2003"),
        ("I - *(Revogado pela Lei nº 13.146, de 6/7/2015, publicada no DOU de 7/7/2015)*", True, None),
        ("Arts. 89 a 99. *(Revogados pela Lei nº 14.133, de 1º/4/2021)*", True, None),
        ("**§ 1º** *(Revogado pela Lei nº 9.268, de 1º/4/1996)*", True, None),
        ("§ 2º (Vetado).", False, "Vetado"),
        ("Parágrafo único. (VETADO)", False, "VETADO"),
        ("III - (VETADO).", False, "VETADO"),
        ("IV - (Revogado pela Constituição Federal de 1988);", True, "Revogado pela Constituição Federal de 1988"),
        (
            "Art. 11. Texto antigo mantido. *(Revogado pela Lei nº 1, de 2/1/2004)*",
            True,
            "Revogado pela Lei nº 1, de 2/1/2004",
        ),
    ],
)
def test_provision_status_from_annotation(text, revoked, note):
    status = ProvisionNotices.status(text)
    assert status is not None
    assert (status.revoked, status.vetoed) == (revoked, not revoked)
    if note:
        assert status.note == note


@pytest.mark.parametrize(
    "text",
    [
        "**Art. 88.** Revogado o livramento, não poderá ser novamente concedido.",  # Penal Code: not a repeal
        "**Art. 2º** A lei posterior revoga a anterior (art. 2º, § 1º).",
        "**Art. 3º** São absolutamente incapazes:\nI - *(Revogado pela Lei nº 13.146)*",
        "**Art. 3º**\nI - *(Revogado pela Lei nº 13.146)*",
        "**Art. 5º** O prazo é de trinta dias (vetado o § 2º).",
        "**Art. 6º** Ficam mantidos os demais dispositivos (revogados os incisos I a III).",
    ],
)
def test_provision_with_text_has_no_status(text):
    assert ProvisionNotices.status(text) is None


def test_nested_repeals_are_counted():
    text = "**Art. 3º** São incapazes:\nI - *(Revogado pela Lei nº 1)*\nII - *(Revogado pela Lei nº 1)*"
    assert ProvisionNotices.for_text(text, None) == [
        "O trecho contém 2 dispositivo(s) marcado(s) como revogado(s) no texto."
    ]


def test_mention_of_repeal_is_not_a_notice():
    assert ProvisionNotices.for_text("**Art. 88.** Revogado o livramento, não poderá ser concedido.", None) == []


def test_unknown_validity_is_explicit():
    validity = NormValidity.read({}, [])
    wording = TextWording.read({"versao_texto": "compilada (texto vigente)"}, "abc")
    assert NormNotices.for_norm(validity, wording) == [
        "A fonte não informa se a norma está vigente: não presuma que está."
    ]


def test_no_repeal_recorded_has_no_notice():
    validity = NormValidity.read({"revogada": False}, [])
    wording = TextWording.read({"versao_texto": "compilada (texto vigente)"}, "abc")
    assert NormNotices.for_norm(validity, wording) == []


def test_note_on_the_line_after_the_label():
    status = ProvisionNotices.status("**Art. 17.**\n\n*(Revogado pela Lei nº 13.874, de 20/9/2019)*")
    assert status is not None and status.note == "Revogado pela Lei nº 13.874, de 20/9/2019"
