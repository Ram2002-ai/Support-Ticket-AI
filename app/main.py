"""FastAPI application: /health, /query, /anomalies, /stats."""
from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app import db
from app.config import get_settings
from app.errors import (
    ApiError,
    DataNotReadyError,
    IngestionError,
    LLMOutputError,
    LLMUnavailableError,
    QueryFailedError,
)
from app.ingestion import ingest
from app.llm.client import build_llm
from app.schemas import (
    AnomalyResponse,
    AnomalyType,
    HealthResponse,
    QueryRequest,
    QueryResponse,
    Severity,
    StatsResponse,
)
from app.services import anomalies, qa

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("app")

PING_TTL_S = 30


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    app.state.llm = build_llm(settings)
    app.state.ping = (0.0, False)
    try:
        report = ingest()
        app.state.ready = True
        log.info("Loaded %d tickets (%d duplicates, %d invalid rows dropped)",
                 report.rows_loaded, report.duplicates_dropped, report.invalid_rows_dropped)
    except IngestionError as exc:
        app.state.ready = db.row_count() > 0  # fall back to a previously built database
        log.error("Ingestion failed: %s%s", exc, " (using existing database)" if app.state.ready else "")
    yield


app = FastAPI(
    title="Support Ticket AI",
    description="Ask natural-language questions about support tickets and detect anomalies.",
    version="1.0.0",
    lifespan=lifespan,
)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


def _error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message}})


@app.middleware("http")
async def log_requests(request: Request, call_next):
    start = time.perf_counter()
    response = await call_next(request)
    log.info("%s %s -> %d (%.0f ms)", request.method, request.url.path, response.status_code,
             (time.perf_counter() - start) * 1000)
    return response


@app.exception_handler(ApiError)
async def _api_error(_: Request, exc: ApiError):
    return _error(exc.status, exc.code, exc.message)


@app.exception_handler(LLMUnavailableError)
async def _llm_unavailable(_: Request, exc: LLMUnavailableError):
    return _error(503, "llm_unavailable", str(exc))


@app.exception_handler(LLMOutputError)
async def _llm_output(_: Request, exc: LLMOutputError):
    return _error(502, "llm_bad_output", f"The language model returned an unusable reply. {exc}")


@app.exception_handler(QueryFailedError)
async def _query_failed(_: Request, exc: QueryFailedError):
    return _error(422, "query_failed", str(exc))


@app.exception_handler(DataNotReadyError)
async def _data_not_ready(_: Request, exc: DataNotReadyError):
    return _error(503, "data_not_ready", str(exc))


@app.exception_handler(RequestValidationError)
async def _validation(_: Request, exc: RequestValidationError):
    parts = []
    for err in exc.errors():
        where = ".".join(str(p) for p in err["loc"][1:]) or "request"
        parts.append(f"{where}: {str(err['msg']).removeprefix('Value error, ')}")
    return _error(422, "validation_error", "; ".join(parts))


@app.exception_handler(Exception)
async def _unexpected(_: Request, exc: Exception):
    log.exception("Unhandled error")
    return _error(500, "internal_error", "Something went wrong on the server.")


def _require_ready(request: Request) -> None:
    if not request.app.state.ready:
        raise ApiError(503, "data_not_ready",
                       "No ticket data is loaded. Place support_tickets.csv in data/ and restart the API.")


def _llm_reachable(app_state) -> bool:
    checked_at, value = app_state.ping
    if time.monotonic() - checked_at > PING_TTL_S or checked_at == 0.0:
        value = app_state.llm.ping()
        app_state.ping = (time.monotonic(), value)
    return value


@app.get("/health", response_model=HealthResponse, tags=["system"])
def health(request: Request) -> HealthResponse:
    rows = db.row_count()
    reachable = _llm_reachable(request.app.state)
    return HealthResponse(
        status="ok" if rows and reachable else "degraded",
        db_rows=rows,
        llm_provider=request.app.state.llm.name,
        llm_reachable=reachable,
        reference_time=db.format_ref(db.reference_time()) if rows else None,
    )


@app.post("/query", response_model=QueryResponse, tags=["query"])
def query(body: QueryRequest, request: Request) -> QueryResponse:
    """Answer a natural-language question about the tickets (text-to-SQL or anomaly engine)."""
    _require_ready(request)
    return qa.answer_question(body.question, request.app.state.llm)


@app.get("/anomalies", response_model=AnomalyResponse, tags=["anomalies"])
def get_anomalies(
    request: Request,
    days: int | None = Query(None, ge=1, le=3650, description="Only tickets created in the last N days (vs. reference time)"),
    type: AnomalyType | None = Query(None, description="Filter by anomaly type"),
    severity: Severity | None = Query(None, description="Filter by severity"),
    limit: int = Query(100, ge=1, le=1000),
) -> AnomalyResponse:
    _require_ready(request)
    report = anomalies.detect(days=days, types=[type] if type else None, severity=severity, limit=limit)
    return AnomalyResponse(**report)


@app.get("/stats", response_model=StatsResponse, tags=["system"])
def get_stats(request: Request) -> StatsResponse:
    _require_ready(request)
    return StatsResponse(**db.stats())
