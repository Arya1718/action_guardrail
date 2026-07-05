from datetime import datetime, timezone
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


Operator = Literal[
    "eq", "ne", "gt", "gte", "lt", "lte", "in", "not_in", "contains", "regex"
]

Action = Literal["block", "require_hitl", "log_and_allow"]

Outcome = Literal["block", "require_hitl", "log_and_allow", "allow"]


class Condition(BaseModel):
    field: str
    operator: Operator
    value: Any


class Match(BaseModel):
    tool: str
    conditions: list[Condition]


class Rule(BaseModel):
    id: str
    description: str = ""
    priority: int = 10
    action: Action
    match: Match


class ToolCall(BaseModel):
    tool: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    agent_id: Optional[str] = None
    session_id: Optional[str] = None
    call_id: Optional[str] = None
    timestamp: Optional[datetime] = None


class Decision(BaseModel):
    outcome: Outcome
    matched_rule_id: Optional[str] = None
    reason: str = ""
    evaluated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class AuditRecord(BaseModel):
    id: str
    tool_call: ToolCall
    decision: Decision
    dry_run: bool = False
    original_intended_decision: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    hitl_request_id: Optional[str] = None
    hitl_resolved_by: Optional[str] = None
    hitl_resolution: Optional[Literal["approved", "rejected"]] = None
    hitl_resolved_at: Optional[datetime] = None
    org_id: str = ""


class HitlRequest(BaseModel):
    id: str
    tool_call: ToolCall
    decision: Decision
    status: Literal["pending", "approved", "rejected"] = "pending"
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    resolved_at: Optional[datetime] = None
    resolved_by: Optional[str] = None
    audit_record_id: Optional[str] = None
    org_id: str = ""
