"""Deterministic, explainable anomaly detection (no LLM involved).

Rules
-----
1. unresolved_high_priority_aging : High/Critical, status != Resolved, age at reference time > AGING_HOURS.
2. abnormal_resolution_time       : robust z-score (median/MAD on log1p hours) > 3.5, per category x priority group.
3. abnormal_response_time         : same method on response time.
4. agent_outlier                  : agents whose mean rating is far below / mean resolution far above the team median.
5. data_inconsistency             : resolution < response, rating on an Open ticket, Resolved without resolution time.

Log-transforming hours makes the right-skewed time distributions roughly symmetric, so the
median/MAD z-score does not flag the ordinary long tail as anomalous.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import numpy as np
import pandas as pd

from app import db
from app.config import get_settings
from app.constants import ANOMALY_TYPES

Z_THRESHOLD = 3.5       # robust z-score cut-off for ticket-level time outliers
MIN_GROUP = 8           # minimum tickets for a group to have its own baseline
AGENT_Z = 2.5           # robust z cut-off on agent aggregates
MIN_AGENT_TICKETS = 5   # minimum tickets per agent for agent-level stats
MIN_AGENTS = 4          # minimum agents needed to compare against a team median
SEVERITY_RANK = {"low": 1, "medium": 2, "high": 3}

_LEVELS = (
    (["category", "priority"], "category+priority"),
    (["priority"], "priority"),
    ([], "all tickets"),
)


def load_tickets() -> pd.DataFrame:
    with db.session() as conn:
        return pd.read_sql_query("SELECT * FROM tickets", conn, parse_dates=["created_at"])


def _robust_scale(values: pd.Series) -> tuple[float, float]:
    """Median and a robust std-dev estimate (MAD/0.6745, or 1.2533*mean-abs-dev if MAD is 0)."""
    median = float(values.median())
    deviations = (values - median).abs()
    mad = float(deviations.median())
    if mad > 0:
        return median, mad / 0.6745
    return median, 1.2533 * float(deviations.mean())


def _clean(value: Any) -> str | None:
    return None if value is None or pd.isna(value) else str(value)


def _item(row: pd.Series, kind: str, severity: str, reason: str, value: float, threshold: float | None) -> dict[str, Any]:
    return {
        "ticket_id": _clean(row["ticket_id"]),
        "agent_id": _clean(row["agent_id"]),
        "type": kind,
        "severity": severity,
        "reason": reason,
        "value": round(float(value), 2),
        "threshold": None if threshold is None else round(float(threshold), 2),
        "created_at": row["created_at"].strftime("%Y-%m-%d %H:%M"),
    }


def _aging(df: pd.DataFrame, ref: datetime, hours: float) -> list[dict[str, Any]]:
    age = (pd.Timestamp(ref) - df["created_at"]).dt.total_seconds() / 3600
    mask = df["priority"].isin(["High", "Critical"]) & (df["status"] != "Resolved") & (age > hours)
    out = []
    for idx in df.index[mask]:
        row, hrs = df.loc[idx], float(age.loc[idx])
        severity = "high" if row["priority"] == "Critical" or hrs > 72 else "medium"
        reason = f"{row['priority']} ticket still {row['status']} after {hrs:.0f}h (limit {hours:g}h)."
        out.append(_item(row, "unresolved_high_priority_aging", severity, reason, hrs, hours))
    return out


def _group_stats(df: pd.DataFrame, col: str) -> dict[Any, tuple[float, float, str]]:
    """Per-ticket (median, scale, basis) on log1p(col), using the most specific group with enough data."""
    valid = df[df[col].notna()].copy()
    valid["_v"] = np.log1p(valid[col].astype(float))
    result: dict[Any, tuple[float, float, str]] = {}
    for keys, basis in _LEVELS:
        groups = valid.groupby(keys) if keys else [(None, valid)]
        for _, group in groups:
            pending = [i for i in group.index if i not in result]
            if len(group) < MIN_GROUP or not pending:
                continue
            median, scale = _robust_scale(group["_v"])
            for i in pending:
                result[i] = (median, scale, basis)
    return result


def _time_outliers(df: pd.DataFrame, col: str, kind: str, label: str) -> list[dict[str, Any]]:
    out = []
    for idx, (median, scale, basis) in _group_stats(df, col).items():
        if not scale > 0:
            continue
        row = df.loc[idx]
        z = (np.log1p(float(row[col])) - median) / scale
        if z <= Z_THRESHOLD:
            continue
        threshold = float(np.expm1(median + Z_THRESHOLD * scale))
        typical = float(np.expm1(median))
        severity = "high" if z >= 6 else "medium" if z >= 4.5 else "low"
        reason = (
            f"{label} {float(row[col]):.1f}h is unusually long for {row['category']}/{row['priority']} tickets "
            f"(typical {typical:.1f}h, threshold {threshold:.1f}h, basis: {basis})."
        )
        out.append(_item(row, kind, severity, reason, float(row[col]), threshold))
    return out


def _agents(df: pd.DataFrame) -> list[dict[str, Any]]:
    if df.empty:
        return []
    g = df.groupby("agent_id")
    agg = pd.DataFrame(
        {
            "rating": g["customer_rating"].mean(),
            "rated": g["customer_rating"].count(),
            "res": g["resolution_time_hrs"].mean(),
            "res_n": g["resolution_time_hrs"].count(),
        }
    )
    out = []
    checks = (("rating", "rated", -1, "average customer rating"), ("res", "res_n", 1, "average resolution time (h)"))
    for metric, n_col, direction, label in checks:
        pool = agg.loc[agg[n_col] >= MIN_AGENT_TICKETS, metric].dropna()
        if len(pool) < MIN_AGENTS:
            continue
        median, scale = _robust_scale(pool)
        if not scale > 0:
            continue
        for agent, value in pool.items():
            z = direction * (value - median) / scale
            if z <= AGENT_Z:
                continue
            out.append(
                {
                    "ticket_id": None,
                    "agent_id": str(agent),
                    "type": "agent_outlier",
                    "severity": "high" if z > 4 else "medium",
                    "reason": (
                        f"Agent {agent}: {label} {value:.2f} is far {'below' if direction < 0 else 'above'} "
                        f"the team median {median:.2f}."
                    ),
                    "value": round(float(value), 2),
                    "threshold": round(float(median + direction * AGENT_Z * scale), 2),
                    "created_at": None,
                }
            )
    return out


def _inconsistencies(df: pd.DataFrame) -> list[dict[str, Any]]:
    out = []
    checks = (
        (
            df["resolution_time_hrs"].notna() & df["response_time_hrs"].notna()
            & (df["resolution_time_hrs"] < df["response_time_hrs"]),
            "medium",
            lambda r: f"Resolution time ({r['resolution_time_hrs']:.1f}h) is shorter than response time ({r['response_time_hrs']:.1f}h).",
            "resolution_time_hrs",
        ),
        (
            (df["status"] == "Open") & df["customer_rating"].notna(),
            "low",
            lambda r: f"Open ticket already has a customer rating ({int(r['customer_rating'])}).",
            "customer_rating",
        ),
        (
            (df["status"] == "Resolved") & df["resolution_time_hrs"].isna(),
            "medium",
            lambda r: "Ticket is Resolved but has no resolution time.",
            None,
        ),
    )
    for mask, severity, reason, value_col in checks:
        for idx in df.index[mask]:
            row = df.loc[idx]
            value = row[value_col] if value_col else 0.0
            out.append(_item(row, "data_inconsistency", severity, reason(row), float(value), None))
    return out


def detect(
    days: int | None = None,
    types: list[str] | None = None,
    severity: str | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """Run the anomaly rules. ``days`` restricts results to tickets created in the window ending at the reference time."""
    settings = get_settings()
    df = load_tickets()
    ref = db.reference_time()
    wanted = set(types) if types else set(ANOMALY_TYPES)
    cutoff = (ref - timedelta(days=days)).strftime("%Y-%m-%d %H:%M") if days else None

    items: list[dict[str, Any]] = []
    if not df.empty:
        if "unresolved_high_priority_aging" in wanted:
            items += _aging(df, ref, settings.aging_hours)
        if "abnormal_resolution_time" in wanted:
            items += _time_outliers(df, "resolution_time_hrs", "abnormal_resolution_time", "Resolution time")
        if "abnormal_response_time" in wanted:
            items += _time_outliers(df, "response_time_hrs", "abnormal_response_time", "Response time")
        if "data_inconsistency" in wanted:
            items += _inconsistencies(df)
        if cutoff:
            items = [i for i in items if i["created_at"] >= cutoff]
        if "agent_outlier" in wanted:
            window_df = df[df["created_at"] >= pd.Timestamp(cutoff)] if cutoff else df
            items += _agents(window_df)

    if severity:
        items = [i for i in items if i["severity"] == severity]
    items.sort(key=lambda i: i["created_at"] or "", reverse=True)
    items.sort(key=lambda i: SEVERITY_RANK[i["severity"]], reverse=True)

    by_type: dict[str, int] = {}
    by_severity: dict[str, int] = {}
    for i in items:
        by_type[i["type"]] = by_type.get(i["type"], 0) + 1
        by_severity[i["severity"]] = by_severity.get(i["severity"], 0) + 1

    return {
        "reference_time": db.format_ref(ref),
        "window_days": days,
        "total": len(items),
        "by_type": by_type,
        "by_severity": by_severity,
        "anomalies": items[:limit] if limit else items,
    }
