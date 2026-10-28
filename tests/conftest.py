from pathlib import Path

import pytest

from leis_mcp.content import FrontMatterParser
from leis_mcp.document import LegalDocument
from leis_mcp.legal_text import DocumentParser, ProvisionExtractor

FIXTURES = Path(__file__).parent / "fixtures"


def load_document(name: str) -> LegalDocument:
    source = FrontMatterParser().parse((FIXTURES / name).read_text("utf-8"))
    return DocumentParser().parse(source.body)


@pytest.fixture(scope="session")
def variations() -> LegalDocument:
    return load_document("variacoes.md")


@pytest.fixture(scope="session")
def extractor(variations) -> ProvisionExtractor:
    return ProvisionExtractor(variations)


@pytest.fixture(scope="session")
def blocks() -> LegalDocument:
    return load_document("blocos.md")
