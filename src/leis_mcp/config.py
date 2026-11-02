"""Runtime configuration: data directories, source repository URLs and server defaults."""

import os
from dataclasses import dataclass, field
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


def default_host() -> str:
    """`LEIS_MCP_HOST` (the Docker image sets 0.0.0.0) or loopback."""
    return os.environ.get("LEIS_MCP_HOST") or DEFAULT_HOST


def default_port() -> int:
    """`LEIS_MCP_PORT`, then `PORT` (set by Railway, Render, Heroku...), then 8000."""
    return int(os.environ.get("LEIS_MCP_PORT") or os.environ.get("PORT") or DEFAULT_PORT)


@dataclass(frozen=True, slots=True)
class Settings:
    home: Path
    repo_url: str = REPO_URL
    raw_base_url: str = RAW_BASE_URL
    branch: str = DEFAULT_BRANCH
    # HTTP transport only: when set, requests must carry `Authorization: Bearer <api_key>`. Never in repr/logs.
    api_key: str | None = field(default=None, repr=False)
    # HTTP transport only: prefetch the aliased laws in the background at startup.
    warm_on_start: bool = False

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
            api_key=os.environ.get("LEIS_MCP_API_KEY") or None,
            warm_on_start=os.environ.get("LEIS_MCP_WARM_ON_START", "").lower() in ("1", "true", "yes"),
        )
