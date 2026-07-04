import uuid
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from decimal import Decimal
from threading import Lock
from typing import Any, Literal, Optional

from app.models import AuditRecord, Decision, HitlRequest, ToolCall


class StorageBackend(ABC):

    @abstractmethod
    def write_audit_record(self, record: AuditRecord) -> None:
        ...

    @abstractmethod
    def list_audit_records(
        self,
        limit: int = 100,
        tool: Optional[str] = None,
        outcome: Optional[str] = None,
        since: Optional[datetime] = None,
        until: Optional[datetime] = None,
    ) -> list[AuditRecord]:
        ...

    @abstractmethod
    def create_hitl_request(self, request: HitlRequest) -> str:
        ...

    @abstractmethod
    def get_hitl_request(self, request_id: str) -> Optional[HitlRequest]:
        ...

    @abstractmethod
    def list_pending_hitl(self) -> list[HitlRequest]:
        ...

    @abstractmethod
    def resolve_hitl_request(
        self,
        request_id: str,
        resolution: Literal["approved", "rejected"],
        resolved_by: str,
    ) -> HitlRequest:
        ...

    def update_audit_record(
        self,
        record_id: str,
        **updates: Any,
    ) -> Optional[AuditRecord]:
        """Update specific fields of an audit record by ID.
        Default: no-op (DynamoDBStorage does not support this).
        """
        return None


class InMemoryStorage(StorageBackend):

    def __init__(self) -> None:
        self._lock = Lock()
        self._audit_records: list[AuditRecord] = []
        self._hitl_requests: dict[str, HitlRequest] = {}

    def write_audit_record(self, record: AuditRecord) -> None:
        with self._lock:
            self._audit_records.append(record)

    def update_audit_record(
        self,
        record_id: str,
        **updates: Any,
    ) -> Optional[AuditRecord]:
        with self._lock:
            for r in self._audit_records:
                if r.id == record_id:
                    for k, v in updates.items():
                        setattr(r, k, v)
                    return r
        return None

    def list_audit_records(
        self,
        limit: int = 100,
        tool: Optional[str] = None,
        outcome: Optional[str] = None,
        since: Optional[datetime] = None,
        until: Optional[datetime] = None,
    ) -> list[AuditRecord]:
        with self._lock:
            results = list(self._audit_records)
        results.reverse()
        if tool is not None:
            results = [r for r in results if r.tool_call.tool == tool]
        if outcome is not None:
            results = [r for r in results if r.decision.outcome == outcome]
        if since is not None:
            results = [r for r in results if r.created_at >= since]
        if until is not None:
            results = [r for r in results if r.created_at <= until]
        return results[:limit]

    def create_hitl_request(self, request: HitlRequest) -> str:
        with self._lock:
            request_id = str(uuid.uuid4())
            request.id = request_id
            self._hitl_requests[request_id] = request
            return request_id

    def get_hitl_request(self, request_id: str) -> Optional[HitlRequest]:
        with self._lock:
            return self._hitl_requests.get(request_id)

    def list_pending_hitl(self) -> list[HitlRequest]:
        with self._lock:
            return [
                r for r in self._hitl_requests.values() if r.status == "pending"
            ]

    def resolve_hitl_request(
        self,
        request_id: str,
        resolution: Literal["approved", "rejected"],
        resolved_by: str,
    ) -> HitlRequest:
        with self._lock:
            req = self._hitl_requests.get(request_id)
            if req is None:
                raise KeyError(f"HitlRequest '{request_id}' not found")
            if req.status != "pending":
                raise ValueError(
                    f"HitlRequest '{request_id}' is already {req.status}"
                )
            req.status = resolution
            req.resolved_by = resolved_by
            req.resolved_at = datetime.now(timezone.utc)
            return req


class DynamoDBStorage(StorageBackend):
    """
    DynamoDB-backed storage for audit log and HITL queue.

    Tables (created by SAM template):
      - guardrail-audit-log (partition key: id)
      - guardrail-hitl-queue  (partition key: id,
                               GSI: status-index on status)

    Production-scale note:
      For high-volume audit-log queries filtered by tool or outcome, a GSI on
      (tool, created_at) and (outcome, created_at) would replace the full scan.
      For HITL, the existing status-index GSI is sufficient at all expected
      volumes because pending requests are a tiny fraction of total.
    """

    AUDIT_TABLE = "guardrail-audit-log"
    HITL_TABLE = "guardrail-hitl-queue"

    def __init__(self) -> None:
        import boto3

        self._dynamodb = boto3.resource("dynamodb")
        self._audit_table = self._dynamodb.Table(self.AUDIT_TABLE)
        self._hitl_table = self._dynamodb.Table(self.HITL_TABLE)

    # ── serialization helpers ──────────────────────────────────────────

    @staticmethod
    def _to_item(obj: Any) -> Any:
        """Recursively convert Pydantic values for DynamoDB."""
        if isinstance(obj, dict):
            return {k: DynamoDBStorage._to_item(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [DynamoDBStorage._to_item(v) for v in obj]
        if hasattr(obj, "isoformat"):
            return obj.isoformat()
        return obj

    @staticmethod
    def _from_item(obj: Any) -> Any:
        """Recursively convert DynamoDB items back to Python types."""
        if isinstance(obj, Decimal):
            return int(obj) if obj % 1 == 0 else float(obj)
        if isinstance(obj, dict):
            return {k: DynamoDBStorage._from_item(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [DynamoDBStorage._from_item(v) for v in obj]
        return obj

    @staticmethod
    def _parse_dt(value: Any) -> Optional[datetime]:
        if isinstance(value, str):
            return datetime.fromisoformat(value)
        return None

    # ── audit log ──────────────────────────────────────────────────────

    def write_audit_record(self, record: AuditRecord) -> None:
        from botocore.exceptions import ClientError

        item = self._to_item(record.model_dump())
        try:
            self._audit_table.put_item(Item=item)
        except ClientError as e:
            raise RuntimeError(
                f"DynamoDB write failed: {e.response['Error']['Message']}"
            ) from e

    def list_audit_records(
        self,
        limit: int = 100,
        tool: Optional[str] = None,
        outcome: Optional[str] = None,
        since: Optional[datetime] = None,
        until: Optional[datetime] = None,
    ) -> list[AuditRecord]:
        from botocore.exceptions import ClientError

        kwargs: dict[str, Any] = {"Limit": limit}
        expr_parts: list[str] = []
        attr_vals: dict[str, Any] = {}

        if tool is not None:
            expr_parts.append("tool_call.tool = :tool")
            attr_vals[":tool"] = tool

        if outcome is not None:
            expr_parts.append("decision.outcome = :outcome")
            attr_vals[":outcome"] = outcome

        if expr_parts:
            kwargs["FilterExpression"] = " AND ".join(expr_parts)
            kwargs["ExpressionAttributeValues"] = attr_vals

        try:
            resp = self._audit_table.scan(**kwargs)
        except ClientError as e:
            raise RuntimeError(
                f"DynamoDB scan failed: {e.response['Error']['Message']}"
            ) from e

        items = resp.get("Items", [])
        result: list[AuditRecord] = []
        for item in items:
            cleaned = self._from_item(item)
            cleaned["created_at"] = self._parse_dt(cleaned.get("created_at"))
            if "decision" in cleaned and "evaluated_at" in cleaned["decision"]:
                cleaned["decision"]["evaluated_at"] = self._parse_dt(
                    cleaned["decision"]["evaluated_at"]
                )
            try:
                result.append(AuditRecord.model_validate(cleaned))
            except Exception:
                continue

        result.reverse()
        return result[:limit]

    # ── HITL queue ─────────────────────────────────────────────────────

    def create_hitl_request(self, request: HitlRequest) -> str:
        from botocore.exceptions import ClientError

        request_id = str(uuid.uuid4())
        request.id = request_id
        item = self._to_item(request.model_dump())
        try:
            self._hitl_table.put_item(Item=item)
        except ClientError as e:
            raise RuntimeError(
                f"DynamoDB write failed: {e.response['Error']['Message']}"
            ) from e
        return request_id

    def get_hitl_request(self, request_id: str) -> Optional[HitlRequest]:
        from botocore.exceptions import ClientError

        try:
            resp = self._hitl_table.get_item(Key={"id": request_id})
        except ClientError as e:
            raise RuntimeError(
                f"DynamoDB get failed: {e.response['Error']['Message']}"
            ) from e
        item = resp.get("Item")
        if item is None:
            return None
        return self._hitl_from_item(item)

    def list_pending_hitl(self) -> list[HitlRequest]:
        from botocore.exceptions import ClientError

        try:
            resp = self._hitl_table.query(
                IndexName="status-index",
                KeyConditionExpression="#s = :pending",
                ExpressionAttributeNames={"#s": "status"},
                ExpressionAttributeValues={":pending": "pending"},
            )
        except ClientError as e:
            raise RuntimeError(
                f"DynamoDB query failed: {e.response['Error']['Message']}"
            ) from e

        result: list[HitlRequest] = []
        for item in resp.get("Items", []):
            try:
                result.append(self._hitl_from_item(item))
            except Exception:
                continue
        return result

    def resolve_hitl_request(
        self,
        request_id: str,
        resolution: Literal["approved", "rejected"],
        resolved_by: str,
    ) -> HitlRequest:
        from botocore.exceptions import ClientError

        existing = self.get_hitl_request(request_id)
        if existing is None:
            raise KeyError(f"HitlRequest '{request_id}' not found")
        if existing.status != "pending":
            raise ValueError(
                f"HitlRequest '{request_id}' is already {existing.status}"
            )

        now_iso = datetime.now(timezone.utc).isoformat()
        try:
            self._hitl_table.update_item(
                Key={"id": request_id},
                UpdateExpression=(
                    "SET #s = :res, resolved_by = :by, resolved_at = :at"
                ),
                ExpressionAttributeNames={"#s": "status"},
                ExpressionAttributeValues={
                    ":res": resolution,
                    ":by": resolved_by,
                    ":at": now_iso,
                    ":pending": "pending",
                },
                ConditionExpression="#s = :pending",
            )
        except ClientError as e:
            code = e.response["Error"]["Code"]
            if code == "ConditionalCheckFailedException":
                raise ValueError(
                    f"HitlRequest '{request_id}' was already resolved"
                ) from e
            raise RuntimeError(
                f"DynamoDB update failed: {e.response['Error']['Message']}"
            ) from e

        updated = self.get_hitl_request(request_id)
        if updated is None:
            raise RuntimeError(f"HitlRequest '{request_id}' lost after update")
        return updated

    # ── internal helpers ───────────────────────────────────────────────

    @staticmethod
    def _hitl_from_item(item: dict) -> HitlRequest:
        cleaned = {
            k: DynamoDBStorage._from_item(v) for k, v in item.items()
        }
        cleaned["created_at"] = DynamoDBStorage._parse_dt(
            cleaned.get("created_at")
        )
        resolved_at = cleaned.get("resolved_at")
        if resolved_at:
            cleaned["resolved_at"] = DynamoDBStorage._parse_dt(resolved_at)
        if "decision" in cleaned and "evaluated_at" in cleaned["decision"]:
            cleaned["decision"]["evaluated_at"] = DynamoDBStorage._parse_dt(
                cleaned["decision"]["evaluated_at"]
            )
        return HitlRequest.model_validate(cleaned)


class MongoStorage(StorageBackend):
    """
    MongoDB-backed storage for audit log and HITL queue.

    Collections (in database ``guardrail``):
      - audit_log  (indexed on tool_call.tool, decision.outcome)
      - hitl_queue (indexed on status)

    Documents use a UUID string as ``_id`` (not ObjectId) to keep the
    response schema consistent with InMemoryStorage.
    """

    def __init__(self, uri: str = "", db_name: str = "guardrail") -> None:
        from pymongo import MongoClient
        from pymongo.errors import PyMongoError

        self._client: Optional[MongoClient] = None
        self._db = None
        self._connected = False

        if not uri:
            import logging as _lg
            _lg.getLogger(__name__).warning(
                "MONGO_URI is empty — MongoStorage will be unavailable"
            )
            return

        try:
            self._client = MongoClient(uri, serverSelectionTimeoutMS=3000)
            self._client.admin.command("ping")
            self._db = self._client[db_name]
            self._ensure_indexes()
            self._connected = True
        except PyMongoError as exc:
            import logging as _lg
            _lg.getLogger(__name__).error(
                "MongoDB connection failed: %s", exc
            )

    # ── public helpers ────────────────────────────────────────────────

    @property
    def is_connected(self) -> bool:
        return self._connected

    # ── internal helpers ──────────────────────────────────────────────

    def _require_db(self):
        if self._db is None:
            raise RuntimeError(
                "MongoDB is not connected — storage unavailable"
            )
        return self._db

    def _ensure_indexes(self) -> None:
        self._db.audit_log.create_index("tool_call.tool")
        self._db.audit_log.create_index("decision.outcome")
        self._db.hitl_queue.create_index("status")

    # ── audit log ─────────────────────────────────────────────────────

    def write_audit_record(self, record: AuditRecord) -> None:
        coll = self._require_db()["audit_log"]
        doc = record.model_dump()
        doc["_id"] = doc.pop("id")
        try:
            coll.insert_one(doc)
        except Exception as exc:
            raise RuntimeError(f"MongoDB write failed: {exc}") from exc

    def update_audit_record(
        self,
        record_id: str,
        **updates: Any,
    ) -> Optional[AuditRecord]:
        coll = self._require_db()["audit_log"]
        try:
            doc = coll.find_one_and_update(
                {"_id": record_id},
                {"$set": updates},
                return_document=True,
            )
        except Exception as exc:
            raise RuntimeError(f"MongoDB update failed: {exc}") from exc
        if doc is None:
            return None
        doc["id"] = str(doc.pop("_id"))
        return AuditRecord.model_validate(doc)

    def list_audit_records(
        self,
        limit: int = 100,
        tool: Optional[str] = None,
        outcome: Optional[str] = None,
        since: Optional[datetime] = None,
        until: Optional[datetime] = None,
    ) -> list[AuditRecord]:
        coll = self._require_db()["audit_log"]
        query: dict[str, Any] = {}
        if tool is not None:
            query["tool_call.tool"] = tool
        if outcome is not None:
            query["decision.outcome"] = outcome
        if since is not None or until is not None:
            time_q: dict[str, Any] = {}
            if since is not None:
                time_q["$gte"] = since
            if until is not None:
                time_q["$lte"] = until
            query["created_at"] = time_q

        try:
            cursor = coll.find(query).sort("created_at", -1).limit(limit)
            result: list[AuditRecord] = []
            for doc in cursor:
                doc["id"] = str(doc.pop("_id"))
                try:
                    result.append(AuditRecord.model_validate(doc))
                except Exception:
                    continue
            return result
        except Exception as exc:
            raise RuntimeError(f"MongoDB query failed: {exc}") from exc

    # ── HITL queue ────────────────────────────────────────────────────

    def create_hitl_request(self, request: HitlRequest) -> str:
        coll = self._require_db()["hitl_queue"]
        import uuid as _uu
        request_id = str(_uu.uuid4())
        request.id = request_id
        doc = request.model_dump()
        doc["_id"] = doc.pop("id")
        try:
            coll.insert_one(doc)
        except Exception as exc:
            raise RuntimeError(f"MongoDB write failed: {exc}") from exc
        return request_id

    def get_hitl_request(self, request_id: str) -> Optional[HitlRequest]:
        coll = self._require_db()["hitl_queue"]
        try:
            doc = coll.find_one({"_id": request_id})
        except Exception as exc:
            raise RuntimeError(f"MongoDB query failed: {exc}") from exc
        if doc is None:
            return None
        doc["id"] = str(doc.pop("_id"))
        return HitlRequest.model_validate(doc)

    def list_pending_hitl(self) -> list[HitlRequest]:
        coll = self._require_db()["hitl_queue"]
        try:
            cursor = coll.find({"status": "pending"}).sort("created_at", -1)
            result: list[HitlRequest] = []
            for doc in cursor:
                doc["id"] = str(doc.pop("_id"))
                try:
                    result.append(HitlRequest.model_validate(doc))
                except Exception:
                    continue
            return result
        except Exception as exc:
            raise RuntimeError(f"MongoDB query failed: {exc}") from exc

    def resolve_hitl_request(
        self,
        request_id: str,
        resolution: Literal["approved", "rejected"],
        resolved_by: str,
    ) -> HitlRequest:
        coll = self._require_db()["hitl_queue"]
        from datetime import timezone as _tz
        now = datetime.now(_tz.utc)
        try:
            doc = coll.find_one_and_update(
                {"_id": request_id, "status": "pending"},
                {
                    "$set": {
                        "status": resolution,
                        "resolved_by": resolved_by,
                        "resolved_at": now,
                    }
                },
                return_document=True,
            )
        except Exception as exc:
            raise RuntimeError(f"MongoDB update failed: {exc}") from exc

        if doc is None:
            existing = coll.find_one({"_id": request_id})
            if existing is None:
                raise KeyError(f"HitlRequest '{request_id}' not found")
            raise ValueError(
                f"HitlRequest '{request_id}' is already {existing['status']}"
            )
        doc["id"] = str(doc.pop("_id"))
        return HitlRequest.model_validate(doc)
