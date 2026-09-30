"""Orchestrates one question: safety check -> route -> (NL->SQL | anomaly engine) -> formatted response."""
from __future__ import annotations

import logging

from app import db
from app.config import get_settings
from app.errors import UnsafeSQLError
from app.llm.client import LLMClient
from app.schemas import QueryResponse
from app.services import anomalies, formatter, nl2sql, router

log = logging.getLogger(__name__)

BLOCKED_MESSAGE = (
    "I can only answer read-only questions about the support-ticket data. "
    "I can't modify or delete data, or reveal system instructions."
)
OUT_OF_SCOPE_MESSAGE = (
    "That doesn't look like a question about the support-ticket data. Try something like: "
    "'How many tickets are open?', 'Which agent has the lowest average rating?' or "
    "'Are there any anomalies in resolution times this week?'"
)


def _provider(llm: LLMClient) -> str:
    return getattr(llm, "last_used", None) or llm.name


def answer_question(question: str, llm: LLMClient) -> QueryResponse:
    ref = db.reference_time()
    ref_text = db.format_ref(ref)

    if router.looks_unsafe(question):
        log.warning("Blocked unsafe question: %r", question[:80])
        return QueryResponse(intent="blocked", answer=BLOCKED_MESSAGE, reference_time=ref_text)

    decision = router.classify(llm, question)

    if decision.intent == "out_of_scope":
        return QueryResponse(
            intent="out_of_scope", answer=OUT_OF_SCOPE_MESSAGE, llm_provider=_provider(llm), reference_time=ref_text
        )

    if decision.intent == "anomaly":
        report = anomalies.detect(days=decision.days, types=decision.types, limit=20)
        return QueryResponse(
            intent="anomaly",
            answer=formatter.anomaly_summary(report),
            anomalies=report["anomalies"],
            anomaly_total=report["total"],
            llm_provider=_provider(llm),
            reference_time=ref_text,
        )

    try:
        result = nl2sql.generate_and_run(llm, question, ref)
    except UnsafeSQLError as exc:
        log.warning("Guard blocked generated SQL: %s", exc)
        return QueryResponse(intent="blocked", answer=BLOCKED_MESSAGE, reference_time=ref_text)

    if result["sql"] is None:
        return QueryResponse(
            intent="out_of_scope",
            answer=f"{result['explanation']} {OUT_OF_SCOPE_MESSAGE}",
            llm_provider=_provider(llm),
            reference_time=ref_text,
        )

    answer = formatter.format_answer(result["columns"], result["rows"])
    if get_settings().llm_summary and result["rows"]:
        answer = formatter.summarize(llm, question, result["columns"], result["rows"]) or answer

    return QueryResponse(
        intent="data_query",
        answer=answer,
        sql=result["sql"],
        explanation=result["explanation"],
        assumptions=result["assumptions"],
        columns=result["columns"],
        rows=result["rows"],
        row_count=len(result["rows"]),
        llm_provider=_provider(llm),
        reference_time=ref_text,
    )
