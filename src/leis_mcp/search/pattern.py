"""Turns a literal query into an accent- and whitespace-tolerant regex."""

import re

from ..text import TextNormalizer


class AccentInsensitivePattern:
    """'acao civil' -> regex matching 'Ação  Civil', 'acao civil', 'AÇÃO\\tcivil'..."""

    _VARIANTS = {
        "a": "aáàâãä",
        "e": "eéèêë",
        "i": "iíìîï",
        "o": "oóòôõö",
        "u": "uúùûü",
        "c": "cç",
        "n": "nñ",
    }
    _WHITESPACE = r"\s+"

    def __init__(self, query: str):
        self.query = query
        self.regex = "".join(map(self._token, " ".join(query.split())))
        self.compiled = re.compile(self.regex, re.IGNORECASE)

    def _token(self, char: str) -> str:
        if char == " ":
            return self._WHITESPACE
        variants = self._VARIANTS.get(TextNormalizer.fold(char))
        return f"[{variants}]" if variants else re.escape(char)
