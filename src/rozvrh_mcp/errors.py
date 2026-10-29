"""Exception hierarchy. Tools convert these into MCP tool errors with a readable message."""

from __future__ import annotations


class RozvrhError(Exception):
    """Base class for all errors raised by rozvrh-mcp."""


class ConfigError(RozvrhError):
    """Invalid or missing configuration."""


class LoginError(RozvrhError):
    """The page requires a login that is missing, failed, or expired."""


class NotFoundError(RozvrhError):
    """Unknown class / teacher / room identifier or name."""

    def __init__(self, message: str, suggestions: list[str] | None = None) -> None:
        super().__init__(message)
        self.suggestions = suggestions or []

    def __str__(self) -> str:
        base = super().__str__()
        if self.suggestions:
            return f"{base} Did you mean: {', '.join(self.suggestions)}"
        return base


class TimetableUnavailableError(RozvrhError):
    """The page loaded but contains no timetable (feature disabled or empty entity)."""


class UpstreamError(RozvrhError):
    """The school server failed (HTTP 5xx, timeout, connection error)."""


class ParseError(RozvrhError):
    """The page markup did not match any known Bakaláři layout."""
