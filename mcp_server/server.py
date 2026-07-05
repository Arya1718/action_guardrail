#!/usr/bin/env python3
"""MCP (Model Context Protocol) server for Action Guardrail.

Provides tools that any MCP-compatible client (Claude Desktop, Claude Code,
etc.) can call as part of their own tool-calling loop.

Run locally:
    python mcp_server/server.py

Then connect via Claude Desktop config (see mcp_server/README.md).
"""

import asyncio
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")
logger = logging.getLogger("mcp-guardrail")

# ── Bootstrap storage & evaluator (same pattern as app/main.py) ──────────────

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import Settings
from app.evaluator import evaluate_action
from app.hitl import create_hitl_request, get_pending_hitl_requests, resolve_request
from app.notifications import notify_slack
from app.models import Rule, ToolCall
from app.policy_loader import load_policies
from app.storage import InMemoryStorage, StorageBackend

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


_policy_path = settings.POLICY_FILE_PATH
logger.info("Loading policies from %s", _policy_path)
_rules: list[Rule] = load_policies(_policy_path)
logger.info("Loaded %d rules", len(_rules))

_storage: StorageBackend = _make_storage()
logger.info("Storage backend: %s", type(_storage).__name__)


# ── Tool implementations (called by MCP) ─────────────────────────────────────

def _tool_evaluate_action(tool_name: str, parameters: dict[str, Any]) -> dict:
    """Evaluate a tool call against all loaded policies."""
    tc = ToolCall(tool=tool_name, parameters=parameters)
    decision = evaluate_action(tc, _rules)

    record_id = ""
    hitl_request_id = ""
    if decision.outcome == "require_hitl":
        from app.audit import write_audit_log
        audit = write_audit_log(_storage, tc, decision, dry_run=False)
        record_id = audit.id
        hitl = create_hitl_request(_storage, tc, decision, audit_record_id=record_id)
        hitl_request_id = hitl.id
    elif decision.outcome != "allow":
        from app.audit import write_audit_log
        audit = write_audit_log(_storage, tc, decision, dry_run=False)
        record_id = audit.id

    return {
        "outcome": decision.outcome,
        "matched_rule_id": decision.matched_rule_id,
        "reason": decision.reason,
        "audit_id": record_id,
        "hitl_request_id": hitl_request_id,
        "evaluated_at": decision.evaluated_at.isoformat(),
    }


def _tool_list_pending_reviews() -> list[dict]:
    """List all pending HITL review requests."""
    pending = get_pending_hitl_requests(_storage)
    result = []
    for req in pending:
        result.append({
            "id": req.id,
            "tool": req.tool_call.tool,
            "parameters": req.tool_call.parameters,
            "matched_rule_id": req.decision.matched_rule_id,
            "reason": req.decision.reason,
            "created_at": req.created_at.isoformat(),
            "status": req.status,
        })
    return result


def _tool_approve_review(request_id: str, resolved_by: str) -> dict:
    """Approve a pending HITL request."""
    try:
        req = resolve_request(_storage, request_id, "approved", resolved_by)
    except KeyError:
        return {"error": f"HITL request '{request_id}' not found"}
    except ValueError as e:
        return {"error": str(e)}

    if req.audit_record_id:
        try:
            _storage.update_audit_record(
                req.audit_record_id,
                hitl_resolved_by=resolved_by,
                hitl_resolution="approved",
                hitl_resolved_at=datetime.now(timezone.utc),
            )
        except Exception as exc:
            logger.warning("Failed to update audit record %s: %s", req.audit_record_id, exc)

    return {
        "status": "approved",
        "request_id": request_id,
        "resolved_by": resolved_by,
        "resolved_at": req.resolved_at.isoformat() if req.resolved_at else "",
    }


def _tool_reject_review(request_id: str, resolved_by: str) -> dict:
    """Reject a pending HITL request."""
    try:
        req = resolve_request(_storage, request_id, "rejected", resolved_by)
    except KeyError:
        return {"error": f"HITL request '{request_id}' not found"}
    except ValueError as e:
        return {"error": str(e)}

    if req.audit_record_id:
        try:
            _storage.update_audit_record(
                req.audit_record_id,
                hitl_resolved_by=resolved_by,
                hitl_resolution="rejected",
                hitl_resolved_at=datetime.now(timezone.utc),
            )
        except Exception as exc:
            logger.warning("Failed to update audit record %s: %s", req.audit_record_id, exc)

    return {
        "status": "rejected",
        "request_id": request_id,
        "resolved_by": resolved_by,
        "resolved_at": req.resolved_at.isoformat() if req.resolved_at else "",
    }


# ── MCP server setup ─────────────────────────────────────────────────────────

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

server = Server("action-guardrail")


@server.list_tools()
async def handle_list_tools() -> list[Tool]:
    return [
        Tool(
            name="evaluate_action",
            title="Evaluate a tool call against guardrail policies",
            description="Checks whether an AI agent's proposed tool call is allowed, blocked, or requires human review. Returns a decision with outcome, matched rule, and reason.",
            inputSchema={
                "type": "object",
                "properties": {
                    "tool_name": {"type": "string", "description": "Name of the tool the agent wants to call (e.g. delete_records, send_email)"},
                    "parameters": {"type": "object", "description": "Parameters passed to the tool (e.g. {\"count\": 500})"},
                },
                "required": ["tool_name", "parameters"],
            },
        ),
        Tool(
            name="list_pending_reviews",
            title="List pending HITL review requests",
            description="Returns all guardrail reviews that are waiting for a human decision. Each entry shows the tool, parameters, matched rule, and reason.",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        Tool(
            name="approve_review",
            title="Approve a pending HITL review",
            description="Approves a pending human-in-the-loop review, allowing the blocked action to proceed.",
            inputSchema={
                "type": "object",
                "properties": {
                    "request_id": {"type": "string", "description": "ID of the pending review request"},
                    "resolved_by": {"type": "string", "description": "Name or identifier of the person approving"},
                },
                "required": ["request_id", "resolved_by"],
            },
        ),
        Tool(
            name="reject_review",
            title="Reject a pending HITL review",
            description="Rejects a pending human-in-the-loop review, permanently blocking the action.",
            inputSchema={
                "type": "object",
                "properties": {
                    "request_id": {"type": "string", "description": "ID of the pending review request"},
                    "resolved_by": {"type": "string", "description": "Name or identifier of the person rejecting"},
                },
                "required": ["request_id", "resolved_by"],
            },
        ),
    ]


@server.call_tool()
async def handle_call_tool(name: str, arguments: dict) -> list[TextContent]:
    logger.info("Tool call: %s %s", name, arguments)

    if name == "evaluate_action":
        result = _tool_evaluate_action(
            tool_name=arguments["tool_name"],
            parameters=arguments.get("parameters", {}),
        )
        if result.get("outcome") in ("block", "require_hitl"):
            asyncio.create_task(
                notify_slack(
                    arguments["tool_name"],
                    result["outcome"],
                    result.get("matched_rule_id"),
                    result.get("reason", ""),
                )
            )
    elif name == "list_pending_reviews":
        result = _tool_list_pending_reviews()
    elif name == "approve_review":
        result = _tool_approve_review(
            request_id=arguments["request_id"],
            resolved_by=arguments["resolved_by"],
        )
    elif name == "reject_review":
        result = _tool_reject_review(
            request_id=arguments["request_id"],
            resolved_by=arguments["resolved_by"],
        )
    else:
        raise ValueError(f"Unknown tool: {name}")

    return [TextContent(type="text", text=json.dumps(result, indent=2))]


async def main():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    import anyio
    anyio.run(main)
