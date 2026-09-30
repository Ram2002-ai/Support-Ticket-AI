"""Shared fixtures: a small deterministic ticket dataset loaded into a temp SQLite DB."""
from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from app.config import get_settings
from app.ingestion import ingest


def make_frame() -> pd.DataFrame:
    """83 tickets with known, injected anomalies. Reference time = 2024-03-16 03:00 (latest created_at)."""
    rng = np.random.default_rng(0)
    base = datetime(2024, 3, 1, 9, 0)
    rows: list[dict] = []

    def add(ticket_id: str, hours: float, **overrides) -> None:
        row = dict(
            ticket_id=ticket_id,
            created_at=(base + timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M"),
            category="Billing", priority="Medium", status="Resolved",
            response_time_hrs=1.0, resolution_time_hrs=5.0,
            agent_id="AGT-01", customer_rating=4, issue_summary="Invoice question",
        )
        row.update(overrides)
        rows.append(row)

    for i in range(60):  # normal baseline, spans 354h
        add(
            f"TKT-{i + 1:03d}", 6 * i,
            response_time_hrs=round(float(rng.uniform(0.5, 2.0)), 1),
            resolution_time_hrs=round(float(rng.uniform(3, 8)), 1),
            agent_id=f"AGT-{i % 6 + 1:02d}",
            customer_rating=int(rng.integers(3, 6)),
        )
    for i in range(8):   # a weak agent: every rating is 1
        add(f"TKT-8{i:02d}", 3 * i + 1, agent_id="AGT-07", customer_rating=1)
    for i in range(10):  # Critical Technical tickets that normally take ~30h (normal for their group)
        add(f"TKT-95{i}", 12 * i + 2, category="Technical", priority="Critical",
            resolution_time_hrs=round(28 + 0.4 * i, 1), agent_id="AGT-01")

    add("TKT-901", 100, resolution_time_hrs=400.0)                       # extreme resolution time
    add("TKT-902", 5, priority="Critical", status="Open",                # old unresolved Critical
        resolution_time_hrs=None, customer_rating=None, agent_id="AGT-02")
    add("TKT-903", 5, priority="Low", status="Open",                     # old unresolved Low: must NOT be flagged
        resolution_time_hrs=None, customer_rating=None)
    add("TKT-904", 50, response_time_hrs=5.0, resolution_time_hrs=2.0)   # resolution < response
    add("TKT-905", 60, resolution_time_hrs=30.0)                         # 30h is abnormal for Billing/Medium
    return pd.DataFrame(rows)


@pytest.fixture()
def tickets_env(tmp_path, monkeypatch):
    df = make_frame()
    csv = tmp_path / "tickets.csv"
    df.to_csv(csv, index=False)
    monkeypatch.setenv("CSV_PATH", str(csv))
    monkeypatch.setenv("DB_PATH", str(tmp_path / "tickets.db"))
    monkeypatch.setenv("REFERENCE_TIME", "")
    monkeypatch.setenv("GROQ_API_KEY", "")
    monkeypatch.setenv("LLM_SUMMARY", "false")
    get_settings.cache_clear()
    ingest()
    yield SimpleNamespace(df=df, csv=csv)
    get_settings.cache_clear()
