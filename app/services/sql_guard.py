"""Validate LLM-generated SQL before it touches the database.

Defence in depth: this guard (AST-based, via sqlglot) + a read-only SQLite connection
+ ``PRAGMA query_only`` + a query time limit.
"""
from __future__ import annotations

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError

from app.config import get_settings
from app.constants import COLUMNS, TABLE
from app.errors import UnsafeSQLError

ALLOWED_TABLES = {TABLE}
ALLOWED_COLUMNS = set(COLUMNS)

# Functions the model may legitimately need on SQLite. Anything unknown (load_extension,
# readfile, randomblob, pragma_* table functions, ...) is rejected.
ALLOWED_ANONYMOUS_FUNCTIONS = {
    "strftime", "julianday", "date", "datetime", "time", "unixepoch", "ifnull", "iif", "printf",
    "group_concat", "total", "instr", "substr", "round", "abs", "length", "lower", "upper",
    "trim", "ltrim", "rtrim", "replace", "nullif", "coalesce", "typeof",
}

_FORBIDDEN = tuple(
    getattr(exp, name)
    for name in (
        "Insert", "Update", "Delete", "Drop", "Create", "Alter", "Command", "Pragma", "Attach",
        "Detach", "Merge", "Transaction", "Commit", "Rollback", "Set",
    )
    if hasattr(exp, name)
)


def validate_sql(sql: str, limit: int | None = None) -> str:
    """Return a safe, LIMIT-capped version of ``sql`` or raise ``UnsafeSQLError``."""
    cap = limit or get_settings().default_row_limit
    text = (sql or "").strip().rstrip(";").strip()

    if not text:
        raise UnsafeSQLError("The query is empty.", repairable=True)
    if "\x00" in text:
        raise UnsafeSQLError("The query contains invalid characters.")
    if "--" in text or "/*" in text:
        raise UnsafeSQLError("SQL comments are not allowed.", repairable=True)

    try:
        statements = [s for s in sqlglot.parse(text, read="sqlite") if s is not None]
    except SqlglotError as exc:
        raise UnsafeSQLError(f"The query could not be parsed: {str(exc)[:150]}", repairable=True) from exc

    if len(statements) != 1:
        raise UnsafeSQLError("Only a single SQL statement is allowed.")
    tree = statements[0]
    if not isinstance(tree, (exp.Select, exp.Union)):
        raise UnsafeSQLError("Only read-only SELECT queries are allowed.")
    if tree.find(*_FORBIDDEN):
        raise UnsafeSQLError("Data-modifying or administrative statements are not allowed.")

    for fn in tree.find_all(exp.Anonymous):
        if str(fn.this).lower() not in ALLOWED_ANONYMOUS_FUNCTIONS:
            raise UnsafeSQLError(f"Function '{fn.this}' is not allowed.", repairable=True)

    cte_names = {c.alias_or_name.lower() for c in tree.find_all(exp.CTE)}
    for table in tree.find_all(exp.Table):
        name = (table.name or "").lower()
        if table.args.get("db") or table.args.get("catalog"):
            raise UnsafeSQLError("Schema-qualified tables are not allowed.")
        if name not in ALLOWED_TABLES and name not in cte_names:
            raise UnsafeSQLError(f"Unknown table '{table.name}'. The only table is '{TABLE}'.", repairable=True)

    aliases = {a.alias.lower() for a in tree.find_all(exp.Alias) if a.alias} | cte_names
    for col in tree.find_all(exp.Column):
        name = col.name.lower()
        if name in ("", "*") or name in ALLOWED_COLUMNS or name in aliases:
            continue
        raise UnsafeSQLError(
            f"Unknown column '{col.name}'. Allowed columns: {', '.join(COLUMNS)}. "
            "String values must use single quotes.",
            repairable=True,
        )

    limit_node = tree.args.get("limit")
    if limit_node is None:
        return f"{text} LIMIT {cap}"
    try:
        current = int(limit_node.expression.name)
    except (AttributeError, ValueError):
        current = None
    if current is None or current > cap:
        return tree.limit(cap).sql(dialect="sqlite")
    return text
