"""Turn query results / anomaly reports into readable answers (done in code, not by the LLM)."""
from __future__ import annotations

import logging
from typing import Any

from app.errors import AppError
from app.llm.client import LLMClient
from app.llm.parsing import ask_json
from app.llm.prompts import SUMMARY_SYSTEM

log = logging.getLogger(__name__)


def _fmt(value: Any) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:,.2f}".rstrip("0").rstrip(".")
    return str(value)


def _label(column: str) -> str:
    return column.replace("_", " ")


def format_answer(columns: list[str], rows: list[list[Any]]) -> str:
    if not rows:
        return "No tickets match your question."
    first = rows[0]
    if all(v is None for v in first):
        return "No matching data: the calculation returned no value."
    if len(rows) == 1 and len(columns) == 1:
        return f"{_label(columns[0]).capitalize()}: {_fmt(first[0])}"
    pairs = "; ".join(f"{_label(c)}: {_fmt(v)}" for c, v in zip(columns, first))
    if len(rows) == 1:
        return pairs
    return f"Found {len(rows)} rows. First result: {pairs}."


def summarize(llm: LLMClient, question: str, columns: list[str], rows: list[list[Any]]) -> str | None:
    """Optional LLM sentence (LLM_SUMMARY=true). Returns None on any failure so the code-made answer is used."""
    payload = f"Question: {question}\nColumns: {columns}\nRows (up to 20): {rows[:20]}\nTotal rows: {len(rows)}"
    try:
        answer = ask_json(llm, SUMMARY_SYSTEM, payload).get("answer")
    except AppError as exc:
        log.warning("Summary skipped: %s", exc)
        return None
    return answer.strip() if isinstance(answer, str) and answer.strip() else None


def anomaly_summary(report: dict[str, Any]) -> str:
    window = f"the last {report['window_days']} days up to {report['reference_time']}" if report["window_days"] else "all data"
    if report["total"] == 0:
        return f"No anomalies found for {window}."
    by_type = ", ".join(f"{k.replace('_', ' ')}: {v}" for k, v in report["by_type"].items())
    sev = report["by_severity"]
    lines = [
        f"Found {report['total']} anomalies in {window} "
        f"(high: {sev.get('high', 0)}, medium: {sev.get('medium', 0)}, low: {sev.get('low', 0)}).",
        f"By type: {by_type}.",
        "Top items:",
    ]
    for a in report["anomalies"][:5]:
        subject = a["ticket_id"] or a["agent_id"] or "n/a"
        lines.append(f"- [{a['severity']}] {subject}: {a['reason']}")
    return "\n".join(lines)
