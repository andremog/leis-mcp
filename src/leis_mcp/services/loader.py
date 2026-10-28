import threading
from collections import OrderedDict

from ..content.front_matter import FrontMatterParser
from ..content.store import ContentStore
from ..legal_text.document_parser import DocumentParser
from ..models import LoadedNorm, Norm


class NormLoader:
    """Reads, splits and parses norms, keeping the most recently used ones in memory.

    Thread-safe. Two threads missing the same norm may both parse it; the result is identical.
    """

    def __init__(self, store: ContentStore, cache_size: int = 32):
        self._store = store
        self._front_matter = FrontMatterParser()
        self._documents = DocumentParser()
        self._cache: OrderedDict[Norm, LoadedNorm] = OrderedDict()
        self._cache_size = cache_size
        self._lock = threading.Lock()

    def load(self, norm: Norm) -> LoadedNorm:
        with self._lock:
            if cached := self._cache.get(norm):
                self._cache.move_to_end(norm)
                return cached
        loaded = self._parse(norm)
        with self._lock:
            self._cache[norm] = loaded
            if len(self._cache) > self._cache_size:
                self._cache.popitem(last=False)
        return loaded

    def _parse(self, norm: Norm) -> LoadedNorm:
        source = self._front_matter.parse(self._store.read(norm), norm.path)
        return LoadedNorm(norm, source.metadata, self._documents.parse(source.body), source.body_offset)
