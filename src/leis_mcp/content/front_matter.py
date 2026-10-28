"""Splits a norm's Markdown into YAML front matter and body."""

import re
from typing import Any

import yaml

from .. import messages
from ..config import CONSTITUTION_PATH
from ..models import SourceField, SourceText


class _StringDateLoader(yaml.SafeLoader):
    """SafeLoader that keeps dates as ISO strings instead of `datetime.date`."""

    yaml_constructors = {
        **yaml.SafeLoader.yaml_constructors,
        "tag:yaml.org,2002:timestamp": yaml.SafeLoader.construct_yaml_str,
    }


class FrontMatterParser:
    # A `---` line, the YAML, a closing `---` line; tolerates CRLF and trailing spaces.
    _FRONT_MATTER = re.compile(r"\A---[ \t]*\r?\n(?P<yaml>.*?)\r?\n---[ \t]*(?:\r?\n|\Z)", re.DOTALL)
    # The Constitution has no YAML header: title and source come from the body.
    _BODY_FALLBACKS = {
        SourceField.TITLE: re.compile(r"^#\s+(?P<value>.+)$", re.MULTILINE),
        SourceField.SOURCE_URL: re.compile(r"^>\s*Fonte:\s*\[[^\]]*\]\((?P<value>[^)]+)\)", re.MULTILINE),
    }
    _CONSTITUTION_DEFAULTS = {
        SourceField.KIND: messages.CONSTITUTION_KIND,
        SourceField.DATE: "1988-10-05",
        SourceField.REVOKED: False,
        SourceField.TEXT_VERSION: messages.CONSTITUTION_TEXT_VERSION,
    }

    def parse(self, text: str, path: str | None = None) -> SourceText:
        metadata, body = self._split(text)
        if not metadata:
            metadata = self._from_body(body)
        if path == CONSTITUTION_PATH:
            metadata = {**metadata, **self._CONSTITUTION_DEFAULTS}
        offset = text[: len(text) - len(body)].count("\n")
        return SourceText(metadata, body, offset)

    def _split(self, text: str) -> tuple[dict[str, Any], str]:
        match = self._FRONT_MATTER.match(text)
        if not match:
            return {}, text
        try:
            metadata = yaml.load(match["yaml"], Loader=_StringDateLoader)  # noqa: S506 - SafeLoader subclass
        except yaml.YAMLError:
            return {}, text
        return (metadata, text[match.end() :]) if isinstance(metadata, dict) else ({}, text)

    def _from_body(self, body: str) -> dict[str, Any]:
        return {
            key: match["value"].strip()
            for key, pattern in self._BODY_FALLBACKS.items()
            if (match := pattern.search(body))
        }
