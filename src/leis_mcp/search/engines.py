"""Search engines over cached files: ripgrep when installed, pure Python otherwise."""

import json
import logging
import re
import shutil
import subprocess
from collections.abc import Iterator
from itertools import batched, chain, islice
from pathlib import Path
from typing import Any, Protocol

from ..models import Occurrence

log = logging.getLogger(__name__)


class SearchEngine(Protocol):
    name: str

    def search(self, regex: str, files: list[Path], limit: int, per_file: int) -> list[Occurrence]: ...


class RipgrepEngine:
    name = "ripgrep"
    _BATCH = 500  # files per invocation, keeps argv short
    _ERROR_EXIT = 2  # 0 = matches, 1 = no match

    @staticmethod
    def available() -> bool:
        return shutil.which("rg") is not None

    def search(self, regex: str, files: list[Path], limit: int, per_file: int) -> list[Occurrence]:
        runs = (self._run(regex, batch, per_file) for batch in batched(files, self._BATCH))
        return list(islice(chain.from_iterable(runs), limit))

    def _run(self, regex: str, files: tuple[Path, ...], per_file: int) -> Iterator[Occurrence]:
        command = [
            "rg", "--no-config", "--json", "-i", "--max-count", str(per_file), "-e", regex, "--", *map(str, files),
        ]  # fmt: skip
        process = subprocess.run(command, capture_output=True, text=True)
        if process.returncode == self._ERROR_EXIT:
            if not process.stdout:
                raise RuntimeError(f"ripgrep failed: {process.stderr.strip()}")
            log.warning("ripgrep reported errors, keeping partial results: %s", process.stderr.strip())
        events = map(json.loads, process.stdout.splitlines())
        yield from (self._occurrence(event["data"]) for event in events if event["type"] == "match")

    @staticmethod
    def _occurrence(data: dict[str, Any]) -> Occurrence:
        return Occurrence(Path(data["path"]["text"]), data["line_number"], data["lines"].get("text", "").rstrip("\n"))


class PythonEngine:
    name = "python"

    def search(self, regex: str, files: list[Path], limit: int, per_file: int) -> list[Occurrence]:
        pattern = re.compile(regex, re.IGNORECASE)
        scans = (self._scan(pattern, file, per_file) for file in files)
        return list(islice(chain.from_iterable(scans), limit))

    @staticmethod
    def _scan(pattern: re.Pattern[str], file: Path, per_file: int) -> Iterator[Occurrence]:
        with file.open(encoding="utf-8", errors="replace") as handle:
            matches = (
                Occurrence(file, number, line.rstrip("\n"))
                for number, line in enumerate(handle, start=1)
                if pattern.search(line)
            )
            yield from islice(matches, per_file)
