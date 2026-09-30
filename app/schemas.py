"""Pydantic request/response models."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

AnomalyType = Literal[
    "unresolved_high_priority_aging",
    "abnormal_resolution_time",
    "abnormal_response_time",
    "agent_outlier",
    "data_inconsistency",
]
Severity = Literal["low", "medium", "high"]
Intent = Literal["data_query", "anomaly", "out_of_scope", "blocked"]


class QueryRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=500, description="Natural-language question")

    @field_validator("question")
    @classmethod
    def _must_be_text(cls, value: str) -> str:
        value = value.strip()
        if sum(ch.isalpha() for ch in value) < 2:
            raise ValueError("Please enter a question in words.")
        return value


class Anomaly(BaseModel):
    ticket_id: str | None = None
    agent_id: str | None = None
    type: AnomalyType
    severity: Severity
    reason: str
    value: float | None = None
    threshold: float | None = None
    created_at: str | None = None


class AnomalyResponse(BaseModel):
    reference_time: str
    window_days: int | None = None
    total: int
    by_type: dict[str, int]
    by_severity: dict[str, int]
    anomalies: list[Anomaly]


class QueryResponse(BaseModel):
    intent: Intent
    answer: str
    sql: str | None = None
    explanation: str | None = None
    assumptions: str | None = None
    columns: list[str] = []
    rows: list[list[Any]] = []
    row_count: int = 0
    anomalies: list[Anomaly] | None = None
    anomaly_total: int | None = None
    llm_provider: str | None = None
    reference_time: str | None = None


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    db_rows: int
    llm_provider: str
    llm_reachable: bool
    reference_time: str | None = None


class StatsResponse(BaseModel):
    total: int
    by_status: dict[str, int]
    by_priority: dict[str, int]
    by_category: dict[str, int]
    avg_rating: float | None = None
    avg_resolution_hrs: float | None = None
    reference_time: str | None = None
