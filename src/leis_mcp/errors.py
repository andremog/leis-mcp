"""Exceptions. Messages are user-facing (pt-BR) and come from `messages`."""

from typing import Self

from . import messages


class LeisMcpError(Exception):
    """Base class for errors raised by this package."""


class IndexMissingError(LeisMcpError):
    pass


class IndexPreparingError(LeisMcpError):
    """The index is still being prepared in the background."""


class SnapshotError(LeisMcpError):
    """The prebuilt index could not be downloaded or failed verification."""


class ContentUnavailableError(LeisMcpError):
    pass


class GitError(LeisMcpError):
    def __init__(self, command: str, details: str):
        super().__init__(messages.GIT_FAILED.format(command=command, details=details.strip() or "?"))


class QueryError(LeisMcpError, ValueError):
    """Invalid, unknown or ambiguous user input."""


class ProvisionNotFoundError(QueryError):
    def __init__(self, message: str, suggestions: list[str] | None = None):
        super().__init__(message)
        self.message = message
        self.suggestions = suggestions or []

    def __str__(self) -> str:
        if not self.suggestions:
            return self.message
        return self.message + messages.SUGGESTIONS.format(items=", ".join(self.suggestions))

    def in_norm(self, title: str) -> Self:
        return type(self)(self.message + messages.IN_NORM.format(title=title), self.suggestions)
