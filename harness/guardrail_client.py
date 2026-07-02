import os
import time
from typing import Any, Optional

import httpx

GUARDRAIL_API_URL = os.environ.get(
    "GUARDRAIL_API_URL", "http://localhost:8000"
)
GUARDRAIL_API_KEY = os.environ.get("GUARDRAIL_API_KEY", "dev-placeholder-key")


class GuardrailConnectionError(Exception):
    pass


class GuardrailResponseError(Exception):
    def __init__(self, status_code: int, body: Any):
        self.status_code = status_code
        self.body = body
        super().__init__(f"Guardrail API returned {status_code}: {body}")


def _headers() -> dict:
    return {"X-API-Key": GUARDRAIL_API_KEY, "Content-Type": "application/json"}


def evaluate(tool_call: dict, dry_run: bool = False) -> dict:
    body = {"tool_call": tool_call, "dry_run": dry_run}
    try:
        resp = httpx.post(
            f"{GUARDRAIL_API_URL}/evaluate",
            json=body,
            headers=_headers(),
            timeout=10,
        )
    except httpx.ConnectError:
        raise GuardrailConnectionError(
            f"Cannot connect to guardrail API at {GUARDRAIL_API_URL}. "
            f"Start the server first: uvicorn app.main:app --reload"
        )
    if resp.status_code != 200:
        raise GuardrailResponseError(resp.status_code, resp.text)
    return resp.json()


def get_hitl_request(request_id: str) -> dict:
    try:
        resp = httpx.get(
            f"{GUARDRAIL_API_URL}/hitl/{request_id}",
            headers=_headers(),
            timeout=10,
        )
    except httpx.ConnectError:
        raise GuardrailConnectionError(
            f"Cannot connect to guardrail API at {GUARDRAIL_API_URL}"
        )
    if resp.status_code == 404:
        raise GuardrailResponseError(404, f"HITL request {request_id} not found")
    if resp.status_code != 200:
        raise GuardrailResponseError(resp.status_code, resp.text)
    return resp.json()


def approve_hitl(request_id: str, resolved_by: str = "scenario-runner") -> dict:
    try:
        resp = httpx.post(
            f"{GUARDRAIL_API_URL}/hitl/{request_id}/approve",
            json={"resolved_by": resolved_by},
            headers=_headers(),
            timeout=10,
        )
    except httpx.ConnectError:
        raise GuardrailConnectionError(
            f"Cannot connect to guardrail API at {GUARDRAIL_API_URL}"
        )
    if resp.status_code != 200:
        raise GuardrailResponseError(resp.status_code, resp.text)
    return resp.json()


def reject_hitl(request_id: str, resolved_by: str = "scenario-runner") -> dict:
    try:
        resp = httpx.post(
            f"{GUARDRAIL_API_URL}/hitl/{request_id}/reject",
            json={"resolved_by": resolved_by},
            headers=_headers(),
            timeout=10,
        )
    except httpx.ConnectError:
        raise GuardrailConnectionError(
            f"Cannot connect to guardrail API at {GUARDRAIL_API_URL}"
        )
    if resp.status_code != 200:
        raise GuardrailResponseError(resp.status_code, resp.text)
    return resp.json()


def list_audit_log(
    limit: int = 100,
    tool: Optional[str] = None,
    outcome: Optional[str] = None,
) -> dict:
    params: dict[str, Any] = {"limit": limit}
    if tool:
        params["tool"] = tool
    if outcome:
        params["outcome"] = outcome
    try:
        resp = httpx.get(
            f"{GUARDRAIL_API_URL}/audit-log",
            params=params,
            headers=_headers(),
            timeout=10,
        )
    except httpx.ConnectError:
        raise GuardrailConnectionError(
            f"Cannot connect to guardrail API at {GUARDRAIL_API_URL}"
        )
    if resp.status_code != 200:
        raise GuardrailResponseError(resp.status_code, resp.text)
    return resp.json()


def poll_hitl(
    request_id: str,
    timeout_s: int = 30,
    interval_s: int = 2,
) -> dict:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        data = get_hitl_request(request_id)
        req = data["request"]
        status = req["status"]
        if status != "pending":
            return req
        time.sleep(interval_s)
    return {"status": "timeout", "id": request_id}
