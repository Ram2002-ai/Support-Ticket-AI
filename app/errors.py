"""Domain-specific exceptions."""
from __future__ import annotations


class AppError(Exception):
    """Base class for expected application errors."""


class ApiError(AppError):
    """An error that maps directly to an HTTP response."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


class IngestionError(AppError):
    """The CSV could not be read or contained no usable rows."""


class DataNotReadyError(AppError):
    """The database has not been created / loaded yet."""


class LLMUnavailableError(AppError):
    """No LLM provider could serve the request (offline, bad key, rate limited)."""


class LLMOutputError(AppError):
    """The LLM replied, but not with usable structured output."""


class UnsafeSQLError(AppError):
    """Generated SQL was rejected by the guard.

    ``repairable`` is True for mistakes the LLM can plausibly fix (unknown column,
    syntax error) and False for unsafe intent (DML/DDL, multiple statements, ...).
    """

    def __init__(self, message: str, repairable: bool = False) -> None:
        super().__init__(message)
        self.repairable = repairable


class QueryExecutionError(AppError):
    """SQLite rejected or failed to run a (validated) query."""


class QueryFailedError(AppError):
    """No valid query could be produced even after one repair attempt."""
