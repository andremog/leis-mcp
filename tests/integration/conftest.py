import dataclasses
import os

import pytest

from leis_mcp.config import Settings
from leis_mcp.services import LegislationService


@pytest.fixture(scope="session")
def settings() -> Settings:
    settings = Settings.from_env()
    if not settings.db_path.exists():
        pytest.skip(f"No index at {settings.db_path}: run `uv run leis-mcp index` before the integration tests.")
    return settings


@pytest.fixture(scope="session")
def service(settings) -> LegislationService:
    return LegislationService.from_settings(dataclasses.replace(settings, online=False))  # no API calls


@pytest.fixture(scope="session")
def online_service(settings) -> LegislationService:
    if not os.environ.get("LEIS_MCP_LIVE_TESTS"):
        pytest.skip("Set LEIS_MCP_LIVE_TESTS=1 to query normas.leg.br and the Senado.")
    return LegislationService.from_settings(dataclasses.replace(settings, online_ttl_days=0))
