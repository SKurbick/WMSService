"""Stable PostgreSQL error codes shared by HTTP and background writers."""

import asyncpg

from app.core.exceptions import DomainException


class KizGuardError(asyncpg.PostgresError):
    sqlstate = "P7501"


class KizConflictError(DomainException):
    def __init__(self, message: str, *, diagnostics=None):
        super().__init__(message)
        self.diagnostics = diagnostics


class KizNotFoundError(DomainException):
    pass


def write_conflict_code(exc):
    if isinstance(exc, KizConflictError) or getattr(exc, "sqlstate", None) == "P7501":
        return "KIZ_CONFLICT"
    if getattr(exc, "sqlstate", None) in {"40001", "40P01"}:
        return "CONCURRENT_WRITE_CONFLICT"
    return None


def write_conflict_message(exc):
    if write_conflict_code(exc) == "CONCURRENT_WRITE_CONFLICT":
        return "Конкурентное изменение остатка. Повторите операцию целиком."
    return str(exc)
