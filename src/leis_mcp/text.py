"""Text normalization shared by reference parsing, block matching and search."""

import re
import unicodedata


class TextNormalizer:
    _NON_REFERENCE_CHARS = re.compile(r"[^a-z0-9./ -]")
    _SPACES = re.compile(r"\s+")

    @staticmethod
    def strip_accents(text: str) -> str:
        decomposed = unicodedata.normalize("NFD", text)
        return "".join(char for char in decomposed if unicodedata.category(char) != "Mn")

    @classmethod
    def fold(cls, text: str) -> str:
        """Lowercase without accents: 'Transitórias' -> 'transitorias'."""
        return cls.strip_accents(text).lower()

    @classmethod
    def normalize(cls, text: str) -> str:
        """Folded text keeping only characters meaningful in a norm reference."""
        cleaned = cls._NON_REFERENCE_CHARS.sub(" ", cls.fold(text))
        return cls._SPACES.sub(" ", cleaned).strip()
