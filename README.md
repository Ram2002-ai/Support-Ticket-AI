# Support Ticket AI

Ask natural-language questions about a customer-support ticket CSV, detect anomalies, and use it all through a **REST API (FastAPI)** and a **minimal UI (Streamlit)**. Built for the DOTMappers AI Intern assessment.

- **NL questions** → LLM writes SQL → validated → executed read-only on SQLite → answer + the SQL that produced it.
- **Anomalies** → deterministic, explainable rules and robust statistics (no LLM).
- **Runs at zero cost**: Groq free tier, or a local Ollama model.

## Quick start

### 0. Data
The repository includes a 500-row demo dataset at `data/support_tickets.csv`, with injected anomalies. Replace it with your own CSV to use other tickets. To regenerate the demo file, run `python -m scripts.generate_sample_data --force`.

### 1. Configure the LLM
```bash
copy .env.example .env        # Windows   (macOS/Linux: cp .env.example .env)
```
Pick one:
- **Groq (default, free):** create a key at https://console.groq.com/keys and set `GROQ_API_KEY=` in `.env`.
- Uses Groq's currently available `openai/gpt-oss-120b` model by default; the previously configured Llama 3.3 model is no longer available to this account.
- **Ollama (local, no key):** install https://ollama.com, run `ollama pull llama3.1:8b`, and set `LLM_PROVIDER=ollama`.

With `LLM_FALLBACK=true` (default) the other provider is tried automatically if the primary fails.

### 2a. Docker (single command)
```bash
docker-compose up
```
API: http://localhost:8000/docs  ·  UI: http://localhost:8501

### 2b. Without Docker
```bash
python -m venv .venv
.venv\Scripts\activate                       # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --port 8000             # terminal 1
streamlit run ui/streamlit_app.py            # terminal 2
```

### Tests
```bash
pytest
```
Tests use a mocked LLM, so they need no network or API key.

## Architecture

```
Streamlit UI ──HTTP──► FastAPI ──► safety pre-check ──► Intent router (LLM → JSON)
                                                          ├─ data_query ─► NL→SQL (LLM) ─► SQL guard ─► SQLite (read-only) ─► formatter
                                                          │                     └─ on error: feed error back, repair once
                                                          ├─ anomaly ────► Anomaly engine (rules + stats) ─► SQLite
                                                          └─ out_of_scope ► polite refusal
support_tickets.csv ─► ingestion / cleaning ─► SQLite (rebuilt at startup)
```

| Choice | Why |
|---|---|
| **Text-to-SQL** (not "LLM reads the table", not RAG) | LLMs miscount rows; SQL gives exact aggregates and is auditable, since every answer shows its query. |
| **SQLite** | Zero setup, single file, enough for this size. Swap for Postgres to scale. |
| **sqlglot AST guard** + read-only connection + `query_only` + time limit | Defence in depth: one statement, `SELECT`/`WITH` only, allow-listed table/columns/functions, comments rejected, `LIMIT` enforced. A prompt injection can't modify data even if the LLM is fooled. |
| **Deterministic anomalies** | Reproducible, explainable (every flag has a reason, value and threshold), free and fast. The LLM only routes the question. |
| **Groq + Ollama behind one interface** | Fast free hosted model, plus a local option that always works at zero cost. |
| **Reference time** | The data is historical, so "this week" / "older than 24h" are measured from `MAX(created_at)` (override with `REFERENCE_TIME`), not the wall clock. |

### Anomaly rules (`GET /anomalies`)
| Type | Rule |
|---|---|
| `unresolved_high_priority_aging` | High/Critical, status ≠ Resolved, older than `AGING_HOURS` (24) at the reference time. Critical (or >72h) = high severity. |
| `abnormal_resolution_time` | Robust z-score (median/MAD of `log1p(hours)`) > 3.5, per **category × priority** group (falls back to priority, then global, when a group has < 8 tickets). |
| `abnormal_response_time` | Same method on response time. |
| `agent_outlier` | Agent mean rating far below, or mean resolution far above, the team median (robust z > 2.5; ≥ 5 tickets per agent, ≥ 4 agents). |
| `data_inconsistency` | Resolution < response time; Open ticket with a rating; Resolved without a resolution time. |

Log-transforming hours makes the skewed time distributions roughly symmetric, so the normal long tail isn't flagged. Each anomaly has `ticket_id`, `agent_id`, `type`, `severity`, `reason`, `value`, `threshold`, `created_at`.

### Data handling
- Accepts both header spellings in the brief (`resp_time_hrs`/`response_time_hrs`, `resol_time_hrs`/`resolution_time_hrs`, `cust_rating`/`customer_rating`).
- Empty values stay `NULL` (never 0). Enums are case/whitespace-normalised. Invalid rows, duplicate ticket IDs and out-of-range numbers are dropped or nulled and reported in the startup log.
- **"Unresolved" = status Open or Escalated.** Stated to the LLM and documented here.

## API

| Endpoint | Purpose |
|---|---|
| `GET /health` | Status, row count, LLM provider and reachability, reference time |
| `POST /query` | `{"question": "..."}` → `{intent, answer, sql, explanation, assumptions, columns, rows, row_count, anomalies, ...}` |
| `GET /anomalies?days=&type=&severity=&limit=` | Anomaly report with summary counts |
| `GET /stats` | Dataset summary |

Errors are always `{"error": {"code", "message"}}`: 422 for invalid input, 503 if no LLM is available, never a stack trace.

```bash
curl http://localhost:8000/health
curl -X POST http://localhost:8000/query -H "Content-Type: application/json" \
     -d "{\"question\": \"How many critical tickets are unresolved?\"}"
curl "http://localhost:8000/anomalies?type=unresolved_high_priority_aging&severity=high"
```

## Example queries

Captured by running the FastAPI endpoints against the bundled 500-row dataset (reference time `2024-03-31 06:06`). A scripted test LLM supplied deterministic routes and SQL so the examples are reproducible without a key; the SQL guard, SQLite execution, API formatting, and results are real. A live LLM may phrase or format an equivalent query differently. Anomaly requests use the deterministic engine and therefore have no generated SQL.

**How many tickets are currently open?**

Answer: `Open tickets: 98` (1 row)

```sql
SELECT COUNT(*) AS open_tickets FROM tickets WHERE status = 'Open' LIMIT 100
```

**How many Critical tickets are unresolved?**

Answer: `Unresolved critical tickets: 22` (unresolved means Open or Escalated)

```sql
SELECT COUNT(*) AS unresolved_critical_tickets FROM tickets
WHERE priority = 'Critical' AND status IN ('Open', 'Escalated') LIMIT 100
```

**Which agent has the lowest average customer rating?**

Answer: `AGT-07`, average rating `2.76` across `17` rated tickets.

```sql
SELECT agent_id, ROUND(AVG(customer_rating), 2) AS avg_rating,
                COUNT(customer_rating) AS rated_tickets
FROM tickets
WHERE customer_rating IS NOT NULL
GROUP BY agent_id
ORDER BY avg_rating ASC, agent_id
LIMIT 1
```

**Which agent resolved the most tickets this month?**

Answer: `AGT-04`, `19` resolved tickets. Since the schema has no resolution timestamp, the month filter uses `created_at`.

```sql
SELECT agent_id, COUNT(*) AS resolved_tickets
FROM tickets
WHERE status = 'Resolved'
     AND strftime('%Y-%m', created_at) = strftime('%Y-%m', '2024-03-31 06:06:00')
GROUP BY agent_id
ORDER BY resolved_tickets DESC, agent_id
LIMIT 1
```

**Show Critical tickets not resolved within 12 hours.**

Answer: `Found 22 rows`; the first is `TKT-006` (Open, resolution time `NULL`). Tickets with no resolution time count as not resolved within the threshold.

```sql
SELECT ticket_id, created_at, status, resolution_time_hrs, agent_id, issue_summary
FROM tickets
WHERE priority = 'Critical'
     AND (resolution_time_hrs IS NULL OR resolution_time_hrs > 12)
ORDER BY created_at
LIMIT 100
```

**What is the average customer rating for Technical tickets?**

Answer: `Avg rating: 3.67`

```sql
SELECT ROUND(AVG(customer_rating), 2) AS avg_rating
FROM tickets
WHERE category = 'Technical'
LIMIT 100
```

**Are there any anomalies in resolution times this week?**

Answer: `No anomalies found for the last 7 days up to 2024-03-31 06:06.` This routes to `abnormal_resolution_time`; the deterministic engine reports no matches in that date window. Across the full dataset, `GET /anomalies?type=abnormal_resolution_time` finds 4 abnormal resolution-time tickets.

For a live run, `python -m scripts.capture_examples` posts the same sample questions to the running API and writes the returned answers and SQL to `docs/example_outputs.md`.

## Models and tools
Python 3.11 · FastAPI · Pydantic v2 · pandas/numpy · SQLite · sqlglot · httpx · Streamlit · pytest · Groq `openai/gpt-oss-120b` (or Ollama `llama3.1:8b`).

Prompts (`app/llm/prompts.py`) use: exact schema with enum values, the reference time, SQLite rules, 8 few-shot examples, strict JSON output at temperature 0, an explicit "cannot answer" path (`sql: null`), and instructions to ignore in-question instructions. Output parsing strips code fences and retries once on malformed JSON.

## Known limitations
- **Reference-time assumption** for relative dates; there is no resolution timestamp, so "this month" uses `created_at`.
- The LLM can still misread ambiguous questions; it states its assumptions and the SQL is always visible. Smaller local models may be less accurate than the hosted Groq model.
- Single-table scope; no conversation memory.
- Groq free-tier rate limits (falls back to Ollama if available).
- No authentication; CORS is open for demo purposes.
- Anomaly thresholds (z = 3.5, 24h) are sensible defaults, not tuned on labelled data.

## Future improvements
Semantic search over `issue_summary` (embeddings), result caching, auth and rate limiting, an evaluation set for text-to-SQL accuracy, Postgres plus an async job queue for scale, conversation memory, monitoring.

## Project layout
```
app/        main.py · config.py · schemas.py · db.py · ingestion.py · errors.py · constants.py
app/llm/    client.py (Groq/Ollama/fallback) · parsing.py · prompts.py
app/services/ router.py · nl2sql.py · sql_guard.py · anomalies.py · formatter.py · qa.py
ui/         streamlit_app.py
scripts/    generate_sample_data.py · capture_examples.py
tests/      guard · ingestion · anomalies · nl2sql (mocked LLM) · ground truth vs pandas · API
```
#   S u p p o r t - T i c k e t - A I  
 