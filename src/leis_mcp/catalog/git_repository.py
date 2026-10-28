"""Read-only git operations on a blobless, shallow clone of the source repository."""

import logging
import os
import shutil
import subprocess
from collections.abc import Iterator
from functools import partial
from pathlib import Path
from typing import IO

from ..config import Settings
from ..errors import GitError

log = logging.getLogger(__name__)


class GitRepository:
    _READ_CHUNK = 1 << 20
    _NETWORK_TIMEOUT = 900  # seconds; the first clone downloads ~250 MB of trees
    _LOCAL_TIMEOUT = 120

    def __init__(self, settings: Settings):
        self._settings = settings
        self._dir = settings.repo_dir

    @property
    def exists(self) -> bool:
        return (self._dir / ".git").exists()

    def clone(self) -> None:
        """Shallow clone with trees only: lists every path without downloading content.

        Clones into a staging directory and renames it, so an interrupted clone leaves no half-made repo.
        """
        if self.exists:
            return
        staging = self._dir.with_name(f"{self._dir.name}.partial")
        shutil.rmtree(staging, ignore_errors=True)
        self._settings.home.mkdir(parents=True, exist_ok=True)
        log.info("Cloning %s (blobless)...", self._settings.repo_url)
        self._run(
            "clone", "--depth", "1", "--filter=blob:none", "--sparse", "--branch", self._settings.branch,
            self._settings.repo_url, str(staging),
            timeout=self._NETWORK_TIMEOUT,
            env={**os.environ, "GIT_LFS_SKIP_SMUDGE": "1"},
        )  # fmt: skip
        staging.rename(self._dir)

    def head(self) -> str:
        return self._run("rev-parse", "HEAD", cwd=self._dir).decode().strip()

    def fetch_latest(self) -> str:
        """Fetches the branch tip and moves HEAD to it. Returns the new commit."""
        branch = self._settings.branch
        self._run(
            "fetch",
            "--depth",
            "1",
            "--filter=blob:none",
            "origin",
            branch,
            cwd=self._dir,
            timeout=self._NETWORK_TIMEOUT,
        )
        commit = self._run("rev-parse", "FETCH_HEAD", cwd=self._dir).decode().strip()
        self._run("update-ref", "HEAD", commit, cwd=self._dir)
        return commit

    def list_tree(self, commit: str) -> Iterator[tuple[str, str]]:
        """Yields (path, blob sha) for every file in `commit`, offline. Skips paths that are not UTF-8."""
        command = ["git", "ls-tree", "-r", "-z", "--full-tree", commit]
        with subprocess.Popen(command, cwd=self._dir, stdout=subprocess.PIPE, stderr=subprocess.PIPE) as process:
            assert process.stdout is not None and process.stderr is not None
            entries = map(self._blob_entry, self._null_separated(process.stdout))
            yield from filter(None, entries)
            errors = process.stderr.read().decode(errors="replace")
        if process.returncode:
            raise GitError("ls-tree", errors)

    def read_blob(self, sha: str) -> bytes:
        """Reads a blob through git (lazy fetch from the partial clone: slow on first access)."""
        return self._run("cat-file", "blob", sha, cwd=self._dir, timeout=self._NETWORK_TIMEOUT)

    def _run(
        self,
        command: str,
        *args: str,
        cwd: Path | None = None,
        timeout: float = _LOCAL_TIMEOUT,
        env: dict[str, str] | None = None,
    ) -> bytes:
        """Runs `git <command> <args>`; failures become `GitError` carrying git's stderr."""
        try:
            return subprocess.run(
                ["git", command, *args], cwd=cwd, check=True, capture_output=True, timeout=timeout, env=env
            ).stdout
        except subprocess.CalledProcessError as exc:
            raise GitError(command, exc.stderr.decode(errors="replace")) from exc
        except subprocess.TimeoutExpired as exc:
            raise GitError(command, f"timeout ({timeout:.0f} s)") from exc

    @staticmethod
    def _blob_entry(record: bytes) -> tuple[str, str] | None:
        info, _, raw_path = record.partition(b"\t")
        _mode, object_type, sha = info.split(b" ")
        if object_type != b"blob":
            return None
        try:
            return raw_path.decode("utf-8"), sha.decode()
        except UnicodeDecodeError:
            log.warning("Skipping non UTF-8 path: %r", raw_path)
            return None

    @classmethod
    def _null_separated(cls, stream: IO[bytes]) -> Iterator[bytes]:
        pending = b""
        for chunk in iter(partial(stream.read, cls._READ_CHUNK), b""):
            *records, pending = (pending + chunk).split(b"\0")
            yield from records
