"""Derives norm metadata (sphere, kind, year, number...) from repository paths."""

import re

from ..config import CONSTITUTION_PATH
from ..models import FileNameInfo, PathInfo


class FileNameParser:
    """Extracts number, year and date from a file name stem."""

    # lei-8245-1991-10-18, mpv-1697-59-1998-10-26, leis-ordinarias-1206-1995-sapl-1303
    _STANDARD = re.compile(
        r"^[a-z][a-z0-9-]*?-(?P<number>\d+)(?:-\d+)?-(?P<year>\d{4})(?:-(?P<month>\d\d)-(?P<day>\d\d))?(?=-|$)"
    )
    # portaria-n-18.602-de-13-de-janeiro-de-2026-681177890
    _REGULATOR_NUMBER = re.compile(r"-no?-(?P<number>\d[\d.]*(?:-\d{3})*)-")
    _SPELLED_DATE = re.compile(r"-de-(?P<day>\d{1,2})-de-(?P<month>[a-z]+)-de-(?P<year>\d{4})")
    _FIRST_NUMBER = re.compile(r"\d+")
    _UNNUMBERED = "-sn-"
    _MONTHS = {
        name: number
        for number, name in enumerate(
            ("janeiro", "fevereiro", "marco", "abril", "maio", "junho",
             "julho", "agosto", "setembro", "outubro", "novembro", "dezembro"),
            start=1,
        )
    }  # fmt: skip

    def parse(self, stem: str) -> FileNameInfo:
        return self._standard(stem) or self._loose(stem)

    def _standard(self, stem: str) -> FileNameInfo | None:
        match = self._STANDARD.match(stem)
        if not match:
            return None
        date = f"{match['year']}-{match['month']}-{match['day']}" if match["month"] else None
        return FileNameInfo(str(int(match["number"])), int(match["year"]), date)

    def _loose(self, stem: str) -> FileNameInfo:
        year, date = self._spelled_date(stem)
        return FileNameInfo(self._regulator_number(stem) or self._first_number(stem), year, date)

    def _regulator_number(self, stem: str) -> str | None:
        match = self._REGULATOR_NUMBER.search(stem)
        return str(int(re.sub(r"[.-]", "", match["number"]))) if match else None

    def _first_number(self, stem: str) -> str | None:
        if self._UNNUMBERED in f"-{stem}-":
            return None
        match = self._FIRST_NUMBER.search(stem)
        return str(int(match.group())) if match else None

    def _spelled_date(self, stem: str) -> tuple[int | None, str | None]:
        match = self._SPELLED_DATE.search(stem)
        month = self._MONTHS.get(match["month"]) if match else None
        if not month:
            return None, None
        year = int(match["year"])
        return year, f"{year:04d}-{month:02d}-{int(match['day']):02d}"


class PathParser:
    # Directory components after the sphere, in order.
    _LAYOUTS: dict[str, tuple[str, ...]] = {
        "federal": ("kind", "year"),
        "estaduais": ("state", "kind", "year"),
        "municipais": ("state", "city", "kind", "year"),
        "reguladores": ("agency", "kind", "year"),
        "jurisprudencia": ("agency", "kind", "year"),
        "tratados": ("kind", "year"),
    }
    SPHERES = tuple(_LAYOUTS)

    _CONSTITUTION = PathInfo(
        path=CONSTITUTION_PATH, sphere="federal", kind="constituicao", year=1988, date="1988-10-05"
    )

    def __init__(self) -> None:
        self._file_names = FileNameParser()

    def is_indexable(self, path: str) -> bool:
        """Norm Markdown files only; skips collection metadata (`_falhas`, `_lotes`, `_fonte`...)."""
        if path == CONSTITUTION_PATH:
            return True
        parts = path.split("/")
        return (
            path.endswith(".md")
            and len(parts) > 1
            and parts[0] in self._LAYOUTS
            and not any(part.startswith("_") for part in parts)
        )

    def parse(self, path: str) -> PathInfo:
        if path == CONSTITUTION_PATH:
            return self._CONSTITUTION
        sphere, *directories, file_name = path.split("/")
        fields = dict(zip(self._LAYOUTS[sphere], directories, strict=False))
        year_directory = fields.pop("year", None)
        name = self._file_names.parse(file_name.removesuffix(".md"))
        return PathInfo(
            path=path,
            sphere=sphere,
            year=self._year(year_directory) or name.year,
            number=name.number,
            date=name.date,
            **fields,
        )

    @staticmethod
    def _year(directory: str | None) -> int | None:
        return int(directory) if directory and directory.isdigit() and len(directory) == 4 else None
