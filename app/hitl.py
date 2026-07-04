from datetime import datetime, timezone
from typing import Literal, Optional

from app.models import Decision, HitlRequest, ToolCall
from app.storage import StorageBackend


def get_hitl_request_by_id(
    storage: StorageBackend, request_id: str
) -> Optional[HitlRequest]:
    return storage.get_hitl_request(request_id)


def create_hitl_request(
    storage: StorageBackend,
    tool_call: ToolCall,
    decision: Decision,
    audit_record_id: Optional[str] = None,
) -> HitlRequest:
    request = HitlRequest(
        id="",
        tool_call=tool_call,
        decision=decision,
        status="pending",
        created_at=datetime.now(timezone.utc),
        audit_record_id=audit_record_id,
    )
    request_id = storage.create_hitl_request(request)
    request.id = request_id
    return request


def get_pending_hitl_requests(
    storage: StorageBackend,
) -> list[HitlRequest]:
    return storage.list_pending_hitl()


def resolve_request(
    storage: StorageBackend,
    request_id: str,
    resolution: Literal["approved", "rejected"],
    resolved_by: str,
) -> HitlRequest:
    return storage.resolve_hitl_request(
        request_id=request_id, resolution=resolution, resolved_by=resolved_by
    )
