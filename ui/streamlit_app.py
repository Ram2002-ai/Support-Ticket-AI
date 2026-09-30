"""Minimal Streamlit UI for the Support Ticket AI API."""
from __future__ import annotations

import os

import pandas as pd
import requests
import streamlit as st

API_URL = os.getenv("API_URL", "http://localhost:8000").rstrip("/")
ANOMALY_TYPES = [
    "unresolved_high_priority_aging",
    "abnormal_resolution_time",
    "abnormal_response_time",
    "agent_outlier",
    "data_inconsistency",
]
SAMPLES = [
    "How many tickets are currently open?",
    "Which agent resolved the most tickets this month?",
    "Show me all Critical tickets not resolved within 12 hours.",
    "What is the average customer rating for Technical category tickets?",
    "Are there any anomalies in resolution times this week?",
]

st.set_page_config(page_title="Support Ticket AI", page_icon="🎫", layout="wide")


def call_api(method: str, path: str, **kwargs):
    """Return (json, error_message). Never raises."""
    try:
        resp = requests.request(method, f"{API_URL}{path}", timeout=120, **kwargs)
    except requests.RequestException:
        return None, f"Cannot reach the API at {API_URL}. Is it running?"
    if resp.ok:
        return resp.json(), None
    try:
        return None, resp.json()["error"]["message"]
    except (ValueError, KeyError):
        return None, f"The API returned HTTP {resp.status_code}."


@st.cache_data(ttl=15, show_spinner=False)
def cached(path: str):
    return call_api("GET", path)


def use_sample(text: str) -> None:
    st.session_state["question"] = text


# ---------------------------------------------------------------- sidebar
with st.sidebar:
    st.header("System")
    health, err = cached("/health")
    if err:
        st.error(err)
    else:
        (st.success if health["status"] == "ok" else st.warning)(f"API: {health['status']}")
        st.caption(f"LLM: {health['llm_provider']} ({'reachable' if health['llm_reachable'] else 'unreachable'})")
        st.caption(f"Rows loaded: {health['db_rows']}")
        st.caption(f"Reference time: {health['reference_time']}")
        stats, _ = cached("/stats")
        if stats:
            st.subheader("Dataset")
            st.metric("Tickets", stats["total"])
            st.write("**By status**", stats["by_status"])
            st.write("**By priority**", stats["by_priority"])
            st.write("**By category**", stats["by_category"])

st.title("🎫 Support Ticket AI")
tab_ask, tab_anomalies = st.tabs(["Ask a question", "Anomalies"])

# ---------------------------------------------------------------- ask tab
with tab_ask:
    with st.expander("Sample questions", expanded=True):
        for i, sample in enumerate(SAMPLES):
            st.button(sample, key=f"sample_{i}", on_click=use_sample, args=(sample,))

    st.text_input("Your question", key="question", max_chars=500, placeholder="e.g. How many critical tickets are unresolved?")
    if st.button("Ask", type="primary"):
        question = st.session_state.get("question", "").strip()
        if question:
            with st.spinner("Thinking..."):
                st.session_state["result"] = call_api("POST", "/query", json={"question": question})
        else:
            st.warning("Please type a question first.")

    if "result" in st.session_state:
        data, error = st.session_state["result"]
        if error:
            st.error(error)
        else:
            if data["intent"] in ("blocked", "out_of_scope"):
                st.warning(data["answer"])
            else:
                st.success(data["answer"])
            if data.get("rows"):
                st.dataframe(pd.DataFrame(data["rows"], columns=data["columns"]), use_container_width=True, hide_index=True)
            if data.get("anomalies"):
                st.dataframe(pd.DataFrame(data["anomalies"]), use_container_width=True, hide_index=True)
            if data.get("sql") or data.get("explanation"):
                with st.expander("How this was answered (SQL, explanation, assumptions)"):
                    if data.get("sql"):
                        st.code(data["sql"], language="sql")
                    if data.get("explanation"):
                        st.write(f"**Explanation:** {data['explanation']}")
                    if data.get("assumptions"):
                        st.write(f"**Assumptions:** {data['assumptions']}")
            st.caption(f"Intent: {data['intent']} | Provider: {data.get('llm_provider') or 'n/a'}")

# ---------------------------------------------------------------- anomalies tab
with tab_anomalies:
    c1, c2, c3, c4 = st.columns(4)
    a_type = c1.selectbox("Type", ["all"] + ANOMALY_TYPES)
    a_sev = c2.selectbox("Severity", ["all", "high", "medium", "low"])
    a_days = c3.number_input("Last N days (0 = all)", min_value=0, max_value=3650, value=0)
    a_limit = c4.number_input("Max rows", min_value=1, max_value=1000, value=100)

    params = {"limit": int(a_limit)}
    if a_type != "all":
        params["type"] = a_type
    if a_sev != "all":
        params["severity"] = a_sev
    if a_days:
        params["days"] = int(a_days)

    report, err = call_api("GET", "/anomalies", params=params)
    if err:
        st.error(err)
    else:
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Total", report["total"])
        for col, level in zip((m2, m3, m4), ("high", "medium", "low")):
            col.metric(level.capitalize(), report["by_severity"].get(level, 0))
        st.caption(f"Reference time: {report['reference_time']}")
        if report["anomalies"]:
            st.dataframe(pd.DataFrame(report["anomalies"]), use_container_width=True, hide_index=True)
        else:
            st.info("No anomalies match these filters.")
