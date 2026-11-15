"""Provision addresses in the online sources.

normas.leg.br (LexML): `art3_cpt_inc1`, `art4_par1u`, `art206_par1_inc2_ali1`, `art44-1` (art. 44-A).
Fragments of inserted provisions carry the inserting law: `art44_cpt_inc4,lei-10825-2003-12-22`.

Senado: `Art. 15, caput, Item 3`, `Art. 24, § 2, Inciso 1`, `Art. 4, Parágrafo Único, Alínea a`.
Both become the same key: article, paragraph (`caput`, `unico`, `2`), clause number, subclause letter.
"""

import re
from typing import NamedTuple

from ..document import Level
from ..errors import QueryError
from ..legal_text.labels import ArticleLabel, ClauseLabel, Label, ParagraphLabel


class Address(NamedTuple):
    article: str
    paragraph: str = ""  # "" for the whole article, "caput", "unico" or a paragraph key
    clause: str = ""  # arabic number of the inciso
    subclause: str = ""  # letter

    @classmethod
    def requested(cls, article: str, units: tuple[tuple[int, str], ...]) -> "Address":
        keys = {Level(level): key for level, key in units}
        clause = keys.get(Level.CLAUSE, "")
        paragraph = keys.get(Level.PARAGRAPH) or ("caput" if clause or keys.get(Level.SUBCLAUSE) else "")
        return cls(article, paragraph, str(Roman.value(clause)) if clause else "", keys.get(Level.SUBCLAUSE, ""))

    @property
    def is_article(self) -> bool:
        return not (self.paragraph or self.clause or self.subclause)

    def contains(self, other: "Address") -> bool:
        """Whether `other` is this provision or nested in it."""
        own = [part for part in self if part]
        return list(other)[: len(own)] == own

    def ancestors(self) -> list["Address"]:
        parts = list(self)
        filled = [index for index, part in enumerate(parts) if part]
        return [type(self)(*parts[: index + 1], *[""] * (3 - index)) for index in filled[:-1]]

    def describe(self) -> str:
        parts = [f"art. {Label.ordinal(self.article)}"]
        if self.paragraph and self.paragraph != "caput":
            parts.append(ParagraphLabel.display(self.paragraph))
        if self.clause:
            head, *suffixes = self.clause.split("-")
            parts.append(ClauseLabel.to_roman(int(head)) + "".join(f"-{Suffix.letter(item)}" for item in suffixes))
        if self.subclause:
            parts.append(f"alínea {self.subclause}")
        return ", ".join(parts)


class Roman:
    _VALUES = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}

    @classmethod
    def value(cls, key: str) -> str:
        """'IV' -> '4', 'I-A' -> '1-1' (letter suffixes become numbers, as in LexML)."""
        numeral, *suffixes = key.split("-")
        total = 0
        for current, following in zip(numeral, [*numeral[1:], ""], strict=True):
            value = cls._VALUES[current]
            total += -value if following and cls._VALUES[following] > value else value
        return "-".join([str(total), *(Suffix.number(letter) for letter in suffixes)])


class Suffix:
    @staticmethod
    def number(letter: str) -> str:
        return str(ord(letter.upper()) - ord("A") + 1)

    @staticmethod
    def letter(number: str) -> str:
        return chr(ord("A") + int(number) - 1)


class LexmlAddress:
    _TOKEN = re.compile(r"^(?P<kind>art|cpt|par|inc|ali)(?P<number>\d+(?:-\d+)*)?(?P<sole>u)?$")

    @classmethod
    def parse(cls, fragment: str) -> Address | None:
        """Address of a provision fragment, or None for structure (parts, titles, chapters)."""
        tokens = [token.split(",", 1)[0] for token in fragment.split("_")]
        parsed = [cls._TOKEN.match(token) for token in tokens]
        if not parsed or not all(parsed) or parsed[0]["kind"] != "art":
            return None
        values = {"art": "", "par": "", "inc": "", "ali": ""}
        for match in parsed:
            values[match["kind"] if match["kind"] != "cpt" else "par"] = cls._value(match)
        return Address(values["art"], values["par"], values["inc"], values["ali"])

    @staticmethod
    def _value(match: re.Match[str]) -> str:
        kind, number = match["kind"], match["number"] or ""
        if kind == "cpt":
            return "caput"
        if kind == "par" and match["sole"]:
            return ParagraphLabel.SOLE
        if kind == "ali":
            return chr(ord("a") + int(number) - 1)
        head, *suffixes = number.split("-")
        if kind == "inc":
            return "-".join([head, *suffixes])
        return head + "".join(f"-{Suffix.letter(suffix)}" for suffix in suffixes)


class SenadoAddress:
    _ARTICLE = re.compile(r"^Art\.\s*(?P<article>.+)$", re.IGNORECASE)
    _PARTS = (
        (re.compile(r"^caput$", re.IGNORECASE), "paragraph", lambda match: "caput"),
        (re.compile(r"^Par[áa]grafo [ÚúUu]nico$", re.IGNORECASE), "paragraph", lambda match: ParagraphLabel.SOLE),
        (re.compile(r"^§\s*(?P<value>\S+)$"), "paragraph", lambda match: ParagraphLabel.parse(match["value"])),
        (
            re.compile(r"^(?:Inciso|Item)\s+(?P<value>\S+)$", re.IGNORECASE),
            "clause",
            lambda match: match["value"] if match["value"][0].isdigit() else Roman.value(match["value"].upper()),
        ),
        (re.compile(r"^Al[íi]nea\s+(?P<value>[a-z])$", re.IGNORECASE), "subclause", lambda match: match["value"]),
    )

    @classmethod
    def parse(cls, name: str) -> Address | None:
        """'Art. 15, caput, Item 3 [Lei nº 8.666 de 21/06/1993]' -> Address('15', 'caput', '3')."""
        head, *rest = [part.strip() for part in name.split(" [", 1)[0].split(",")]
        article = cls._ARTICLE.match(head)
        if not article:
            return None
        try:
            values = {"article": ArticleLabel.parse(article["article"])}
            for part in rest:
                values.update(cls._part(part))
        except (QueryError, ValueError, KeyError):
            return None
        if not values.get("paragraph") and (values.get("clause") or values.get("subclause")):
            values["paragraph"] = "caput"  # 'Art. 15, Item 3' is the caput's clause, as in LexML
        return Address(**values)

    @classmethod
    def _part(cls, text: str) -> dict[str, str]:
        for pattern, field, value in cls._PARTS:
            if match := pattern.match(text):
                return {field: value(match).lower() if field == "subclause" else value(match)}
        raise ValueError(text)
