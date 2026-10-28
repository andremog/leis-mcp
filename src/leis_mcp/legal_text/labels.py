"""Provision labels: canonical keys from user input and from source lines.

Covers the variations found in the corpus: `**Art. 1º**`, `Art. 1o`, `Art. 1.º`, `Art. 10.`,
`Art. 1.000.`, `Art. 4º-A`, `Art. 359-M-A`, `Art. l97.` (OCR), `§ 2°`, `Parágrafo único`,
`III -`, `II–`, `a)`.
"""

import re
from abc import ABC, abstractmethod
from typing import ClassVar

from .. import messages
from ..document import Level
from ..errors import QueryError
from ..text import TextNormalizer


class Label(ABC):
    """A provision label: parses user input and recognizes the label at the start of a source line."""

    _MARKUP = re.compile(r"^\s*[*_]+\s*")
    # Ordinal marker and letter suffix right after a number: "º-A.", "o", ".º"
    _SUFFIX = re.compile(r"^\s*(?:\.?\s*[º°ª]|o(?![a-zà-ú]))?\s*\.?\s*(?P<suffix>(?:-[A-Z](?![a-zà-ú]))*)")

    @classmethod
    def strip_markup(cls, text: str) -> str:
        return cls._MARKUP.sub("", text)

    @classmethod
    def suffix(cls, rest: str) -> str:
        match = cls._SUFFIX.match(rest)
        return match["suffix"].upper() if match else ""

    @staticmethod
    def ordinal(key: str) -> str:
        """'9' -> '9º', '4-A' -> '4º-A', '10' -> '10' (ordinals are used up to nine)."""
        number, _, suffix = key.partition("-")
        if number.isdigit() and int(number) < 10:
            return f"{number}º" + (f"-{suffix}" if suffix else "")
        return key

    @classmethod
    @abstractmethod
    def from_line(cls, line: str) -> str | None:
        """Canonical key of the label opening `line`, or None."""

    @classmethod
    @abstractmethod
    def parse(cls, value: str | int) -> str:
        """Canonical key from user input. Raises `QueryError` when invalid."""


class UnitLabel(Label, ABC):
    """Label of a unit nested in an article."""

    level: ClassVar[Level]


class ArticleLabel(Label):
    _LINE = re.compile(r"^Art(?:igo)?\s*\.?\s*(?P<number>(?:l(?=\d))?\d+(?:\.\d{3})*)(?P<rest>.*)$")
    _USER_PREFIX = re.compile(r"^art(?:igo)?\s*\.?\s*", re.IGNORECASE)
    _USER_NUMBER = re.compile(r"(?:l(?=\d))?\d+(?:\.\d{3})*")
    _LOOSE_SUFFIX = re.compile(r"-\s*([A-Za-z])(?![a-z])")

    @classmethod
    def from_line(cls, line: str) -> str | None:
        match = cls._LINE.match(cls.strip_markup(line))
        return cls._number(match["number"]) + cls.suffix(match["rest"]) if match else None

    @classmethod
    def parse(cls, value: str | int) -> str:
        """'Art. 4º-A' -> '4-A'; '1.000' -> '1000'; '9º' -> '9'; 'l97' -> '197'."""
        text = cls._USER_PREFIX.sub("", cls.strip_markup(str(value).strip()))
        match = cls._USER_NUMBER.match(text)
        if not match:
            raise QueryError(messages.INVALID_ARTICLE.format(value=value))
        rest = text[match.end() :]
        suffix = cls.suffix(rest) or "".join(f"-{letter.upper()}" for letter in cls._LOOSE_SUFFIX.findall(rest))
        return cls._number(match.group()) + suffix

    @staticmethod
    def number_of(key: str) -> int:
        return int(key.partition("-")[0])

    @staticmethod
    def _number(raw: str) -> str:
        return str(int(raw.replace("l", "1").replace(".", "")))


class ParagraphLabel(UnitLabel):
    level = Level.PARAGRAPH
    SOLE = "unico"

    _LINE = re.compile(r"^(?:§\s*(?P<number>\d+)(?P<rest>.*)|(?P<sole>Par[áa]grafo\s+[úu]nico).*)$", re.IGNORECASE)
    _USER_NUMBER = re.compile(r"\d+")
    _USER_SUFFIX = re.compile(r"-\s*([a-z])\b")

    @classmethod
    def from_line(cls, line: str) -> str | None:
        match = cls._LINE.match(cls.strip_markup(line))
        if not match:
            return None
        return cls.SOLE if match["sole"] else str(int(match["number"])) + cls.suffix(match["rest"])

    @classmethod
    def parse(cls, value: str | int) -> str:
        """'§ 2º' -> '2'; 'único' / 'Parágrafo único' -> 'unico'; '1º-A' -> '1-A'."""
        text = TextNormalizer.fold(cls.strip_markup(str(value).strip()))
        if cls.SOLE in text:
            return cls.SOLE
        match = cls._USER_NUMBER.search(text)
        if not match:
            raise QueryError(messages.INVALID_PARAGRAPH.format(value=value))
        suffix = "".join(f"-{letter.upper()}" for letter in cls._USER_SUFFIX.findall(text[match.end() :]))
        return str(int(match.group())) + suffix

    @classmethod
    def display(cls, key: str) -> str:
        return messages.SOLE_PARAGRAPH if key == cls.SOLE else f"§ {cls.ordinal(key)}"


class ClauseLabel(UnitLabel):
    """Inciso: roman numeral followed by a dash."""

    level = Level.CLAUSE

    _LINE = re.compile(r"^(?P<numeral>[IVXLCDM]+)(?P<suffix>(?:-[A-Z])*)\s*[-–—]\s*")
    _USER = re.compile(r"(?:(?P<numeral>[IVXLCDM]+)|(?P<number>\d+))(?P<suffix>(?:-[A-Z])*)")
    _MAX_NUMBER = 3999  # largest value written with standard roman numerals
    _ROMAN = (
        (1000, "M"), (900, "CM"), (500, "D"), (400, "CD"), (100, "C"), (90, "XC"),
        (50, "L"), (40, "XL"), (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I"),
    )  # fmt: skip

    @classmethod
    def from_line(cls, line: str) -> str | None:
        match = cls._LINE.match(cls.strip_markup(line))
        return match["numeral"] + match["suffix"] if match else None

    @classmethod
    def parse(cls, value: str | int) -> str:
        """'III' -> 'III'; 3 -> 'III'; 'iv' -> 'IV'; 'I-A' / '1-A' -> 'I-A'."""
        match = cls._USER.fullmatch(str(value).strip().upper().replace(" ", ""))
        number = int(match["number"]) if match and match["number"] else None
        if not match or (number is not None and not 1 <= number <= cls._MAX_NUMBER):
            raise QueryError(messages.INVALID_CLAUSE.format(value=value))
        numeral = match["numeral"] or cls.to_roman(number)
        return numeral + match["suffix"]

    @classmethod
    def to_roman(cls, number: int) -> str:
        numerals = []
        for value, numeral in cls._ROMAN:
            count, number = divmod(number, value)
            numerals.append(numeral * count)
        return "".join(numerals)


class SubclauseLabel(UnitLabel):
    """Alínea: lowercase letter followed by ')'."""

    level = Level.SUBCLAUSE

    _LINE = re.compile(r"^(?P<letter>[a-z])\)")

    @classmethod
    def from_line(cls, line: str) -> str | None:
        match = cls._LINE.match(cls.strip_markup(line))
        return match["letter"] if match else None

    @classmethod
    def parse(cls, value: str | int) -> str:
        text = str(value).strip().lower().rstrip(")")
        if len(text) != 1 or not text.isalpha():
            raise QueryError(messages.INVALID_SUBCLAUSE.format(value=value))
        return text


UNIT_LABELS: tuple[type[UnitLabel], ...] = (ParagraphLabel, ClauseLabel, SubclauseLabel)


class Citation:
    """Formats a provision citation: 'art. 10, § 1º, II, alínea a'."""

    _PARTS = {
        Level.PARAGRAPH: ParagraphLabel.display,
        Level.CLAUSE: str,
        Level.SUBCLAUSE: lambda key: messages.SUBCLAUSE_CITATION.format(subclause=key),
    }

    @classmethod
    def format(cls, article: str, units: dict[Level, str]) -> str:
        head = messages.ARTICLE_CITATION.format(article=Label.ordinal(article))
        parts = [head, *(cls._PARTS[level](key) for level, key in units.items())]
        return ", ".join(parts)
