import pytest

from app.errors import UnsafeSQLError
from app.services.sql_guard import validate_sql


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT COUNT(*) FROM tickets WHERE status = 'Open'",
        "SELECT agent_id, AVG(customer_rating) AS r FROM tickets GROUP BY agent_id ORDER BY r LIMIT 1",
        "WITH t AS (SELECT * FROM tickets WHERE priority = 'Critical') SELECT COUNT(*) FROM t",
        "SELECT * FROM tickets WHERE created_at >= datetime('2024-03-01 00:00:00', '-7 days')",
        "SELECT category, COUNT(*) AS n FROM tickets GROUP BY category;",
        "SELECT * FROM tickets WHERE issue_summary LIKE '%login%' COLLATE NOCASE",
    ],
)
def test_accepts_valid_selects(sql):
    out = validate_sql(sql)
    assert out.lower().startswith(("select", "with"))


@pytest.mark.parametrize(
    "sql",
    [
        "DROP TABLE tickets",
        "DELETE FROM tickets",
        "UPDATE tickets SET status = 'Open'",
        "INSERT INTO tickets (ticket_id) VALUES ('x')",
        "SELECT 1; DROP TABLE tickets",
        "PRAGMA table_info(tickets)",
        "ATTACH DATABASE 'x.db' AS x",
        "SELECT * FROM sqlite_master",
        "SELECT * FROM users",
        "SELECT password FROM tickets",
        "SELECT * FROM tickets WHERE status = \"Open\"",   # double-quoted string = unknown column
        "SELECT 1 -- sneaky",
        "SELECT * FROM tickets /* hidden */",
        "SELECT load_extension('evil')",
        "SELECT * FROM pragma_table_info('tickets')",
        "",
    ],
)
def test_rejects_unsafe_or_invalid(sql):
    with pytest.raises(UnsafeSQLError):
        validate_sql(sql)


def test_destructive_statements_are_not_repairable():
    with pytest.raises(UnsafeSQLError) as exc:
        validate_sql("DELETE FROM tickets")
    assert exc.value.repairable is False


def test_unknown_column_is_repairable():
    with pytest.raises(UnsafeSQLError) as exc:
        validate_sql("SELECT statuss FROM tickets")
    assert exc.value.repairable is True


def test_limit_is_added_and_capped():
    assert validate_sql("SELECT * FROM tickets", limit=5).endswith("LIMIT 5")
    capped = validate_sql("SELECT * FROM tickets LIMIT 100000", limit=50)
    assert "100000" not in capped and "50" in capped
    assert validate_sql("SELECT * FROM tickets LIMIT 3", limit=50).endswith("LIMIT 3")
