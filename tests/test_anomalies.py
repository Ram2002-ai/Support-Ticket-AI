from app.services import anomalies


def _ids(report, kind):
    return {a["ticket_id"] for a in report["anomalies"] if a["type"] == kind}


def test_old_unresolved_critical_is_flagged_but_low_priority_is_not(tickets_env):
    ids = _ids(anomalies.detect(), "unresolved_high_priority_aging")
    assert "TKT-902" in ids
    assert "TKT-903" not in ids


def test_extreme_resolution_time_is_flagged(tickets_env):
    assert "TKT-901" in _ids(anomalies.detect(), "abnormal_resolution_time")


def test_thresholds_are_per_group(tickets_env):
    flagged = _ids(anomalies.detect(), "abnormal_resolution_time")
    assert "TKT-905" in flagged                                   # 30h is abnormal for Billing/Medium
    assert not any(t and t.startswith("TKT-95") for t in flagged)  # ...but normal for Technical/Critical


def test_inconsistency_and_agent_outlier(tickets_env):
    report = anomalies.detect()
    assert "TKT-904" in _ids(report, "data_inconsistency")
    assert any(a["type"] == "agent_outlier" and a["agent_id"] == "AGT-07" for a in report["anomalies"])


def test_filters_and_window(tickets_env):
    only = anomalies.detect(types=["unresolved_high_priority_aging"])
    assert set(only["by_type"]) == {"unresolved_high_priority_aging"}

    recent = anomalies.detect(days=1)
    assert "TKT-902" not in _ids(recent, "unresolved_high_priority_aging")  # created ~14 days before reference
    assert recent["window_days"] == 1

    high = anomalies.detect(severity="high")
    assert all(a["severity"] == "high" for a in high["anomalies"])
    assert len(anomalies.detect(limit=2)["anomalies"]) == 2


def test_results_sorted_by_severity(tickets_env):
    ranks = {"high": 3, "medium": 2, "low": 1}
    sev = [ranks[a["severity"]] for a in anomalies.detect()["anomalies"]]
    assert sev == sorted(sev, reverse=True)
