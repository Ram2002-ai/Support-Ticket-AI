"""Shared domain constants (single source of truth for schema, enums and anomaly types)."""
from __future__ import annotations

TABLE = "tickets"

CATEGORIES = ("Billing", "Technical", "General")
PRIORITIES = ("Low", "Medium", "High", "Critical")
STATUSES = ("Open", "Resolved", "Escalated")

COLUMNS = (
    "ticket_id",
    "created_at",
    "category",
    "priority",
    "status",
    "response_time_hrs",
    "resolution_time_hrs",
    "agent_id",
    "customer_rating",
    "issue_summary",
)

ANOMALY_TYPES = (
    "unresolved_high_priority_aging",
    "abnormal_resolution_time",
    "abnormal_response_time",
    "agent_outlier",
    "data_inconsistency",
)

SEVERITIES = ("low", "medium", "high")
