import os
import time
from typing import Any, Callable, Optional

import httpx


def _api_url() -> str:
    return os.environ.get("GUARDRAIL_API_URL", "http://localhost:8000")


def _request_timeout() -> int:
    return int(os.environ.get("GUARDRAIL_TIMEOUT", "30"))


class GuardrailConnectionError(Exception):
    pass


class GuardrailResponseError(Exception):
    def __init__(self, status_code: int, body: Any):
        self.status_code = status_code
        self.body = body
        super().__init__(f"Guardrail API returned {status_code}: {body}")


def _headers() -> dict:
    api_key = os.environ.get(
        "GUARDRAIL_API_KEY",
        os.environ.get("API_KEY", "dev-placeholder-key"),
    )
    return {"X-API-Key": api_key, "Content-Type": "application/json"}


def _retry_on_connection_errors(fn: Callable[[], httpx.Response]) -> httpx.Response:
    """Retry the callable up to 3x (2s, 5s, 10s backoff) for connection
    errors or timeouts only.  4xx/5xx responses are surfaced immediately."""
    delays = [2, 5, 10]
    for attempt in range(len(delays) + 1):
        try:
            resp = fn()
            return resp
        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            if attempt >= len(delays):
                raise GuardrailConnectionError(
                    f"Cannot reach guardrail at {_api_url()} "
                    f"after {len(delays) + 1} attempts: {exc}"
                ) from exc
            print(
                f"  [RETRY] Connection error (attempt {attempt + 1}), "
                f"retrying in {delays[attempt]}s..."
            )
            time.sleep(delays[attempt])
    raise RuntimeError("unreachable")


def evaluate(tool_call: dict, dry_run: bool = False) -> dict:
    body = {"tool_call": tool_call, "dry_run": dry_run}

    def _do() -> httpx.Response:
        return httpx.post(
            f"{_api_url()}/evaluate",
            json=body,
            headers=_headers(),
            timeout=_request_timeout(),
        )

    resp = _retry_on_connection_errors(_do)
    if resp.status_code != 200:
        raise GuardrailResponseError(resp.status_code, resp.text)
    return resp.json()


def get_hitl_request(request_id: str) -> dict:
    def _do() -> httpx.Response:
        return httpx.get(
            f"{_api_url()}/hitl/{request_id}",
            headers=_headers(),
            timeout=_request_timeout(),
        )

    resp = _retry_on_connection_errors(_do)
    if resp.status_code == 404:
        raise GuardrailResponseError(404, f"HITL request {request_id} not found")
    if resp.status_code != 200:
        raise GuardrailResponseError(resp.status_code, resp.text)
    return resp.json()


def approve_hitl(request_id: str, resolved_by: str = "scenario-runner") -> dict:
    def _do() -> httpx.Response:
        return httpx.post(
            f"{_api_url()}/hitl/{request_id}/approve",
            json={"resolved_by": resolved_by},
            headers=_headers(),
            timeout=_request_timeout(),
        )

    resp = _retry_on_connection_errors(_do)
    if resp.status_code != 200:
        raise GuardrailResponseError(resp.status_code, resp.text)
    return resp.json()


def reject_hitl(request_id: str, resolved_by: str = "scenario-runner") -> dict:
    def _do() -> httpx.Response:
        return httpx.post(
            f"{_api_url()}/hitl/{request_id}/reject",
            json={"resolved_by": resolved_by},
            headers=_headers(),
            timeout=_request_timeout(),
        )

    resp = _retry_on_connection_errors(_do)
    if resp.status_code != 200:
        raise GuardrailResponseError(resp.status_code, resp.text)
    return resp.json()


def list_pending_hitl() -> list[dict]:
    def _do() -> httpx.Response:
        return httpx.get(
            f"{_api_url()}/hitl/pending",
            headers=_headers(),
            timeout=_request_timeout(),
        )

    resp = _retry_on_connection_errors(_do)
    if resp.status_code != 200:
        raise GuardrailResponseError(resp.status_code, resp.text)
    return resp.json().get("pending", [])


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

    def _do() -> httpx.Response:
        return httpx.get(
            f"{_api_url()}/audit-log",
            params=params,
            headers=_headers(),
            timeout=_request_timeout(),
        )

    resp = _retry_on_connection_errors(_do)
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
