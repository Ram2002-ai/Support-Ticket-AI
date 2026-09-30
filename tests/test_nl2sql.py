import json

import pytest

from app import db
from app.errors import LLMOutputError, LLMUnavailableError, QueryFailedError, UnsafeSQLError
from app.llm.parsing import parse_json_object
from app.services.nl2sql import generate_and_run
from tests.fakes import FakeLLM


def reply(sql, explanation="test", assumptions=""):
    return json.dumps({"sql": sql, "explanation": explanation, "assumptions": assumptions})


def run(llm, question="q"):
    return generate_and_run(llm, question, db.reference_time())


def test_happy_path(tickets_env):
    llm = FakeLLM(reply("SELECT COUNT(*) AS open_tickets FROM tickets WHERE status = 'Open'"))
    result = run(llm)
    assert result["columns"] == ["open_tickets"]
    assert result["rows"] == [[2]]
    assert result["sql"].endswith("LIMIT 100")
    assert len(llm.calls) == 1


def test_code_fenced_json_is_parsed():
    assert parse_json_object('```json\n{"sql": "SELECT 1"}\n```') == {"sql": "SELECT 1"}
    assert parse_json_object('Sure! {"a": 1} hope that helps') == {"a": 1}
    with pytest.raises(LLMOutputError):
        parse_json_object("no json here")


def test_malformed_json_is_retried_once(tickets_env):
    llm = FakeLLM("Sure, here you go!", reply("SELECT COUNT(*) AS n FROM tickets"))
    assert run(llm)["rows"] == [[83]]
    assert "not valid JSON" in llm.calls[1][1]


def test_malformed_json_twice_raises(tickets_env):
    with pytest.raises(LLMOutputError):
        run(FakeLLM("nope", "still nope"))


def test_bad_column_is_repaired_once(tickets_env):
    llm = FakeLLM(
        reply("SELECT COUNT(*) FROM tickets WHERE statuss = 'Open'"),
        reply("SELECT COUNT(*) AS n FROM tickets WHERE status = 'Open'"),
    )
    result = run(llm)
    assert result["rows"] == [[2]]
    assert len(llm.calls) == 2
    assert "statuss" in llm.calls[1][1]  # the error was fed back to the model


def test_repair_failure_gives_query_failed(tickets_env):
    bad = reply("SELECT nope FROM tickets")
    with pytest.raises(QueryFailedError):
        run(FakeLLM(bad, bad))


def test_destructive_sql_is_blocked_without_repair(tickets_env):
    llm = FakeLLM(reply("DELETE FROM tickets"))
    with pytest.raises(UnsafeSQLError):
        run(llm)
    assert len(llm.calls) == 1
    assert db.row_count() == 83


def test_null_sql_means_unanswerable(tickets_env):
    llm = FakeLLM(json.dumps({"sql": None, "explanation": "Not about tickets."}))
    result = run(llm)
    assert result["sql"] is None and "Not about tickets" in result["explanation"]


def test_now_is_pinned_to_reference_time(tickets_env):
    llm = FakeLLM(reply("SELECT COUNT(*) AS n FROM tickets WHERE created_at >= datetime('now', '-1 days')"))
    result = run(llm)
    assert "'now'" not in result["sql"]
    assert result["rows"][0][0] > 0


def test_llm_outage_propagates(tickets_env):
    with pytest.raises(LLMUnavailableError):
        run(FakeLLM(LLMUnavailableError("down")))
