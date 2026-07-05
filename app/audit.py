import uuid
from datetime import datetime
from typing import Optional

from app.models import AuditRecord, Decision, ToolCall
from app.storage import StorageBackend


def write_audit_log(
    storage: StorageBackend,
    tool_call: ToolCall,
    decision: Decision,
    dry_run: bool = False,
    original_intended_decision: Optional[str] = None,
    org_id: str = "",
) -> AuditRecord:
    record = AuditRecord(
        id=str(uuid.uuid4()),
        tool_call=tool_call,
        decision=decision,
        dry_run=dry_run,
        original_intended_decision=original_intended_decision,
        org_id=org_id,
    )
    storage.write_audit_record(record)
    return record


def query_audit_log(
    storage: StorageBackend,
    limit: int = 100,
    tool: Optional[str] = None,
    outcome: Optional[str] = None,
    since: Optional[datetime] = None,
    until: Optional[datetime] = None,
    org_id: Optional[str] = None,
) -> list[AuditRecord]:
    return storage.list_audit_records(
        limit=limit, tool=tool, outcome=outcome, since=since, until=until, org_id=org_id,
    )
