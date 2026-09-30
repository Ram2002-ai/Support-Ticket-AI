"""SQLite access. Queries always run on a read-only connection."""
from __future__ import annotations

import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Iterator

from app.config import get_settings
from app.errors import DataNotReadyError

QUERY_TIMEOUT_S = 5.0


def format_ref(value: datetime) -> str:
    return value.strftime("%Y-%m-%d %H:%M")


@contextmanager
def session(readonly: bool = True) -> Iterator[sqlite3.Connection]:
    """Yield a connection; read-only connections also enforce ``query_only`` and a time limit."""
    path = get_settings().db_path.resolve()
    if readonly:
        if not path.exists():
            raise DataNotReadyError("The ticket database has not been created yet.")
        conn = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)
        conn.execute("PRAGMA query_only = ON")
        deadline = time.monotonic() + QUERY_TIMEOUT_S
        conn.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 50_000)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path)
    try:
        yield conn
    finally:
        conn.close()


def row_count() -> int:
    try:
        with session() as conn:
            return int(conn.execute("SELECT COUNT(*) FROM tickets").fetchone()[0])
    except (sqlite3.Error, DataNotReadyError):
        return 0


def reference_time() -> datetime:
    """The 'now' used for relative-time logic: REFERENCE_TIME, else MAX(created_at), else wall clock."""
    configured = get_settings().reference_time
    if configured:
        return datetime.fromisoformat(configured.strip())
    try:
        with session() as conn:
            latest = conn.execute("SELECT MAX(created_at) FROM tickets").fetchone()[0]
    except (sqlite3.Error, DataNotReadyError):
        latest = None
    return datetime.fromisoformat(latest) if latest else datetime.now().replace(microsecond=0)


def stats() -> dict[str, Any]:
    """Dataset summary for the UI sidebar and /stats."""
    with session() as conn:
        total = int(conn.execute("SELECT COUNT(*) FROM tickets").fetchone()[0])

        def grouped(column: str) -> dict[str, int]:  # column comes from a fixed internal list
            return {k: v for k, v in conn.execute(f"SELECT {column}, COUNT(*) FROM tickets GROUP BY {column}")}

        avg_rating, avg_res = conn.execute(
            "SELECT ROUND(AVG(customer_rating), 2), ROUND(AVG(resolution_time_hrs), 2) FROM tickets"
        ).fetchone()
        return {
            "total": total,
            "by_status": grouped("status"),
            "by_priority": grouped("priority"),
            "by_category": grouped("category"),
            "avg_rating": avg_rating,
            "avg_resolution_hrs": avg_res,
            "reference_time": format_ref(reference_time()),
        }
