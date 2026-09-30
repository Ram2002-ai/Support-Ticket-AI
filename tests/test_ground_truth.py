"""Hand-written reference SQL (run through the guard) must agree with independent pandas calculations."""
import pandas as pd

from app.services.nl2sql import execute_sql
from app.services.sql_guard import validate_sql


def run(sql):
    return execute_sql(validate_sql(sql))[1]


def test_open_ticket_count(tickets_env):
    expected = int((tickets_env.df["status"] == "Open").sum())
    assert run("SELECT COUNT(*) FROM tickets WHERE status = 'Open'") == [[expected]]


def test_lowest_rated_agent(tickets_env):
    means = tickets_env.df.groupby("agent_id")["customer_rating"].mean().sort_values()
    rows = run(
        "SELECT agent_id, AVG(customer_rating) FROM tickets WHERE customer_rating IS NOT NULL "
        "GROUP BY agent_id ORDER BY AVG(customer_rating), agent_id LIMIT 1"
    )
    assert rows[0][0] == means.index[0]
    assert abs(rows[0][1] - means.iloc[0]) < 1e-9


def test_agent_with_most_resolved_this_month(tickets_env):
    df = tickets_env.df
    resolved = df[df["status"] == "Resolved"]
    counts = resolved.groupby("agent_id").size().reset_index(name="n").sort_values(["n", "agent_id"], ascending=[False, True])
    rows = run(
        "SELECT agent_id, COUNT(*) AS n FROM tickets WHERE status = 'Resolved' "
        "AND strftime('%Y-%m', created_at) = '2024-03' GROUP BY agent_id ORDER BY n DESC, agent_id LIMIT 1"
    )
    assert rows == [[counts.iloc[0]["agent_id"], int(counts.iloc[0]["n"])]]


def test_critical_tickets_not_resolved_within_12_hours(tickets_env):
    df = tickets_env.df
    expected = df[(df["priority"] == "Critical") & (df["resolution_time_hrs"].isna() | (df["resolution_time_hrs"] > 12))]
    rows = run(
        "SELECT ticket_id FROM tickets WHERE priority = 'Critical' "
        "AND (resolution_time_hrs IS NULL OR resolution_time_hrs > 12)"
    )
    assert {r[0] for r in rows} == set(expected["ticket_id"])


def test_average_rating_for_technical(tickets_env):
    df = tickets_env.df
    expected = df.loc[df["category"] == "Technical", "customer_rating"].mean()
    rows = run("SELECT AVG(customer_rating) FROM tickets WHERE category = 'Technical'")
    assert pd.notna(expected) and abs(rows[0][0] - expected) < 1e-9
