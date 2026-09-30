"""CSV -> cleaned DataFrame -> SQLite (idempotent: the table is rebuilt on every start)."""
from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd

from app import db
from app.config import get_settings
from app.constants import CATEGORIES, COLUMNS, PRIORITIES, STATUSES
from app.errors import IngestionError

log = logging.getLogger(__name__)

# The brief uses two spellings (schema preview vs. column table); accept both and more.
ALIASES = {
    "resp_time_hrs": "response_time_hrs",
    "response_time": "response_time_hrs",
    "resol_time_hrs": "resolution_time_hrs",
    "resolution_time": "resolution_time_hrs",
    "cust_rating": "customer_rating",
    "rating": "customer_rating",
}

SCHEMA_SQL = """
DROP TABLE IF EXISTS tickets;
CREATE TABLE tickets (
    ticket_id           TEXT PRIMARY KEY,
    created_at          TEXT NOT NULL,
    category            TEXT NOT NULL,
    priority            TEXT NOT NULL,
    status              TEXT NOT NULL,
    response_time_hrs   REAL,
    resolution_time_hrs REAL,
    agent_id            TEXT,
    customer_rating     INTEGER CHECK (customer_rating BETWEEN 1 AND 5),
    issue_summary       TEXT
);
CREATE INDEX idx_tickets_status   ON tickets(status);
CREATE INDEX idx_tickets_priority ON tickets(priority);
CREATE INDEX idx_tickets_agent    ON tickets(agent_id);
CREATE INDEX idx_tickets_created  ON tickets(created_at);
"""


@dataclass
class IngestionReport:
    rows_read: int = 0
    rows_loaded: int = 0
    duplicates_dropped: int = 0
    invalid_rows_dropped: int = 0
    values_nulled: int = 0
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _normalise_columns(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    names = [str(c).strip().lower().replace(" ", "_") for c in df.columns]
    df.columns = [ALIASES.get(n, n) for n in names]
    return df


def clean(raw: pd.DataFrame) -> tuple[pd.DataFrame, IngestionReport]:
    """Normalise headers, enums, dates and numbers. NULLs stay NULL; bad rows are dropped and counted."""
    report = IngestionReport(rows_read=len(raw))
    df = _normalise_columns(raw)
    missing = [c for c in COLUMNS if c not in df.columns]
    if missing:
        raise IngestionError(f"CSV is missing required columns: {', '.join(missing)}")
    df = df[list(COLUMNS)].copy()

    for col in ("ticket_id", "agent_id", "issue_summary"):
        df[col] = df[col].astype("string").str.strip().replace("", pd.NA)

    df["created_at"] = pd.to_datetime(df["created_at"], errors="coerce")
    for col, allowed in (("category", CATEGORIES), ("priority", PRIORITIES), ("status", STATUSES)):
        lookup = {value.lower(): value for value in allowed}
        df[col] = df[col].astype("string").str.strip().str.lower().map(lookup)

    bad = df["ticket_id"].isna() | df["created_at"].isna() | df[["category", "priority", "status"]].isna().any(axis=1)
    if bad.any():
        report.invalid_rows_dropped = int(bad.sum())
        ids = df.loc[bad, "ticket_id"].astype(object).where(df.loc[bad, "ticket_id"].notna(), "<no id>")
        report.notes.append(
            f"Dropped {report.invalid_rows_dropped} rows with missing/invalid id, date, category, priority or status "
            f"(e.g. {', '.join(map(str, ids.head(5)))})."
        )
    df = df[~bad]

    before = len(df)
    df = df.drop_duplicates("ticket_id", keep="first")
    report.duplicates_dropped = before - len(df)
    if report.duplicates_dropped:
        report.notes.append(f"Dropped {report.duplicates_dropped} duplicate ticket_id rows (kept first).")

    for col in ("response_time_hrs", "resolution_time_hrs"):
        num = pd.to_numeric(df[col], errors="coerce")
        invalid = (df[col].notna() & num.isna()) | (num < 0)
        if invalid.any():
            report.values_nulled += int(invalid.sum())
            report.notes.append(f"Set {int(invalid.sum())} invalid/negative {col} values to NULL.")
        df[col] = num.mask(invalid)

    rating = pd.to_numeric(df["customer_rating"], errors="coerce")
    bad_rating = (df["customer_rating"].notna() & rating.isna()) | (rating.notna() & ~rating.isin([1, 2, 3, 4, 5]))
    if bad_rating.any():
        report.values_nulled += int(bad_rating.sum())
        report.notes.append(f"Set {int(bad_rating.sum())} out-of-range customer_rating values to NULL.")
    df["customer_rating"] = rating.mask(bad_rating)

    df = df.sort_values(["created_at", "ticket_id"]).reset_index(drop=True)
    report.rows_loaded = len(df)
    return df, report


def _float(v: Any) -> float | None:
    return None if pd.isna(v) else float(v)


def _int(v: Any) -> int | None:
    return None if pd.isna(v) else int(v)


def _text(v: Any) -> str | None:
    return None if pd.isna(v) else str(v)


def ingest(csv_path: Path | None = None) -> IngestionReport:
    """Load the CSV into SQLite, replacing any previous table. Returns a cleaning report."""
    settings = get_settings()
    path = Path(csv_path or settings.csv_path)
    if not path.exists():
        raise IngestionError(
            f"CSV not found at {path}. Put support_tickets.csv in the data/ folder, "
            "or run `python -m scripts.generate_sample_data` to create demo data."
        )
    try:
        raw = pd.read_csv(path, encoding="utf-8-sig")
    except (pd.errors.ParserError, pd.errors.EmptyDataError, UnicodeDecodeError) as exc:
        raise IngestionError(f"Could not parse {path.name}: {exc}") from exc

    df, report = clean(raw)
    if df.empty:
        raise IngestionError("The CSV contained no valid ticket rows after cleaning.")

    rows = [
        (
            r.ticket_id,
            r.created_at.strftime("%Y-%m-%d %H:%M:%S"),
            r.category,
            r.priority,
            r.status,
            _float(r.response_time_hrs),
            _float(r.resolution_time_hrs),
            _text(r.agent_id),
            _int(r.customer_rating),
            _text(r.issue_summary),
        )
        for r in df.itertuples(index=False)
    ]
    with db.session(readonly=False) as conn:
        conn.executescript(SCHEMA_SQL)
        conn.executemany("INSERT INTO tickets VALUES (?,?,?,?,?,?,?,?,?,?)", rows)
        conn.commit()

    log.info("Ingestion complete: %s", report.as_dict())
    return report
