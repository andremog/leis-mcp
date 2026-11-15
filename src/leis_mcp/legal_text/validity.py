"""What the source says about a norm being in force, and which wording its text holds.

Reads the front matter fields that the source fills differently per origin (`revogada`, `situacao`,
`forca_legal_fonte`, `vigente_fonte`) plus the repeal note some compiled texts carry before the
articles, e.g. `*(Revogada em 30/12/2023 pela Lei nº 14.133, de 1º/4/2021)*`. Missing data stays
`nao_informada`: nothing here assumes a norm is in force.
"""

import contextlib
import json
import re
from typing import Any

from ..models import SourceField, Validity, ValidityState, Wording

State = ValidityState


class NormValidity:
    # Order matters: "sem revogação expressa" must not be read as "revogada".
    _STATUS_PATTERNS = (
        (re.compile(r"sem revoga|n[ãa]o consta revoga|n[ãa]o revogad", re.IGNORECASE), State.NO_REPEAL_RECORDED),
        (re.compile(r"revoga", re.IGNORECASE), State.REVOKED),
        (
            re.compile(
                r"sem efic|efeitos? cessad|anulad|inconstitucional|suspens|caduc|sem efeito|extint|"
                r"n[ãa]o (?:est[áa] )?(?:mais )?(?:vigente|em vigor)",
                re.IGNORECASE,
            ),
            State.NOT_IN_FORCE,
        ),
        (re.compile(r"vigente|em vigor", re.IGNORECASE), State.IN_FORCE),
    )
    _LEGAL_FORCE = {"NotInForce": State.NOT_IN_FORCE, "InForce": State.IN_FORCE}
    _BOOLEAN_FIELDS = {
        SourceField.REVOKED: {True: State.REVOKED, False: State.NO_REPEAL_RECORDED},
        SourceField.IN_FORCE: {True: State.IN_FORCE, False: State.NOT_IN_FORCE},
    }
    _RAW_FIELDS = (
        SourceField.REVOKED,
        SourceField.STATUS,
        SourceField.LEGAL_FORCE,
        SourceField.IN_FORCE,
        SourceField.REPEAL_EVIDENCE,
    )
    _NEGATIVE = (State.REVOKED, State.NOT_IN_FORCE)
    # Precedence when the fields do not conflict: the strongest statement wins.
    _PRECEDENCE = (State.REVOKED, State.NOT_IN_FORCE, State.IN_FORCE, State.OTHER, State.NO_REPEAL_RECORDED)
    _REPEAL_NOTE = re.compile(r"^\W*\(\s*(?P<note>Revogad[ao]\b[^()]*(?:\([^()]*\)[^()]*)*)\)\W*$", re.IGNORECASE)
    _PREAMBLE_LINES = 12  # repeal notes sit right after the title, before the ementa

    @classmethod
    def read(cls, metadata: dict[str, Any], lines: list[str]) -> Validity:
        fields = {str(field): metadata[field] for field in cls._RAW_FIELDS if metadata.get(field) not in (None, "")}
        declared = metadata.get(SourceField.VIGENCIA)
        if declared not in (None, ""):
            # Overlay marker (`nao_verificada`). It is evidence, not a claim that the norm is in force.
            fields[str(SourceField.VIGENCIA)] = declared
        states = {state for field in cls._RAW_FIELDS if (state := cls._state_of(field, metadata.get(field)))}
        repealed_by = cls.repeal_note(lines)
        if repealed_by:
            states.add(State.REVOKED)
        return Validity(cls.combine(states), fields, repealed_by)

    @classmethod
    def combine(cls, states: set[ValidityState]) -> ValidityState:
        if State.CONFLICTING in states or (State.IN_FORCE in states and states.intersection(cls._NEGATIVE)):
            return State.CONFLICTING
        return next((state for state in cls._PRECEDENCE if state in states), State.UNKNOWN)

    @classmethod
    def repeal_note(cls, lines: list[str]) -> str | None:
        """The repeal note of the whole norm, from the lines before its first article."""
        for line in lines[: cls._PREAMBLE_LINES]:
            if line.lstrip("*_ ").startswith("Art"):
                return None
            if match := cls._REPEAL_NOTE.match(line.strip()):
                return match["note"].strip()
        return None

    @classmethod
    def _state_of(cls, field: SourceField, value: Any) -> ValidityState | None:
        if value in (None, ""):
            return None
        if field in cls._BOOLEAN_FIELDS:
            return cls._BOOLEAN_FIELDS[field].get(cls._boolean(value))
        if field == SourceField.STATUS:
            return cls._status(str(value))
        if field == SourceField.LEGAL_FORCE:
            return cls._legal_force(value)
        return None

    @classmethod
    def _status(cls, status: str) -> ValidityState:
        return next((state for pattern, state in cls._STATUS_PATTERNS if pattern.search(status)), State.OTHER)

    @classmethod
    def _legal_force(cls, value: Any) -> ValidityState | None:
        """`'["NotInForce"]'` (JSON text), a list, or a plain string."""
        if isinstance(value, str):
            with contextlib.suppress(ValueError):
                value = json.loads(value)
        values = value if isinstance(value, list) else [value]
        found = {cls._LEGAL_FORCE[item] for item in values if isinstance(item, str) and item in cls._LEGAL_FORCE}
        return cls.combine(found) if found else None

    @staticmethod
    def _boolean(value: Any) -> bool | None:
        if isinstance(value, bool):
            return value
        return {"true": True, "false": False}.get(str(value).strip().lower())


class TextWording:
    # "não consolidado" / "não compilado" are the opposite of a compiled text. The lookbehind is fixed-width.
    _CONSOLIDATED = re.compile(
        r"(?<!não )(?<!nao )(?:compilad|atualizad|reda[çc][ãa]o atual|texto vigente|consolidad)",
        re.IGNORECASE,
    )
    _ORIGINAL = re.compile(r"original", re.IGNORECASE)

    @classmethod
    def read(cls, metadata: dict[str, Any], commit: str) -> Wording:
        version = metadata.get(SourceField.TEXT_VERSION)
        captured_at = metadata.get(SourceField.DOWNLOADED_AT)
        return Wording(
            str(version) if version else None,
            cls._consolidated(str(version or "")),
            str(captured_at) if captured_at else None,
            commit,
        )

    @classmethod
    def _consolidated(cls, version: str) -> bool | None:
        if cls._CONSOLIDATED.search(version):
            return True
        if cls._ORIGINAL.search(version):
            return False
        return None
