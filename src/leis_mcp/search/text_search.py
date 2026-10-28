from pathlib import Path

from ..models import Occurrence
from .engines import PythonEngine, RipgrepEngine, SearchEngine
from .pattern import AccentInsensitivePattern


class TextSearch:
    """Accent- and case-insensitive literal search, delegated to the best available engine."""

    def __init__(self, engine: SearchEngine | None = None):
        self._engine: SearchEngine = engine or (RipgrepEngine() if RipgrepEngine.available() else PythonEngine())

    @property
    def engine_name(self) -> str:
        return self._engine.name

    def search(self, query: str, files: list[Path], limit: int = 50, per_file: int = 5) -> list[Occurrence]:
        if not files or not query.strip():
            return []
        return self._engine.search(AccentInsensitivePattern(query).regex, files, limit, per_file)
