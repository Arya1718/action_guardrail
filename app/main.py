import logging
import time
from contextlib import asynccontextmanager
from typing import Any, Literal, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
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
from app.models import Rule, ToolCall
from app.policy_loader import load_policies
from app.storage import InMemoryStorage, StorageBackend

logger = logging.getLogger(__name__)

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


# Rules are loaded once and reused. Storage is re-created fresh when the
# lifespan runs (TestClient, uvicorn), but the module-level fallback
# ensures Mangum (lifespan="off") still works on Lambda.
logger.info("Loading policies from %s", settings.POLICY_FILE_PATH)
_rules = load_policies(settings.POLICY_FILE_PATH)
logger.info("Loaded %d rules", len(_rules))


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.rules = _rules
    app.state.storage = _make_storage()
    yield


app = FastAPI(title="Action Guardrail", version="2.0.0", lifespan=lifespan)
app.state.rules = _rules
app.state.storage = _make_storage()


# ── Request/Response models ──────────────────────────────────────────────


class EvaluateRequest(BaseModel):
    tool_call: ToolCall
    dry_run: bool = False


class EvaluateResponse(BaseModel):
    outcome: Literal["block", "require_hitl", "log_and_allow", "allow"]
    matched_rule_id: Optional[str] = None
    reason: str = ""
    dry_run: bool = False
    hitl_request_id: Optional[str] = None
    message: str = ""


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


class HealthResponse(BaseModel):
    status: str
    policies_loaded: int
    database: Optional[str] = None


# ── Exception handlers ───────────────────────────────────────────────────


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.exception("Unhandled exception on %s %s", request.method, request.url)
    msg = str(exc) if str(exc) else "Internal server error"
    status = 503 if "MongoDB" in msg or "storage unavailable" in msg else 500
    return JSONResponse(
        status_code=status,
        content={"error": "Internal server error", "detail": msg},
    )


# ── Endpoints ────────────────────────────────────────────────────────────


@app.post("/evaluate", response_model=EvaluateResponse)
async def evaluate(req: EvaluateRequest, request: Request):
    rules: list[Rule] = request.app.state.rules
    storage: StorageBackend = request.app.state.storage

    start = time.perf_counter()
    decision = evaluate_action(req.tool_call, rules)
    latency_ms = round((time.perf_counter() - start) * 1000, 2)

    audit_record = write_audit_log(storage, req.tool_call, decision, dry_run=req.dry_run)

    hitl_request_id: Optional[str] = None
    message = ""
    outcome = decision.outcome

    if outcome == "block":
        if req.dry_run:
            message = (
                f"DRY-RUN: Would have blocked call to '{req.tool_call.tool}'. "
                f"Matched rule '{decision.matched_rule_id}'. "
                "No enforcement applied."
            )
        else:
            message = (
                f"Call to '{req.tool_call.tool}' blocked by rule "
                f"'{decision.matched_rule_id}'. Do not execute."
            )

    elif outcome == "require_hitl":
        if req.dry_run:
            message = (
                f"DRY-RUN: Would have required HITL for call to "
                f"'{req.tool_call.tool}'. No pending request created."
            )
        else:
            hitl_req = create_hitl_request(storage, req.tool_call, decision)
            hitl_request_id = hitl_req.id
            message = (
                f"HITL required for call to '{req.tool_call.tool}'. "
                f"Pending request id={hitl_request_id}. "
                "Await human approval before executing."
            )

    elif outcome == "log_and_allow":
        message = (
            f"Call to '{req.tool_call.tool}' logged and allowed. "
            f"Matched rule '{decision.matched_rule_id}'."
        )

    else:
        message = f"Call to '{req.tool_call.tool}' allowed (no matching rule)."

    logger.info(
        "EVALUATE tool=%s outcome=%s latency_ms=%s dry_run=%s rule=%s audit_id=%s",
        req.tool_call.tool,
        outcome,
        latency_ms,
        req.dry_run,
        decision.matched_rule_id,
        audit_record.id,
    )

    return EvaluateResponse(
        outcome=outcome,
        matched_rule_id=decision.matched_rule_id,
        reason=decision.reason,
        dry_run=req.dry_run,
        hitl_request_id=hitl_request_id,
        message=message,
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
    return {"request": req}


@app.get("/audit-log")
async def list_audit_log(
    request: Request,
    limit: int = 100,
    tool: Optional[str] = None,
    outcome: Optional[str] = None,
):
    storage: StorageBackend = request.app.state.storage
    records = query_audit_log(storage, limit=limit, tool=tool, outcome=outcome)
    return {
        "records": [
            AuditLogEntry(
                id=r.id,
                tool=r.tool_call.tool,
                outcome=r.decision.outcome,
                dry_run=r.dry_run,
                reason=r.decision.reason,
                created_at=r.created_at.isoformat(),
            )
            for r in records
        ]
    }


@app.get("/health", response_model=HealthResponse)
async def health(request: Request):
    rules: list[Rule] = request.app.state.rules
    storage: StorageBackend = request.app.state.storage
    db_status: Optional[str] = None
    if hasattr(storage, "is_connected"):
        db_status = "connected" if storage.is_connected else "unreachable"
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


import os as _os
_static_dir = _os.path.join(_os.path.dirname(__file__), "static")
if _os.path.isdir(_static_dir):
    app.mount("/static", StaticFiles(directory=_static_dir), name="static")

    @app.get("/")
    async def dashboard():
        dash_path = _os.path.join(_static_dir, "dashboard.html")
        with open(dash_path, encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
else:
    logger.warning("No static directory found at %s — dashboard disabled", _static_dir)
