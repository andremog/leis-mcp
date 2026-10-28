"""Runtime configuration: data directories, source repository URLs and server defaults."""

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Self

REPO_URL = "https://github.com/ctresb/leis"
RAW_BASE_URL = "https://raw.githubusercontent.com/ctresb/leis"
DEFAULT_BRANCH = "main"
DEFAULT_HOME = Path.home() / ".cache" / "leis-mcp"
CONSTITUTION_PATH = "constituicao.md"

Transport = Literal["stdio", "streamable-http"]
TRANSPORTS: tuple[Transport, ...] = ("stdio", "streamable-http")
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000


@dataclass(frozen=True, slots=True)
class Settings:
    home: Path
    repo_url: str = REPO_URL
    raw_base_url: str = RAW_BASE_URL
    branch: str = DEFAULT_BRANCH

    @property
    def repo_dir(self) -> Path:
        return self.home / "repo"

    @property
    def db_path(self) -> Path:
        return self.home / "index.sqlite"

    @property
    def cache_dir(self) -> Path:
        return self.home / "cache"

    @property
    def blob_base_url(self) -> str:
        """Base of GitHub file links: `<blob_base_url>/<commit>/<path>`."""
        return f"{self.repo_url}/blob"

    @classmethod
    def from_env(cls) -> Self:
        return cls(
            home=Path(os.environ.get("LEIS_MCP_HOME", DEFAULT_HOME)).expanduser(),
            repo_url=os.environ.get("LEIS_MCP_REPO_URL", REPO_URL),
            raw_base_url=os.environ.get("LEIS_MCP_RAW_BASE", RAW_BASE_URL),
            branch=os.environ.get("LEIS_MCP_BRANCH", DEFAULT_BRANCH),
        )
