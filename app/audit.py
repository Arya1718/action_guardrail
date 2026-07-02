import uuid
from typing import Optional

from app.models import AuditRecord, Decision, ToolCall
from app.storage import StorageBackend


def write_audit_log(
    storage: StorageBackend,
    tool_call: ToolCall,
    decision: Decision,
    dry_run: bool = False,
) -> AuditRecord:
    record = AuditRecord(
        id=str(uuid.uuid4()),
        tool_call=tool_call,
        decision=decision,
        dry_run=dry_run,
    )
    storage.write_audit_record(record)
    return record


def query_audit_log(
    storage: StorageBackend,
    limit: int = 100,
    tool: Optional[str] = None,
    outcome: Optional[str] = None,
) -> list[AuditRecord]:
    return storage.list_audit_records(limit=limit, tool=tool, outcome=outcome)
