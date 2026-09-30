import sqlite3

import pytest

from app.config import get_settings
from app.errors import IngestionError
from app.ingestion import ingest

MESSY_CSV = """ticket_id,created_at,category,priority,status,resp_time_hrs,resol_time_hrs,agent_id,cust_rating,issue_summary
TKT-001,2024-01-03 09:12,billing ,High,resolved,0.5,2.3,AGT-04,4,Incorrect charge
TKT-001,2024-01-03 09:12,Billing,High,Resolved,0.5,2.3,AGT-04,4,duplicate row
TKT-002,2024-01-03 11:45,Technical,Critical,Escalated,1.2,,AGT-07,,Login failure
TKT-003,not-a-date,General,Low,Resolved,3.1,5.0,AGT-02,5,bad date
TKT-004,2024-01-04 14:22,Weird,High,Resolved,0.8,4.7,AGT-04,3,bad category
TKT-005,2024-01-05 10:05,Billing,Medium,Open,2.0,,AGT-09,,Refund not processed
TKT-006,2024-01-06 10:05,General,Low,Resolved,1.0,-3,AGT-01,9,negative time and bad rating
"""


@pytest.fixture()
def messy(tmp_path, monkeypatch):
    csv = tmp_path / "messy.csv"
    csv.write_text(MESSY_CSV, encoding="utf-8")
    monkeypatch.setenv("CSV_PATH", str(csv))
    monkeypatch.setenv("DB_PATH", str(tmp_path / "t.db"))
    get_settings.cache_clear()
    yield ingest()
    get_settings.cache_clear()


def _rows(sql):
    conn = sqlite3.connect(get_settings().db_path)
    try:
        return conn.execute(sql).fetchall()
    finally:
        conn.close()


def test_report_counts(messy):
    assert messy.rows_read == 7
    assert messy.duplicates_dropped == 1
    assert messy.invalid_rows_dropped == 2
    assert messy.rows_loaded == 4
    assert messy.values_nulled == 2  # negative resolution + out-of-range rating


def test_short_column_names_are_normalised(messy):
    cols = [r[1] for r in _rows("PRAGMA table_info(tickets)")]
    assert "response_time_hrs" in cols and "resolution_time_hrs" in cols and "customer_rating" in cols


def test_enums_are_canonicalised_and_nulls_preserved(messy):
    assert _rows("SELECT category, status FROM tickets WHERE ticket_id='TKT-001'") == [("Billing", "Resolved")]
    assert _rows("SELECT resolution_time_hrs, customer_rating FROM tickets WHERE ticket_id='TKT-002'") == [(None, None)]
    assert _rows("SELECT resolution_time_hrs, customer_rating FROM tickets WHERE ticket_id='TKT-006'") == [(None, None)]


def test_ingest_is_idempotent(messy):
    ingest()
    assert _rows("SELECT COUNT(*) FROM tickets") == [(4,)]


def test_missing_csv_gives_clear_error(tmp_path, monkeypatch):
    monkeypatch.setenv("CSV_PATH", str(tmp_path / "nope.csv"))
    monkeypatch.setenv("DB_PATH", str(tmp_path / "t.db"))
    get_settings.cache_clear()
    with pytest.raises(IngestionError, match="not found"):
        ingest()
    get_settings.cache_clear()


def test_missing_columns_rejected(tmp_path, monkeypatch):
    csv = tmp_path / "bad.csv"
    csv.write_text("ticket_id,created_at\nTKT-1,2024-01-01 00:00\n", encoding="utf-8")
    monkeypatch.setenv("CSV_PATH", str(csv))
    monkeypatch.setenv("DB_PATH", str(tmp_path / "t.db"))
    get_settings.cache_clear()
    with pytest.raises(IngestionError, match="missing required columns"):
        ingest()
    get_settings.cache_clear()
