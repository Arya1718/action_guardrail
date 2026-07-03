import csv
import io
import logging
import os
import time
import uuid
from collections import defaultdict
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, Literal, Optional

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel

from app.audit import query_audit_log, write_audit_log
from app.config import Settings
from app.evaluator import evaluate_action
from app.hitl import (
    create_hitl_request,
    get_hitl_request_by_id,
    get_pending_hitl_requests,
    resolve_request,
)
from app.logging_setup import setup_logging
from app.models import Rule, ToolCall
from app.policy_loader import load_policies
from app.storage import InMemoryStorage, StorageBackend

from dotenv import load_dotenv
load_dotenv()

setup_logging()

logger = logging.getLogger(__name__)


def _parse_iso_as_utc(s: str | None) -> datetime | None:
    """Parse an ISO 8601 string; treat missing timezone as UTC."""
    if s is None:
        return None
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt

settings = Settings()


def _make_storage() -> StorageBackend:
    backend = settings.STORAGE_BACKEND
    if backend == "dynamodb":
        from app.storage import DynamoDBStorage
        return DynamoDBStorage()
    if backend == "mongo":
        from app.storage import MongoStorage
        return MongoStorage(uri=settings.MONGO_URI, db_name=settings.MONGO_DB_NAME)
    return InMemoryStorage()


logger.info("Loading policies from %s", settings.POLICY_FILE_PATH)
_rules = load_policies(settings.POLICY_FILE_PATH)
logger.info("Loaded %d rules", len(_rules))
api_key_preview = settings.API_KEY[:4] + "..." if len(settings.API_KEY) > 4 else settings.API_KEY
logger.info("Resolved API_KEY=%s", api_key_preview)


# ── Rate limiter (in-memory sliding window) ─────────────────────────────

_RATE_LIMIT_WINDOW = 60
_RATE_LIMIT_MAX = 60
_rate_limit_store: dict[str, list[float]] = defaultdict(list)


def _check_rate_limit(key: str) -> Optional[int]:
    now = time.time()
    window_start = now - _RATE_LIMIT_WINDOW
    bucket = _rate_limit_store[key]
    _rate_limit_store[key] = [t for t in bucket if t > window_start]
    bucket = _rate_limit_store[key]
    if len(bucket) >= _RATE_LIMIT_MAX:
        return int(_RATE_LIMIT_WINDOW - (now - bucket[0]))
    bucket.append(now)
    return None


# ── Lifespan ────────────────────────────────────────────────────────────


@asynccontextmanager
async def lifespan_fn(app: FastAPI):
    app.state.rules = _rules
    app.state.storage = _make_storage()
    yield


# ── App instance ────────────────────────────────────────────────────────

app = FastAPI(title="Action Guardrail", version="2.0.0", lifespan=lifespan_fn)
app.state.rules = _rules
app.state.storage = _make_storage()


# ── Production middleware ───────────────────────────────────────────────


@app.middleware("http")
async def _production_middleware(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    correlation_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
    request.state.request_id = correlation_id

    path = request.url.path
    protected = (
        path.startswith("/evaluate")
        or path.startswith("/hitl")
        or path.startswith("/audit-log")
        or path == "/policies"
        or path == "/query"
    )

    if protected:
        api_key = request.headers.get("X-API-Key", "")
        if not api_key or api_key != settings.API_KEY:
            return JSONResponse(
                status_code=401,
                content={
                    "error": "Unauthorized",
                    "detail": "Missing or invalid X-API-Key header",
                },
                headers={
                    "X-Request-ID": correlation_id,
                    "WWW-Authenticate": 'ApiKey realm="guardrail"',
                },
            )

        if path.startswith("/evaluate"):
            retry_after = _check_rate_limit(api_key)
            if retry_after is not None:
                return JSONResponse(
                    status_code=429,
                    content={
                        "error": "Too Many Requests",
                        "detail": f"Rate limit exceeded. Try again in {retry_after}s.",
                    },
                    headers={
                        "X-Request-ID": correlation_id,
                        "Retry-After": str(retry_after),
                    },
                )

    if path.startswith("/evaluate"):
        content_length = request.headers.get("content-length")
        if content_length and int(content_length) > 100_000:
            return JSONResponse(
                status_code=413,
                content={
                    "error": "Payload Too Large",
                    "detail": "Request body exceeds 100KB limit",
                },
                headers={"X-Request-ID": correlation_id},
            )

    start = time.perf_counter()
    response = await call_next(request)
    elapsed_ms = round((time.perf_counter() - start) * 1000, 2)

    response.headers["X-Request-ID"] = correlation_id
    response.headers["X-Process-Time-Ms"] = str(elapsed_ms)
    return response


# ── Request/Response models ──────────────────────────────────────────────


class EvaluateRequest(BaseModel):
    tool_call: ToolCall
    dry_run: bool = False


class EvaluateResponse(BaseModel):
    tool_call_id: str = ""
    tool_name: str = ""
    requested_args: dict[str, Any] = {}
    outcome: Literal["block", "require_hitl", "log_and_allow", "allow"]
    matched_rule_id: Optional[str] = None
    reason: str = ""
    dry_run: bool = False
    dry_run_override: bool = False
    original_intended_decision: Optional[str] = None
    hitl_request_id: Optional[str] = None
    message: str = ""
    request_id: str = ""
    audit_written: bool = True


class ErrorDetail(BaseModel):
    error: str
    detail: Any = None


class ResolveRequest(BaseModel):
    resolved_by: str


class AuditLogEntry(BaseModel):
    id: str
    tool: str
    outcome: str
    dry_run: bool
    reason: str
    created_at: str
    original_intended_decision: Optional[str] = None
    matched_rule_id: Optional[str] = None
    hitl_resolved_by: Optional[str] = None
    hitl_resolution: Optional[str] = None
    hitl_resolved_at: Optional[str] = None


class HealthResponse(BaseModel):
    status: str
    policies_loaded: int
    database: Optional[str] = None


class QueryRequest(BaseModel):
    prompt: str


# ── Exception handlers ───────────────────────────────────────────────────


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    rid = getattr(request.state, "request_id", "unknown")
    logger.exception(
        "Unhandled exception request_id=%s on %s %s",
        rid, request.method, request.url,
    )
    msg = str(exc) if str(exc) else "Internal server error"
    status = 503 if "MongoDB" in msg or "storage unavailable" in msg else 500
    return JSONResponse(
        status_code=status,
        content={"error": "Internal server error", "detail": msg},
        headers={"X-Request-ID": rid},
    )


# ── Endpoints ────────────────────────────────────────────────────────────


@app.post("/evaluate", response_model=EvaluateResponse)
async def evaluate(req: EvaluateRequest, request: Request):
    rules: list[Rule] = request.app.state.rules
    storage: StorageBackend = request.app.state.storage
    rid = getattr(request.state, "request_id", "")

    start = time.perf_counter()
    decision = evaluate_action(req.tool_call, rules)
    latency_ms = round((time.perf_counter() - start) * 1000, 2)

    tool_call_id = req.tool_call.call_id or str(uuid.uuid4())
    tool_name = req.tool_call.tool
    requested_args = dict(req.tool_call.parameters)

    outcome = decision.outcome
    dry_run_override = False
    original_intended_decision: Optional[str] = None
    hitl_request_id: Optional[str] = None
    message = ""

    # Write audit log FIRST (before HITL, so we can link audit ID to HITL)
    audit_written = True
    try:
        audit_record = write_audit_log(
            storage, req.tool_call, decision, dry_run=req.dry_run,
            original_intended_decision=original_intended_decision,
        )
        audit_id = audit_record.id
    except Exception as exc:
        logger.warning(
            "AUDIT_WRITE_FAILED request_id=%s tool=%s error=%s",
            rid, tool_name, exc,
        )
        audit_written = False
        audit_id = "failed"

    if req.dry_run and outcome in ("block", "require_hitl"):
        dry_run_override = True
        original_intended_decision = outcome
        outcome = "allow"
        if original_intended_decision == "block":
            message = (
                f"[DRY RUN] Would have blocked call to '{tool_name}'. "
                f"Matched rule '{decision.matched_rule_id}'. "
                "No enforcement applied."
            )
        else:
            message = (
                f"[DRY RUN] Would have required HITL for call to "
                f"'{tool_name}'. No pending request created."
            )
    else:
        if outcome == "block":
            message = (
                f"Call to '{tool_name}' blocked by rule "
                f"'{decision.matched_rule_id}'. Do not execute."
            )
        elif outcome == "require_hitl":
            try:
                hitl_req = create_hitl_request(
                    storage, req.tool_call, decision,
                    audit_record_id=audit_id if audit_written else None,
                )
                hitl_request_id = hitl_req.id
                if audit_written:
                    storage.update_audit_record(
                        audit_id, hitl_request_id=hitl_request_id,
                    )
            except Exception as exc:
                logger.warning(
                    "HITL_CREATE_FAILED request_id=%s tool=%s error=%s",
                    rid, tool_name, exc,
                )
                message = (
                    f"HITL required for call to '{tool_name}' "
                    f"but storage write failed: {exc}. Action not executed."
                )
                return EvaluateResponse(
                    tool_call_id=tool_call_id,
                    tool_name=tool_name,
                    requested_args=requested_args,
                    outcome=outcome,
                    matched_rule_id=decision.matched_rule_id,
                    reason=decision.reason,
                    dry_run=req.dry_run,
                    dry_run_override=dry_run_override,
                    original_intended_decision=original_intended_decision,
                    request_id=rid,
                    audit_written=audit_written,
                    message=message,
                )
            message = (
                f"HITL required for call to '{tool_name}'. "
                f"Pending request id={hitl_request_id}. "
                "Await human approval before executing."
            )
        elif outcome == "log_and_allow":
            dr_label = "[DRY RUN] " if req.dry_run else ""
            message = (
                f"{dr_label}Call to '{tool_name}' logged and allowed. "
                f"Matched rule '{decision.matched_rule_id}'."
            )
        else:
            dr_label = "[DRY RUN] " if req.dry_run else ""
            message = f"{dr_label}Call to '{tool_name}' allowed (no matching rule)."

    logger.info(
        "EVALUATE request_id=%s tool=%s outcome=%s latency_ms=%s "
        "dry_run=%s rule=%s audit_id=%s audit_written=%s",
        rid, tool_name, decision.outcome, latency_ms,
        req.dry_run, decision.matched_rule_id, audit_id, audit_written,
    )

    return EvaluateResponse(
        tool_call_id=tool_call_id,
        tool_name=tool_name,
        requested_args=requested_args,
        outcome=outcome,
        matched_rule_id=decision.matched_rule_id,
        reason=decision.reason,
        dry_run=req.dry_run,
        dry_run_override=dry_run_override,
        original_intended_decision=original_intended_decision,
        hitl_request_id=hitl_request_id,
        message=message,
        request_id=rid,
        audit_written=audit_written,
    )


@app.get("/hitl/pending")
async def list_pending_hitl(request: Request):
    storage: StorageBackend = request.app.state.storage
    pending = get_pending_hitl_requests(storage)
    return {"pending": pending}


@app.get("/hitl/{request_id}")
async def get_hitl_status(request_id: str, request: Request):
    storage: StorageBackend = request.app.state.storage
    req = get_hitl_request_by_id(storage, request_id)
    if req is None:
        raise HTTPException(status_code=404, detail=f"HITL request '{request_id}' not found")
    return {"request": req}


@app.post("/hitl/{request_id}/approve")
async def approve_hitl(request_id: str, body: ResolveRequest, request: Request):
    return _resolve(request, request_id, "approved", body.resolved_by)


@app.post("/hitl/{request_id}/reject")
async def reject_hitl(request_id: str, body: ResolveRequest, request: Request):
    return _resolve(request, request_id, "rejected", body.resolved_by)


def _resolve(
    request: Request,
    request_id: str,
    resolution: Literal["approved", "rejected"],
    resolved_by: str,
) -> dict:
    storage: StorageBackend = request.app.state.storage
    try:
        req = resolve_request(storage, request_id, resolution, resolved_by)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"HITL request '{request_id}' not found")
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))

    if req.audit_record_id:
        try:
            storage.update_audit_record(
                req.audit_record_id,
                hitl_resolved_by=resolved_by,
                hitl_resolution=resolution,
                hitl_resolved_at=datetime.now(timezone.utc),
            )
        except Exception as exc:
            logger.warning(
                "AUDIT_UPDATE_FAILED hitl_id=%s audit_id=%s error=%s",
                request_id, req.audit_record_id, exc,
            )

    return {"request": req}


@app.get("/audit-log")
async def list_audit_log(
    request: Request,
    limit: int = 100,
    tool: Optional[str] = None,
    outcome: Optional[str] = None,
    since: Optional[str] = None,
    until: Optional[str] = None,
):
    storage: StorageBackend = request.app.state.storage
    since_dt = _parse_iso_as_utc(since)
    until_dt = _parse_iso_as_utc(until)
    records = query_audit_log(
        storage, limit=limit, tool=tool, outcome=outcome,
        since=since_dt, until=until_dt,
    )
    return {
        "records": [
            AuditLogEntry(
                id=r.id,
                tool=r.tool_call.tool,
                outcome=r.decision.outcome,
                dry_run=r.dry_run,
                reason=r.decision.reason,
                created_at=r.created_at.isoformat(),
                original_intended_decision=r.original_intended_decision,
                matched_rule_id=r.decision.matched_rule_id,
                hitl_resolved_by=r.hitl_resolved_by,
                hitl_resolution=r.hitl_resolution,
                hitl_resolved_at=r.hitl_resolved_at.isoformat() if r.hitl_resolved_at else None,
            )
            for r in records
        ]
    }


@app.get("/audit-log/summary")
async def audit_log_summary(
    request: Request,
    tool: Optional[str] = None,
    outcome: Optional[str] = None,
    since: Optional[str] = None,
    until: Optional[str] = None,
):
    storage: StorageBackend = request.app.state.storage
    since_dt = _parse_iso_as_utc(since)
    until_dt = _parse_iso_as_utc(until)
    records = query_audit_log(storage, limit=10_000, tool=tool, outcome=outcome,
                              since=since_dt, until=until_dt)
    by_outcome: dict[str, int] = {}
    by_tool: dict[str, int] = {}
    dry_run_count = 0
    for r in records:
        by_outcome[r.decision.outcome] = by_outcome.get(r.decision.outcome, 0) + 1
        by_tool[r.tool_call.tool] = by_tool.get(r.tool_call.tool, 0) + 1
        if r.dry_run:
            dry_run_count += 1
    return {
        "total": len(records),
        "by_outcome": by_outcome,
        "by_tool": by_tool,
        "dry_run_count": dry_run_count,
    }


@app.get("/audit-log/export")
async def audit_log_export(
    request: Request,
    tool: Optional[str] = None,
    outcome: Optional[str] = None,
    since: Optional[str] = None,
    until: Optional[str] = None,
):
    storage: StorageBackend = request.app.state.storage
    since_dt = _parse_iso_as_utc(since)
    until_dt = _parse_iso_as_utc(until)
    records = query_audit_log(storage, limit=10_000, tool=tool, outcome=outcome,
                              since=since_dt, until=until_dt)
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "id", "tool", "outcome", "dry_run", "reason", "created_at",
        "original_intended_decision", "hitl_resolved_by", "hitl_resolution", "hitl_resolved_at",
    ])
    for r in records:
        writer.writerow([
            r.id,
            r.tool_call.tool,
            r.decision.outcome,
            str(r.dry_run),
            r.decision.reason,
            r.created_at.isoformat(),
            r.original_intended_decision or "",
            r.hitl_resolved_by or "",
            r.hitl_resolution or "",
            r.hitl_resolved_at.isoformat() if r.hitl_resolved_at else "",
        ])
    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=audit-log.csv"},
    )


@app.post("/query")
async def query_audit_groq(req: QueryRequest, request: Request):
    storage: StorageBackend = request.app.state.storage
    records = query_audit_log(storage, limit=200)
    context_lines: list[str] = []
    for r in records:
        context_lines.append(
            f"[{r.created_at.isoformat()}] tool={r.tool_call.tool} "
            f"outcome={r.decision.outcome} rule={r.decision.matched_rule_id or 'none'} "
            f"dry_run={r.dry_run} agent={r.tool_call.agent_id or '?'} "
            f"resolved_by={r.hitl_resolved_by or '—'} "
            f"resolution={r.hitl_resolution or '—'}"
        )
    context = "\n".join(context_lines) if context_lines else "(no records)"

    groq_key = os.environ.get("GROQ_API_KEY", "")
    if not groq_key:
        return JSONResponse(
            status_code=503,
            content={"error": "GROQ_API_KEY not configured on server"},
        )

    try:
        from groq import Groq
        client = Groq(api_key=groq_key)
        resp = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a security audit analyst. Below is the guardrail audit log "
                        "showing tool calls, policy decisions, and HITL resolutions. "
                        "Answer the user's question based ONLY on this data. "
                        "Be concise and cite specific records when relevant."
                    ),
                },
                {
                    "role": "user",
                    "content": f"Audit log data:\n{context}\n\nQuestion: {req.prompt}",
                },
            ],
            temperature=0.1,
            max_tokens=500,
        )
        answer = resp.choices[0].message.content.strip()
    except Exception as exc:
        logger.warning("GROQ_QUERY_FAILED error=%s", exc)
        return JSONResponse(
            status_code=502,
            content={"error": f"Groq API call failed: {exc}"},
        )

    return {"answer": answer}


@app.get("/health", response_model=HealthResponse)
async def health(request: Request):
    rules: list[Rule] = request.app.state.rules
    storage: StorageBackend = request.app.state.storage
    db_status: Optional[str] = None
    if hasattr(storage, "is_connected"):
        db_status = "connected" if storage.is_connected else "unreachable"
    elif isinstance(storage, InMemoryStorage):
        db_status = "memory"
    return HealthResponse(
        status="ok",
        policies_loaded=len(rules),
        database=db_status,
    )


@app.get("/policies")
async def list_policies(request: Request):
    rules: list[Rule] = request.app.state.rules
    return {
        "policies": [
            {
                "id": r.id,
                "description": r.description,
                "priority": r.priority,
                "action": r.action,
                "tool": r.match.tool,
                "conditions": [
                    {
                        "field": c.field,
                        "operator": c.operator,
                        "value": c.value,
                    }
                    for c in r.match.conditions
                ],
            }
            for r in rules
        ]
    }


# ── Root ──────────────────────────────────────────────────────────────────

@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard():
    return HTMLResponse(_DASHBOARD_HTML)


_DASHBOARD_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Action Guardrail &mdash; Dashboard</title>
<style>
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, sans-serif;
    background: #0d1117;
    color: #c9d1d9;
    padding: 32px 24px;
    -webkit-font-smoothing: antialiased;
  }
  .container { max-width: 1300px; margin: 0 auto; }

  /* ── header ─────────────────────────────────────── */
  h1 { font-size: 22px; font-weight: 700; color: #e6edf3; margin-bottom: 2px; }
  .subtitle { color: #8b949e; margin-bottom: 28px; font-size: 14px; }

  /* ── api key row ─────────────────────────────────── */
  .api-row {
    display: flex; gap: 10px; align-items: center;
    background: #161b22; border: 1px solid #30363d;
    border-radius: 8px; padding: 14px 18px; margin-bottom: 20px;
  }
  .api-row label { font-size: 12px; font-weight: 600; color: #8b949e; text-transform: uppercase; letter-spacing: .4px; }
  .api-row input {
    padding: 7px 12px; border: 1px solid #30363d; border-radius: 6px;
    font-size: 13px; flex: 1; max-width: 340px;
    font-family: 'SF Mono', 'Cascadia Code', 'Consolas', 'Liberation Mono', monospace;
    background: #0d1117; color: #c9d1d9; outline: none;
    transition: border-color .15s ease;
  }
  .api-row input:focus { border-color: #5b7cfa; }
  .api-row input::placeholder { color: #484f58; }
  .api-row button {
    padding: 7px 22px; background: #5b7cfa; color: #fff;
    border: none; border-radius: 6px; cursor: pointer;
    font-size: 13px; font-weight: 600;
    transition: background .15s ease;
  }
  .api-row button:hover { background: #4b6ae8; }
  .conn-error {
    color: #ff6b7a; background: rgba(220,53,69,0.1);
    padding: 10px 16px; border-radius: 8px; margin-bottom: 16px;
    display: none; font-size: 13px;
    border: 1px solid rgba(220,53,69,0.25);
  }

  /* ── summary cards ────────────────────────────────── */
  .cards {
    display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
    gap: 14px; margin-bottom: 24px;
  }
  .card {
    background: #161b22; border: 1px solid #30363d;
    border-radius: 8px; padding: 20px 22px;
    transition: border-color .15s ease;
  }
  .card:hover { border-color: #484f58; }
  .card h3 {
    font-size: 11px; text-transform: uppercase; letter-spacing: .5px;
    color: #8b949e; margin-bottom: 8px; font-weight: 600;
  }
  .card .value {
    font-size: 30px; font-weight: 800; color: #5b7cfa; line-height: 1.2;
  }
  .card .detail {
    font-size: 12px; color: #c9d1d9; margin-top: 6px;
    font-family: 'SF Mono', 'Cascadia Code', 'Consolas', 'Liberation Mono', monospace;
    line-height: 1.6;
  }

  /* ── filters ─────────────────────────────────────── */
  .filters {
    display: flex; gap: 10px; flex-wrap: wrap; align-items: end; margin-bottom: 16px;
    background: #161b22; border: 1px solid #30363d;
    border-radius: 8px; padding: 13px 16px;
  }
  .filters label {
    font-size: 11px; font-weight: 600; color: #8b949e;
    text-transform: uppercase; letter-spacing: .3px;
    display: flex; flex-direction: column; gap: 4px;
  }
  .filters input, .filters select {
    padding: 6px 10px; border: 1px solid #30363d; border-radius: 6px;
    font-size: 12px; background: #0d1117; color: #c9d1d9;
    outline: none; transition: border-color .15s ease;
  }
  .filters input:focus, .filters select:focus { border-color: #5b7cfa; }
  .filters select option { background: #161b22; color: #c9d1d9; }
  .filters button {
    padding: 7px 18px; background: #5b7cfa; color: #fff;
    border: none; border-radius: 6px; cursor: pointer;
    font-size: 12px; font-weight: 600;
    transition: background .15s ease;
  }
  .filters button:hover { background: #4b6ae8; }
  .export-btn { background: #2ec4b6 !important; }
  .export-btn:hover { background: #25a89c !important; }

  /* ── table ────────────────────────────────────────── */
  table { width: 100%; border-collapse: collapse; background: #161b22; border-radius: 8px; overflow: hidden; border: 1px solid #30363d; }
  th {
    background: #1c2129; padding: 11px 14px; text-align: left;
    font-size: 11px; text-transform: uppercase; letter-spacing: .4px;
    color: #8b949e; font-weight: 600;
    border-bottom: 1px solid #30363d;
  }
  td {
    padding: 10px 14px; font-size: 12px; border-bottom: 1px solid #21262d;
    font-family: 'SF Mono', 'Cascadia Code', 'Consolas', 'Liberation Mono', monospace;
    color: #c9d1d9;
  }
  tr:last-child td { border-bottom: none; }
  tr:hover td { background: rgba(255,255,255,0.03); }

  /* ── outcome badges ───────────────────────────────── */
  .badge {
    display: inline-block; padding: 2px 9px; border-radius: 20px;
    font-size: 11px; font-weight: 600; letter-spacing: .2px;
  }
  .badge.block { background: rgba(220,53,69,0.15); color: #ff6b7a; border: 1px solid rgba(220,53,69,0.2); }
  .badge.allow { background: rgba(40,167,69,0.12); color: #51cf66; border: 1px solid rgba(40,167,69,0.18); }
  .badge.require_hitl { background: rgba(255,193,7,0.12); color: #ffd43b; border: 1px solid rgba(255,193,7,0.18); }
  .badge.log_and_allow { background: rgba(23,162,184,0.12); color: #66d9e8; border: 1px solid rgba(23,162,184,0.18); }

  /* ── utilities ───────────────────────────────────── */
  .error {
    color: #ff6b7a; padding: 12px 16px;
    background: rgba(220,53,69,0.08); border-radius: 8px;
    border: 1px solid rgba(220,53,69,0.2);
    font-family: 'SF Mono', 'Cascadia Code', 'Consolas', 'Liberation Mono', monospace;
    font-size: 12px;
  }
  .loading {
    color: #8b949e; padding: 24px; text-align: center;
    font-size: 13px;
  }

  /* ── groq query ───────────────────────────────────── */
  .query-section {
    margin-top: 20px;
    background: #161b22; border: 1px solid #30363d;
    border-radius: 8px; padding: 16px;
  }
  .query-section h3 {
    font-size: 13px; font-weight: 600; color: #e6edf3;
    margin-bottom: 10px;
  }
  .query-row {
    display: flex; gap: 10px; align-items: flex-start;
  }
  .query-row textarea {
    flex: 1; resize: vertical; min-height: 52px; max-height: 150px;
    padding: 8px 12px; border: 1px solid #30363d; border-radius: 6px;
    font-size: 13px; font-family: 'SF Mono', 'Cascadia Code', 'Consolas', 'Liberation Mono', monospace;
    background: #0d1117; color: #c9d1d9; outline: none;
    transition: border-color .15s ease;
  }
  .query-row textarea:focus { border-color: #5b7cfa; }
  .query-row textarea::placeholder { color: #484f58; }
  .query-row button {
    padding: 8px 22px; background: #5b7cfa; color: #fff;
    border: none; border-radius: 6px; cursor: pointer;
    font-size: 13px; font-weight: 600; white-space: nowrap;
    transition: background .15s ease;
  }
  .query-row button:hover { background: #4b6ae8; }
  .query-row button:disabled { opacity: .5; cursor: not-allowed; }
  #queryAnswer {
    margin-top: 10px; padding: 12px 14px;
    background: #0d1117; border: 1px solid #30363d; border-radius: 6px;
    font-size: 13px; line-height: 1.6; color: #c9d1d9;
    white-space: pre-wrap;
  }
</style>
</head>
<body>
<div class="container">
  <h1>Action Guardrail</h1>
  <div class="subtitle">Audit Log Dashboard</div>

  <div class="api-row">
    <label>API Key</label>
    <input type="text" id="apiKeyInput" value="dev-placeholder-key" placeholder="Enter API key">
    <button onclick="connect()">Connect</button>
  </div>
  <div class="conn-error" id="connError"></div>

  <div class="cards" id="summaryCards">
    <div class="card"><h3>Total Records</h3><div class="value" id="total">&mdash;</div></div>
    <div class="card"><h3>Dry Run</h3><div class="value" id="dryRun">&mdash;</div></div>
    <div class="card"><h3>By Outcome</h3><div class="detail" id="byOutcome">&mdash;</div></div>
    <div class="card"><h3>By Tool</h3><div class="detail" id="byTool">&mdash;</div></div>
  </div>

  <div class="filters">
    <label>Tool <select id="filterTool"><option value="">All</option></select></label>
    <label>Outcome <select id="filterOutcome"><option value="">All</option><option>block</option><option>require_hitl</option><option>log_and_allow</option><option>allow</option></select></label>
    <label>Since <input type="datetime-local" id="filterSince"></label>
    <label>Until <input type="datetime-local" id="filterUntil"></label>
    <button onclick="loadAuditLog()">Search</button>
    <button class="export-btn" onclick="exportCSV()">Export CSV</button>
  </div>

  <div id="tableContainer"><div class="loading">Enter API key and click Connect.</div></div>

  <div class="query-section" id="querySection">
    <h3>Ask Groq about the audit log</h3>
    <div class="query-row">
      <textarea id="queryPrompt" placeholder="e.g. Summarize blocked actions in the last 200 records"></textarea>
      <button id="queryBtn" onclick="queryGroq()">Ask</button>
    </div>
    <div id="queryAnswer" style="display:none"></div>
  </div>
</div>

<script>
const BASE = window.location.origin;
let KEY = "";
const HEADERS = () => ({ "X-API-Key": KEY, "Content-Type": "application/json" });

async function connect() {
  try {
    KEY = document.getElementById("apiKeyInput").value.trim();
    if (!KEY) { showError("Please enter an API key."); return; }
    hideError();
    document.getElementById("tableContainer").innerHTML = "<div class='loading'>Connecting...</div>";
    await loadSummary();
    await loadAuditLog();
  } catch(e) {
    showError("connect() error: " + e.message + " (see browser console)");
    console.error("connect error:", e);
  }
}

function showError(msg) {
  const el = document.getElementById("connError");
  el.textContent = msg;
  el.style.display = "block";
}

function hideError() {
  document.getElementById("connError").style.display = "none";
}

function isoVal(id) {
  const el = document.getElementById(id);
  if (!el || !el.value) return "";
  const d = new Date(el.value);
  if (isNaN(d.getTime())) return "";
  return d.toISOString();
}

async function loadSummary() {
  try {
    const r = await fetch(BASE + "/audit-log/summary", { headers: HEADERS() });
    if (!r.ok) {
      const body = await r.text();
      showError("API error " + r.status + ": " + body.slice(0, 200));
      return;
    }
    const d = await r.json();
    document.getElementById("total").textContent = d.total;
    document.getElementById("dryRun").textContent = d.dry_run_count;
    document.getElementById("byOutcome").textContent = Object.entries(d.by_outcome).map(([k,v]) => k+"="+v).join(", ");
    document.getElementById("byTool").textContent = Object.entries(d.by_tool).map(([k,v]) => k+"="+v).join(", ");
    const sel = document.getElementById("filterTool");
    const tools = Object.keys(d.by_tool).sort();
    sel.innerHTML = "<option value=''>All</option>" + tools.map(t => "<option>"+t+"</option>").join("");
  } catch(e) {
    showError("Network error: " + e.message);
  }
}

async function loadAuditLog() {
  const tc = document.getElementById("tableContainer");
  tc.innerHTML = "<div class='loading'>Loading...</div>";
  const tool = document.getElementById("filterTool").value;
  const outcome = document.getElementById("filterOutcome").value;
  const since = isoVal("filterSince");
  const until = isoVal("filterUntil");
  let url = BASE + "/audit-log?limit=200";
  if (tool) url += "&tool=" + encodeURIComponent(tool);
  if (outcome) url += "&outcome=" + encodeURIComponent(outcome);
  if (since) url += "&since=" + encodeURIComponent(since);
  if (until) url += "&until=" + encodeURIComponent(until);
  try {
    const r = await fetch(url, { headers: HEADERS() });
    if (!r.ok) {
      const body = await r.text();
      tc.innerHTML = "<div class='error'>API error " + r.status + ": " + esc(body.slice(0, 300)) + "</div>";
      return;
    }
    const d = await r.json();
    if (!d.records || d.records.length === 0) {
      tc.innerHTML = "<div class='loading'>No records found.</div>";
      return;
    }
    let html = "<table><thead><tr><th>Time</th><th>Tool</th><th>Outcome</th><th>Rule</th><th>Reason</th><th>Dry Run</th><th>Resolved By</th><th>Resolution</th></tr></thead><tbody>";
    for (const rec of d.records) {
      const t = rec.created_at ? rec.created_at.replace("T"," ").substring(0,19) : "";
      const rule = rec.matched_rule_id || rec.original_intended_decision || "";
      const resolvedBy = rec.hitl_resolved_by || "";
      const resolution = rec.hitl_resolution || "";
      html += "<tr><td>"+t+"</td><td>"+esc(rec.tool)+"</td><td><span class='badge "+rec.outcome+"'>"+rec.outcome+"</span></td><td>"+esc(rule)+"</td><td>"+esc(rec.reason||"")+"</td><td>"+(rec.dry_run?"Yes":"No")+"</td><td>"+esc(resolvedBy)+"</td><td>"+esc(resolution)+"</td></tr>";
    }
    html += "</tbody></table>";
    tc.innerHTML = html;
  } catch(e) {
    tc.innerHTML = "<div class='error'>Failed to load: " + esc(e.message) + "</div>";
  }
}

async function exportCSV() {
  const tool = document.getElementById("filterTool").value;
  const outcome = document.getElementById("filterOutcome").value;
  let url = BASE + "/audit-log/export";
  const params = [];
  if (tool) params.push("tool="+encodeURIComponent(tool));
  if (outcome) params.push("outcome="+encodeURIComponent(outcome));
  if (params.length) url += "?" + params.join("&");
  try {
    const r = await fetch(url, { headers: HEADERS() });
    if (!r.ok) { showError("Export failed: " + r.status); return; }
    const blob = await r.blob();
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "audit-log.csv";
    a.click();
  } catch(e) {
    showError("Export error: " + e.message);
  }
}

async function queryGroq() {
  const prompt = document.getElementById("queryPrompt").value.trim();
  if (!prompt) return;
  const btn = document.getElementById("queryBtn");
  const answerEl = document.getElementById("queryAnswer");
  btn.disabled = true;
  btn.textContent = "Asking...";
  answerEl.style.display = "block";
  answerEl.textContent = "thinking...";
  try {
    const r = await fetch(BASE + "/query", {
      method: "POST",
      headers: HEADERS(),
      body: JSON.stringify({ prompt }),
    });
    if (!r.ok) {
      const body = await r.text();
      answerEl.textContent = "Error " + r.status + ": " + body.slice(0, 500);
      return;
    }
    const d = await r.json();
    answerEl.textContent = d.answer || "(empty response)";
  } catch(e) {
    answerEl.textContent = "Network error: " + e.message;
  } finally {
    btn.disabled = false;
    btn.textContent = "Ask";
  }
}

function esc(s) { const d = document.createElement("div"); d.textContent = s; return d.innerHTML; }
</script>
</body>
</html>
"""


@app.get("/")
async def root():
    return HTMLResponse(_DASHBOARD_HTML)
