"""Natural language -> SQL pipeline: generate -> validate -> execute (read-only) -> repair once."""
from __future__ import annotations

import logging
import re
import sqlite3
from datetime import datetime
from typing import Any

from app import db
from app.errors import QueryExecutionError, QueryFailedError, UnsafeSQLError
from app.llm import prompts
from app.llm.client import LLMClient
from app.llm.parsing import ask_json
from app.services.sql_guard import validate_sql

log = logging.getLogger(__name__)

_NOW = re.compile(r"'now'", re.IGNORECASE)


def _pin_now(sql: str, ref: datetime) -> str:
    """The data is historical: swap any 'now' the model used for the reference time."""
    return _NOW.sub(f"'{ref:%Y-%m-%d %H:%M:%S}'", sql)


def execute_sql(sql: str) -> tuple[list[str], list[list[Any]]]:
    """Run an already-validated query on a read-only connection."""
    try:
        with db.session() as conn:
            cursor = conn.execute(sql)
            columns = [d[0] for d in cursor.description or []]
            rows = [list(r) for r in cursor.fetchall()]
    except sqlite3.Error as exc:
        raise QueryExecutionError(str(exc)) from exc
    return columns, rows


def _validate_and_run(draft: dict, ref: datetime) -> dict[str, Any]:
    raw = draft.get("sql")
    if not isinstance(raw, str) or not raw.strip():
        raise UnsafeSQLError("The 'sql' field was missing or not a string.", repairable=True)
    sql = validate_sql(_pin_now(raw, ref))
    columns, rows = execute_sql(sql)
    return {
        "sql": sql,
        "explanation": str(draft.get("explanation") or ""),
        "assumptions": str(draft.get("assumptions") or ""),
        "columns": columns,
        "rows": rows,
    }


def _is_fatal(exc: Exception) -> bool:
    return isinstance(exc, UnsafeSQLError) and not exc.repairable


def generate_and_run(llm: LLMClient, question: str, ref: datetime) -> dict[str, Any]:
    """Return ``{sql, explanation, assumptions, columns, rows}``; ``sql`` is None if the LLM says it can't be answered.

    Raises UnsafeSQLError for unsafe intent, QueryFailedError if the repair attempt also fails,
    LLMOutputError / LLMUnavailableError for model problems.
    """
    system = prompts.sql_system_prompt(ref)
    draft = ask_json(llm, system, question)

    if draft.get("sql") is None:
        return {
            "sql": None,
            "explanation": str(draft.get("explanation") or "This question can't be answered from the ticket data."),
            "assumptions": str(draft.get("assumptions") or ""),
            "columns": [],
            "rows": [],
        }

    try:
        return _validate_and_run(draft, ref)
    except (UnsafeSQLError, QueryExecutionError) as exc:
        if _is_fatal(exc):
            raise
        log.info("Query rejected (%s); asking the model to repair it once", exc)
        fixed = ask_json(llm, system, prompts.repair_prompt(question, str(draft.get("sql")), str(exc)))
        if not fixed.get("sql"):
            raise QueryFailedError(f"The model could not produce a valid query: {exc}") from exc
        try:
            return _validate_and_run(fixed, ref)
        except (UnsafeSQLError, QueryExecutionError) as exc2:
            if _is_fatal(exc2):
                raise
            raise QueryFailedError(f"Could not build a valid query for that question ({exc2}). Try rephrasing.") from exc2

